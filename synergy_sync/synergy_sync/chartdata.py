# MODIFIED from sniereffo/synergy-battery-analyser (src/chartdata.py), Elastic License 2.0.
# Trimmed to the half-hourly parser; usage-column detection generalised for flat-rate
# plans. See NOTICE.md and LICENSE.

"""Parse the Synergy portal's `MyAccount_ChartData` half-hourly responses.

Trimmed from synergy-battery-analyser's src/chartdata.py. Each row carries the
slot's grid-import kWh under exactly one tariff-period column, its solar export
under VAL_SOLAR, and the slot start (local Perth time) as VAL_DAY + VAL_TIME.
"""

from datetime import datetime

TARIFF_COLUMNS = ["PEAK", "OFF_PEAK", "OFF_PEAK(AM)", "OVERNIGHT", "SUPER_OFFPEAK"]

# Columns that are never grid usage. Flat-rate plans (e.g. Home Plan A1) don't
# use the time-of-use columns above, so any other numeric column is treated as
# the slot's usage, named after its column.
_NON_USAGE = {"VAL_DAY", "VAL_TIME", "VAL_SOLAR", "BillingStatus"}
_NON_USAGE_HINTS = ("COST", "TEMP", "PRICE", "AMOUNT", "RATE", "CHARGE")


def usage_columns(row: dict) -> list[str]:
    """Column names in `row` holding grid-import kWh."""
    cols = []
    for k, v in row.items():
        if k in _NON_USAGE or any(h in k.upper() for h in _NON_USAGE_HINTS) or v in (None, ""):
            continue
        try:
            float(v)
        except (TypeError, ValueError):
            continue
        cols.append(k)
    return cols


def column_summary(response: dict) -> dict[str, int]:
    """How many rows carry each column, for diagnosing unfamiliar plan layouts."""
    counts: dict[str, int] = {}
    for row in _extract_rows(response):
        for k, v in row.items():
            if v not in (None, ""):
                counts[k] = counts.get(k, 0) + 1
    return counts


def _extract_rows(response: dict) -> list[dict]:
    node = response
    if "IPResult" in node:
        node = node["IPResult"]
    if "ChartData" in node:
        node = node["ChartData"]
    rows = node.get("Response")
    if not isinstance(rows, list):
        raise ValueError("No interval Response array found in ChartData payload")
    return rows


def parse_halfhourly_response(response: dict) -> list[dict]:
    """Return [{timestamp (naive local datetime), consumption_kwh, export_kwh, tariff_name}]."""
    intervals = []
    for row in _extract_rows(response):
        val_time = str(row["VAL_TIME"]).zfill(4)
        timestamp = datetime.strptime(f"{row['VAL_DAY']}{val_time}", "%Y-%m-%d%H%M")

        present = usage_columns(row)
        intervals.append({
            "timestamp": timestamp,
            "consumption_kwh": sum(float(row[c]) for c in present),
            "export_kwh": float(row.get("VAL_SOLAR") or 0.0),
            "tariff_name": present[0] if present else "UNKNOWN",
        })

    intervals.sort(key=lambda i: i["timestamp"])
    return intervals
