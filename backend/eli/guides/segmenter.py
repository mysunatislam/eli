"""Teach-me mode, part 2: turn a recording into a guide the engine can run.

Two segmenters produce the same step schema (see eli/guides/__init__.py and planner.py):

  heuristic(recording)          deterministic, offline, always available. One step per click /
                                double-click / right-click / shortcut, typing folded into the click
                                that focused the field. Targets come from the OCR label under the
                                cursor; completion conditions from the OCR lines that appeared or
                                disappeared right after the action, or the window title change.

  segment(llm, vision, rec)     asks the vision model to rewrite the same trace (text + a handful
                                of marked-up screenshots) into fewer, better-narrated steps, then
                                validates it with planner.build-style cleaning and back-fills any
                                target/expect the model left vague from the recorded evidence.
                                Falls back to heuristic() when the model is unavailable or the
                                result does not survive validation.

A recorded guide is stored with `recorded: True`, its source recording id, and the event indices
each step came from, so a later review UI can show "step 3 came from this click" and let the
author fix it.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Optional

from .planner import _clean_step, EXPECT_KINDS  # same validation as dynamic plans
from .recorder import Recording, TraceEvent

log = logging.getLogger("eli.guide.segment")

MAX_RECORDED_STEPS = 30
MAX_IMAGES = 6
GENERIC_LABELS = {"ok", "cancel", "close", "yes", "no", "apply", "x", "-", "+"}


# --- helpers ----------------------------------------------------------------------------------------------

def _title_changed(ev: TraceEvent) -> Optional[str]:
    b = (ev.window_before or {}).get("title") or ""
    a = (ev.window_after or {}).get("title") or ""
    if a and a != b:
        # the distinctive part of the new title (drop the app suffix after " - ")
        head = a.split(" - ")[0].strip()
        return head[:60] if len(head) >= 3 else a[:60]
    return None


def _best_appeared(ev: TraceEvent, exclude: str = "") -> Optional[str]:
    ex = exclude.strip().lower()
    for line in ev.appeared or []:
        l = line.strip()
        if len(l) >= 3 and l.lower() != ex and l.lower() not in GENERIC_LABELS:
            return l[:80]
    return None


def _best_disappeared(ev: TraceEvent, exclude: str = "") -> Optional[str]:
    ex = exclude.strip().lower()
    for line in ev.disappeared or []:
        l = line.strip()
        if len(l) >= 3 and l.lower() != ex and l.lower() not in GENERIC_LABELS:
            return l[:80]
    return None


def expect_from_event(ev: TraceEvent, label: str = "") -> list[dict]:
    """Completion conditions supported by what actually changed on screen after this action."""
    out: list[dict] = []
    a = _best_appeared(ev, label)
    if a:
        out.append({"kind": "ocr_contains", "text": a})
    t = _title_changed(ev)
    if t:
        out.append({"kind": "title_contains", "text": t})
    if not out:
        d = _best_disappeared(ev, label)
        if d:
            out.append({"kind": "ocr_absent", "text": d})
    if not out:
        out.append({"kind": "manual"})
    return out[:3]


def target_from_event(ev: TraceEvent) -> dict:
    if ev.kind in ("shortcut", "key") and ev.keys:
        return {"kind": "key", "keys": ev.keys}
    if ev.label:
        t: dict = {"kind": "ui_text", "text": ev.label[:60]}
        if ev.region in ("top", "left", "right", "bottom"):
            t["region"] = ev.region
        return t
    if ev.rel and len(ev.rel) == 2:
        fx, fy = ev.rel
        return {"kind": "window_region", "rel": [max(0.0, fx - 0.06), max(0.0, fy - 0.05), min(1.0, fx + 0.06), min(1.0, fy + 0.05)]}
    return {"kind": "window_region", "rel": [0.3, 0.25, 0.7, 0.75]}


def _verb(ev: TraceEvent) -> str:
    return {"click": "Click", "dblclick": "Double-click", "rclick": "Right-click", "mclick": "Middle-click",
            "shortcut": "Press", "key": "Press", "scroll": "Scroll"}.get(ev.kind, "Do")


def _describe(ev: TraceEvent, typed: Optional[TraceEvent] = None) -> tuple[str, str, str]:
    """(title, say, instruction) for one event, optionally with the typing that followed it."""
    where = f" in the {ev.region} of the window" if ev.region and ev.region != "center" else ""
    if ev.kind in ("shortcut", "key"):
        title = f"Press {ev.keys}"
        instr = f"Press {ev.keys}."
        say = f"Press {ev.keys.replace('+', ' ')}."
    elif ev.label:
        title = f"{_verb(ev)} {ev.label}"[:60]
        instr = f"{_verb(ev)} “{ev.label}”{where}."
        say = f"{_verb(ev)} {ev.label}."
    else:
        title = f"{_verb(ev)} here"
        instr = f"{_verb(ev)} at the highlighted spot{where}."
        say = f"{_verb(ev)} where I'm pointing."
    if typed is not None:
        if typed.text:
            instr += f" Then type “{typed.text}”."
            say += f" Then type {typed.text}."
        else:
            instr += f" Then type your value ({typed.chars} characters were entered when this was recorded)."
            say += " Then type your value."
        title = f"{title} and type"[:60]
    return title, say, instr


def _app_of(rec: Recording) -> tuple[str, dict]:
    proc = (rec.app.get("process") or "").strip().lower()
    title = (rec.app.get("title") or "").strip()
    label = (rec.app.get("app") or "").strip() or (title.split(" - ")[-1].strip() if title else "") or "the app"
    match = {"process": [proc] if proc else [], "title": []}
    if label and label.lower() in title.lower():
        match["title"].append(label.lower())
    elif title:
        match["title"].append(title.lower()[:40])
    return label[:40], match


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return (s or "guide")[:48]


def keywords_for(name: str, goal: str = "") -> list[list[str]]:
    """One any-of group: the full name plus its distinctive words (so 'guide me through X' finds it)."""
    stop = {"the", "and", "with", "into", "from", "this", "that", "for", "how", "your", "my", "in", "on", "to", "of", "a"}
    words = {w for w in re.findall(r"[a-z0-9]{4,}", f"{name} {goal}".lower()) if w not in stop}
    group = [name.lower().strip()] + sorted(words)
    return [group] if group else []


def assemble(steps: list[dict], rec: Recording, name: str = "", done_say: str = "", source_events: Optional[list[list[int]]] = None) -> Optional[dict]:
    """Validate steps and wrap them into a workflow dict (same shape as planner.build_workflow, larger cap)."""
    clean: list[dict] = []
    for i, s in enumerate(steps[:MAX_RECORDED_STEPS]):
        cs = _clean_step(s, i)
        if cs:
            if source_events and i < len(source_events):
                cs["events"] = list(source_events[i])
            elif isinstance(s.get("events"), list):
                cs["events"] = [int(e) for e in s["events"] if isinstance(e, (int, float))]
            clean.append(cs)
    if len(clean) < 1:
        return None
    label, match = _app_of(rec)
    nm = (name or rec.name or rec.goal or "recorded guide").strip()[:80]
    wf_id = f"rec_{_slug(nm)}_{rec.id[-6:]}"
    return {
        "id": wf_id, "recorded": True, "recording_id": rec.id, "name": nm, "goal": rec.goal,
        "keywords": keywords_for(nm, rec.goal), "recognize": None,
        "apps": {"recorded": {"label": label, "match": match, "steps": clean}},
        "default_app": "recorded",
        "done_say": (done_say or "").strip()[:300] or f"That's it - {nm}. Nice work.",
        "created": rec.ended or rec.started,
    }


# --- deterministic segmenter --------------------------------------------------------------------------------

def heuristic(rec: Recording, name: str = "") -> Optional[dict]:
    """One step per meaningful action, no model needed."""
    evs = rec.events
    steps: list[dict] = []
    src: list[list[int]] = []
    i = 0
    while i < len(evs):
        ev = evs[i]
        if ev.kind == "scroll":
            i += 1
            continue
        if ev.kind == "type" and not steps:
            # typing before any click: its own step
            title, say, instr = "Type your value", "Type your value.", (f"Type “{ev.text}”." if ev.text else "Type your value.")
            steps.append({"id": f"type_{ev.i}", "title": title, "say": say, "instruction": instr,
                          "target": {"kind": "window_region", "rel": [0.3, 0.3, 0.7, 0.7]}, "expect": [{"kind": "manual"}], "timeout": 90})
            src.append([ev.i])
            i += 1
            continue
        if ev.kind == "type":
            i += 1
            continue
        typed = None
        used = [ev.i]
        if i + 1 < len(evs) and evs[i + 1].kind == "type":
            typed = evs[i + 1]
            used.append(typed.i)
            # an Enter right after typing belongs to the same step
            if i + 2 < len(evs) and evs[i + 2].kind == "key" and evs[i + 2].keys == "Enter":
                used.append(evs[i + 2].i)
                ev_for_expect = evs[i + 2]
            else:
                ev_for_expect = typed if (typed.appeared or typed.disappeared) else ev
        else:
            ev_for_expect = ev
        title, say, instr = _describe(ev, typed)
        if typed is not None and any(e.kind == "key" and e.keys == "Enter" for e in evs[i + 2:i + 3]):
            instr += " Press Enter."
            say += " Then press Enter."
        step = {
            "id": f"{ev.kind}_{ev.i}", "title": title, "say": say, "instruction": instr,
            "target": target_from_event(ev), "expect": expect_from_event(ev_for_expect, ev.label),
            "timeout": 90,
        }
        steps.append(step)
        src.append(used)
        i += len(used)
    return assemble(steps, rec, name=name, source_events=src)


# --- model segmenter -------------------------------------------------------------------------------------------

SEGMENT_SYSTEM = (
    "You turn a recording of an expert using a desktop application into a step-by-step guided walkthrough. Eli, a screen "
    "assistant, will draw your steps over the live app for a learner (glowing boxes on the labels you name, key badges for "
    "shortcuts), speak the narration, and advance only when the step's expect conditions hold on screen. You answer with "
    "ONE JSON object and nothing else."
)

SEGMENT_SCHEMA = """Write the walkthrough as JSON:
{
 "name": "3-6 word name",
 "steps": [
  {"id": "snake_case",
   "events": [3, 4],                        <- the trace event numbers this step covers (REQUIRED, in order)
   "title": "2-5 words",
   "say": "conversational narration, 1-3 short sentences, explains WHY when it helps",
   "instruction": "precise card text: exact labels, menu paths, keys, what value to type",
   "target": ONE of:
     {"kind": "ui_text", "text": "exact label from the trace", "alt": ["other spellings"], "region": "top|left|right|bottom", "key": "T"}
     {"kind": "geometry", "query": "what to find in the canvas/viewport", "multi": true}
     {"kind": "key", "keys": "Ctrl+E"}
     {"kind": "window_region", "rel": [x0, y0, x1, y1]},
   "expect": 1-3 of (any one holding advances):
     {"kind": "ocr_contains", "text": "text that APPEARED in the trace after this step"}
     {"kind": "ocr_absent", "text": "text that DISAPPEARED"}
     {"kind": "title_contains", "text": "..."}
     {"kind": "vision", "question": "yes/no question about the screenshot, YES only when done"},
   "timeout": 90,
   "alt": "an alternative route if stuck",
   "optional": false}
 ],
 "done_say": "one closing sentence"
}

Rules:
- Merge trace events that form ONE logical action for a learner (open a menu + pick an item; click a field + type + Enter;
  a burst of clicks that select things) into one step. Keep every distinct action. Do not invent actions that are not in the trace.
- Prefer targets and expect texts that literally appear in the trace (labels under the cursor, lines that appeared/disappeared):
  those are what Eli can point at and verify. Use "geometry" only for things inside a canvas/viewport that OCR can't read.
- Every step needs at least one expect condition that distinguishes done from not-done. When the trace shows nothing
  changing, ask a vision question about the visible result.
- Narrate like a patient expert: short, concrete, confident."""


def _trace_text(rec: Recording, image_events: list[int]) -> str:
    lines = [f"Recording: “{rec.name}”" + (f" — goal: {rec.goal}" if rec.goal else ""),
             f"Application: {rec.app.get('app') or rec.app.get('process') or 'unknown'} — window “{rec.app.get('title', '')}”",
             f"Duration: {rec.duration:.0f}s, {len(rec.events)} events.", "", "Trace (event number: what the expert did):"]
    for ev in rec.events:
        img = " [screenshot attached]" if ev.i in image_events else ""
        if ev.kind in ("click", "dblclick", "rclick", "mclick"):
            what = f"{_verb(ev)} on “{ev.label}”" if ev.label else f"{_verb(ev)} at ({ev.rel[0]:.2f}, {ev.rel[1]:.2f}) of the window" if ev.rel else _verb(ev)
            if ev.region:
                what += f" ({ev.region})"
        elif ev.kind in ("shortcut", "key"):
            what = f"Press {ev.keys}"
        elif ev.kind == "type":
            what = f"Type “{ev.text}”" if ev.text else f"Type {ev.chars} characters"
        elif ev.kind == "scroll":
            what = f"Scroll {ev.keys}"
        else:
            what = ev.kind
        extra = []
        t = _title_changed(ev)
        if t:
            extra.append(f"window title became “{t}”")
        if ev.appeared:
            extra.append("appeared: " + "; ".join(f"“{a}”" for a in ev.appeared[:5]))
        if ev.disappeared:
            extra.append("disappeared: " + "; ".join(f"“{d}”" for d in ev.disappeared[:4]))
        lines.append(f"{ev.i}: t={ev.ts:.1f}s {what}{img}" + (f" -> {'; '.join(extra)}" if extra else ""))
    return "\n".join(lines)


def _pick_image_events(rec: Recording, max_images: int = MAX_IMAGES) -> list[int]:
    cands = [ev.i for ev in rec.events if ev.before_image and ev.kind in ("click", "dblclick", "rclick")]
    if len(cands) <= max_images:
        return cands
    step = len(cands) / float(max_images)
    return [cands[int(k * step)] for k in range(max_images)]


def _load_b64(rec_dir: str, name: str, max_side: int = 1000) -> Optional[tuple[str, str]]:
    try:
        from PIL import Image
        p = Path(rec_dir) / name
        img = Image.open(p)
        w, h = img.size
        s = max(w, h) / float(max_side)
        if s > 1.0:
            img = img.resize((int(w / s), int(h / s)), Image.BILINEAR)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=75)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception as e:
        log.debug("image %s: %s", name, e)
        return None


def _backfill(step: dict, rec: Recording) -> dict:
    """Ground the model's step in recorded evidence when it left target/expect weak."""
    evs = {e.i: e for e in rec.events}
    src = [evs[i] for i in step.get("events", []) if i in evs]
    if not src:
        return step
    first = src[0]
    tgt = step.get("target") or {}
    # a ui_text the recorder never saw on screen -> replace with what was actually under the cursor
    if tgt.get("kind") == "ui_text" and first.label and tgt.get("text", "").lower() != first.label.lower():
        seen = {first.label.lower()} | {a.lower() for e in src for a in (e.appeared or [])} | {e.label.lower() for e in src if e.label}
        if tgt["text"].lower() not in seen:
            alts = [a for a in tgt.get("alt") or [] if a.lower() in seen]
            step["target"] = {"kind": "ui_text", "text": first.label[:60], **({"alt": alts} if alts else {}),
                              **({"region": first.region} if first.region in ("top", "left", "right", "bottom") else {})}
    elif tgt.get("kind") == "window_region" and first.label:
        step["target"] = target_from_event(first)
    if not step.get("expect") or all(c.get("kind") == "manual" for c in step["expect"]):
        last = src[-1]
        ev_for_expect = next((e for e in reversed(src) if e.appeared or e.disappeared or _title_changed(e)), last)
        step["expect"] = expect_from_event(ev_for_expect, first.label)
    return step


async def segment(llm, vision, rec: Recording, name: str = "") -> tuple[Optional[dict], str]:
    """Best guide for `rec`: model-written when possible, heuristic otherwise. Returns (workflow, method)."""
    if not rec.events:
        return None, "empty"
    if llm is None or not getattr(llm, "available", False):
        return heuristic(rec, name), "heuristic"
    from ..llm import image_part, text_part
    image_events = _pick_image_events(rec)
    parts = []
    for i in image_events:
        ev = next(e for e in rec.events if e.i == i)
        loaded = _load_b64(rec.dir, ev.before_image) if rec.dir else None
        if loaded:
            parts.append(text_part(f"Screenshot just before event {i} (the pink ring marks the click):"))
            parts.append(image_part(loaded[1], loaded[0]))
    parts.append(text_part(_trace_text(rec, image_events) + "\n\n" + SEGMENT_SCHEMA))
    data = None
    try:
        r = await llm.complete((SEGMENT_SYSTEM, ""), [{"role": "user", "parts": parts}], [])
        m = re.search(r"\{.*\}", r.text, re.S)
        data = json.loads(m.group(0)) if m else None
    except Exception as e:
        log.warning("segmenting with the model failed: %s", e)
    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        steps = [_backfill(s, rec) for s in data["steps"] if isinstance(s, dict)]
        wf = assemble(steps, rec, name=name or str(data.get("name") or ""), done_say=str(data.get("done_say") or ""))
        if wf and len(wf["apps"]["recorded"]["steps"]) >= 1:
            return wf, "model"
        log.info("model segmentation rejected; using heuristic")
    return heuristic(rec, name), "heuristic"
