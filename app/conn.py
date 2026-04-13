

# """
# conn.py — Warehouse DB cursor with Excel fallback.
# Never raises outside fetch_warehouse_data(); always falls back gracefully.
# """

# import logging
# import re
# from contextlib import contextmanager
# from pathlib import Path

# import pandas as pd
# from django.db import connections, DatabaseError, OperationalError

# logger = logging.getLogger(__name__)

# BACKUP_FILE = Path(__file__).resolve().parent / "data" / "backup.xlsx"

# WAREHOUSE_COLUMNS = (
#     "GENDER", "SPECIMEN_SOURCE", "FACILITY_NAME", "COLLECTION_DATE",
#     "DATE_TESTED", "ORGANISM_ISOLATED", "TIME_POSTED", "C_ANALYSIS_NAME",
#     "ORGANISM_ISOLATED_CDW", "SPECIMEN_SOURCE_CDW", "AGE",
# )

# # Facilities to remove (by use code)
# FACILITIES_TO_REMOVE = {
#     "15077",   # Longisa County Referral Hospital
#     "19543",   # THE MATER HOSPITAL (WESTLANDS)
#     "2",
#     "13098",   # MP SHAH HOSPITAL (WESTLANDS)
#     "25582",   # THE NAIROBI HOSPITAL (CAPITAL CENTRE)
#     "99999",   # LIMS TEST FACILITY
#     "13240",   # Umoja Health Centre
#     "12345",   # Kiseuni Dispensary
#     "25412",   # Ammusait General Hospital
#     "13359",   # HAGADERA HOSPITAL
#     "2",
# }

# _WRITE_RE = re.compile(
#     r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|EXEC|MERGE)\b", re.I
# )


# def _table() -> str:
#     from django.conf import settings
#     return getattr(settings, "WAREHOUSE_AMR_TABLE", "dbo.AMR_DATA")


# def _is_facility_excluded(use_code) -> bool:
#     """
#     Check if a facility should be filtered out by use code.
    

    
#     Returns:
#         True if facility should be excluded, False otherwise.
#     """
#     if not use_code:
#         return False
    
#     code = str(use_code).strip()
#     return code in FACILITIES_TO_REMOVE


# def _filter_excluded_facilities(rows: list[dict]) -> list[dict]:
#     """
#     Filter out rows from excluded facilities by USE_CODE.
    
#     Args:
#         rows: List of warehouse/Excel rows.
    
#     Returns:
#         Filtered list excluding rows with use codes in FACILITIES_TO_REMOVE.
#     """
#     initial_count = len(rows)
#     filtered = [
#         row for row in rows
#         if not _is_facility_excluded(row.get("FACILITY_NAME"))
#     ]
#     removed_count = initial_count - len(filtered)
    
#     if removed_count > 0:
#         logger.info(
#             "_filter_excluded_facilities: Removed %d rows from excluded facilities.",
#             removed_count
#         )
    
#     return filtered


# # -----------------------------------------------------------------------
# # Cursor — lazy, never calls ensure_connection() at import / startup time
# # -----------------------------------------------------------------------

# @contextmanager
# def get_warehouse_cursor(timeout_seconds: int = 2):
#     """
#     Yields a live pyodbc cursor pointed at the warehouse.
#     Raises OperationalError / DatabaseError on failure — caller decides
#     what to do (fetch_warehouse_data falls back to Excel).

#     KEY CHANGE: we do NOT call conn.ensure_connection() explicitly.
#     Django does that lazily when we call conn.cursor(), so management
#     commands (createsuperuser, migrate, etc.) never touch the warehouse
#     unless this function is actually called from request-handling code.
#     """
#     cur = None
#     try:
#         conn = connections["warehouse"]

#         # --- set login/query timeout BEFORE opening the connection ---
#         # django-mssql / pyodbc respects these on the raw Options dict
#         # before the socket is opened, so the 2-second cap is enforced
#         # at the TCP handshake level, not just at the query level.
#         db_settings = conn.settings_dict
#         db_settings.setdefault("OPTIONS", {})
#         db_settings["OPTIONS"]["timeout"] = timeout_seconds  # pyodbc login timeout

#         # cursor() opens the connection lazily if not already open
#         cur = conn.cursor()

#         # per-session query timeout (ms) — belt-and-suspenders
#         try:
#             cur.execute(f"SET LOCK_TIMEOUT {timeout_seconds * 1000}")
#         except Exception:
#             pass

#         cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")

#         yield cur

#     except (OperationalError, DatabaseError):
#         logger.warning(
#             "Warehouse unreachable (timeout=%ss) — caller should fall back",
#             timeout_seconds,
#         )
#         raise  # let fetch_warehouse_data() handle it

#     finally:
#         if cur is not None:
#             try:
#                 cur.close()
#             except Exception:
#                 pass
#         # Close the underlying connection so a failed/timed-out socket
#         # is never reused by the next request in the same worker process.
#         try:
#             connections["warehouse"].close()
#         except Exception:
#             pass


# # -----------------------------------------------------------------------
# # Excel fallback
# # -----------------------------------------------------------------------

# def fetch_backup_excel(columns: tuple = WAREHOUSE_COLUMNS) -> list[dict]:
#     try:
#         if not BACKUP_FILE.exists():
#             logger.error("Backup file not found: %s", BACKUP_FILE)
#             return []

#         # read header first to validate columns
#         df_preview = pd.read_excel(BACKUP_FILE, nrows=0, engine="openpyxl")
#         excel_cols = list(df_preview.columns)

#         valid_cols = [c for c in columns if c in excel_cols]
#         missing_cols = [c for c in columns if c not in excel_cols]

#         if missing_cols:
#             logger.warning("Missing columns in backup.xlsx: %s", missing_cols)

#         df = pd.read_excel(BACKUP_FILE, usecols=valid_cols, engine="openpyxl")

#         for col in missing_cols:
#             df[col] = None

#         df = df[list(columns)]

#         logger.warning("Using Excel backup (%d rows loaded)", len(df))
        
#         # Filter excluded facilities before returning
#         rows = df.to_dict(orient="records")
#         rows = _filter_excluded_facilities(rows)
        
#         return rows

#     except Exception:
#         logger.exception("Failed loading Excel backup")
#         return []


# # -----------------------------------------------------------------------
# # Main entry point
# # -----------------------------------------------------------------------

# def fetch_warehouse_data(
#     columns: tuple = WAREHOUSE_COLUMNS,
#     batch_size: int = 10_000,
# ) -> list[dict]:
#     """
#     Returns rows from the warehouse, or from Excel if the warehouse is
#     unreachable.  Never raises — always returns a (possibly empty) list.
    
#     Filters out rows from excluded facilities (by FACILITY_NAME) before
#     returning, regardless of source (warehouse or Excel backup).
#     """
#     sql = "SELECT {} FROM {}".format(
#         ", ".join(f"[{c}]" for c in columns),
#         _table(),
#     )

#     if _WRITE_RE.search(sql):
#         logger.critical("Write SQL blocked: %s", sql[:120])
#         return []

#     try:
#         rows: list[dict] = []
#         with get_warehouse_cursor() as cur:
#             cur.execute(sql)
#             col_names = [d[0] for d in cur.description]
#             while batch := cur.fetchmany(batch_size):
#                 rows.extend(dict(zip(col_names, row)) for row in batch)

#         logger.info("Warehouse fetch OK: %d rows", len(rows))
        
#         # Filter excluded facilities before returning
#         rows = _filter_excluded_facilities(rows)
        
#         return rows

#     except Exception:
#         logger.warning("Warehouse fetch failed — switching to Excel backup")
#         return fetch_backup_excel(columns)



"""
conn.py — Warehouse DB cursor with Excel fallback.
Never raises outside fetch_warehouse_data(); always falls back gracefully.
"""

import logging
import re
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
from django.db import connections, DatabaseError, OperationalError

logger = logging.getLogger(__name__)

BACKUP_FILE = Path(__file__).resolve().parent / "data" / "backup.xlsx"

WAREHOUSE_COLUMNS = (
    "GENDER", "SPECIMEN_SOURCE", "FACILITY_NAME", "COLLECTION_DATE",
    "DATE_TESTED", "ORGANISM_ISOLATED", "TIME_POSTED", "C_ANALYSIS_NAME",
    "ORGANISM_ISOLATED_CDW", "SPECIMEN_SOURCE_CDW", "AGE",
)

# Facilities to remove (by use code)
FACILITIES_TO_REMOVE = {
    "15077",   # Longisa County Referral Hospital
    "19543",   # THE MATER HOSPITAL (WESTLANDS)
    "2",
    "13098",   # MP SHAH HOSPITAL (WESTLANDS)
    "25582",   # THE NAIROBI HOSPITAL (CAPITAL CENTRE)
    "99999",   # LIMS TEST FACILITY
    "13240",   # Umoja Health Centre
    "12345",   # Kiseuni Dispensary
    "25412",   # Ammusait General Hospital
    "13359",   # HAGADERA HOSPITAL
    "2",
}

# ---------------------------------------------------------------------------
# Master switch — set to True to query the live warehouse,
# False to read directly from the Excel backup (fast path, no DB required).
# ---------------------------------------------------------------------------
CONNECT_TO_WAREHOUSE: bool = False

_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|EXEC|MERGE)\b", re.I
)


def _table() -> str:
    from django.conf import settings
    return getattr(settings, "WAREHOUSE_AMR_TABLE", "dbo.AMR_DATA")


def _is_facility_excluded(use_code) -> bool:
    """
    Check if a facility should be filtered out by use code.
    

    
    Returns:
        True if facility should be excluded, False otherwise.
    """
    if not use_code:
        return False
    
    code = str(use_code).strip()
    return code in FACILITIES_TO_REMOVE


def _filter_excluded_facilities(rows: list[dict]) -> list[dict]:
    """
    Filter out rows from excluded facilities by USE_CODE.
    
    Args:
        rows: List of warehouse/Excel rows.
    
    Returns:
        Filtered list excluding rows with use codes in FACILITIES_TO_REMOVE.
    """
    initial_count = len(rows)
    filtered = [
        row for row in rows
        if not _is_facility_excluded(row.get("FACILITY_NAME"))
    ]
    removed_count = initial_count - len(filtered)
    
    if removed_count > 0:
        logger.info(
            "_filter_excluded_facilities: Removed %d rows from excluded facilities.",
            removed_count
        )
    
    return filtered


# -----------------------------------------------------------------------
# Cursor — lazy, never calls ensure_connection() at import / startup time
# -----------------------------------------------------------------------

@contextmanager
def get_warehouse_cursor(timeout_seconds: int = 2):
    """
    Yields a live pyodbc cursor pointed at the warehouse.
    Raises OperationalError / DatabaseError on failure — caller decides
    what to do (fetch_warehouse_data falls back to Excel).

    KEY CHANGE: we do NOT call conn.ensure_connection() explicitly.
    Django does that lazily when we call conn.cursor(), so management
    commands (createsuperuser, migrate, etc.) never touch the warehouse
    unless this function is actually called from request-handling code.
    """
    cur = None
    try:
        conn = connections["warehouse"]

        # --- set login/query timeout BEFORE opening the connection ---
        # django-mssql / pyodbc respects these on the raw Options dict
        # before the socket is opened, so the 2-second cap is enforced
        # at the TCP handshake level, not just at the query level.
        db_settings = conn.settings_dict
        db_settings.setdefault("OPTIONS", {})
        db_settings["OPTIONS"]["timeout"] = timeout_seconds  # pyodbc login timeout

        # cursor() opens the connection lazily if not already open
        cur = conn.cursor()

        # per-session query timeout (ms) — belt-and-suspenders
        try:
            cur.execute(f"SET LOCK_TIMEOUT {timeout_seconds * 1000}")
        except Exception:
            pass

        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")

        yield cur

    except (OperationalError, DatabaseError):
        logger.warning(
            "Warehouse unreachable (timeout=%ss) — caller should fall back",
            timeout_seconds,
        )
        raise  # let fetch_warehouse_data() handle it

    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        # Close the underlying connection so a failed/timed-out socket
        # is never reused by the next request in the same worker process.
        try:
            connections["warehouse"].close()
        except Exception:
            pass


# -----------------------------------------------------------------------
# Excel fallback
# -----------------------------------------------------------------------

def fetch_backup_excel(columns: tuple = WAREHOUSE_COLUMNS) -> list[dict]:
    """
    Read backup.xlsx as fast as possible.

    Fast path  — tries calamine (rust-based, ~5-10x faster than openpyxl).
    Slow path  — falls back to openpyxl if calamine is not installed.
    Both paths skip unused columns and avoid a full in-memory copy by
    converting only the needed columns directly to records.
    """
    try:
        if not BACKUP_FILE.exists():
            logger.error("Backup file not found: %s", BACKUP_FILE)
            return []

        # ---- choose the fastest available engine ----
        # calamine (python-calamine) is a Rust-backed reader; install with:
        #   pip install python-calamine
        # It is read-only and does not evaluate formulae, which is fine here.
        try:
            import python_calamine  # noqa: F401 — presence check only
            engine = "calamine"
        except ImportError:
            engine = "openpyxl"

        # Read header row only to identify which requested columns exist
        df_preview = pd.read_excel(BACKUP_FILE, nrows=0, engine=engine)
        excel_cols = set(df_preview.columns)

        valid_cols   = [c for c in columns if c in excel_cols]
        missing_cols = [c for c in columns if c not in excel_cols]

        if missing_cols:
            logger.warning("Missing columns in backup.xlsx: %s", missing_cols)

        # Read only the columns we need — reduces I/O and memory
        df = pd.read_excel(BACKUP_FILE, usecols=valid_cols, engine=engine)

        # Re-order / add missing columns as None
        for col in missing_cols:
            df[col] = None
        df = df[list(columns)]

        logger.warning(
            "Using Excel backup via %s engine (%d rows loaded)", engine, len(df)
        )

        # Filter excluded facilities before returning
        rows = df.to_dict(orient="records")
        rows = _filter_excluded_facilities(rows)

        return rows

    except Exception:
        logger.exception("Failed loading Excel backup")
        return []


# -----------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------

def fetch_warehouse_data(
    columns: tuple = WAREHOUSE_COLUMNS,
    batch_size: int = 10_000,
) -> list[dict]:
    """
    Returns rows from the warehouse, or from Excel if the warehouse is
    unreachable.  Never raises — always returns a (possibly empty) list.
    
    Filters out rows from excluded facilities (by FACILITY_NAME) before
    returning, regardless of source (warehouse or Excel backup).

    When CONNECT_TO_WAREHOUSE is False the live DB is never contacted and
    the Excel backup is read immediately (fast path).
    """
    if not CONNECT_TO_WAREHOUSE:
        logger.info(
            "CONNECT_TO_WAREHOUSE=False — reading directly from Excel backup."
        )
        return fetch_backup_excel(columns)

    sql = "SELECT {} FROM {}".format(
        ", ".join(f"[{c}]" for c in columns),
        _table(),
    )

    if _WRITE_RE.search(sql):
        logger.critical("Write SQL blocked: %s", sql[:120])
        return []

    try:
        rows: list[dict] = []
        with get_warehouse_cursor() as cur:
            cur.execute(sql)
            col_names = [d[0] for d in cur.description]
            while batch := cur.fetchmany(batch_size):
                rows.extend(dict(zip(col_names, row)) for row in batch)

        logger.info("Warehouse fetch OK: %d rows", len(rows))
        
        # Filter excluded facilities before returning
        rows = _filter_excluded_facilities(rows)
        
        return rows

    except Exception:
        logger.warning("Warehouse fetch failed — switching to Excel backup")
        return fetch_backup_excel(columns)