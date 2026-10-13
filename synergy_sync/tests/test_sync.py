import json
from datetime import datetime, timedelta
from pathlib import Path

from synergy_sync import stats
from synergy_sync.cache import IntervalCache
from synergy_sync.chartdata import parse_halfhourly_response

FIXTURE = Path(__file__).parent / "fixtures" / "chartdata_halfhourly_day.json"


def _day():
    return parse_halfhourly_response(json.loads(FIXTURE.read_text()))


def test_parser_reads_fixture():
    ivs = _day()
    assert len(ivs) == 48
    assert ivs[0]["timestamp"] == datetime(2026, 8, 2, 0, 0)
    assert ivs[0]["tariff_name"] == "OVERNIGHT"
    # Cross-check totals recorded in the analyser's smoke script for 2026-08-02
    assert abs(sum(i["export_kwh"] for i in ivs) - 15.811) < 0.01
    assert abs(sum(i["consumption_kwh"] for i in ivs) - (3.209 + 1.596 + 0.986 + 3.195 + 8.311)) < 0.01


def test_hourly_buckets_preserve_totals_and_convert_to_utc():
    ivs = _day()
    rows = stats.build_stats(ivs, "consumption_kwh")
    assert len(rows) == 24
    # Perth midnight is 16:00 UTC the previous day
    assert rows[0]["start"] == "2026-08-01T16:00:00+00:00"
    assert rows[0]["state"] == round(ivs[0]["consumption_kwh"] + ivs[1]["consumption_kwh"], 3)
    assert abs(rows[-1]["sum"] - sum(i["consumption_kwh"] for i in ivs)) < 0.01
    sums = [r["sum"] for r in rows]
    assert sums == sorted(sums)


def test_cache_merge_dedupes_and_skips_unknown(tmp_path):
    cache = IntervalCache(str(tmp_path / "c.json"))
    ivs = _day()
    cache.merge(ivs)
    cache.merge(ivs)  # re-fetch overlap
    assert len(cache) == 48
    missing = {"timestamp": datetime(2026, 8, 3), "consumption_kwh": 0, "export_kwh": 0, "tariff_name": "UNKNOWN"}
    assert cache.merge([missing]) == 0
    cache.save()
    reloaded = IntervalCache(str(tmp_path / "c.json"))
    assert reloaded.last_day().isoformat() == "2026-08-02"
    assert reloaded.day_totals(reloaded.last_day())["slots"] == 48
    # Rebuilding stats from cache matches building from the raw day: re-import is idempotent
    assert stats.build_stats(reloaded.intervals(), "export_kwh") == stats.build_stats(ivs, "export_kwh")


def test_metadata_has_required_fields():
    md = stats.metadata(stats.IMPORT_ID, "Synergy grid import")
    assert md["statistic_id"].startswith("synergy:") and md["source"] == "synergy"
    assert md["has_sum"] and md["mean_type"] == 0 and md["unit_class"] == "energy"


def test_flat_rate_plan_usage_column_is_detected():
    # Home Plan A1 rows don't use the time-of-use columns; usage is under another name.
    resp = {"ChartData": {"Response": [
        {"BillingStatus": "Billed", "ANYTIME": "0.412", "VAL_SOLAR": "0.000", "VAL_TIME": "0000", "VAL_DAY": "2026-10-01"},
        {"BillingStatus": "Billed", "ANYTIME": "0.100", "VAL_SOLAR": "0.800", "VAL_TIME": "1200", "VAL_DAY": "2026-10-01", "Total_Cost": "1.23"},
        {"BillingStatus": "Unbilled", "VAL_SOLAR": "", "VAL_TIME": "0030", "VAL_DAY": "2026-10-02"},
    ]}}
    ivs = parse_halfhourly_response(resp)
    by_ts = {i["timestamp"]: i for i in ivs}
    assert by_ts[datetime(2026, 10, 1, 0, 0)]["consumption_kwh"] == 0.412
    assert by_ts[datetime(2026, 10, 1, 0, 0)]["tariff_name"] == "ANYTIME"
    noon = by_ts[datetime(2026, 10, 1, 12, 0)]
    assert noon["consumption_kwh"] == 0.1 and noon["export_kwh"] == 0.8  # cost column ignored
    assert by_ts[datetime(2026, 10, 2, 0, 30)]["tariff_name"] == "UNKNOWN"  # no reading yet
