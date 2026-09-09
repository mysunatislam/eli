"""Thread-safe event hub. Agents and worker threads publish events; every connected
WebSocket client (desktop overlay, phone) receives them as JSON."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Iterable

log = logging.getLogger("eli.bus")

ALL = ("desktop", "mobile")


class EventHub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: dict[str, set] = {"desktop": set(), "mobile": set()}
        self.state = "idle"

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def add(self, kind: str, ws) -> None:
        self.clients.setdefault(kind, set()).add(ws)

    def remove(self, kind: str, ws) -> None:
        self.clients.get(kind, set()).discard(ws)

    def connected(self, kind: str) -> int:
        return len(self.clients.get(kind, ()))

    # -- publishing -------------------------------------------------------------------------
    def emit(self, event: dict, to: Iterable[str] = ALL) -> None:
        """Safe to call from any thread."""
        event.setdefault("ts", time.time())
        if self.loop is None or self.loop.is_closed():
            return
        targets = tuple(to)
        try:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._broadcast(event, targets)))
        except RuntimeError:
            pass

    async def _broadcast(self, event: dict, to: tuple[str, ...]) -> None:
        payload = json.dumps(event, default=str)
        for kind in to:
            for ws in list(self.clients.get(kind, ())):
                try:
                    await ws.send_text(payload)
                except Exception:
                    self.clients[kind].discard(ws)

    # -- convenience --------------------------------------------------------------------------
    def set_state(self, state: str) -> None:
        """idle | listening | thinking | talking | happy | sleeping"""
        self.state = state
        self.emit({"type": "state", "state": state})

    def transcript(self, role: str, text: str, source: str = "desktop") -> None:
        self.emit({"type": "transcript", "role": role, "text": text, "source": source})

    def tool(self, name: str, detail: str = "") -> None:
        self.emit({"type": "tool", "name": name, "detail": detail})

    def status(self, **fields) -> None:
        self.emit({"type": "status", **fields})

    def toast(self, text: str) -> None:
        self.emit({"type": "toast", "text": text})

    def notify(self, title: str, body: str = "") -> None:
        self.emit({"type": "notification", "title": title, "body": body}, to=("mobile",))
