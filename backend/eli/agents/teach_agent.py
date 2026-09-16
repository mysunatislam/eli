"""Teach agent: "Eli, let me teach you how to <X>" -> watch once -> a guide Eli can give anyone.

Lifecycle
    start(name, goal)   asks for screen permission (same broker as the guide), refuses in private mode
                        or on block-listed apps, installs the recorder, tells the user to do it once.
    stop()              stops the recorder, segments the trace (vision model when available, offline
                        heuristic otherwise), validates, stores the guide in the library, registers it
                        with the engine, and offers to run it right away or show it for review.
    review(guide_id)    plain-language listing of the steps so the author can fix names, targets or
                        expect conditions by voice ("rename step 3 to ...", "delete step 2") - kept
                        deliberately small; a visual review UI is the next upgrade.

Everything here runs in the asyncio loop; the recorder itself runs on its own threads.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from ..guides.library import GuideLibrary
from ..guides.recorder import Recorder, Recording, TraceEvent
from ..guides import segmenter

log = logging.getLogger("eli.teach")


class TeachAgent:
    def __init__(self, hub, settings, vision, llm, memory, broker, data_dir: Path, guide=None):
        self.hub, self.settings, self.vision, self.llm, self.memory, self.broker = hub, settings, vision, llm, memory, broker
        self.guide = guide
        self.library = GuideLibrary(data_dir)
        self.recorder = Recorder(vision, settings, data_dir, on_event=self._on_event)
        self.last_guide: Optional[dict] = None
        self.last_recording: Optional[Recording] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.speech = None
        self._restore: dict = {}

    def attach(self, loop, speech=None) -> None:
        self.loop, self.speech = loop, speech
        self.library.load()

    # -- helpers -----------------------------------------------------------------------------------------

    def _say(self, text: str) -> None:
        if self.speech and self.settings.get("voice_replies", True):
            try:
                self.speech.say(text)
            except Exception:
                pass

    def _on_event(self, ev: TraceEvent) -> None:
        # called from the recorder's worker thread: keep the panel's badge live
        try:
            self.hub.status(teach=self.status())
            if ev.kind in ("click", "dblclick", "rclick", "shortcut") and (ev.label or ev.keys):
                self.hub.emit({"type": "teach_event", "i": ev.i, "kind": ev.kind, "label": ev.label or ev.keys})
        except Exception:
            pass

    def status(self) -> dict:
        return {"recording": self.recorder.status(), "guides": len(self.library.guides),
                "last_guide": self.last_guide["id"] if self.last_guide else ""}

    # -- lifecycle -----------------------------------------------------------------------------------------

    async def start(self, name: str = "", goal: str = "") -> str:
        if self.settings.get("private_mode"):
            return "Private mode is on, so I won't record anything. Turn it off and ask again."
        if self.guide is not None and getattr(self.guide, "active", False):
            return "A guide is running. Stop it first, then I can learn something new."
        ok = await self.broker.ensure_screen("Eli wants to watch the screen once to learn how you do this.")
        if not ok:
            return "I need to see the screen to learn from you. Allow screen access and ask again."
        name = (name or goal or "").strip()
        if not name:
            return "What should I call this guide? For example: teach you how to export a STEP file."
        # watch quickly while recording, like the guide does
        self._restore = {"observe_enabled": self.settings.get("observe_enabled"), "interval": self.vision.base_interval}
        self.settings.set("observe_enabled", True)
        self.vision.base_interval = 1.0
        self.vision.interval = 1.0
        r = await asyncio.to_thread(self.recorder.start, name, goal or name)
        if not self.recorder.active:
            self._restore_capture()
            return r
        self.hub.status(observe_enabled=True, teach=self.status())
        self.hub.toast(f"Recording: {name}")
        self.hub.transcript("eli", r)
        self._say("Okay, show me. Do it once at your normal pace and say stop recording when you're done.")
        return r

    async def stop(self, run_now: bool = False) -> str:
        if not self.recorder.active:
            return "I'm not recording anything right now."
        rec = await asyncio.to_thread(self.recorder.stop)
        self._restore_capture()
        self.hub.status(teach=self.status())
        if rec is None or not rec.events:
            return "I stopped, but I didn't see any actions. Try again and do the steps in the app."
        self.last_recording = rec
        self.hub.set_state("thinking")
        self._say("Got it. Let me write that up as a guide.")
        wf, method = await segmenter.segment(self.llm, self.vision, rec)
        if not wf:
            self.hub.set_state("idle")
            return "I couldn't turn that into clear steps. Try recording again with one action at a time."
        self.library.add(wf)
        self.last_guide = wf
        steps = wf["apps"]["recorded"]["steps"]
        app = wf["apps"]["recorded"]["label"]
        self.memory.remember(f"Eli learned the guide '{wf['name']}' for {app} ({len(steps)} steps) from the user.",
                             kind="fact", importance=0.6, source="agent")
        self.hub.set_state("success")
        self.hub.status(teach=self.status())
        how = "with the vision model" if method == "model" else "from the recording alone"
        msg = (f"Learned “{wf['name']}” for {app}: {len(steps)} steps ({len(rec.events)} actions, {rec.duration:.0f}s), written {how}. "
               f"Say “guide me through {wf['name']}” any time, “review that guide” to check the steps, or “export that guide” to share it.")
        if run_now and self.guide is not None:
            await asyncio.sleep(0.5)
            await self.guide.start(workflow_id=wf["id"])
        return msg

    def _restore_capture(self) -> None:
        if self._restore:
            self.settings.set("observe_enabled", bool(self._restore.get("observe_enabled")))
            self.vision.base_interval = self._restore.get("interval", self.vision.base_interval)
            self.vision.interval = self.vision.base_interval
            self.hub.status(observe_enabled=self.settings.get("observe_enabled"))
            self._restore = {}

    async def cancel(self) -> str:
        if not self.recorder.active:
            return ""
        await asyncio.to_thread(self.recorder.stop)
        self._restore_capture()
        self.hub.status(teach=self.status())
        return "Recording cancelled; nothing was learned."

    # -- review & management ----------------------------------------------------------------------------------

    def _pick(self, guide_id: str = "", text: str = "") -> Optional[dict]:
        if guide_id and guide_id in self.library.guides:
            return self.library.guides[guide_id]
        if text:
            f = self.library.find(text)
            if f:
                return f
        return self.last_guide

    def review(self, guide_id: str = "", text: str = "") -> str:
        wf = self._pick(guide_id, text)
        if not wf:
            return self.library.describe()
        app = wf["apps"][wf["default_app"]]
        lines = [f"“{wf['name']}” for {app['label']} — {len(app['steps'])} steps:"]
        for i, s in enumerate(app["steps"], 1):
            t = s.get("target", {})
            tgt = {"ui_text": f"click “{t.get('text')}”", "key": f"press {t.get('keys')}", "geometry": f"find {t.get('query')}",
                   "window_region": "a region of the window"}.get(t.get("kind"), "?")
            ex = ", ".join(c.get("text") or c.get("question") or c.get("kind") for c in s.get("expect", [])) or "manual"
            lines.append(f"{i}. {s['title']} — {tgt}; done when: {ex}")
        return "\n".join(lines)

    def list_guides(self) -> str:
        return self.library.describe()

    def delete(self, guide_id: str = "", text: str = "") -> str:
        wf = self._pick(guide_id, text)
        if not wf:
            return "I don't have a guide by that name."
        self.library.delete(wf["id"])
        if self.last_guide and self.last_guide["id"] == wf["id"]:
            self.last_guide = None
        self.hub.status(teach=self.status())
        return f"Deleted the guide “{wf['name']}”."

    def rename(self, name: str, guide_id: str = "") -> str:
        wf = self._pick(guide_id)
        if not wf:
            return "Which guide? Record one first, or say its name."
        old = wf["name"]
        self.library.rename(wf["id"], name)
        self.last_guide = self.library.get(wf["id"])
        return f"Renamed “{old}” to “{name}”."

    def edit_step(self, index: int, guide_id: str = "", title: str = "", instruction: str = "", say: str = "",
                  delete: bool = False) -> str:
        wf = self._pick(guide_id)
        if not wf:
            return "Which guide? Record one first, or say its name."
        steps = wf["apps"][wf["default_app"]]["steps"]
        if not 1 <= index <= len(steps):
            return f"That guide has {len(steps)} steps."
        if delete:
            if len(steps) == 1:
                return "That's the only step; delete the guide instead."
            removed = steps.pop(index - 1)
            self.library.add(wf)
            return f"Removed step {index} (“{removed['title']}”)."
        s = steps[index - 1]
        if title:
            s["title"] = title.strip()[:60]
        if instruction:
            s["instruction"] = instruction.strip()[:400]
        if say:
            s["say"] = say.strip()[:500]
        self.library.add(wf)
        return f"Updated step {index}: {s['title']}."

    def export(self, guide_id: str = "", path: str = "", text: str = "") -> str:
        wf = self._pick(guide_id, text)
        if not wf:
            return "Which guide should I export? Say its name."
        p = self.library.export(wf["id"], path)
        return f"Exported “{wf['name']}” to {p}. Anyone with Eli can import it with “import guide {p}”." if p else "Export failed."

    def import_file(self, path: str) -> str:
        try:
            wf = self.library.import_file(path)
        except Exception as e:
            return f"I couldn't import that: {e}"
        self.last_guide = wf
        self.hub.status(teach=self.status())
        return f"Imported “{wf['name']}” ({len(wf['apps'][wf['default_app']]['steps'])} steps). Say “guide me through {wf['name']}”."
