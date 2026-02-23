"""
AMR Data Pipeline
============================================
Entry point for the AMR data pipeline.  Coordinates:
    1. Fetch   — single DB read from the warehouse (conn.py)
    2. Clean   — gender, dates, facility, specimen, organism (clean.py)
    3. Transform — shape data into the JSON response format (transform.py)

"""

import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any

from app.conn import fetch_warehouse_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL in-process cache (avoids hammering the warehouse on every request)
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 300   
_cache: dict[str, Any] = {}


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    value, expiry = entry
    if time.monotonic() > expiry:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int = _CACHE_TTL_SECONDS) -> None:
    _cache[key] = (value, time.monotonic() + ttl)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """
    Encapsulates the full output of the AMR pipeline.

    Attributes:
        records      (list[dict]):  Cleaned + transformed records ready for
                                    downstream analysis or serialisation.
        total        (int):         Total number of records returned.
        fetched_at   (str):         ISO-8601 UTC-like timestamp of when data
                                    was fetched (wall-clock date).
        flags        (dict):        Aggregate flag counts for data-quality
                                    monitoring (e.g. unknown gender count,
                                    flagged dates, missing organisms).
        errors       (list[str]):   Non-fatal issues encountered during the run.
    """
    records: list[dict] = field(default_factory=list)
    total: int = 0
    fetched_at: str = ""
    flags: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict representation."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_flags(records: list[dict]) -> dict:
    """
    Compute data-quality flag counts across all records.

    Args:
        records (list[dict]): Cleaned + transformed records.

    Returns:
        dict: Keys are flag names, values are integer counts.
    """
    flags: dict[str, int] = {
        "unknown_gender": 0,
        "flagged_specimen_date": 0,
        "empty_specimen_date": 0,
        "missing_organism": 0,
        "unmapped_facility": 0,
        "missing_county": 0,
        "empty_specimen": 0,
    }
    for r in records:
        if r.get("gender") == "Unknown":
            flags["unknown_gender"] += 1
        if r.get("specimen_date_flag"):
            flags["flagged_specimen_date"] += 1
        if not r.get("specimen_date"):
            flags["empty_specimen_date"] += 1
        if r.get("organism_flag"):
            flags["missing_organism"] += 1
        if not r.get("facility_name"):
            flags["unmapped_facility"] += 1
        if not r.get("county"):
            flags["missing_county"] += 1
        if not r.get("specimen"):
            flags["empty_specimen"] += 1
    return flags


def _transform_record(row: dict) -> dict:
    """
    Project a cleaned row into the canonical JSON response shape.

    Includes only the fields needed by the analysis layer.  Raw warehouse
    columns are excluded to keep response payloads lean.

    Args:
        row (dict): A cleaned row from ``clean.clean_records()``.

    Returns:
        dict: Record with keys:
            facility_name, county, specimen, specimen_date,
            specimen_date_flag, organism, organism_flag,
            gender, age
    """
    return {
        "facility_name":      row.get("facility_name", ""),
        "county":             row.get("county", ""),
        "specimen":           row.get("specimen", ""),
        "specimen_date":      row.get("specimen_date", ""),
        "specimen_date_flag": bool(row.get("specimen_date_flag", False)),
        "organism":           row.get("organism", ""),
        "organism_flag":      bool(row.get("organism_flag", False)),
        "gender":             row.get("gender", "Unknown"),
        "age":                row.get("AGE"),
    }


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(force_refresh: bool = False) -> PipelineResult:
    """
    Execute the full AMR data pipeline and return a ``PipelineResult``.

    Steps:
        1. Check in-process TTL cache (skip if ``force_refresh=True``).
        2. Fetch raw rows from the warehouse via ``conn.fetch_warehouse_data()``.
        3. Clean rows via ``clean.clean_records()``.
        4. Transform each row to the canonical response shape.
        5. Compute aggregate data-quality flags.
        6. Cache the result and return.

    The DB connection is held only during step 2 (fetch).  Steps 3–5 are
    pure Python and do not touch the DB.

    Args:
        force_refresh (bool): If True, bypass the cache and re-fetch from DB.
                              Useful for scheduled refresh jobs.

    Returns:
        PipelineResult: Contains ``records``, ``total``, ``fetched_at``,
                        ``flags``, and ``errors``.

    Raises:
        Does not raise — errors are caught, logged, and included in
        ``PipelineResult.errors``.  An empty ``PipelineResult`` is returned
        on total failure.
    """
    cache_key = "amr_pipeline_result"
    if not force_refresh:
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.debug("Pipeline: returning cached result (%d records).", cached.total)
            return cached

    result = PipelineResult(fetched_at=date.today().isoformat())
    errors: list[str] = []

    # --- Step 1: Fetch -------------------------------------------------------
    try:
    
        raw_rows = fetch_warehouse_data()
    except Exception as exc:
        msg = f"Fetch failed: {exc}"
        logger.error(msg, exc_info=True)
        result.errors.append(msg)
        return result

    if not raw_rows:
        result.errors.append("Fetch returned 0 rows — check warehouse connectivity.")
        return result

    # --- Step 2: Clean -------------------------------------------------------
    try:
        from app.services.clean import clean_records
        cleaned = clean_records(raw_rows)
    except Exception as exc:
        msg = f"Cleaning failed: {exc}"
        logger.error(msg, exc_info=True)
        result.errors.append(msg)
        return result

    # --- Step 3: Transform ---------------------------------------------------
    transformed: list[dict] = []
    for row in cleaned:
        try:
            transformed.append(_transform_record(row))
        except Exception as exc:
            errors.append(f"Transform error on row: {exc}")

    # --- Step 4: Flags -------------------------------------------------------
    result.records = transformed
    result.total = len(transformed)
    result.flags = _compute_flags(transformed)
    result.errors = errors

    logger.info(
        "Pipeline complete — %d records, flags=%s, errors=%d",
        result.total, result.flags, len(errors),
    )

    _cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Convenience wrapper — returns JSON-serialisable dict
# ---------------------------------------------------------------------------

def get_response_data(force_refresh: bool = False) -> dict:
    """
    Run the pipeline and return a JSON-serialisable response dict.

    This is the recommended entry point for Django views and DRF serialisers.

    Response shape::

        {
            "status":     "ok" | "error",
            "total":      500000,
            "fetched_at": "2025-09-19",
            "flags": {
                "unknown_gender":        12,
                "flagged_specimen_date":  3,
                "empty_specimen_date":    7,
                "missing_organism":      45,
                "unmapped_facility":      0,
                "missing_county":         0,
                "empty_specimen":         5
            },
            "errors": [],
            "records": [
                {
                    "facility_name":      "Kenyatta National Hospital",
                    "county":             "Nairobi",
                    "specimen":           "Urine",
                    "specimen_date":      "09/09/2025",
                    "specimen_date_flag": false,
                    "organism":           "Klebsiella Oxytoca",
                    "organism_flag":      false,
                    "gender":             "Male",
                    "age":                94.0
                },
                ...
            ]
        }

    Args:
        force_refresh (bool): Bypass TTL cache and re-fetch from warehouse.

    Returns:
        dict: JSON-serialisable response.
    """
    result = run_pipeline(force_refresh=force_refresh)
    status = "error" if (not result.records and result.errors) else "ok"
    return {
        "status":     status,
        "total":      result.total,
        "fetched_at": result.fetched_at,
        "flags":      result.flags,
        "errors":     result.errors,
        "records":    result.records,
    }