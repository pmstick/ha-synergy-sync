"""Local cache of half-hourly intervals, persisted under the add-on's /data.

Stored as JSON {"<iso local timestamp>": {consumption_kwh, export_kwh, tariff_name}}
so re-fetched slots simply overwrite older copies.
"""

import csv
import io
import json
import os
from datetime import date, datetime


class IntervalCache:
    def __init__(self, path: str):
        self._path = path
        self._data: dict[str, dict] = {}
        if os.path.exists(path):
            with open(path) as f:
                self._data = json.load(f)

    def __len__(self) -> int:
        return len(self._data)

    def merge(self, intervals: list[dict]) -> int:
        """Add/overwrite intervals; returns how many were stored.

        Slots with no tariff column (UNKNOWN) are ones Synergy hasn't got a
        reading for yet, so they're skipped rather than cached as zero.
        """
        stored = 0
        for iv in intervals:
            if iv["tariff_name"] == "UNKNOWN":
                continue
            self._data[iv["timestamp"].isoformat()] = {
                "consumption_kwh": iv["consumption_kwh"],
                "export_kwh": iv["export_kwh"],
                "tariff_name": iv["tariff_name"],
            }
            stored += 1
        return stored

    def save(self):
        tmp = f"{self._path}.tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, sort_keys=True)
        os.replace(tmp, self._path)

    def intervals(self) -> list[dict]:
        """All cached intervals, chronological, with `timestamp` as a naive local datetime."""
        return [
            {"timestamp": datetime.fromisoformat(ts), **v}
            for ts, v in sorted(self._data.items())
        ]

    def last_day(self) -> date | None:
        return datetime.fromisoformat(max(self._data)).date() if self._data else None

    def first_day(self) -> date | None:
        return datetime.fromisoformat(min(self._data)).date() if self._data else None

    def day_totals(self, day: date) -> dict:
        prefix = day.isoformat()
        rows = [v for ts, v in self._data.items() if ts.startswith(prefix)]
        return {
            "slots": len(rows),
            "import_kwh": round(sum(r["consumption_kwh"] for r in rows), 3),
            "export_kwh": round(sum(r["export_kwh"] for r in rows), 3),
        }

    def to_csv(self) -> str:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["timestamp", "grid_import_kwh", "grid_export_kwh", "tariff"])
        for ts, v in sorted(self._data.items()):
            w.writerow([ts, v["consumption_kwh"], v["export_kwh"], v["tariff_name"]])
        return out.getvalue()
