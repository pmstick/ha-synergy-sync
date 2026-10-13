"""Minimal Home Assistant WebSocket client, authenticated with the Supervisor token."""

import os

import aiohttp

DEFAULT_URL = "ws://supervisor/core/websocket"
BATCH = 1000  # stats rows per import message, to keep messages small


class HomeAssistant:
    def __init__(self, url: str | None = None, token: str | None = None):
        self._url = url or os.environ.get("HA_WS_URL", DEFAULT_URL)
        self._token = token or os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN")
        self._session = None
        self._ws = None
        self._id = 0

    async def __aenter__(self):
        if not self._token:
            raise RuntimeError("No SUPERVISOR_TOKEN/HA_TOKEN available")
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url, max_msg_size=0)
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected HA greeting: {msg}")
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"HA auth failed: {msg}")
        return self

    async def __aexit__(self, *exc):
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def call(self, payload: dict) -> dict:
        self._id += 1
        msg_id = self._id
        await self._ws.send_json({"id": msg_id, **payload})
        while True:
            msg = await self._ws.receive_json()
            if msg.get("id") == msg_id and msg.get("type") == "result":
                if not msg.get("success"):
                    raise RuntimeError(f"HA {payload['type']} failed: {msg.get('error')}")
                return msg.get("result")

    async def import_statistics(self, metadata: dict, stats: list[dict]):
        for i in range(0, len(stats), BATCH):
            await self.call({
                "type": "recorder/import_statistics",
                "metadata": metadata,
                "stats": stats[i:i + BATCH],
            })

    async def notify(self, title: str, message: str, notification_id: str = "synergy_sync"):
        await self.call({
            "type": "call_service",
            "domain": "persistent_notification",
            "service": "create",
            "service_data": {"title": title, "message": message, "notification_id": notification_id},
        })

    async def dismiss(self, notification_id: str = "synergy_sync"):
        await self.call({
            "type": "call_service",
            "domain": "persistent_notification",
            "service": "dismiss",
            "service_data": {"notification_id": notification_id},
        })
