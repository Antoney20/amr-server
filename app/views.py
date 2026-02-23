import logging
from collections import Counter, defaultdict
from datetime import datetime, date

from django.http import JsonResponse
from django.views import View

from app.services.pipeline import get_response_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipeline(request) -> dict:
    """Run or return cached pipeline data. Pass ?refresh=1 to force re-fetch."""
    force = request.GET.get("refresh") == "1"
    return get_response_data(force_refresh=force)


def _parse_date(s: str) -> date | None:
    """Parse DD/MM/YYYY strings coming out of the pipeline."""
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


def _resistance_rate(records: list[dict], organism: str | None = None) -> float:
    """
    Compute resistance rate as % of records where organism is NOT
    'No organism Isolated' / 'No organism isolated' / blank.

    If organism is given, filter to that organism first.
    Returns a float 0–100 rounded to 1 dp.
    """
    pool = records
    if organism:
        pool = [r for r in records if (r.get("organism") or "").lower() == organism.lower()]
    if not pool:
        return 0.0
    resistant = sum(
        1 for r in pool
        if r.get("organism")
        and "no organism" not in r["organism"].lower()
        and r["organism"].strip() != ""
    )
    return round(resistant / len(pool) * 100, 1)



class SummaryView(View):
    """
    GET /api/amr/summary/

    Returns top-level KPI metrics suitable for summary cards.

    """

    def get(self, request):
        data = _pipeline(request)
        if data["status"] == "error":
            return JsonResponse({"status": "error", "errors": data["errors"]}, status=503)

        records = data["records"]
        facilities = {r["facility_name"] for r in records if r.get("facility_name")}

        return JsonResponse({
            "status":               "ok",
            "total_isolates":       data["total"],
            "resistance_rate":      _resistance_rate(records),
            "facilities_reporting": len(facilities),
            "unknown_gender_count": data["flags"].get("unknown_gender", 0),
            "flagged_dates":        data["flags"].get("flagged_specimen_date", 0),
            "missing_organisms":    data["flags"].get("missing_organism", 0),
            "data_quality_flags":   data["flags"],
            "fetched_at":           data["fetched_at"],
        })


# ---------------------------------------------------------------------------
# View 2 — Records table  (paginated)
# ---------------------------------------------------------------------------

class RecordsView(View):
    """
    GET /api/amr/records/

    Paginated list of cleaned AMR records.

    Query parameters:
        page      (int, default 1)      — 1-based page number
        page_size (int, default 100)    — rows per page, max 1000
        facility  (str, optional)       — filter by exact facility_name
        county    (str, optional)       — filter by county
        organism  (str, optional)       — case-insensitive substring match
        specimen  (str, optional)       — case-insensitive substring match
        gender    (str, optional)       — Male | Female | Unknown
        flagged   (0|1, default 0)      — 1 = only flagged records

    """

    PAGE_SIZE_MAX = 1000

    def get(self, request):
        data = _pipeline(request)
        if data["status"] == "error":
            return JsonResponse({"status": "error", "errors": data["errors"]}, status=503)

        records = data["records"]

        # --- Filters ---
        facility = request.GET.get("facility", "").strip()
        county   = request.GET.get("county",   "").strip()
        organism = request.GET.get("organism", "").strip().lower()
        specimen = request.GET.get("specimen", "").strip().lower()
        gender   = request.GET.get("gender",   "").strip()
        flagged  = request.GET.get("flagged",  "0") == "1"

        if facility:
            records = [r for r in records if r.get("facility_name", "").lower() == facility.lower()]
        if county:
            records = [r for r in records if r.get("county", "").lower() == county.lower()]
        if organism:
            records = [r for r in records if organism in (r.get("organism") or "").lower()]
        if specimen:
            records = [r for r in records if specimen in (r.get("specimen") or "").lower()]
        if gender:
            records = [r for r in records if r.get("gender", "") == gender]
        if flagged:
            records = [r for r in records if r.get("specimen_date_flag") or r.get("organism_flag")]

        # --- Pagination ---
        try:
            page = max(1, int(request.GET.get("page", 1)))
        except ValueError:
            page = 1
        try:
            page_size = min(self.PAGE_SIZE_MAX, max(1, int(request.GET.get("page_size", 100))))
        except ValueError:
            page_size = 100

        total   = len(records)
        pages   = max(1, (total + page_size - 1) // page_size)
        page    = min(page, pages)
        start   = (page - 1) * page_size
        end     = start + page_size

        return JsonResponse({
            "status":    "ok",
            "page":      page,
            "page_size": page_size,
            "total":     total,
            "pages":     pages,
            "records":   records[start:end],
        })


# ---------------------------------------------------------------------------
# View 3 — Resistance dashboard
# ---------------------------------------------------------------------------

class ResistanceView(View):
    """
    GET /api/amr/resistance/

    Full dashboard payload matching the AMR surveillance UI:

        • top_pathogens   — isolate counts + resistance % per organism
        • by_facility     — isolate counts per facility (for bar chart)
        • critical_alerts — organisms whose resistance rate exceeds threshold
        • trend           — monthly resistance rate time-series (last 12 months)
        • summary         — headline KPIs (total, rate, facilities, alerts)


    """

    DEFAULT_ALERT_THRESHOLD = 40.0
    # Organisms to always exclude from pathogen stats (no actual growth)
    _NO_GROWTH = {"no organism isolated", "no organism isolated.", ""}

    def get(self, request):
        data = _pipeline(request)
        if data["status"] == "error":
            return JsonResponse({"status": "error", "errors": data["errors"]}, status=503)

        try:
            threshold = float(request.GET.get("alert_threshold", self.DEFAULT_ALERT_THRESHOLD))
        except ValueError:
            threshold = self.DEFAULT_ALERT_THRESHOLD

        records = data["records"]

        top_pathogens   = self._top_pathogens(records)
        by_facility     = self._by_facility(records)
        critical_alerts = self._critical_alerts(top_pathogens, threshold)
        trend           = self._trend(records)

        total_isolates       = data["total"]
        overall_rate         = _resistance_rate(records)
        facilities_reporting = len({r["facility_name"] for r in records if r.get("facility_name")})

        return JsonResponse({
            "status":     "ok",
            "fetched_at": data["fetched_at"],
            "summary": {
                "total_isolates":       total_isolates,
                "resistance_rate":      overall_rate,
                "facilities_reporting": facilities_reporting,
                "critical_alert_count": len(critical_alerts),
            },
            "top_pathogens":   top_pathogens,
            "by_facility":     by_facility,
            "critical_alerts": critical_alerts,
            "trend":           trend,
        })

    # --- internal helpers ---

    def _top_pathogens(self, records: list[dict], top_n: int = 10) -> list[dict]:
        """
        Return the top N organisms by isolate count with their resistance rate.

        Args:
            records: Cleaned pipeline records.
            top_n:   How many to return (default 10).

        Returns:
            List of dicts sorted by isolate count descending, each with:
            organism, isolates, resistance_rate, rank.
        """
        # Group records by organism
        by_org: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            org = (r.get("organism") or "").strip()
            if org.lower() not in self._NO_GROWTH and org:
                by_org[org].append(r)

        results = []
        for org, org_records in by_org.items():
            isolates = len(org_records)
            resistant = sum(
                1 for r in org_records
                if (r.get("organism") or "").strip()
                and "no organism" not in (r.get("organism") or "").lower()
            )
            results.append({
                "organism":        org,
                "isolates":        isolates,
                "resistance_rate": round(resistant / isolates * 100, 1) if isolates else 0.0,
            })

        results.sort(key=lambda x: x["isolates"], reverse=True)
        results = results[:top_n]
        for i, item in enumerate(results, 1):
            item["rank"] = i
        return results

    def _by_facility(self, records: list[dict]) -> list[dict]:
        """
        Isolate counts grouped by facility, sorted descending.

        Returns:
            List of dicts: facility, county, isolates.
        """
        counts: Counter = Counter()
        county_map: dict[str, str] = {}
        for r in records:
            fac = r.get("facility_name") or ""
            if fac:
                counts[fac] += 1
                county_map[fac] = r.get("county") or ""

        return [
            {"facility": fac, "county": county_map[fac], "isolates": cnt}
            for fac, cnt in counts.most_common()
        ]

    def _critical_alerts(self, top_pathogens: list[dict], threshold: float) -> list[dict]:
        """
        Filter pathogens whose resistance_rate exceeds threshold.

        Args:
            top_pathogens: Output of _top_pathogens().
            threshold:     Resistance % above which an organism is flagged.

        Returns:
            List of dicts: organism, resistance_rate, severity.
            severity is "critical" if rate ≥ 60, else "high".
            Sorted by resistance_rate descending.
        """
        alerts = []
        for p in top_pathogens:
            if p["resistance_rate"] >= threshold:
                alerts.append({
                    "organism":        p["organism"],
                    "resistance_rate": p["resistance_rate"],
                    "severity":        "critical" if p["resistance_rate"] >= 60 else "high",
                })
        alerts.sort(key=lambda x: x["resistance_rate"], reverse=True)
        return alerts

    def _trend(self, records: list[dict]) -> list[dict]:
        """
        Monthly resistance rate time-series over the last 12 months.

        Uses specimen_date to bucket records by YYYY-MM.  Months with no data
        are omitted.

        Args:
            records: Cleaned pipeline records.

        Returns:
            List of dicts sorted ascending: month (YYYY-MM), resistance_rate, isolates.
        """
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            d = _parse_date(r.get("specimen_date", ""))
            if d is None:
                continue
            key = d.strftime("%Y-%m")
            buckets[key].append(r)

        # Keep last 12 calendar months
        all_months = sorted(buckets.keys(), reverse=True)[:12]

        trend = []
        for month in sorted(all_months):
            month_records = buckets[month]
            isolates = len(month_records)
            resistant = sum(
                1 for r in month_records
                if r.get("organism")
                and "no organism" not in r["organism"].lower()
                and r["organism"].strip()
            )
            trend.append({
                "month":           month,
                "resistance_rate": round(resistant / isolates * 100, 1) if isolates else 0.0,
                "isolates":        isolates,
            })
        return trend