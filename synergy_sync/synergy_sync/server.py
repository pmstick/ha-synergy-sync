"""Ingress web panel + sync job for the Synergy Sync add-on.

The panel shows sync status and a Sync button. While a sync waits for login it
embeds noVNC, which talks to x11vnc (on the Xvfb display Chromium runs on) via
the /websockify bridge below — so everything rides HA Ingress and its auth.
"""

import asyncio
import collections
import html
import json
import logging
import os
import stat
import time
from datetime import date, datetime, timedelta

from aiohttp import WSMsgType, web

from . import stats
from .browser_client import SynergyBrowserClient
from .cache import IntervalCache
from .ha import HomeAssistant

_LOGGER = logging.getLogger("synergy_sync")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
NOVNC_DIR = os.environ.get("NOVNC_DIR", "/usr/share/novnc")
VNC_PORT = int(os.environ.get("VNC_PORT", "5900"))
LOGIN_TIMEOUT = 30 * 60
REFETCH_DAYS = 3  # re-fetch the last few cached days in case Synergy filled gaps since


def _options() -> dict:
    path = os.path.join(DATA_DIR, "options.json")
    opts = {
        "service_id": "",
        "initial_days": 90,
        "usage_rate_cents": 32.3719,
        "supply_charge_cents_per_day": 116.0505,
        "feed_in_peak_cents": 10.0,
        "feed_in_offpeak_cents": 2.0,
        "rate_history": [],
    }
    if os.path.exists(path):
        with open(path) as f:
            opts.update(json.load(f))
    return opts


def _perth_today() -> date:
    return datetime.now(stats.PERTH).date()


class SyncJob:
    def __init__(self):
        self.state = "idle"  # idle | starting | waiting_login | fetching | importing | done | error
        self.log: collections.deque[str] = collections.deque(maxlen=200)
        self.task: asyncio.Task | None = None
        self.state_path = os.path.join(DATA_DIR, "state.json")
        self.cache = IntervalCache(os.path.join(DATA_DIR, "intervals.json"))
        self.last = {}
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                self.last = json.load(f)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def say(self, line: str):
        _LOGGER.info(line)
        self.log.append(f"{datetime.now():%H:%M:%S}  {line}")

    def start(self, notify: bool = False, from_day: date | None = None) -> bool:
        if self.running:
            return False
        self.log.clear()
        self.task = asyncio.create_task(self._run(notify, from_day))
        return True

    def cancel(self):
        if self.running:
            self.task.cancel()

    async def _run(self, notify: bool, from_day: date | None = None):
        opts = _options()
        client = SynergyBrowserClient(os.path.join(DATA_DIR, "browser-profile"))
        try:
            self.state = "starting"
            self.say("Opening Synergy portal…")
            await client.start()

            self.state = "waiting_login"
            self.say("Waiting for login — use the browser below.")
            if notify:
                await self._notify(
                    "Synergy Sync needs you to log in",
                    "Open the **Synergy Sync** panel in the sidebar and log in to My Synergy.",
                )
            t0 = time.monotonic()
            await client.wait_until_logged_in(timeout=LOGIN_TIMEOUT)
            waited = time.monotonic() - t0
            self.say(f"Logged in after {waited:.0f}s"
                     + (" (saved session still valid — no login needed)" if waited < 10 else ""))

            self.state = "fetching"
            service_id = opts.get("service_id") or ""
            if not service_id:
                services = await client.get_services()
                if not services:
                    raise RuntimeError("No Synergy services found on this account")
                for s in services:
                    self.say(f"Service {s['service_id']}: {s['address']} ({s['tariff']})")
                service_id = services[0]["service_id"]

            end = _perth_today() - timedelta(days=1)
            last = self.cache.last_day()
            if from_day:  # one-off history fetch requested from the panel
                start = from_day
            elif last:
                start = last - timedelta(days=REFETCH_DAYS)
            else:
                start = end - timedelta(days=int(opts["initial_days"]) - 1)
            self.say(f"Fetching {start} → {end}")
            intervals = await client.get_range(
                service_id, start, end,
                progress=lambda d, t, day: self.say(f"  fetched {d}/{t} days (to {day})"),
            )
            await client.close()
            self.say("Columns seen: " + ", ".join(f"{c}×{n}" for c, n in sorted(client.columns_seen.items())))

            stored = self.cache.merge(intervals)
            self.cache.save()
            self.say(f"Got {len(intervals)} half-hour slots, {stored} with readings; cache now {len(self.cache)} slots")
            if intervals and not stored:
                raise RuntimeError(
                    "Synergy returned half-hours but no usage column was recognised "
                    "(see 'Columns seen' above) — nothing imported"
                )

            self.state = "importing"
            await self._import()

            last_day = self.cache.last_day()
            self.last = {
                "synced_at": datetime.now(stats.PERTH).isoformat(timespec="seconds"),
                "login_wait_s": round(waited),
                "first_day": str(self.cache.first_day()),
                "last_day": str(last_day),
                "last_day_totals": self.cache.day_totals(last_day) if last_day else {},
            }
            with open(self.state_path, "w") as f:
                json.dump(self.last, f)
            if notify:
                await self._dismiss()
            self.state = "done"
            self.say("Done.")
        except asyncio.CancelledError:
            self.state = "idle"
            self.say("Cancelled.")
        except Exception as e:  # surface anything in the panel
            _LOGGER.exception("Sync failed")
            self.state = "error"
            self.say(f"Error: {e}")
        finally:
            await client.close()

    async def _import(self):
        opts = _options()
        intervals = self.cache.intervals()
        imp = stats.build_stats(intervals, "consumption_kwh")
        exp = stats.build_stats(intervals, "export_kwh")
        rates = stats.RateSchedule(opts, opts.get("rate_history"))
        cost = stats.build_import_cost(intervals, rates)
        credit = stats.build_export_credit(intervals, rates)
        self.say(f"Importing {len(imp)} hourly rows into Home Assistant…")
        async with HomeAssistant() as ha:
            currency = (await ha.call({"type": "get_config"})).get("currency")
            await ha.import_statistics(stats.metadata(stats.IMPORT_ID, "Synergy grid import"), imp)
            await ha.import_statistics(stats.metadata(stats.EXPORT_ID, "Synergy grid export"), exp)
            await ha.import_statistics(
                stats.money_metadata(stats.IMPORT_COST_ID, "Synergy grid import cost"), cost)
            await ha.import_statistics(
                stats.money_metadata(stats.EXPORT_CREDIT_ID, "Synergy grid export credit"), credit)
        self.say("Rates used (AUD):")
        for line in rates.describe():
            self.say(f"  {line}")
        if currency and currency != stats.CURRENCY:
            self.say(f"Note: HA's currency is {currency}; set it to AUD under Settings → System → General "
                     "so the Energy dashboard shows $ and lists the Synergy cost statistics.")

    async def _notify(self, title: str, message: str):
        try:
            async with HomeAssistant() as ha:
                await ha.notify(title, message)
        except Exception as e:
            self.say(f"(couldn't post HA notification: {e})")

    async def _dismiss(self):
        try:
            async with HomeAssistant() as ha:
                await ha.dismiss()
        except Exception:
            pass

    def status(self) -> dict:
        return {
            "state": self.state,
            "running": self.running,
            "log": list(self.log),
            "last": self.last,
            "cached_slots": len(self.cache),
        }


# --- web -------------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    ingress = request.headers.get("X-Ingress-Path", "")
    ws_path = f"{ingress.strip('/')}/websockify".lstrip("/")
    page = PAGE.replace("__WS_PATH__", html.escape(ws_path, quote=True))
    return web.Response(text=page, content_type="text/html")


async def status(request: web.Request) -> web.Response:
    return web.json_response(request.app["job"].status())


async def sync(request: web.Request) -> web.Response:
    from_day = None
    if request.can_read_body:
        body = await request.json()
        if body.get("from"):
            try:
                from_day = date.fromisoformat(body["from"])
            except ValueError:
                return web.json_response({"error": "Invalid date"}, status=400)
            if not (date(2015, 1, 1) <= from_day < _perth_today()):
                return web.json_response({"error": "Date must be in the past"}, status=400)
    started = request.app["job"].start(from_day=from_day)
    return web.json_response({"started": started})


async def cancel(request: web.Request) -> web.Response:
    request.app["job"].cancel()
    return web.json_response({"ok": True})


async def csv_download(request: web.Request) -> web.Response:
    return web.Response(
        text=request.app["job"].cache.to_csv(),
        content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="synergy-halfhourly.csv"'},
    )


async def websockify(request: web.Request) -> web.WebSocketResponse:
    """Bridge noVNC's WebSocket to x11vnc's raw TCP port."""
    ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=0)
    await ws.prepare(request)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", VNC_PORT)
    except OSError:
        await ws.close()
        return ws

    async def tcp_to_ws():
        while data := await reader.read(65536):
            await ws.send_bytes(data)
        await ws.close()

    pump = asyncio.create_task(tcp_to_ws())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
    finally:
        pump.cancel()
        writer.close()
    return ws


async def _stdin_listener(app: web.Application):
    """Accept `sync` commands from the hassio.addon_stdin service."""
    if not stat.S_ISFIFO(os.fstat(0).st_mode):  # HA gives a pipe when `stdin: true`
        return
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    try:
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(0, "rb", 0))
    except (OSError, ValueError):
        return
    while line := await reader.readline():
        text = line.decode().strip()
        try:
            cmd = json.loads(text)
        except ValueError:
            cmd = text
        if isinstance(cmd, dict):
            cmd = cmd.get("cmd", "")
        if str(cmd).strip().lower() == "sync":
            started = app["job"].start(notify=True)
            _LOGGER.info("stdin sync requested (%s)", "started" if started else "already running")


async def _on_startup(app: web.Application):
    app["stdin"] = asyncio.create_task(_stdin_listener(app))


def make_app() -> web.Application:
    app = web.Application()
    app["job"] = SyncJob()
    app.router.add_get("/", index)
    app.router.add_get("/status", status)
    app.router.add_post("/sync", sync)
    app.router.add_post("/cancel", cancel)
    app.router.add_get("/synergy-halfhourly.csv", csv_download)
    app.router.add_get("/websockify", websockify)
    if os.path.isdir(NOVNC_DIR):
        app.router.add_static("/novnc/", NOVNC_DIR)
    app.on_startup.append(_on_startup)
    return app


PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Synergy Sync</title>
<style>
:root { --bg:#fafafa; --card:#fff; --fg:#212121; --muted:#727272; --line:#e0e0e0; --accent:#03a9f4; --err:#db4437; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111; --card:#1c1c1c; --fg:#e1e1e1; --muted:#9b9b9b; --line:#2f2f2f; --accent:#03a9f4; --err:#ff6b5e; }
}
* { box-sizing:border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, sans-serif; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 20px; margin:0 auto 16px; max-width:1320px; }
h1 { font-size:22px; font-weight:400; margin:0 0 12px; }
.row { display:flex; flex-wrap:wrap; gap:8px 28px; align-items:baseline; }
.k { color:var(--muted); font-size:13px; } .v { font-variant-numeric:tabular-nums; }
button, a.btn { font:inherit; border:0; border-radius:8px; padding:8px 18px; cursor:pointer; text-decoration:none; display:inline-block; }
.primary { background:var(--accent); color:#fff; } .secondary { background:transparent; color:var(--accent); border:1px solid var(--line); }
button:disabled { opacity:.5; cursor:default; }
#state { font-weight:600; } .error { color:var(--err); }
#viewer { display:none; } #viewer iframe { width:100%; aspect-ratio:1280/800; border:1px solid var(--line); border-radius:8px; background:#000; }
input[type=date] { font:inherit; padding:7px 10px; border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--fg); color-scheme:light dark; }
details summary { cursor:pointer; color:var(--accent); margin-top:14px; }
pre { margin:0; max-height:260px; overflow:auto; font-size:12.5px; color:var(--muted); white-space:pre-wrap; }
</style></head>
<body>
<div class="card">
  <h1>Synergy Sync</h1>
  <div class="row">
    <div><div class="k">Status</div><div class="v" id="state">…</div></div>
    <div><div class="k">Last sync</div><div class="v" id="synced">—</div></div>
    <div><div class="k">Data</div><div class="v" id="range">—</div></div>
    <div><div class="k">Latest day</div><div class="v" id="latest">—</div></div>
  </div>
  <div class="row" style="margin-top:16px">
    <button class="primary" id="sync">Sync now</button>
    <button class="secondary" id="cancel" style="display:none">Cancel</button>
    <a class="btn secondary" href="synergy-halfhourly.csv">Download half-hourly CSV</a>
  </div>
  <details>
    <summary>Fetch older history</summary>
    <div class="row" style="margin-top:10px">
      <label class="k" for="from">Fetch everything from</label>
      <input type="date" id="from" value="2026-01-01">
      <button class="secondary" id="backfill">Fetch history</button>
    </div>
    <div class="k" style="margin-top:6px">One-off: fills in older days and re-imports. Normal syncs carry on from the latest day.</div>
  </details>
</div>
<div class="card" id="viewer">
  <div class="k" style="margin-bottom:8px">Log in to My Synergy below (MFA code included). This window closes by itself once you're in.</div>
  <iframe id="vnc" title="Synergy login"></iframe>
</div>
<div class="card"><div class="k" style="margin-bottom:6px">Log</div><pre id="log"></pre></div>
<script>
const LABELS = {idle:"Idle", starting:"Starting browser…", waiting_login:"Waiting for you to log in",
  fetching:"Fetching from Synergy…", importing:"Importing into Home Assistant…", done:"Done", error:"Failed"};
const VNC_SRC = "novnc/vnc.html?autoconnect=1&reconnect=1&resize=scale&show_dot=1&path=__WS_PATH__";
const $ = id => document.getElementById(id);
async function refresh() {
  try {
    const s = await (await fetch("status")).json();
    $("state").textContent = LABELS[s.state] || s.state;
    $("state").className = s.state === "error" ? "v error" : "v";
    $("sync").disabled = s.running;
    $("backfill").disabled = s.running;
    $("cancel").style.display = s.running ? "" : "none";
    const showVnc = s.state === "waiting_login";
    $("viewer").style.display = showVnc ? "block" : "none";
    if (showVnc && !$("vnc").src) $("vnc").src = VNC_SRC;
    if (!showVnc && $("vnc").src) $("vnc").removeAttribute("src");
    const L = s.last || {};
    $("synced").textContent = L.synced_at ? L.synced_at.replace("T", " ").slice(0, 16) : "never";
    $("range").textContent = L.first_day ? `${L.first_day} → ${L.last_day}` : "none";
    const t = L.last_day_totals;
    $("latest").textContent = t && t.slots ? `${t.import_kwh} kWh in · ${t.export_kwh} kWh out (${t.slots}/48 slots)` : "—";
    $("log").textContent = s.log.join("\\n");
    $("log").scrollTop = $("log").scrollHeight;
  } catch (e) {}
}
$("sync").onclick = async () => { await fetch("sync", {method:"POST"}); refresh(); };
$("backfill").onclick = async () => {
  const r = await fetch("sync", {method:"POST", headers:{"Content-Type":"application/json"},
                                 body: JSON.stringify({from: $("from").value})});
  if (!r.ok) alert((await r.json()).error || "Couldn't start");
  refresh();
};
$("cancel").onclick = async () => { await fetch("cancel", {method:"POST"}); refresh(); };
refresh(); setInterval(refresh, 2000);
</script>
</body></html>
"""
