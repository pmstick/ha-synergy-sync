# MODIFIED from sniereffo/synergy-battery-analyser (src/chartdata.py), Elastic License 2.0.
# Trimmed to the half-hourly parser. See NOTICE.md and LICENSE.

"""Parse the Synergy portal's `MyAccount_ChartData` half-hourly responses.

Trimmed from synergy-battery-analyser's src/chartdata.py. Each row carries the
slot's grid-import kWh under exactly one tariff-period column, its solar export
under VAL_SOLAR, and the slot start (local Perth time) as VAL_DAY + VAL_TIME.
"""

from datetime import datetime

TARIFF_COLUMNS = ["PEAK", "OFF_PEAK", "OFF_PEAK(AM)", "OVERNIGHT", "SUPER_OFFPEAK"]


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

        present = [c for c in TARIFF_COLUMNS if c in row and row[c] not in (None, "")]
        intervals.append({
            "timestamp": timestamp,
            "consumption_kwh": sum(float(row[c]) for c in present),
            "export_kwh": float(row.get("VAL_SOLAR") or 0.0),
            "tariff_name": present[0] if present else "UNKNOWN",
        })

    intervals.sort(key=lambda i: i["timestamp"])
    return intervals
