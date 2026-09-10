"""Dynamic guide planner tests, fully offline: plan validation (targets, expects, clamps, rejects),
JSON extraction with a stub model, and an end-to-end dynamic guide.start() on a synthetic app frame
with auto-advance when the "user" does the step right.
    .venv\\Scripts\\python.exe tests\\test_guide_plan.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ELI_DATA_DIR"] = tempfile.mkdtemp(prefix="eli-plan-")

from PIL import Image  # noqa: E402

from eli.agents.guide_agent import GuideAgent  # noqa: E402
from eli.agents.vision_agent import Frame, OcrLine, VisionAgent, WindowInfo  # noqa: E402
from eli.guides import find_workflow, planner  # noqa: E402
from eli.guides.merge_holes import MERGE_HOLES  # noqa: E402
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


class Broker:
    async def ensure_screen(self, reason):
        return True


class Memory:
    def remember(self, *a, **k):
        return None


PLAN = {
    "app_label": "WordPad",
    "name": "Make text bold",
    "recognize": None,
    "steps": [
        {"id": "select_text", "title": "Select the text", "say": "Drag across the words you want bold.",
         "instruction": "Click before the first word, hold, drag to the last word.",
         "target": {"kind": "window_region", "rel": [0.1, 0.3, 0.9, 0.6]},
         "expect": [{"kind": "vision", "question": "Is any text highlighted (selected) in the document?"}], "timeout": 60},
        {"id": "click_bold", "title": "Click Bold", "say": "Click the Bold button on the Home ribbon.",
         "instruction": "Home ribbon > Bold (or Ctrl+B).",
         "target": {"kind": "ui_text", "text": "Bold", "alt": ["B"], "region": "top", "key": "Ctrl+B"},
         "expect": [{"kind": "ocr_contains", "text": "BOLD ON"}, {"kind": "bogus", "text": "x"}],
         "timeout": 999, "alt": "Press Ctrl+B instead."},
        {"id": "junk", "title": "", "say": "", "instruction": "", "target": {"kind": "nonsense"}, "expect": []},
    ],
    "done_say": "Bold applied. Nice.",
}


class StubPlanLLM:
    """Returns the canned plan for plan requests; YES to 'highlighted' vision questions; boxes JSON otherwise."""
    available = True
    name = "stub"
    async def complete(self, system, turns, tools, on_text=None):
        text = " ".join(p.get("text", "") for p in turns[-1]["parts"] if p["type"] == "text")
        if "Write the walkthrough as JSON" in text:
            return LLMResponse(text="```json\n" + json.dumps(PLAN) + "\n```")
        if "Return ONLY this JSON" in text:
            return LLMResponse(text='{"boxes": []}')
        return LLMResponse(text="YES\nText is highlighted." if "highlighted" in text.lower() else "NO\nNothing changed.")


class FakeWin:
    def __init__(self, title="Document - WordPad", process="wordpad.exe"):
        self.title, self.process = title, process


def make_frame(lines, title="Document - WordPad", process="wordpad.exe"):
    img = Image.new("RGB", (1920, 1080), "white")
    f = Frame(image=img, ts=time.time(), window=WindowInfo(title=title, process=process, app="WordPad", rect=(0, 0, 1920, 1080)))
    f._ocr = [OcrLine(text=t, box=b) for t, b in lines]
    return f


async def main():
    print("[validation]")
    wf = planner.build_workflow(PLAN, FakeWin(), "make this text bold")
    check(wf and wf["dynamic"] and wf["name"] == "Make text bold", "plan builds a workflow")
    steps = wf["apps"]["dynamic"]["steps"]
    check(len(steps) == 2, f"malformed step dropped ({len(steps)} kept)")
    check(steps[1]["timeout"] == 300, f"timeout clamped to {steps[1]['timeout']}")
    check(steps[1]["expect"] == [{"kind": "ocr_contains", "text": "BOLD ON"}], "unknown expect kinds dropped")
    check(steps[1]["target"]["key"] == "Ctrl+B" and steps[1]["target"]["region"] == "top", "ui_text target kept intact")
    check(wf["apps"]["dynamic"]["match"]["process"] == ["wordpad.exe"], "app matched by process")
    check(wf["apps"]["dynamic"]["label"] == "WordPad" and "wordpad" in wf["apps"]["dynamic"]["match"]["title"], "label + title token")
    check(planner.build_workflow({"steps": [PLAN["steps"][0]]}, FakeWin(), "x") is None, "one-step plan rejected")
    check(planner.build_workflow(PLAN, FakeWin(title="", process="")) is None, "no window identity -> rejected")
    check(planner.build_workflow({"steps": []}, FakeWin()) is None, "empty plan (not doable here) rejected")
    bad_rel = dict(PLAN["steps"][0], target={"kind": "window_region", "rel": [0.9, 0.3, 0.1, 0.6]})
    s = planner._clean_step(bad_rel, 0)
    check(s["target"] == {"kind": "window_region", "rel": [0.3, 0.25, 0.7, 0.75]}, "inverted region -> default fallback box")
    check(find_workflow("i want to merge these 5 big holes you see") is MERGE_HOLES, "built-in workflow still wins for merge-holes")

    hub, settings = Hub(), Settings()
    vision = VisionAgent(hub, settings)
    vision.last_model_scale, vision.last_model_size = 1.0, (1920, 1080)
    ribbon = [("Home", (100, 60, 50, 18)), ("Bold", (700, 60, 40, 18))]
    frame = make_frame(ribbon)
    vision.capture_now = lambda: frame

    print("[planner.plan]")
    wf2 = await planner.plan(StubPlanLLM(), vision, frame, "make this text bold", "Home Bold")
    check(wf2 and len(wf2["apps"]["dynamic"]["steps"]) == 2, "plan() extracts fenced JSON into a workflow")

    print("[dynamic guide end-to-end]")
    guide = GuideAgent(hub, settings, vision, StubPlanLLM(), Memory(), Broker())
    guide.loop = asyncio.get_running_loop()
    reply = await guide.start(request="teach me how to make this text bold in wordpad")
    check(guide.active and guide.wf.get("dynamic"), "dynamic guide started")
    check("Step 1" in reply and "Make text bold" in reply, f"start reply announces step 1: {reply[:90]}")
    check(guide.app_label == "WordPad" and len(guide.steps) == 2, "app + steps from the plan")
    shows = hub.guide_events("show")
    check(shows and shows[-1]["step"]["index"] == 0 and shows[-1]["indicators"], "step 1 drawn with indicators")
    # the user does step 1 right (vision says highlighted) -> auto-advance to step 2
    guide.last_check = 0
    guide.last_vision = 0
    guide.on_frame(frame)
    await asyncio.sleep(1.8)
    check(guide.idx == 1 and hub.guide_events("step_done"), f"auto-advanced to step {guide.idx + 1} when done right")
    shows = hub.guide_events("show")
    check(shows[-1]["step"]["index"] == 1, "step 2 drawn")
    box = next((i for i in shows[-1]["indicators"] if i["kind"] == "box"), None)
    check(box and "Bold" in box["label"], f"step 2 points at the Bold button: {box}")
    done = make_frame(ribbon + [("BOLD ON", (400, 400, 90, 18))])
    check(await guide._satisfied(guide.steps[1], done), "ocr_contains completion detected")
    check(not await guide._satisfied(guide.steps[1], frame), "not satisfied before the action")
    r = await guide.control("stop")
    check(not guide.active and "stopped" in r.lower(), "stop")

    print("\nGUIDE PLAN TESTS", "PASS" if not failures else f"FAIL ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
