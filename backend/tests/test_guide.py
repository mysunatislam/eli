"""Guide engine tests without a CAD app: workflow schema, target resolution on synthetic frames,
completion checks and auto-advance, region fallbacks, and vision-box parsing with a stub model.
    .venv\\Scripts\\python.exe tests\\test_guide.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ELI_DATA_DIR"] = tempfile.mkdtemp(prefix="eli-guide-")

from PIL import Image  # noqa: E402

from eli.agents.guide_agent import GuideAgent  # noqa: E402
from eli.agents.vision_agent import Frame, OcrLine, VisionAgent, WindowInfo  # noqa: E402
from eli.guides import WORKFLOWS, find_workflow  # noqa: E402
from eli.llm import LLMResponse  # noqa: E402

failures = 0


def check(cond, msg):
    global failures
    print(("  ok   " if cond else "  FAIL ") + msg)
    failures += (not cond)


class Hub:
    def __init__(self):
        self.events = []
        self.state = "idle"
    def emit(self, e, to=None):
        self.events.append(e)
    def set_state(self, s):
        self.state = s
    def status(self, **kw):
        self.events.append({"type": "status", **kw})
    def transcript(self, role, text, source="desktop"):
        self.events.append({"type": "transcript", "role": role, "text": text})
    def notify(self, *a):
        pass
    def toast(self, t):
        self.events.append({"type": "toast", "text": t})
    def guide_events(self, action):
        return [e for e in self.events if e.get("type") == "guide" and e.get("action") == action]


class Settings:
    def __init__(self):
        self.d = {"observe_enabled": False, "screen_permission": "granted", "voice_replies": False, "private_mode": False}
    def get(self, k, d=None):
        return self.d.get(k, d)
    def set(self, k, v):
        self.d[k] = v


class StubLLM:
    """Answers YES to vision questions containing 'dashed', returns two boxes for detection."""
    available = True
    name = "stub"
    async def complete(self, system, turns, tools, on_text=None):
        text = " ".join(p.get("text", "") for p in turns[-1]["parts"] if p["type"] == "text")
        if "Return ONLY this JSON" in text:
            return LLMResponse(text='```json\n{"boxes": [{"label": "hole 1", "box_2d": [100, 100, 200, 200]}, {"label": "hole 2", "box_2d": [100, 300, 200, 400]}]}\n```')
        return LLMResponse(text="YES\nDashed arcs are visible." if "dashed" in text.lower() else "NO\nNothing changed.")


class Broker:
    async def ensure_screen(self, reason):
        return True


class Memory:
    def remember(self, *a, **k):
        return None


def make_frame(vision, lines, title="Autodesk Fusion 360", process="Fusion360.exe"):
    img = Image.new("RGB", (1920, 1080), "white")
    f = Frame(image=img, ts=time.time(), window=WindowInfo(title=title, process=process, app="Fusion 360", rect=(0, 0, 1920, 1080)))
    f._ocr = [OcrLine(text=t, box=b) for t, b in lines]
    return f


async def main():
    print("[workflows]")
    wf = WORKFLOWS["merge_holes"]
    for app, spec in wf["apps"].items():
        for s in spec["steps"]:
            ok = all(k in s for k in ("id", "title", "say", "instruction", "target", "expect")) and s["target"].get("kind") in ("ui_text", "geometry", "key", "window_region", "hole_points")
            if not ok:
                check(False, f"{app}/{s.get('id')} malformed")
        check(len(spec["steps"]) >= 4, f"{app}: {len(spec['steps'])} steps")
    check(find_workflow("guide me through merging the five holes but keep the curves") is wf, "keyword match")
    check(find_workflow("make me a sandwich") is None, "no false match")

    hub, settings = Hub(), Settings()
    vision = VisionAgent(hub, settings)
    vision.last_model_scale, vision.last_model_size = 1.0, (1920, 1080)
    guide = GuideAgent(hub, settings, vision, StubLLM(), Memory(), Broker())
    guide.loop = asyncio.get_running_loop()

    print("[targets]")
    frame = make_frame(vision, [("SOLID", (300, 60, 60, 18)), ("MODIFY", (700, 60, 80, 18)), ("Sketches", (40, 300, 90, 16)), ("FINISH SKETCH", (1500, 60, 150, 18))])
    vision.capture_now = lambda: frame
    guide.wf, guide.app_key, guide.steps, guide.active = wf, "fusion360", list(wf["apps"]["fusion360"]["steps"]), True
    guide.shown = True
    inds = await guide.resolve_target(guide.steps[0], frame)   # ui_text "Sketches" in the left region
    box = next((i for i in inds if i["kind"] == "box"), None)
    check(box and abs(box["x"] - 34) < 10 and "Sketches" in box["label"], f"ui_text located: {box}")
    check(any(i["kind"] == "arrow" for i in inds) and any(i["kind"] == "ring" for i in inds), "arrow + ring emitted")
    inds = await guide.resolve_target(guide.steps[2], frame)   # MODIFY + key T
    check(any(i["kind"] == "key" and i["keys"] == "T" for i in inds), "key badge for T")
    inds = await guide.resolve_target(guide.steps[1], frame)   # geometry via stub model boxes
    boxes = [i for i in inds if i["kind"] == "box"]
    check(len(boxes) == 2 and boxes[0]["x"] == 192 and boxes[0]["w"] == 192, f"vision boxes converted to screen px: {boxes[0] if boxes else None}")
    frame2 = make_frame(vision, [("nothing", (10, 10, 40, 10))])
    inds = await guide.resolve_target(guide.steps[4], frame2)  # FINISH SKETCH missing -> region fallback (top)
    reg = next((i for i in inds if i["kind"] == "region"), None)
    check(reg and reg["y"] < 200 and "FINISH SKETCH" in reg["label"], f"region fallback when text not found: {reg}")

    print("[checks + auto-advance]")
    guide.idx, guide.step_started = 0, time.time()
    before = make_frame(vision, [("SOLID", (300, 60, 60, 18))])
    check(not await guide._satisfied(guide.steps[0], before), "edit_sketch not satisfied without FINISH SKETCH")
    after = make_frame(vision, [("SKETCH", (300, 60, 60, 18)), ("FINISH SKETCH", (1500, 60, 150, 18))])
    check(await guide._satisfied(guide.steps[0], after), "edit_sketch satisfied when FINISH SKETCH appears")
    guide.last_check = 0
    guide.on_frame(after)
    await asyncio.sleep(1.8)   # advance() pauses 0.9 s for the check-mark animation before the next step
    check(guide.idx == 1 and hub.guide_events("step_done"), f"auto-advanced to step {guide.idx + 1}")
    shows = hub.guide_events("show")
    check(shows and shows[-1]["step"]["index"] == 1 and shows[-1]["indicators"], "step 2 shown with indicators")
    guide.last_vision = 0
    check(await guide._satisfied(guide.steps[1], after), "vision yes/no satisfied via model")
    guide.step_started = time.time() - 999
    guide.last_check = 0
    guide.idx = 2
    guide.on_frame(before)
    await asyncio.sleep(1.0)
    check(hub.guide_events("stuck") and any(e.get("type") == "nudge" for e in hub.events), "stuck detection offers help")
    away = make_frame(vision, [], title="Google Chrome", process="chrome.exe")
    for _ in range(GuideAgent.AWAY_FRAMES + 1):
        guide.last_check = 0
        guide.on_frame(away)
        await asyncio.sleep(0.15)
    check(guide.paused and hub.guide_events("paused"), "pauses when the user leaves the app")
    guide.last_check = 0
    guide.on_frame(before)
    await asyncio.sleep(0.5)
    check(not guide.paused and hub.guide_events("resumed"), "resumes when back")
    r = await guide.control("stop")
    check(not guide.active and hub.guide_events("clear") and "stopped" in r.lower(), "stop clears the overlay")

    print("\nGUIDE TESTS", "PASS" if not failures else f"FAIL ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
