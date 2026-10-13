"""Turn half-hourly intervals into Home Assistant hourly long-term statistics.

HA statistics are hourly, so the two half-hour slots in each hour are summed.
Synergy timestamps are Perth local time, which is a fixed UTC+8 (no DST).
`sum` is a running total from the first interval; since we always rebuild from
the whole cache, re-importing is idempotent.
"""

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone

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


CURRENCY = "AUD"  # Synergy bills in Australian dollars


def money_metadata(statistic_id: str, name: str, currency: str = CURRENCY) -> dict:
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


RATE_KEYS = ("usage_rate_cents", "supply_charge_cents_per_day", "feed_in_peak_cents", "feed_in_offpeak_cents")


class RateSchedule:
    """Tariff rates by date.

    `current` holds the four RATE_KEYS for the latest period. `history` is a list of
    earlier periods, each with the same keys plus `until` (ISO date, the last day
    those rates applied). A day uses the earliest period whose `until` is on or
    after it; days after every `until` use `current`.
    """

    def __init__(self, current: dict, history: list[dict] | None = None):
        self.current = {k: float(current[k]) for k in RATE_KEYS}
        self.history = sorted(
            ({"until": date.fromisoformat(h["until"]), **{k: float(h[k]) for k in RATE_KEYS}} for h in history or []),
            key=lambda h: h["until"],
        )

    def for_day(self, day: date) -> dict:
        for h in self.history:
            if day <= h["until"]:
                return h
        return self.current

    def describe(self) -> list[str]:
        def fmt(r):
            return (f"{r['usage_rate_cents']}c/kWh + {r['supply_charge_cents_per_day']}c/day, "
                    f"feed-in {r['feed_in_peak_cents']}c/{r['feed_in_offpeak_cents']}c")
        lines, prev = [], None
        for h in self.history:
            lines.append(f"{'up to' if prev is None else f'{prev + timedelta(days=1)} to'} {h['until']}: {fmt(h)}")
            prev = h["until"]
        lines.append(f"{'all days' if prev is None else f'from {prev + timedelta(days=1)}'}: {fmt(self.current)}")
        return lines


def import_cost_buckets(intervals: list[dict], rates: RateSchedule) -> "OrderedDict[datetime, float]":
    """Dollars per local hour: usage × rate, plus the daily supply charge in each day's first hour."""
    buckets: OrderedDict[datetime, float] = OrderedDict()
    charged_days = set()
    for iv in sorted(intervals, key=lambda i: i["timestamp"]):
        hour = iv["timestamp"].replace(minute=0, second=0, microsecond=0, tzinfo=PERTH)
        r = rates.for_day(hour.date())
        amount = iv["consumption_kwh"] * r["usage_rate_cents"] / 100
        if hour.date() not in charged_days:
            charged_days.add(hour.date())
            amount += r["supply_charge_cents_per_day"] / 100
        buckets[hour] = buckets.get(hour, 0.0) + amount
    return buckets


def export_credit_buckets(intervals: list[dict], rates: RateSchedule) -> "OrderedDict[datetime, float]":
    """Dollars per local hour earned for export, at the DEBS peak/off-peak rate."""
    buckets: OrderedDict[datetime, float] = OrderedDict()
    for iv in sorted(intervals, key=lambda i: i["timestamp"]):
        hour = iv["timestamp"].replace(minute=0, second=0, microsecond=0, tzinfo=PERTH)
        r = rates.for_day(hour.date())
        peak = FEED_IN_PEAK_START <= hour.hour < FEED_IN_PEAK_END
        rate = r["feed_in_peak_cents"] if peak else r["feed_in_offpeak_cents"]
        buckets[hour] = buckets.get(hour, 0.0) + iv["export_kwh"] * rate / 100
    return buckets


def build_import_cost(intervals, rates: RateSchedule) -> list[dict]:
    return _rows_from_hourly(import_cost_buckets(intervals, rates))


def build_export_credit(intervals, rates: RateSchedule) -> list[dict]:
    return _rows_from_hourly(export_credit_buckets(intervals, rates))
