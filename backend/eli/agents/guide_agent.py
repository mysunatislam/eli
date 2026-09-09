"""Guide Agent: precise, step-by-step, narrated guidance drawn over the application.

For each step of a workflow (see eli/guides):
  1. resolve the target on the current frame — UI text via OCR (exact button), geometry via the vision
     model (bounding boxes), keys, or a region of the app window — and send indicators (rings, glowing
     boxes, arrows, key badges) to the full-screen guide overlay;
  2. narrate the step in the female voice, time-aligned with the indicator's entrance;
  3. keep watching the viewport (every changed frame, rate-limited) and auto-advance when the step's
     completion conditions hold: OCR text appearing/disappearing, window title, or a vision yes/no;
  4. detect trouble: the user left the app (pause), the step is taking too long (offer repeat / skip /
     alternative), or the user jumped ahead (skip forward).
Voice control: next / done, repeat, back, skip, another way, stop the guide.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

from ..guides import WORKFLOWS, find_workflow
from ..llm import image_part, text_part

log = logging.getLogger("eli.guide")

DETECT_SYSTEM = ("You are a precise visual locator for a CAD/3D application screenshot. You return only JSON, never prose. "
                 "Coordinates are [ymin, xmin, ymax, xmax] normalized to 0-1000 relative to the image.")
YESNO_SYSTEM = ("You check the state of a CAD/3D application from a screenshot. Answer with exactly YES or NO on the first "
                "line, then at most one short sentence of evidence.")


class GuideAgent:
    CHECK_EVERY = 2.5     # seconds between completion checks (on changed frames only)
    VISION_EVERY = 6.0    # seconds between vision-model yes/no questions
    AWAY_FRAMES = 6       # consecutive frames outside the app before pausing

    def __init__(self, hub, settings, vision, llm, memory, broker, auto=None):
        self.hub, self.settings, self.vision, self.llm, self.memory, self.broker = hub, settings, vision, llm, memory, broker
        self.auto = auto
        self.speech = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.active = False
        self.paused = False
        self.wf: dict = {}
        self.app_key = ""
        self.steps: list[dict] = []
        self.idx = 0
        self.step_started = 0.0
        self.last_check = 0.0
        self.last_vision = 0.0
        self.checking = False
        self.stuck_offered = False
        self.away = 0
        self._restore: dict = {}
        self._holes = 0
        self._hole_boxes: list = []
        self.shown = False
        self.last_show: dict = {}

    def attach(self, loop, speech) -> None:
        self.loop, self.speech = loop, speech
        if speech is not None:
            speech.tts.on_start.append(lambda _t: self._emit({"action": "speaking", "on": True}))
            speech.tts.on_done.append(lambda _t: self._emit({"action": "speaking", "on": False}))

    # -- helpers ----------------------------------------------------------------------------------------
    def _emit(self, payload: dict) -> None:
        self.hub.emit({"type": "guide", **payload}, to=("desktop",))

    def _say(self, text: str) -> None:
        if self.speech and self.settings.get("voice_replies", True):
            self.speech.say_now(text)

    @property
    def app_label(self) -> str:
        return self.wf.get("apps", {}).get(self.app_key, {}).get("label", self.app_key or "the app")

    def _detect_app(self, win) -> Optional[str]:
        proc = (getattr(win, "process", "") or "").lower()
        title = (getattr(win, "title", "") or "").lower()
        for key, app in self.wf.get("apps", {}).items():
            m = app.get("match", {})
            if any(p in proc for p in m.get("process", [])) or any(t in title for t in m.get("title", [])):
                return key
        return None

    def _is_app_window(self, win) -> bool:
        return self._detect_app(win) == self.app_key

    def _app_from_text(self, text: str) -> Optional[str]:
        low = (text or "").lower()
        for key, app in self.wf.get("apps", {}).items():
            names = [key, app.get("label", "").lower()] + [t for t in app.get("match", {}).get("title", [])]
            if any(n and n in low for n in names):
                return key
        return None

    def _find_open_app(self, prefer: Optional[str] = None):
        """(app_key, window title) of an open window of a known CAD app, preferring `prefer`."""
        try:
            import pygetwindow as gw  # type: ignore
            wins = [(w.title or "") for w in gw.getAllWindows() if w.title and not w.isMinimized]
        except Exception:
            return None
        order = ([prefer] if prefer else []) + [k for k in self.wf.get("apps", {}) if k != prefer]
        for key in order:
            for t in self.wf["apps"][key].get("match", {}).get("title", []):
                for title in wins:
                    if t in title.lower():
                        return key, title
        return None

    def resend(self) -> None:
        """Re-emit the current step (an overlay that reconnected mid-guide catches up)."""
        if self.active and self.last_show:
            self._emit(self.last_show)

    def status(self) -> dict:
        if not self.active:
            return {"active": False}
        return {"active": True, "workflow": self.wf.get("name"), "app": self.app_label, "step": self.idx + 1,
                "total": len(self.steps), "title": self.steps[self.idx]["title"] if self.steps else "", "paused": self.paused}

    # -- lifecycle ----------------------------------------------------------------------------------------
    async def start(self, request: str = "", workflow_id: str = "", app: str = "") -> str:
        wf = WORKFLOWS.get(workflow_id) or find_workflow(request or workflow_id)
        if not wf:
            return ("I don't have a guided workflow for that yet. I can guide: " +
                    ", ".join(w["name"] for w in WORKFLOWS.values()) + ".")
        if self.settings.get("private_mode"):
            return "Private mode is on, so I can't watch the screen to guide you. Turn it off and ask again."
        ok = await self.broker.ensure_screen("Eli needs to watch the screen to guide you step by step.")
        if not ok:
            return "I need to see the screen to guide you. Allow screen access and ask again."
        if self.active:
            self.stop(silent=True)
        self.wf = wf
        win = self.vision.user_window()
        hinted = app if app in wf["apps"] else self._app_from_text(request)
        detected = self._detect_app(win)
        chosen = hinted or detected
        brought = ""
        if not chosen or (hinted and detected != hinted):
            # the app isn't in front: look for an open window of a known CAD app and bring it forward
            found = await asyncio.to_thread(self._find_open_app, hinted)
            if found:
                chosen = chosen or found[0]
                if self.auto is not None and found[0] == chosen:
                    r = await asyncio.to_thread(self.auto.focus_window, found[1])
                    if r.startswith("Switched"):
                        brought = found[1]
                        await asyncio.sleep(0.8)
                        win = self.vision.user_window()
        self.app_key = chosen or wf.get("default_app", "")
        self.steps = list(wf["apps"][self.app_key]["steps"])
        self.idx = 0
        self.active = True
        self.paused = False
        self.away = 0
        self.shown = False
        self.last_show = {}
        self.step_started = time.time()
        self._hole_boxes = []
        # watch the viewport quickly while guiding
        self._restore = {"observe_enabled": self.settings.get("observe_enabled"), "interval": self.vision.base_interval}
        self.settings.set("observe_enabled", True)
        self.vision.base_interval = 1.5
        self.vision.interval = 1.5
        self.hub.status(observe_enabled=True, guide=self.status())
        in_front = self._detect_app(win) == self.app_key
        intro = f"Starting the guide: {wf['name']}, in {self.app_label}."
        if brought:
            intro += f" I brought {self.app_label} to the front."
        elif not in_front:
            intro += f" I couldn't see {self.app_label} in front; switch to it and I'll follow along."
        self.hub.transcript("eli", intro)
        await self._recognize()
        await self.show_step(0)
        return f"{intro} Step 1 of {len(self.steps)}: {self.steps[0]['title']}. Say “next” when you're done with a step, “repeat” to hear it again, or “stop the guide”."

    async def _recognize(self) -> None:
        rec = self.wf.get("recognize")
        if not rec or not self.llm.available:
            return
        frame = await asyncio.to_thread(self.vision.capture_now)
        if frame.blocked or not self._is_app_window(frame.window):
            return
        boxes = await self.detect_boxes(frame, rec["query"], max_boxes=12)
        boxes = self._filter_similar(boxes)
        self._holes = len(boxes)
        self._hole_boxes = sorted(boxes, key=lambda b: b[0])
        if boxes:
            inds = [{"kind": "box", "x": x, "y": y, "w": w, "h": h, "label": f"{i+1}", "glow": True} for i, (x, y, w, h, _l) in enumerate(boxes)]
            self._emit({"action": "recognized", "indicators": inds, "text": f"{len(boxes)} holes found"})
            n = len(boxes)
            say = rec.get("say", "").format(n=n)
            if rec.get("expected_count") and n != rec["expected_count"]:
                say += f" I expected {rec['expected_count']} but count {n}; I'll guide you through the same steps anyway."
            self._say(say)
            await asyncio.sleep(min(6.0, 1.5 + 0.35 * len(say.split())))

    async def show_step(self, i: int, repeat: bool = False) -> None:
        if not self.active or not (0 <= i < len(self.steps)):
            return
        step = self.steps[i]
        self.idx = i
        self.step_started = time.time()
        self.stuck_offered = False
        self.last_vision = 0.0
        frame = await asyncio.to_thread(self.vision.capture_now)
        await asyncio.to_thread(frame.ocr)
        indicators = await self.resolve_target(step, frame)
        payload = {"action": "show", "step": {"index": i, "total": len(self.steps), "id": step["id"], "title": step["title"],
                                              "instruction": step["instruction"], "optional": bool(step.get("optional"))},
                   "indicators": indicators, "app": self.app_label, "repeat": repeat}
        self.last_show = payload
        self.shown = True
        self._emit(payload)
        self.hub.transcript("eli", f"Step {i+1}/{len(self.steps)} — {step['title']}: {step['instruction']}")
        self._say(("Again: " if repeat else f"Step {i+1}. ") + step["say"])
        self.hub.status(guide=self.status())

    def stop(self, silent: bool = False) -> str:
        if not self.active:
            self._emit({"action": "clear"})  # clears previews or stale cards too
            return "No guide is running."
        self.active = False
        self.paused = False
        self.shown = False
        self.last_show = {}
        if self._restore:
            self.settings.set("observe_enabled", bool(self._restore.get("observe_enabled")))
            self.vision.base_interval = self._restore.get("interval", self.vision.base_interval)
            self.vision.interval = self.vision.base_interval
            self.hub.status(observe_enabled=self.settings.get("observe_enabled"), guide=self.status())
        self._emit({"action": "clear"})
        if not silent:
            self._say("Guide stopped.")
        return "Guide stopped."

    async def complete(self) -> None:
        self._emit({"action": "complete", "text": self.wf.get("done_say", "Done.")})
        self._say(self.wf.get("done_say", "All done."))
        self.hub.transcript("eli", self.wf.get("done_say", "Done."))
        try:
            self.memory.remember(f"Completed the guided workflow '{self.wf['name']}' in {self.app_label}.", kind="episode", importance=0.6, source="agent")
        except Exception:
            pass
        self.stop(silent=True)

    # -- controls -------------------------------------------------------------------------------------------
    async def control(self, action: str) -> str:
        if not self.active:
            return "No guide is running."
        a = action.lower()
        if a in ("next", "done"):
            await self.advance("user")
            return "" if self.active else "Done."
        if a == "repeat":
            await self.show_step(self.idx, repeat=True)
            return ""
        if a == "back":
            if self.idx == 0:
                return "This is the first step."
            await self.show_step(self.idx - 1, repeat=True)
            return ""
        if a == "skip":
            await self.advance("skipped")
            return "" if self.active else "Done."
        if a in ("alt", "alternative", "help"):
            step = self.steps[self.idx]
            alt = step.get("alt") or "There's no alternative route for this step; take it slowly and say repeat if you want to hear it again."
            self._emit({"action": "hint", "text": alt})
            self.hub.transcript("eli", alt)
            self._say(alt)
            return ""
        if a == "stop":
            return self.stop()
        return "I didn't get that. Say next, repeat, back, skip, another way, or stop the guide."

    async def advance(self, reason: str) -> None:
        if not self.active:
            return
        self._emit({"action": "step_done", "index": self.idx, "reason": reason})
        self.hub.set_state("success")
        if self.idx + 1 >= len(self.steps):
            await self.complete()
            return
        await asyncio.sleep(0.9)
        await self.show_step(self.idx + 1)

    # -- monitoring (called from the vision thread) -----------------------------------------------------------
    def on_frame(self, frame) -> None:
        if not self.active or not self.shown or self.checking or self.loop is None:
            return
        if time.time() - self.last_check < self.CHECK_EVERY:
            return
        self.checking = True
        asyncio.run_coroutine_threadsafe(self._check(frame), self.loop)

    async def _check(self, frame) -> None:
        try:
            if not self.active:
                return
            step = self.steps[self.idx]
            if not self._is_app_window(frame.window):
                self.away += 1
                if self.away >= self.AWAY_FRAMES and not self.paused:
                    self.paused = True
                    self._emit({"action": "paused", "app": self.app_label})
                    self._say(f"I'll wait until you're back in {self.app_label}.")
                return
            if self.paused:
                self.paused = False
                self.away = 0
                self._emit({"action": "resumed"})
                await self.show_step(self.idx, repeat=True)
                return
            self.away = 0
            if await self._satisfied(step, frame):
                await self.advance("auto")
                return
            # jumped ahead? (the next step's non-vision conditions already hold)
            nxt = self.steps[self.idx + 1] if self.idx + 1 < len(self.steps) else None
            if nxt and step.get("optional") and await self._satisfied(nxt, frame):
                self._say("Looks like you're already past this one.")
                await self.advance("auto")
                return
            if time.time() - self.step_started > step.get("timeout", 90) and not self.stuck_offered:
                self.stuck_offered = True
                self._emit({"action": "stuck", "index": self.idx})
                msg = (f"Step {self.idx+1} seems to be taking a while. Say “repeat” to hear it again, “next” if it's done, "
                       f"“another way” for an alternative, or “skip”.")
                self.hub.emit({"type": "nudge", "id": f"guide-{self.idx}", "text": msg, "actions": [
                    {"id": "guide:repeat", "label": "Repeat"}, {"id": "guide:alt", "label": "Another way"},
                    {"id": "guide:next", "label": "I did it", "primary": True}, {"id": "guide:skip", "label": "Skip"}]})
                self._say(msg)
        except Exception as e:
            log.warning("guide check failed: %s", e)
        finally:
            self.checking = False
            self.last_check = time.time()

    async def _satisfied(self, step: dict, frame, cheap_only: bool = False) -> bool:
        text = ""
        try:
            text = (await asyncio.to_thread(frame.window_text, 6000)).lower()
        except Exception:
            pass
        for cond in step.get("expect", []):
            k = cond.get("kind")
            if k == "ocr_contains" and cond["text"].lower() in text:
                return True
            if k == "ocr_absent" and cond["text"].lower() not in text and text:
                return True
            if k == "title_contains" and cond["text"].lower() in (frame.window.title or "").lower():
                return True
            if k == "vision" and not cheap_only and self.llm.available and time.time() - self.last_vision >= self.VISION_EVERY:
                self.last_vision = time.time()
                if await self.ask_yes_no(frame, cond["question"]):
                    return True
        return False

    # -- perception -------------------------------------------------------------------------------------------
    async def ask_yes_no(self, frame, question: str) -> bool:
        try:
            b64, media = self.vision.model_image(frame)
            r = await self.llm.complete((YESNO_SYSTEM, ""), [{"role": "user", "parts": [image_part(media, b64), text_part(question)]}], [])
            ans = r.text.strip().upper()
            log.info("vision check %r -> %s", question[:60], ans[:40])
            return ans.startswith("YES")
        except Exception as e:
            log.info("vision check failed: %s", e)
            return False

    async def detect_boxes(self, frame, query: str, max_boxes: int = 8) -> list[tuple[int, int, int, int, str]]:
        """Ask the vision model for boxes; returns screen-pixel (x, y, w, h, label) tuples."""
        if not self.llm.available:
            return []
        try:
            b64, media = self.vision.model_image(frame)
            prompt = (f"Find: {query}. Return ONLY this JSON: {{\"boxes\": [{{\"label\": \"...\", \"box_2d\": [ymin, xmin, ymax, xmax]}}]}} "
                      f"with at most {max_boxes} boxes, coordinates normalized 0-1000. Return {{\"boxes\": []}} if nothing matches.")
            r = await self.llm.complete((DETECT_SYSTEM, ""), [{"role": "user", "parts": [image_part(media, b64), text_part(prompt)]}], [])
            m = re.search(r"\{.*\}", r.text, re.S)
            data = json.loads(m.group(0)) if m else {}
            w_img, h_img = self.vision.last_model_size
            out = []
            for b in data.get("boxes", [])[:max_boxes]:
                y0, x0, y1, x1 = [float(v) for v in b.get("box_2d", [0, 0, 0, 0])]
                sx0, sy0 = self.vision.to_screen_coords(x0 / 1000 * w_img, y0 / 1000 * h_img)
                sx1, sy1 = self.vision.to_screen_coords(x1 / 1000 * w_img, y1 / 1000 * h_img)
                if sx1 - sx0 < 4 or sy1 - sy0 < 4:
                    continue
                out.append((sx0, sy0, sx1 - sx0, sy1 - sy0, str(b.get("label", ""))))
            log.info("detect %r -> %d boxes", query[:50], len(out))
            return out
        except Exception as e:
            log.info("detect_boxes failed: %s", e)
            return []

    async def resolve_target(self, step: dict, frame) -> list[dict]:
        t = step.get("target") or {}
        kind = t.get("kind")
        l, tp, r, b = frame.window.rect
        if r - l < 100:
            l, tp, r, b = 0, 0, frame.size[0], frame.size[1]
        inds: list[dict] = []
        if kind == "ui_text":
            hit = None
            for text in [t.get("text", "")] + list(t.get("alt", [])):
                hit = await asyncio.to_thread(self.vision.find_text_box, text, frame, t.get("region"))
                if hit:
                    break
            if hit:
                x, y, w, h, label = hit
                inds.append({"kind": "box", "x": x - 6, "y": y - 4, "w": w + 12, "h": h + 8, "label": f"Click “{label}”", "glow": True})
                inds.append({"kind": "ring", "x": x + w // 2, "y": y + h // 2, "r": max(22, h)})
                inds.append({"kind": "arrow", "x": x + w // 2, "y": y + h // 2})
            else:
                reg = self._region_fallback(t.get("region"), (l, tp, r, b), f"Look here for “{t.get('text', '')}”")
                inds.append(reg)
                inds.append({"kind": "arrow", "x": reg["x"] + reg["w"] // 2, "y": reg["y"] + reg["h"] // 2})
        elif kind == "geometry":
            boxes = await self.detect_boxes(frame, t.get("query", ""), max_boxes=10 if t.get("multi") else 3)
            if boxes:
                for i, (x, y, w, h, label) in enumerate(boxes):
                    inds.append({"kind": "box", "x": x, "y": y, "w": w, "h": h, "label": label or f"{i+1}", "glow": True})
                x, y, w, h, _ = boxes[0]
                inds.append({"kind": "ring", "x": x + w // 2, "y": y + h // 2, "r": max(24, min(w, h) // 2 + 8)})
                inds.append({"kind": "arrow", "x": x + w // 2, "y": y + h // 2})
            else:
                reg = self._region_fallback("center", (l, tp, r, b), "In the viewport: " + t.get("query", ""))
                inds.append(reg)
                inds.append({"kind": "arrow", "x": reg["x"] + reg["w"] // 2, "y": reg["y"] + reg["h"] // 2})
        elif kind == "window_region":
            x0, y0, x1, y1 = t.get("rel", [0.4, 0.4, 0.6, 0.6])
            W, H = r - l, b - tp
            box = {"kind": "box", "x": int(l + x0 * W), "y": int(tp + y0 * H), "w": int((x1 - x0) * W), "h": int((y1 - y0) * H), "label": step["title"], "glow": True}
            inds.append(box)
            inds.append({"kind": "arrow", "x": box["x"] + box["w"] // 2, "y": box["y"] + box["h"] // 2})
        elif kind == "hole_points":
            inds.extend(self._hole_point_indicators(t.get("pattern", "all"), (l, tp, r, b)))
        elif kind == "key":
            pass
        if t.get("key") or kind == "key":
            inds.append({"kind": "key", "keys": t.get("keys") or t.get("key")})
        return inds

    @staticmethod
    def _filter_similar(boxes):
        """Keep boxes of similar size (the holes); drop stray UI icons and outliers."""
        if len(boxes) < 3:
            return boxes
        areas = sorted(b[2] * b[3] for b in boxes)
        med = areas[len(areas) // 2]
        keep = [b for b in boxes if 0.4 * med <= b[2] * b[3] <= 2.5 * med]
        return keep if len(keep) >= 2 else boxes

    def _hole_point_indicators(self, pattern: str, rect) -> list[dict]:
        """Exact click points derived from the recognised hole boxes (sorted left to right)."""
        boxes = self._hole_boxes
        if not boxes:
            reg = self._region_fallback("center", rect, "The row of holes in the viewport")
            return [reg, {"kind": "arrow", "x": reg["x"] + reg["w"] // 2, "y": reg["y"] + reg["h"] // 2}]
        centers = [(x + w // 2, y + h // 2, w, h) for (x, y, w, h, _l) in boxes]
        out: list[dict] = []
        if pattern == "bridge":
            lx, ly, lw, lh = centers[0]
            rx, ry, rw, rh = centers[-1]
            p1 = (lx, ly - int(0.3 * lh))
            p2 = (rx, ry + int(0.3 * rh))
            out.append({"kind": "ring", "x": p1[0], "y": p1[1], "r": 22, "label": "1: click first"})
            out.append({"kind": "ring", "x": p2[0], "y": p2[1], "r": 22, "label": "2: click second"})
            out.append({"kind": "arrow", "x": p1[0], "y": p1[1]})
        elif pattern == "inner":
            for i, (cx, cy, w, h) in enumerate(centers):
                if i > 0:
                    out.append({"kind": "ring", "x": cx - int(0.45 * w), "y": cy, "r": 14})
                if i < len(centers) - 1:
                    out.append({"kind": "ring", "x": cx + int(0.45 * w), "y": cy, "r": 14})
            x0, x1 = centers[0][0], centers[-1][0]
            y0 = min(c[1] for c in centers)
            out.append({"kind": "box", "x": x0 - 10, "y": y0 - int(0.35 * centers[0][3]), "w": x1 - x0 + 20, "h": int(0.7 * centers[0][3]), "label": "trim everything inside", "glow": True})
            mid = centers[len(centers) // 2]
            out.append({"kind": "arrow", "x": mid[0], "y": mid[1]})
        else:
            for i, (x, y, w, h, _l) in enumerate(boxes):
                out.append({"kind": "box", "x": x, "y": y, "w": w, "h": h, "label": f"{i+1}", "glow": True})
            mid = centers[len(centers) // 2]
            out.append({"kind": "arrow", "x": mid[0], "y": mid[1]})
        return out

    def _region_fallback(self, region: Optional[str], rect, label: str) -> dict:
        l, tp, r, b = rect
        W, H = r - l, b - tp
        if region == "top":
            box = (l + int(0.2 * W), tp + int(0.04 * H), int(0.6 * W), int(0.09 * H))
        elif region == "left":
            box = (l + int(0.01 * W), tp + int(0.12 * H), int(0.2 * W), int(0.5 * H))
        elif region == "right":
            box = (l + int(0.78 * W), tp + int(0.12 * H), int(0.21 * W), int(0.5 * H))
        elif region == "bottom":
            box = (l + int(0.2 * W), tp + int(0.86 * H), int(0.6 * W), int(0.1 * H))
        else:
            box = (l + int(0.3 * W), tp + int(0.25 * H), int(0.4 * W), int(0.5 * H))
        x, y, w, h = box
        return {"kind": "region", "x": x, "y": y, "w": w, "h": h, "label": label}
