"""
conn.py — Warehouse DB cursor, reusing DATABASES["warehouse"] from settings.py.

The DB router (WarehouseRouter) guards ORM model calls.  Raw cursors bypass it
by design — we replicate the write-block here with a regex guard, and set
READ UNCOMMITTED so warehouse reads never acquire shared locks.

Table name is resolved lazily (call-time) from settings.WAREHOUSE_AMR_TABLE so
this module is safe to import before django.setup() runs.
"""

import logging
import re
from contextlib import contextmanager

from django.db import connections, DatabaseError, OperationalError

logger = logging.getLogger(__name__)

WAREHOUSE_COLUMNS = (
    "GENDER", "SPECIMEN_SOURCE", "FACILITY_NAME", "COLLECTION_DATE",
    "DATE_TESTED", "ORGANISM_ISOLATED", "TIME_POSTED", "C_ANALYSIS_NAME",
    "ORGANISM_ISOLATED_CDW", "SPECIMEN_SOURCE_CDW", "AGE",
)

_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|EXEC|MERGE)\b", re.I
)


def _table() -> str:
    """Lazy settings read — safe to call before django.setup()."""
    from django.conf import settings
    return getattr(settings, "WAREHOUSE_AMR_TABLE", "dbo.AMR_DATA")


@contextmanager
def get_warehouse_cursor():
    """
    Yield a READ UNCOMMITTED cursor on DATABASES["warehouse"].

    Raises PermissionError on any write SQL before execution.
    Cursor is always closed on exit.
    """
    cur = None
    try:
        cur = connections["warehouse"].cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        yield cur
    except (OperationalError, DatabaseError):
        logger.exception("Warehouse cursor error")
        raise
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass


def fetch_warehouse_data(columns: tuple = WAREHOUSE_COLUMNS, batch_size: int = 10_000) -> list[dict]:
    """
    Stream all AMR rows from the warehouse in batches.

    Args:
        columns:    Columns to SELECT. Defaults to WAREHOUSE_COLUMNS.
        batch_size: Rows per fetchmany call. Tune for memory vs throughput.

    Returns:
        List of dicts keyed by column name, or [] on error.
    """
    sql = "SELECT {} FROM {}".format(", ".join(f"[{c}]" for c in columns), _table())

    if _WRITE_RE.search(sql):
        logger.critical("Write SQL blocked on warehouse cursor: %s", sql[:120])
        return []

    rows: list[dict] = []
    try:
        with get_warehouse_cursor() as cur:
            cur.execute(sql)
            col_names = [d[0] for d in cur.description]
            while batch := cur.fetchmany(batch_size):
                rows.extend(dict(zip(col_names, row)) for row in batch)
        logger.info("Warehouse fetch: %d rows", len(rows))
    except Exception:
        logger.exception("fetch_warehouse_data failed")
        return []
    return rows