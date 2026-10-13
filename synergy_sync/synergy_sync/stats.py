"""Turn half-hourly intervals into Home Assistant hourly long-term statistics.

HA statistics are hourly, so the two half-hour slots in each hour are summed.
Synergy timestamps are Perth local time, which is a fixed UTC+8 (no DST).
`sum` is a running total from the first interval; since we always rebuild from
the whole cache, re-importing is idempotent.
"""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone

PERTH = timezone(timedelta(hours=8))

IMPORT_ID = "synergy:grid_import"
EXPORT_ID = "synergy:grid_export"


def metadata(statistic_id: str, name: str) -> dict:
    return {
        "source": "synergy",
        "statistic_id": statistic_id,
        "name": name,
        "unit_of_measurement": "kWh",
        "unit_class": "energy",
        "has_sum": True,
        "has_mean": False,
        "mean_type": 0,  # StatisticMeanType.NONE
    }


def hourly_totals(intervals: list[dict], field: str) -> "OrderedDict[datetime, float]":
    """Sum `field` per local hour; keys are tz-aware (Perth) hour starts, chronological."""
    buckets: OrderedDict[datetime, float] = OrderedDict()
    for iv in sorted(intervals, key=lambda i: i["timestamp"]):
        hour = iv["timestamp"].replace(minute=0, second=0, microsecond=0, tzinfo=PERTH)
        buckets[hour] = buckets.get(hour, 0.0) + iv[field]
    return buckets


def build_stats(intervals: list[dict], field: str) -> list[dict]:
    """HA stats rows [{start (UTC ISO), state, sum}] with a running sum."""
    rows = []
    running = 0.0
    for hour, kwh in hourly_totals(intervals, field).items():
        running += kwh
        rows.append({
            "start": hour.astimezone(timezone.utc).isoformat(),
            "state": round(kwh, 3),
            "sum": round(running, 3),
        })
    return rows
