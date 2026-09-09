"""Proactive intelligence: notice patterns while observing and offer help, sparingly.

Signals (all from the local screen pipeline, nothing leaves the PC):
- the same error text seen repeatedly (>= 3 times in 15 minutes)
- a long editing session on one file with errors (>= 40 minutes)
Rules: only while observation is on, never in private mode, at most one nudge every 30 minutes,
each nudge is a question with Yes / Not now, and the user can turn nudges off.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from typing import Optional

log = logging.getLogger("eli.proactive")

NUM_RE = re.compile(r"\d+")


def _norm(snippet: str) -> str:
    return NUM_RE.sub("#", snippet.lower())[:600]


class ProactiveMonitor:
    COOLDOWN = 30 * 60
    WINDOW = 15 * 60
    REPEATS = 3
    LONG_SESSION = 40 * 60

    def __init__(self, hub, settings, coding=None):
        self.hub = hub
        self.settings = settings
        self.coding = coding
        self.errors: dict[str, list[float]] = {}
        self.last_nudge = 0.0
        self.pending: dict[str, dict] = {}
        self.session = {"key": "", "since": 0.0, "errors": 0}
        self.nudged_sessions: set[str] = set()
        self.auto_handler = None   # set by the server: called with (nudge_id) when proactive_mode == "auto"

    def enabled(self) -> bool:
        return bool(self.settings.get("nudges_enabled", True)) and not self.settings.get("private_mode")

    # -- called from the vision loop (worker thread) ---------------------------------------------
    def on_frame(self, frame) -> None:
        if not self.enabled():
            return
        now = time.time()
        snippet = ""
        try:
            snippet = frame.error_snippet()
        except Exception:
            return
        ctx = self.coding.vscode_context(frame.window) if self.coding else None
        key = f"{frame.window.app}|{(ctx or {}).get('file', '')}"
        if key != self.session["key"]:
            self.session = {"key": key, "since": now, "errors": 0}
        if snippet:
            h = hashlib.blake2b(_norm(snippet).encode(), digest_size=8).hexdigest()
            ts = [t for t in self.errors.get(h, []) if now - t < self.WINDOW]
            ts.append(now)
            self.errors[h] = ts
            self.session["errors"] += 1
            if len(ts) >= self.REPEATS and now - self.last_nudge > self.COOLDOWN:
                mins = max(1, int((now - ts[0]) / 60))
                first = snippet.strip().splitlines()[-1][:90]
                self.nudge(f"I've seen the same error {len(ts)} times in the last {mins} minutes ({first}). Want me to take a look?",
                           {"kind": "error", "snippet": snippet})
                self.errors[h] = []
                return
        if (now - self.session["since"] > self.LONG_SESSION and self.session["errors"] >= 2
                and key not in self.nudged_sessions and now - self.last_nudge > self.COOLDOWN and ctx):
            self.nudged_sessions.add(key)
            self.nudge(f"You've been working on {ctx.get('file', 'this file')} for a while and errors keep coming up. Want me to review it?",
                       {"kind": "error", "snippet": snippet})

    def nudge(self, text: str, payload: dict, actions: Optional[list[dict]] = None) -> str:
        nid = secrets.token_hex(4)
        self.pending[nid] = payload
        self.last_nudge = time.time()
        if self.settings.get("proactive_mode") == "auto" and self.auto_handler:
            # Auto mode: don't ask, look straight away and present the fix.
            self.hub.toast("Eli: " + text.split("?")[0] + ". Looking now.")
            log.info("auto nudge: %s", text)
            self.auto_handler(nid)
            return nid
        self.hub.emit({"type": "nudge", "id": nid, "text": text,
                       "actions": actions or [{"id": "yes", "label": "Yes, look", "primary": True}, {"id": "no", "label": "Not now"}]})
        self.hub.notify("Eli noticed something", text)
        log.info("nudge: %s", text)
        return nid

    def resolve(self, nid: str, action: str) -> Optional[dict]:
        payload = self.pending.pop(nid, None)
        if payload is None:
            return None
        if action != "yes":
            return None
        return payload
