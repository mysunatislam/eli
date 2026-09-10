"""Dynamic guide planner: turns "guide me through <anything>" into a runnable guided workflow.

Given the user's goal, a screenshot of the application in front, and its OCR text, the vision model
writes the walkthrough in the same step schema the built-in workflows use (see eli/guides/__init__),
so the guide engine runs it unchanged: on-screen indicators, narration, watch-and-auto-advance.
The plan is validated hard before it runs - unknown target/expect kinds are dropped, timeouts are
clamped, and a plan that does not survive validation is rejected rather than half-run.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from ..llm import image_part, text_part

log = logging.getLogger("eli.guide.plan")

MAX_STEPS = 12
REGIONS = {"top", "left", "right", "bottom", "center"}
EXPECT_KINDS = {"ocr_contains", "ocr_absent", "title_contains", "vision", "manual"}

PLAN_SYSTEM = (
    "You write step-by-step guided walkthroughs for desktop applications. Eli, a screen assistant, draws your steps "
    "over the live app one at a time (glowing boxes, pulsing rings, arrows, key badges), speaks the narration, watches "
    "the screen after every change, and moves to the next step only when the current step's expect conditions hold - "
    "that is how Eli notices the user did the step right. You answer with ONE JSON object and nothing else."
)

SCHEMA = """Write the walkthrough as JSON:
{
 "app_label": "short app name, e.g. FreeCAD",
 "name": "3-6 word name for the walkthrough",
 "recognize": {"query": "what to find and box in the canvas before starting, one box per object",
               "say": "spoken once they are found; may use {n} for the count", "expected_count": 5} or null,
 "steps": [
  {"id": "snake_case",
   "title": "2-5 words",
   "say": "conversational narration, 1-3 short sentences",
   "instruction": "precise card text: exact menu paths, button names, keys",
   "target": ONE of:
     {"kind": "ui_text", "text": "exact visible label", "alt": ["other spellings"], "region": "top|left|right|bottom", "key": "T"}
     {"kind": "geometry", "query": "what to find in the canvas/viewport", "multi": true}
     {"kind": "key", "keys": "Ctrl+E"}
     {"kind": "window_region", "rel": [x0, y0, x1, y1]}  (fractions of the app window; last resort),
   "expect": 1-3 of (any one holding advances the step):
     {"kind": "ocr_contains", "text": "text that appears in the window when the step is done"}
     {"kind": "ocr_absent", "text": "text that disappears when done"}
     {"kind": "title_contains", "text": "..."}
     {"kind": "vision", "question": "yes/no question about the screenshot that is YES only when done"},
   "timeout": 90,
   "alt": "an alternative route for when the user is stuck",
   "optional": false}
 ],
 "done_say": "one closing sentence"
}

Rules:
- 3 to 10 steps, one physical action each (one click, one tool, one dialog). Start from what the screenshot shows NOW.
- Use the exact UI wording of THIS application. Prefer labels you can read in the OCR text - those are what Eli can point at.
- Every step needs at least one expect condition that genuinely distinguishes done from not-done. When no on-screen text
  changes, ask a vision question about the visible result of the action.
- "ui_text" only for labels literally visible on screen; objects inside the canvas/viewport are "geometry".
- Include "recognize" when the request is about objects visible in the canvas ("these holes", "that edge"), else null.
- If the request is not something that can be done step by step in this application, return {"steps": []}."""


def _clean_target(t) -> Optional[dict]:
    if not isinstance(t, dict):
        return None
    kind = t.get("kind")
    key = str(t.get("key") or t.get("keys") or "").strip()[:24]
    if kind == "ui_text":
        text = str(t.get("text") or "").strip()
        if not text:
            return None
        out: dict = {"kind": "ui_text", "text": text[:60]}
        alts = [str(a).strip()[:60] for a in (t.get("alt") or []) if str(a).strip()]
        if alts:
            out["alt"] = alts[:4]
        if t.get("region") in REGIONS:
            out["region"] = t["region"]
        if key:
            out["key"] = key
        return out
    if kind == "geometry":
        q = str(t.get("query") or "").strip()
        if not q:
            return None
        out = {"kind": "geometry", "query": q[:200], "multi": bool(t.get("multi"))}
        if key:
            out["key"] = key
        return out
    if kind == "key":
        return {"kind": "key", "keys": key} if key else None
    if kind == "window_region":
        try:
            rel = [float(v) for v in (t.get("rel") or [])][:4]
        except (TypeError, ValueError):
            return None
        if len(rel) == 4 and all(-0.001 <= v <= 1.001 for v in rel) and rel[2] > rel[0] and rel[3] > rel[1]:
            out = {"kind": "window_region", "rel": [min(1.0, max(0.0, v)) for v in rel]}
            if key:
                out["key"] = key
            return out
        return None
    return None


def _clean_step(s, i: int) -> Optional[dict]:
    if not isinstance(s, dict):
        return None
    title = str(s.get("title") or "").strip()
    instruction = str(s.get("instruction") or "").strip()
    say = str(s.get("say") or "").strip() or instruction
    if not title or not say:
        return None
    expect = []
    for c in (s.get("expect") or [])[:4]:
        if not isinstance(c, dict) or c.get("kind") not in EXPECT_KINDS:
            continue
        if c["kind"] == "vision":
            q = str(c.get("question") or "").strip()
            if q:
                expect.append({"kind": "vision", "question": q[:300]})
        elif c["kind"] == "manual":
            expect.append({"kind": "manual"})
        else:
            txt = str(c.get("text") or "").strip()
            if txt:
                expect.append({"kind": c["kind"], "text": txt[:80]})
    try:
        timeout = max(30, min(300, int(float(s.get("timeout", 90)))))
    except (TypeError, ValueError):
        timeout = 90
    step = {
        "id": re.sub(r"\W+", "_", str(s.get("id") or f"step_{i + 1}")).strip("_").lower() or f"step_{i + 1}",
        "title": title[:60],
        "say": say[:500],
        "instruction": (instruction or say)[:400],
        "target": _clean_target(s.get("target")) or {"kind": "window_region", "rel": [0.3, 0.25, 0.7, 0.75]},
        "expect": expect,
        "timeout": timeout,
    }
    alt = str(s.get("alt") or "").strip()
    if alt:
        step["alt"] = alt[:400]
    if s.get("optional") is True:
        step["optional"] = True
    return step


def build_workflow(data, window, goal: str = "") -> Optional[dict]:
    """Validate a planned dict into a workflow the guide engine can run; None if it can't be trusted."""
    if not isinstance(data, dict):
        return None
    steps = [cs for i, s in enumerate((data.get("steps") or [])[:MAX_STEPS]) if (cs := _clean_step(s, i))]
    if len(steps) < 2:
        return None
    proc = (getattr(window, "process", "") or "").strip().lower()
    title = (getattr(window, "title", "") or "").strip()
    if not proc and not title:
        return None
    label = str(data.get("app_label") or "").strip()[:40] or (title.split(" - ")[-1].strip() or "the app")
    match: dict = {"process": [proc] if proc else [], "title": []}
    if label and label.lower() in title.lower():
        match["title"].append(label.lower())
    elif title:
        match["title"].append(title.lower()[:40])
    recognize = None
    rec = data.get("recognize")
    if isinstance(rec, dict) and str(rec.get("query") or "").strip():
        recognize = {"query": str(rec["query"]).strip()[:200],
                     "say": str(rec.get("say") or "").strip()[:300] or "Found them. Let's go."}
        try:
            n = int(rec.get("expected_count"))
            if n > 0:
                recognize["expected_count"] = n
        except (TypeError, ValueError):
            pass
    name = str(data.get("name") or "").strip()[:80] or (goal.strip()[:80] or "your task")
    return {
        "id": "dynamic", "dynamic": True, "name": name, "keywords": [],
        "recognize": recognize,
        "apps": {"dynamic": {"label": label, "match": match, "steps": steps}},
        "default_app": "dynamic",
        "done_say": str(data.get("done_say") or "").strip()[:300] or f"That's it - {name}. Nice work.",
    }


async def plan(llm, vision, frame, goal: str, ocr_text: str = "") -> Optional[dict]:
    """Ask the vision model to write the walkthrough for `goal` from the current frame."""
    if not llm.available:
        return None
    win = frame.window
    b64, media = vision.model_image(frame)
    head = (f"The user asked: \"{goal.strip()}\"\n"
            f"Application in front: {(win.title or 'unknown window')} (process: {getattr(win, 'process', '') or 'unknown'})\n")
    if ocr_text.strip():
        head += f"Text visible in the app window (OCR):\n{ocr_text.strip()[:3000]}\n"
    head += "A screenshot of the app is attached.\n\n"
    try:
        r = await llm.complete((PLAN_SYSTEM, ""), [{"role": "user", "parts": [image_part(media, b64), text_part(head + SCHEMA)]}], [])
        m = re.search(r"\{.*\}", r.text, re.S)
        data = json.loads(m.group(0)) if m else None
    except Exception as e:
        log.warning("guide planning failed: %s", e)
        return None
    wf = build_workflow(data, win, goal)
    if wf:
        log.info("planned guide %r for %r: %d steps in %s", wf["name"], goal[:60], len(wf["apps"]["dynamic"]["steps"]),
                 wf["apps"]["dynamic"]["label"])
    else:
        log.info("plan for %r rejected (steps=%s)", goal[:60], len((data or {}).get("steps") or []) if isinstance(data, dict) else "no json")
    return wf
