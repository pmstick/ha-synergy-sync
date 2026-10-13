# MODIFIED from sniereffo/synergy-battery-analyser (src/browser_client.py), Elastic License 2.0.
# Persistent profile + virtual-display options added. See NOTICE.md and LICENSE.

"""Browser-assisted Synergy portal client.

Adapted from synergy-battery-analyser's src/browser_client.py. The portal logs
in behind Salesforce MFA + reCAPTCHA, so a real (headed) Chromium runs on the
add-on's virtual display, which the user sees and drives through noVNC in the
HA panel. Once logged in we harvest `aura.token` / `aura.context` from the
page's own traffic and issue `MyAccount_ChartData` calls via the same context.

Unlike the analyser, the browser profile is persistent (under /data) so cookies
and any "remember this device" state survive between syncs.
"""

import asyncio
from datetime import date, timedelta
from urllib.parse import parse_qs

from playwright.async_api import async_playwright

from .aura import build_apex_action, build_chartdata_input, build_ip_action, build_message, parse_ip_result
from .chartdata import column_summary, parse_halfhourly_response

PORTAL_URL = "https://my.synergy.net.au/s/"
AURA_PATH = "/s/sfsites/aura"


class NotLoggedIn(RuntimeError):
    pass


class SynergyBrowserClient:
    def __init__(self, profile_dir: str, window_size: tuple[int, int] = (1280, 800)):
        self._profile_dir = profile_dir
        self._window_size = window_size
        self._pw = None
        self._context = None
        self._page = None
        # Harvested from live traffic once the user is logged in.
        self._aura_context: str | None = None
        self._aura_token: str | None = None
        self._page_uri: str = "/s/"
        self._request_id = 1000
        # Column name -> row count across all fetched rows (diagnostics).
        self.columns_seen: dict[str, int] = {}

    async def start(self):
        """Launch the browser (headed, on $DISPLAY) and begin capturing Aura credentials."""
        width, height = self._window_size
        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            self._profile_dir,
            headless=False,
            no_viewport=True,
            args=[
                "--window-position=0,0",
                f"--window-size={width},{height}",
                "--start-maximized",
                "--disable-dev-shm-usage",
                "--test-type",  # hides the "--no-sandbox is unsupported" infobar (container runs as root)
            ],
        )
        self._context.on("request", self._on_request)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto(PORTAL_URL)

    def _on_request(self, request):
        """Sniff Aura POSTs to harvest the authenticated session token + context.

        Only accept traffic from the community app (`siteforce:communityApp`);
        the login page (`siteforce:loginApp2`) carries a token but no data session.
        """
        if request.method != "POST" or AURA_PATH not in request.url:
            return
        data = request.post_data
        if not data:
            return
        form = parse_qs(data)
        if "aura.token" not in form or "aura.context" not in form:
            return
        context = form["aura.context"][0]
        if "siteforce:communityApp" not in context:
            return
        self._aura_token = form["aura.token"][0]
        self._aura_context = context
        if "aura.pageURI" in form:
            self._page_uri = form["aura.pageURI"][0]

    @property
    def logged_in(self) -> bool:
        return self._aura_token is not None and self._aura_context is not None

    async def wait_until_logged_in(self, timeout: float = 1800.0, poll: float = 1.0):
        waited = 0.0
        while waited < timeout:
            if self.logged_in:
                return
            await asyncio.sleep(poll)
            waited += poll
        raise TimeoutError("Timed out waiting for Synergy login")

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _post_action(self, action: dict) -> dict:
        form = {
            "message": build_message([action]),
            "aura.context": self._aura_context,
            "aura.token": self._aura_token,
            "aura.pageURI": self._page_uri,
        }
        resp = await self._context.request.post(
            f"https://my.synergy.net.au{AURA_PATH}?r={self._next_id()}&aura.ApexAction.execute=1",
            form=form,
            headers={"Origin": "https://my.synergy.net.au"},
        )
        if not resp.ok:
            raise RuntimeError(f"Aura call failed: HTTP {resp.status}")
        return parse_ip_result(await resp.json())

    async def _call_ip(self, ip_method: str, ip_input: dict) -> dict:
        if not self.logged_in:
            raise NotLoggedIn("Log in via the browser first")
        return await self._post_action(build_ip_action(ip_method, ip_input, self._next_id()))

    async def _call_apex(self, namespace: str, classname: str, method: str, params: dict) -> dict:
        if not self.logged_in:
            raise NotLoggedIn("Log in via the browser first")
        return await self._post_action(
            build_apex_action(namespace, classname, method, params, self._next_id())
        )

    async def get_services(self) -> list[dict]:
        """Return active services: [{service_id, account_number, tariff, address, type}]."""
        result = await self._call_apex(
            "", "CommunityServiceController", "getLinkedServices",
            {"contactId": None, "accountId": None},
        )
        return [
            {
                "service_id": s.get("Id") or s.get("value"),
                "account_number": s.get("AccountNumber") or s.get("ContractAccountNumber"),
                "tariff": s.get("Tarrif"),
                "address": s.get("SupplyAddress") or s.get("Address"),
                "type": s.get("Type"),
            }
            for s in result.get("ActiveServices", [])
        ]

    async def get_span(self, service_id: str, start: date, end: date) -> list[dict]:
        result = await self._call_ip(
            "MyAccount_ChartData", build_chartdata_input(service_id, start, end)
        )
        for col, n in column_summary(result).items():
            self.columns_seen[col] = self.columns_seen.get(col, 0) + n
        return parse_halfhourly_response(result)

    async def get_range(
        self, service_id: str, start: date, end: date, progress=None,
        chunk_days: int = 31, concurrency: int = 4,
    ) -> list[dict]:
        """Fetch start..end in ~monthly chunks, a few concurrently. Results are chronological."""
        chunks: list[tuple[date, date]] = []
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
            chunks.append((cur, chunk_end))
            cur = chunk_end + timedelta(days=1)

        total_days = (end - start).days + 1
        sem = asyncio.Semaphore(max(1, concurrency))
        by_index: dict[int, list[dict]] = {}
        done_days = 0

        async def fetch(idx: int, cstart: date, cend: date):
            nonlocal done_days
            async with sem:
                by_index[idx] = await self.get_span(service_id, cstart, cend)
            done_days += (cend - cstart).days + 1
            if progress:
                progress(min(done_days, total_days), total_days, cend)

        await asyncio.gather(*(fetch(i, s, e) for i, (s, e) in enumerate(chunks)))

        intervals = [iv for i in range(len(chunks)) for iv in by_index.get(i, [])]
        intervals.sort(key=lambda iv: iv["timestamp"])
        return intervals

    async def close(self):
        if self._context:
            await self._context.close()
        if self._pw:
            await self._pw.stop()
        self._context = self._pw = self._page = None
