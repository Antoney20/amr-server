import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, date

from django.http import JsonResponse, StreamingHttpResponse
from django.views import View

from app.services.analytics import bust_cache, get_detail, get_facilities, get_pathogens, get_trend
from app.services.pipeline import get_response_data, run_pipeline

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
# class RecordsView(View):
#     """
#     GET /api/amr/records/

#     Returns ALL cleaned AMR records in a single response.
#     No query parameters — filtering and pagination are handled client-side.
#     """

#     def get(self, request):
#         _refresh_if_requested(request)
#         try:
#             result = run_pipeline()
#         except Exception:
#             logger.exception("RecordsView failed")
#             return _err("Could not load records")

#         records = result.records

#         return JsonResponse({
#             "status":     "ok",
#             "fetched_at": result.fetched_at,
#             "total":      len(records),
#             "records":    records,
#         })


class RecordsView(View):
    """
    GET /api/amr/records/
    Returns ALL records as streaming JSON — first byte in <100ms.
    """

    def get(self, request):
        if request.GET.get("refresh") == "1":
            
            run_pipeline(force_refresh=True)

        try:
            result = run_pipeline()
        except Exception:
            logger.exception("RecordsView failed")
            return JsonResponse({"status": "error"}, status=503)

        def stream():
            """Yield JSON in chunks — browser gets header immediately."""
            yield (
                '{"status":"ok",'
                f'"fetched_at":"{result.fetched_at}",'
                f'"total":{result.total},'
                '"records":['
            )
            for i, record in enumerate(result.records):
                prefix = "" if i == 0 else ","
                yield prefix + json.dumps(record, separators=(",", ":"))
            yield "]}"

        response = StreamingHttpResponse(stream(), content_type="application/json")
        response["Cache-Control"] = "no-store"
        return response

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
    
    
    

def _refresh_if_requested(request) -> None:
    if request.GET.get("refresh") == "1":
        run_pipeline(force_refresh=True)
        bust_cache()


def _err(msg: str, code: int = 503) -> JsonResponse:
    return JsonResponse({"status": "error", "detail": msg}, status=code)



class FacilitiesView(View):

    def get(self, request):
        _refresh_if_requested(request)
        try:
            data = get_facilities()
        except Exception:
            logger.exception("FacilitiesView failed")
            return _err("Could not compute facilities")
        result = run_pipeline()
        return JsonResponse({
            "status":     "ok",
            "fetched_at": result.fetched_at,
            "count":      len(data),
            "facilities": data,
        })


class PathogensView(View):

    def get(self, request):
        _refresh_if_requested(request)
        try:
            data = get_pathogens()
        except Exception:
            logger.exception("PathogensView failed")
            return _err("Could not compute pathogens")
        result = run_pipeline()
        return JsonResponse({
            "status":     "ok",
            "fetched_at": result.fetched_at,
            "count":      len(data),
            "pathogens":  data,
        })



class TrendView(View):

    def get(self, request):
        _refresh_if_requested(request)
        try:
            data = get_trend()
        except Exception:
            logger.exception("TrendView failed")
            return _err("Could not compute trend")
        result = run_pipeline()
        return JsonResponse({
            "status":     "ok",
            "fetched_at": result.fetched_at,
            "count":      len(data),
            "trend":      data,
        })



class DetailView(View):

    def get(self, request):
        _refresh_if_requested(request)
        try:
            data = get_detail()
        except Exception:
            logger.exception("DetailView failed")
            return _err("Could not build detail payload")
        return JsonResponse(data)