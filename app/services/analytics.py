"""
AMR Aggregation

"""

import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import TypedDict

from django.core.cache import cache

from app.services.pipeline import run_pipeline

logger = logging.getLogger(__name__)

_TTL = 60 * 60 * 5

_K_FACILITIES = "amr:v1:facilities:{}"
_K_PATHOGENS  = "amr:v1:pathogens:{}"
_K_TREND      = "amr:v1:trend:{}"
_K_DETAIL     = "amr:v1:detail:{}"



class FacilityRow(TypedDict):
    facility_name: str
    county: str
    isolates: int


# class PathogenFacility(TypedDict):
#     """One facility entry inside a PathogenRow."""
#     facility_name: str
#     county: str
#     isolates: int


# class PathogenRow(TypedDict):
#     organism: str
#     total_isolates: int
#     facilities: list[PathogenFacility]   # per-facility breakdown
#     counties: list[str]                  # distinct counties



class PathogenQuality(TypedDict):
    missing_county: int
    missing_date: int
    invalid_date: int
    missing_facility: int


class PathogenFacility(TypedDict):
    facility_name: str
    county: str
    isolates: int          # recomputed on frontend; backend sends full total
    first_seen: str        # "YYYY-MM-DD" | ""
    last_seen: str         # "YYYY-MM-DD" | ""
    monthly_counts: list   # [{month: "YYYY-MM", isolates: int}, ...]


class PathogenRow(TypedDict):
    organism: str
    total_isolates: int    # backend total (all time) — frontend overrides this
    facilities: list[PathogenFacility]
    counties: list[str]
    first_seen: str
    last_seen: str
    months_active: int


class TrendRow(TypedDict):
    month: str           # "YYYY-MM"
    facility_name: str
    county: str
    isolates: int        # raw count — no rate; frontend derives what it needs


class DataQualityFlags(TypedDict):
    unknown_gender: int
    flagged_specimen_date: int
    empty_specimen_date: int
    missing_organism: int
    unmapped_facility: int
    missing_county: int
    empty_specimen: int


class SummaryBlock(TypedDict):
    total_isolates: int
    resistance_rate: float
    facilities_reporting: int
    unknown_gender_count: int
    flagged_dates: int
    missing_organisms: int
    data_quality_flags: DataQualityFlags
    fetched_at: str


class DetailPayload(TypedDict):
    status: str
    fetched_at: str
    summary: SummaryBlock
    records: list[dict]



def _pipeline():
    """Return PipelineResult from in-process cache. Never re-fetches."""
    return run_pipeline(force_refresh=False)


def _is_real_organism(org: str) -> bool:
    """False for blank / 'No organism isolated' strings."""
    return bool(org) and "no organism" not in org.lower()

def _parse_date(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y")
    except (ValueError, AttributeError, TypeError):
        return None



def bust_cache() -> None:
    """
    Invalidate all analytics cache entries.
    Call after run_pipeline(force_refresh=True) to ensure fresh aggregations.
    """
    result = _pipeline()
    stamp = result.fetched_at
    cache.delete_many([
        _K_FACILITIES.format(stamp),
        _K_PATHOGENS.format(stamp),
        _K_TREND.format(stamp),
        _K_DETAIL.format(stamp),
    ])
    logger.info("Analytics cache busted for stamp=%s", stamp)




def get_facilities() -> list[FacilityRow]:
    """
    All facilities with isolate count, sorted by isolate count descending.

    """
    result = _pipeline()
    key = _K_FACILITIES.format(result.fetched_at)
    cached = cache.get(key)
    if cached is not None:
        logger.debug("get_facilities: cache hit")
        return cached

    counts: Counter = Counter()
    county_map: dict[str, str] = {}

    for r in result.records:
        fac = r.get("facility_name") or ""
        if not fac:
            continue
        counts[fac] += 1
        county_map.setdefault(fac, r.get("county") or "")

    out: list[FacilityRow] = [
        {"facility_name": fac, "county": county_map[fac], "isolates": n}
        for fac, n in counts.most_common()
    ]

    cache.set(key, out, _TTL)
    logger.debug("get_facilities: computed %d rows, cached", len(out))
    return out


# def get_pathogens() -> list[PathogenRow]:
#     """
#     All pathogens with total isolates and per-facility breakdown.
#     """
#     result = _pipeline()
#     key = _K_PATHOGENS.format(result.fetched_at)
#     cached = cache.get(key)
#     if cached is not None:
#         logger.debug("get_pathogens: cache hit")
#         return cached

#     # org → facility → {county, count}
#     tree: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(
#         lambda: {"county": "", "isolates": 0}
#     ))

#     for r in result.records:
#         org = (r.get("organism") or "").strip()
#         if not _is_real_organism(org):
#             continue
#         fac = r.get("facility_name") or ""
#         cty = r.get("county") or ""
#         tree[org][fac]["isolates"] += 1
#         tree[org][fac]["county"] = tree[org][fac]["county"] or cty

#     out: list[PathogenRow] = []
#     for org, fac_map in tree.items():
#         fac_rows: list[PathogenFacility] = sorted(
#             [
#                 {"facility_name": fac, "county": data["county"], "isolates": data["isolates"]}
#                 for fac, data in fac_map.items()
#             ],
#             key=lambda x: x["isolates"],
#             reverse=True,
#         )
#         counties = sorted({d["county"] for d in fac_rows if d["county"]})
#         out.append({
#             "organism":       org,
#             "total_isolates": sum(d["isolates"] for d in fac_rows),
#             "facilities":     fac_rows,
#             "counties":       counties,
#         })

#     out.sort(key=lambda x: x["total_isolates"], reverse=True)
#     cache.set(key, out, _TTL)
#     logger.debug("get_pathogens: computed %d pathogens, cached", len(out))
#     return out




def get_pathogens() -> list[PathogenRow]:
    result = _pipeline()
    key    = "amr:v3:pathogens:{}".format(result.fetched_at)
    cached = cache.get(key)
    if cached is not None:
        return cached

    # org → facility → {county, month_counts: {month: int}, dates: []}
    tree: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"county": "", "month_counts": defaultdict(int), "dates": []})
    )
    org_dates: dict[str, list] = defaultdict(list)

    for r in result.records:
        org = (r.get("organism") or "").strip()
        if not org:
            continue

        fac      = (r.get("facility_name") or "").strip()
        cty      = (r.get("county") or "").strip()
        raw_date = (r.get("specimen_date") or "").strip()

        dt = _parse_date(raw_date) if raw_date else None
        month = dt.strftime("%Y-%m") if dt else None

        bucket = tree[org][fac]
        if cty and not bucket["county"]:
            bucket["county"] = cty
        if month:
            bucket["month_counts"][month] += 1
            bucket["dates"].append(dt)
            org_dates[org].append(dt)
        else:
            # still count it — under a sentinel so frontend knows it's undated
            bucket["month_counts"]["0000-00"] = bucket["month_counts"].get("0000-00", 0) + 1

    out: list[PathogenRow] = []
    for org, fac_map in tree.items():
        fac_rows = []
        for fac, data in fac_map.items():
            data["dates"].sort()
            monthly = sorted(
                [{"month": m, "isolates": c} for m, c in data["month_counts"].items()],
                key=lambda x: x["month"]
            )
            total = sum(c for m, c in data["month_counts"].items())
            fac_rows.append({
                "facility_name":  fac or "",
                "county":         data["county"],
                "isolates":       total,
                "first_seen":     data["dates"][0].strftime("%Y-%m-%d")  if data["dates"] else "",
                "last_seen":      data["dates"][-1].strftime("%Y-%m-%d") if data["dates"] else "",
                "monthly_counts": monthly,
            })
        fac_rows.sort(key=lambda x: x["isolates"], reverse=True)

        all_dates = sorted(org_dates[org])
        months_active = len({d.strftime("%Y-%m") for d in all_dates})
        counties = sorted({f["county"] for f in fac_rows if f["county"]})

        out.append({
            "organism":       org,
            "total_isolates": sum(f["isolates"] for f in fac_rows),
            "facilities":     fac_rows,
            "counties":       counties,
            "first_seen":     all_dates[0].strftime("%Y-%m-%d")  if all_dates else "",
            "last_seen":      all_dates[-1].strftime("%Y-%m-%d") if all_dates else "",
            "months_active":  months_active,
        })

    out.sort(key=lambda x: x["total_isolates"], reverse=True)
    cache.set(key, out, _TTL)
    return out

def get_trend() -> list[TrendRow]:
    """
    Monthly isolate counts per facility, sorted by month asc then facility asc.

    """
    result = _pipeline()
    key = _K_TREND.format(result.fetched_at)
    cached = cache.get(key)
    if cached is not None:
        logger.debug("get_trend: cache hit")
        return cached

    # (month, facility) → {county, count}
    buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"county": "", "isolates": 0}
    )

    for r in result.records:
        sd = r.get("specimen_date") or ""
        try:
            month = datetime.strptime(sd, "%d/%m/%Y").strftime("%Y-%m")
        except (ValueError, TypeError):
            continue
        fac = r.get("facility_name") or ""
        cty = r.get("county") or ""
        k = (month, fac)
        buckets[k]["isolates"] += 1
        buckets[k]["county"] = buckets[k]["county"] or cty

    out: list[TrendRow] = sorted(
        [
            {
                "month":         month,
                "facility_name": fac,
                "county":        buckets[(month, fac)]["county"],
                "isolates":      buckets[(month, fac)]["isolates"],
            }
            for (month, fac) in buckets
        ],
        key=lambda x: (x["month"], x["facility_name"]),
    )

    cache.set(key, out, _TTL)
    logger.debug("get_trend: computed %d rows, cached", len(out))
    return out



def get_detail() -> DetailPayload:
    """
    Full summary block + every cleaned record, unfiltered.
s}
    """
    result = _pipeline()
    key = _K_DETAIL.format(result.fetched_at)
    cached = cache.get(key)
    if cached is not None:
        logger.debug("get_detail: cache hit")
        return cached

    records = result.records
    total   = result.total
    flags   = result.flags

    with_org = sum(1 for r in records if _is_real_organism(r.get("organism") or ""))
    rate     = round(with_org / total * 100, 1) if total else 0.0
    fac_set  = {r["facility_name"] for r in records if r.get("facility_name")}

    summary: SummaryBlock = {
        "total_isolates":       total,
        "resistance_rate":      rate,
        "facilities_reporting": len(fac_set),
        "unknown_gender_count": flags.get("unknown_gender", 0),
        "flagged_dates":        flags.get("flagged_specimen_date", 0),
        "missing_organisms":    flags.get("missing_organism", 0),
        "data_quality_flags":   {
            "unknown_gender":        flags.get("unknown_gender", 0),
            "flagged_specimen_date": flags.get("flagged_specimen_date", 0),
            "empty_specimen_date":   flags.get("empty_specimen_date", 0),
            "missing_organism":      flags.get("missing_organism", 0),
            "unmapped_facility":     flags.get("unmapped_facility", 0),
            "missing_county":        flags.get("missing_county", 0),
            "empty_specimen":        flags.get("empty_specimen", 0),
        },
        "fetched_at": result.fetched_at,
    }

    payload: DetailPayload = {
        "status":     "ok" if records else "error",
        "fetched_at": result.fetched_at,
        "summary":    summary,
        "records":    records,
    }

    cache.set(key, payload, _TTL)
    logger.debug("get_detail: cached %d records", total)
    return payload