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


# --- costs ---------------------------------------------------------------------

IMPORT_COST_ID = "synergy:grid_import_cost"
EXPORT_CREDIT_ID = "synergy:grid_export_credit"

FEED_IN_PEAK_START = 15  # DEBS peak window 3pm–9pm (local hour, inclusive start)
FEED_IN_PEAK_END = 21


def money_metadata(statistic_id: str, name: str, currency: str) -> dict:
    return {
        "source": "synergy",
        "statistic_id": statistic_id,
        "name": name,
        "unit_of_measurement": currency,
        "unit_class": None,
        "has_sum": True,
        "has_mean": False,
        "mean_type": 0,
    }


def _rows_from_hourly(buckets: "OrderedDict[datetime, float]") -> list[dict]:
    rows, running = [], 0.0
    for hour, amount in buckets.items():
        running += amount
        rows.append({
            "start": hour.astimezone(timezone.utc).isoformat(),
            "state": round(amount, 4),
            "sum": round(running, 4),
        })
    return rows


def import_cost_buckets(intervals: list[dict], usage_cents: float, supply_cents_per_day: float) -> "OrderedDict[datetime, float]":
    """Dollars per local hour: usage × rate, plus the daily supply charge in each day's first hour."""
    buckets: OrderedDict[datetime, float] = OrderedDict()
    charged_days = set()
    for iv in sorted(intervals, key=lambda i: i["timestamp"]):
        hour = iv["timestamp"].replace(minute=0, second=0, microsecond=0, tzinfo=PERTH)
        amount = iv["consumption_kwh"] * usage_cents / 100
        if hour.date() not in charged_days:
            charged_days.add(hour.date())
            amount += supply_cents_per_day / 100
        buckets[hour] = buckets.get(hour, 0.0) + amount
    return buckets


def export_credit_buckets(intervals: list[dict], peak_cents: float, offpeak_cents: float) -> "OrderedDict[datetime, float]":
    """Dollars per local hour earned for export, at the DEBS peak/off-peak rate."""
    buckets: OrderedDict[datetime, float] = OrderedDict()
    for iv in sorted(intervals, key=lambda i: i["timestamp"]):
        hour = iv["timestamp"].replace(minute=0, second=0, microsecond=0, tzinfo=PERTH)
        rate = peak_cents if FEED_IN_PEAK_START <= hour.hour < FEED_IN_PEAK_END else offpeak_cents
        buckets[hour] = buckets.get(hour, 0.0) + iv["export_kwh"] * rate / 100
    return buckets


def build_import_cost(intervals, usage_cents, supply_cents_per_day) -> list[dict]:
    return _rows_from_hourly(import_cost_buckets(intervals, usage_cents, supply_cents_per_day))


def build_export_credit(intervals, peak_cents, offpeak_cents) -> list[dict]:
    return _rows_from_hourly(export_credit_buckets(intervals, peak_cents, offpeak_cents))
