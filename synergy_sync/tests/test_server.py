import asyncio
from datetime import date

from aiohttp.test_utils import TestClient, TestServer

from synergy_sync import server


def _run(coro):
    return asyncio.run(coro)


async def _with_client(tmp_path, monkeypatch, fn):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    app = server.make_app()
    calls = []
    app["job"].start = lambda notify=False, from_day=None: calls.append(from_day) or True
    async with TestClient(TestServer(app)) as client:
        return await fn(client), calls


def test_plain_sync_has_no_from_day(tmp_path, monkeypatch):
    async def go(c):
        r = await c.post("/sync")
        return r.status, await r.json()
    (status, body), calls = _run(_with_client(tmp_path, monkeypatch, go))
    assert status == 200 and body["started"] and calls == [None]


def test_history_fetch_passes_date(tmp_path, monkeypatch):
    async def go(c):
        r = await c.post("/sync", json={"from": "2026-01-01"})
        return r.status
    status, calls = _run(_with_client(tmp_path, monkeypatch, go))
    assert status == 200 and calls == [date(2026, 1, 1)]


def test_history_fetch_rejects_bad_dates(tmp_path, monkeypatch):
    async def go(c):
        return [(await c.post("/sync", json={"from": d})).status for d in ("2026-13-01", "2099-01-01")]
    statuses, calls = _run(_with_client(tmp_path, monkeypatch, go))
    assert statuses == [400, 400] and calls == []


def test_panel_has_history_controls(tmp_path, monkeypatch):
    async def go(c):
        return await (await c.get("/")).text()
    page, _ = _run(_with_client(tmp_path, monkeypatch, go))
    assert 'id="backfill"' in page and 'id="from"' in page
