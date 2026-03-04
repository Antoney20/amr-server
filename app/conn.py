# """
# conn.py — Warehouse DB cursor, reusing DATABASES["warehouse"] from settings.py.

# The DB router (WarehouseRouter) guards ORM model calls.  Raw cursors bypass it
# by design — we replicate the write-block here with a regex guard, and set
# READ UNCOMMITTED so warehouse reads never acquire shared locks.

# Table name is resolved lazily (call-time) from settings.WAREHOUSE_AMR_TABLE so
# this module is safe to import before django.setup() runs.
# """

# import logging
# import re
# from contextlib import contextmanager

# from django.db import connections, DatabaseError, OperationalError

# logger = logging.getLogger(__name__)

# WAREHOUSE_COLUMNS = (
#     "GENDER", "SPECIMEN_SOURCE", "FACILITY_NAME", "COLLECTION_DATE",
#     "DATE_TESTED", "ORGANISM_ISOLATED", "TIME_POSTED", "C_ANALYSIS_NAME",
#     "ORGANISM_ISOLATED_CDW", "SPECIMEN_SOURCE_CDW", "AGE",
# )

# _WRITE_RE = re.compile(
#     r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|EXEC|MERGE)\b", re.I
# )


# def _table() -> str:
#     """Lazy settings read — safe to call before django.setup()."""
#     from django.conf import settings
#     return getattr(settings, "WAREHOUSE_AMR_TABLE", "dbo.AMR_DATA")


# @contextmanager
# def get_warehouse_cursor():
#     """
#     Yield a READ UNCOMMITTED cursor on DATABASES["warehouse"].

#     Raises PermissionError on any write SQL before execution.
#     Cursor is always closed on exit.
#     """
#     cur = None
#     try:
#         cur = connections["warehouse"].cursor()
#         cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
#         yield cur
#     except (OperationalError, DatabaseError):
#         logger.exception("Warehouse cursor error")
#         raise
#     finally:
#         if cur is not None:
#             try:
#                 cur.close()
#             except Exception:
#                 pass


# def fetch_warehouse_data(columns: tuple = WAREHOUSE_COLUMNS, batch_size: int = 10_000) -> list[dict]:
#     """
#     Stream all AMR rows from the warehouse in batches.

#     Args:
#         columns:    Columns to SELECT. Defaults to WAREHOUSE_COLUMNS.
#         batch_size: Rows per fetchmany call. Tune for memory vs throughput.

#     Returns:
#         List of dicts keyed by column name, or [] on error.
#     """
#     sql = "SELECT {} FROM {}".format(", ".join(f"[{c}]" for c in columns), _table())

#     if _WRITE_RE.search(sql):
#         logger.critical("Write SQL blocked on warehouse cursor: %s", sql[:120])
#         return []

#     rows: list[dict] = []
#     try:
#         with get_warehouse_cursor() as cur:
#             cur.execute(sql)
#             col_names = [d[0] for d in cur.description]
#             while batch := cur.fetchmany(batch_size):
#                 rows.extend(dict(zip(col_names, row)) for row in batch)
#         logger.info("Warehouse fetch: %d rows", len(rows))
#     except Exception:
#         logger.exception("fetch_warehouse_data failed")
#         return []
#     return rows


"""
conn.py — Warehouse DB cursor with Excel fallback.
"""

import logging
import re
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
from django.db import connections, DatabaseError, OperationalError

logger = logging.getLogger(__name__)


# ✅ Direct fallback f")ile path
# BACKUP_FILE = Path("./data/backup.xlsx")
# example alternatives:
# Path("./data/backup.xlsx")
# Path(__file__).resolve().parent / "backup.xlsx"

BACKUP_FILE = (
    Path(__file__).resolve().parent / "data" / "backup.xlsx"
)


WAREHOUSE_COLUMNS = (
    "GENDER", "SPECIMEN_SOURCE", "FACILITY_NAME", "COLLECTION_DATE",
    "DATE_TESTED", "ORGANISM_ISOLATED", "TIME_POSTED", "C_ANALYSIS_NAME",
    "ORGANISM_ISOLATED_CDW", "SPECIMEN_SOURCE_CDW", "AGE",
)

_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|EXEC|MERGE)\b", re.I
)


# ---------------------------------------------------
# Lazy table resolution
# ---------------------------------------------------

def _table() -> str:
    from django.conf import settings
    return getattr(settings, "WAREHOUSE_AMR_TABLE", "dbo.AMR_DATA")


# ---------------------------------------------------
# Cursor
# ---------------------------------------------------

# @contextmanager
# def get_warehouse_cursor():
#     cur = None
#     try:
#         cur = connections["warehouse"].cursor()
#         cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
#         yield cur
#     except (OperationalError, DatabaseError):
#         logger.exception("Warehouse cursor error")
#         raise
#     finally:
#         if cur is not None:
#             try:
#                 cur.close()
#             except Exception:
#                 pass



# ---------------------------------------------------
# Cursor (2s timeout)
# ---------------------------------------------------

@contextmanager
def get_warehouse_cursor(timeout_seconds: int = 2):
    cur = None
    try:
        conn = connections["warehouse"]

        # ensure connection opened
        conn.ensure_connection()

        # ---- FORCE DB timeout (seconds → milliseconds) ----
        # works for SQL Server / pyodbc
        raw_conn = conn.connection
        try:
            raw_conn.timeout = timeout_seconds
        except Exception:
            pass

        cur = conn.cursor()

        # isolation level
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")

        # SQL Server query timeout (milliseconds)
        try:
            cur.execute(f"SET LOCK_TIMEOUT {timeout_seconds * 1000}")
        except Exception:
            pass

        yield cur

    except (OperationalError, DatabaseError) as e:
        logger.error(
            "Warehouse timeout after %ss — fallback activated",
            timeout_seconds,
        )
        raise

    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass

# ---------------------------------------------------
# Excel fallback
# ---------------------------------------------------

# def fetch_backup_excel(columns=WAREHOUSE_COLUMNS) -> list[dict]:
#     try:
#         if not BACKUP_FILE.exists():
#             logger.error("Backup file not found: %s", BACKUP_FILE)
#             return []

#         # df = pd.read_excel(BACKUP_FILE, usecols=list(columns))
#         df = pd.read_excel(  BACKUP_FILE, usecols=list(columns), engine="openpyxl",)

#         logger.warning(
#             "Warehouse unavailable — using Excel backup (%d rows)",
#             len(df),
#         )

#         return df.to_dict(orient="records")

#     except Exception:
#         logger.exception("Failed loading Excel backup")
#         return []


def fetch_backup_excel(columns=WAREHOUSE_COLUMNS) -> list[dict]:
    try:
        if not BACKUP_FILE.exists():
            logger.error("Backup file not found: %s", BACKUP_FILE)
            return []

        # ---- read header first ----
        df_preview = pd.read_excel(
            BACKUP_FILE,
            nrows=0,
            engine="openpyxl",
        )

        excel_cols = list(df_preview.columns)
        logger.info("Excel columns found: %s", excel_cols)

        # ---- keep only existing columns ----
        valid_cols = [c for c in columns if c in excel_cols]
        missing_cols = [c for c in columns if c not in excel_cols]

        if missing_cols:
            logger.warning(
                "Missing columns in backup.xlsx: %s",
                missing_cols,
            )

        # ---- load only valid columns ----
        df = pd.read_excel(
            BACKUP_FILE,
            usecols=valid_cols,
            engine="openpyxl",
        )

        # ---- add missing columns as None ----
        for col in missing_cols:
            df[col] = None

        # ---- enforce column order ----
        df = df[list(columns)]

        logger.warning(
            "Using Excel backup (%d rows loaded)",
            len(df),
        )

        return df.to_dict(orient="records")

    except Exception:
        logger.exception("Failed loading Excel backup")
        return []


# ---------------------------------------------------
# Main fetch
# ---------------------------------------------------

def fetch_warehouse_data(
    columns: tuple = WAREHOUSE_COLUMNS,
    batch_size: int = 10_000,
) -> list[dict]:

    sql = "SELECT {} FROM {}".format(
        ", ".join(f"[{c}]" for c in columns),
        _table(),
    )

    if _WRITE_RE.search(sql):
        logger.critical("Write SQL blocked: %s", sql[:120])
        return []

    rows: list[dict] = []

    try:
        with get_warehouse_cursor() as cur:
            cur.execute(sql)

            col_names = [d[0] for d in cur.description]

            while batch := cur.fetchmany(batch_size):
                rows.extend(dict(zip(col_names, row)) for row in batch)

        logger.info("Warehouse fetch successful: %d rows", len(rows))
        return rows

    except Exception:
        logger.exception("Warehouse fetch failed — switching to backup")
        return fetch_backup_excel(columns)