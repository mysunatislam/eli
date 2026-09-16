"""Teach-me mode: trace -> guide, library, lookup, intents. Pure Python; runs on any OS (no hooks, no LLM).

    cd backend && python -m pytest tests/test_teach.py -q
"""
from __future__ import annotations

import json
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# eli.guides.planner imports eli.llm, which pulls provider SDKs; stub the two helpers we need.
if "eli.llm" not in sys.modules:
    stub = types.ModuleType("eli.llm")
    stub.image_part = lambda media, b64: {"type": "image", "media_type": media, "data": b64}
    stub.text_part = lambda text: {"type": "text", "text": text}
    sys.modules["eli.llm"] = stub

from eli.guides import WORKFLOWS, BUILTIN_IDS, find_workflow, register, unregister  # noqa: E402
from eli.guides.recorder import Recording, TraceEvent, label_under, region_of, ocr_delta, chord, vk_name  # noqa: E402
from eli.guides import segmenter  # noqa: E402
from eli.guides.library import GuideLibrary  # noqa: E402


@dataclass
class Line:
    text: str
    box: tuple


WIN = {"title": "Part1 - FreeCAD 1.0", "process": "freecad.exe", "app": "FreeCAD", "pid": 1, "rect": [0, 0, 1600, 900]}


def _rec() -> Recording:
    """A realistic 4-action demonstration: open Sketch tab, press Ctrl+T (Trim), click Close, type a name + Enter."""
    r = Recording(id="20260916-101500", name="close the sketch", goal="close an edited sketch and name the part", app=WIN,
                  started=1000.0, ended=1030.0)
    r.events = [
        TraceEvent(i=0, kind="click", ts=1.2, x=120, y=60, label="Sketch", label_box=[100, 50, 60, 20], region="top", rel=[0.075, 0.066],
                   window_before=WIN, window_after=WIN, appeared=["Close", "Constrain", "Trim edge"], disappeared=["Part Design"]),
        TraceEvent(i=1, kind="shortcut", ts=4.0, keys="Ctrl+T", window_before=WIN, window_after=WIN, appeared=["Trim edge active"]),
        TraceEvent(i=2, kind="scroll", ts=5.0, keys="down", window_before=WIN, window_after=WIN),
        TraceEvent(i=3, kind="click", ts=9.5, x=1500, y=80, label="Close", label_box=[1480, 70, 50, 20], region="right", rel=[0.94, 0.09],
                   window_before=WIN, window_after={**WIN, "title": "Part1* - FreeCAD 1.0"}, appeared=["Part Design"], disappeared=["Close", "Constrain"]),
        TraceEvent(i=4, kind="click", ts=12.0, x=300, y=400, label="Label", label_box=[280, 390, 40, 18], region="center", rel=[0.19, 0.44],
                   window_before=WIN, window_after=WIN),
        TraceEvent(i=5, kind="type", ts=12.5, chars=7, text="", window_before=WIN),
        TraceEvent(i=6, kind="key", ts=14.0, keys="Enter", window_before=WIN, window_after=WIN, appeared=["Bracket"]),
    ]
    return r


# --- enrichment helpers ------------------------------------------------------------------------------------

def test_label_under_prefers_containing_box_then_nearest():
    lines = [Line("FINISH SKETCH", (100, 10, 120, 20)), Line("MODIFY", (300, 10, 60, 20)), Line("far", (900, 800, 30, 10))]
    assert label_under((150, 20), lines) == ("FINISH SKETCH", [100, 10, 120, 20])
    assert label_under((330, 45), lines)[0] == "MODIFY"          # 15 px below the box
    assert label_under((600, 400), lines) == ("", [])            # nothing within 40 px
    assert label_under((150, 20), lines, window_rect=(0, 500, 1000, 900)) == ("", [])  # outside the window rect


def test_region_of_and_fractions():
    assert region_of((50, 20), (0, 0, 1000, 500))[0] == "top"
    assert region_of((950, 250), (0, 0, 1000, 500))[0] == "right"
    reg, rel = region_of((500, 250), (0, 0, 1000, 500))
    assert reg == "center" and rel == [0.5, 0.5]
    assert region_of((5000, 5000), (0, 0, 1000, 500))[1] == [1.0, 1.0]


def test_ocr_delta_ignores_fragments_and_numbers():
    before = [Line("Part Design", (0, 0, 1, 1)), Line("12.5", (0, 0, 1, 1)), Line("ok", (0, 0, 1, 1))]
    after = [Line("Sketcher", (0, 0, 1, 1)), Line("Trim edge", (0, 0, 1, 1)), Line("99", (0, 0, 1, 1)), Line("ok", (0, 0, 1, 1))]
    appeared, disappeared = ocr_delta(before, after)
    assert appeared == ["Trim edge", "Sketcher"]
    assert disappeared == ["Part Design"]


def test_key_names_and_chords():
    assert vk_name(0x45) == "E" and vk_name(0x0D) == "Enter" and vk_name(0x70) == "F1" and vk_name(0x62) == "Num2"
    assert chord({"Ctrl", "Shift"}, 0x45) == "Ctrl+Shift+E"
    assert chord({"Win"}, 0x44) == "Win+D"


def test_trace_round_trip(tmp_path):
    r = _rec()
    d = r.save(tmp_path)
    loaded = Recording.load(d)
    assert loaded.id == r.id and loaded.goal == r.goal and len(loaded.events) == 7
    assert loaded.events[3].label == "Close" and loaded.events[3].disappeared == ["Close", "Constrain"]
    assert loaded.duration == 30.0


# --- heuristic segmenter ------------------------------------------------------------------------------------

def test_heuristic_segments_actions_into_verifiable_steps():
    wf = segmenter.heuristic(_rec())
    assert wf and wf["recorded"] and wf["id"].startswith("rec_close_the_sketch_")
    app = wf["apps"]["recorded"]
    assert app["label"] == "FreeCAD" and app["match"]["process"] == ["freecad.exe"] and app["match"]["title"] == ["freecad"]
    steps = app["steps"]
    # scroll dropped; click+type+Enter folded into one step -> 4 steps from 7 events
    assert [s["title"] for s in steps] == ["Click Sketch", "Press Ctrl+T", "Click Close", "Click Label and type"]
    assert steps[0]["target"] == {"kind": "ui_text", "text": "Sketch", "region": "top"}
    assert steps[0]["expect"][0] == {"kind": "ocr_contains", "text": "Constrain"}   # longest appeared line that isn't the label
    assert steps[1]["target"] == {"kind": "key", "keys": "Ctrl+T"}
    assert {"kind": "title_contains", "text": "Part1*"} in steps[2]["expect"]
    assert steps[3]["events"] == [4, 5, 6]
    assert steps[3]["expect"][0] == {"kind": "ocr_contains", "text": "Bracket"}     # from the Enter that ended the typing
    assert "Press Enter" in steps[3]["instruction"] and "7 characters" in steps[3]["instruction"]
    assert all(30 <= s["timeout"] <= 300 for s in steps)
    assert wf["keywords"] and "close the sketch" in wf["keywords"][0]


def test_heuristic_never_stores_typed_text_unless_recorded():
    r = _rec()
    wf = segmenter.heuristic(r)
    assert "characters were entered" in wf["apps"]["recorded"]["steps"][3]["instruction"]
    r.events[5].text = "Bracket"   # author enabled teach_capture_text
    wf2 = segmenter.heuristic(r)
    assert "type “Bracket”" in wf2["apps"]["recorded"]["steps"][3]["instruction"]


def test_heuristic_click_without_label_uses_window_region_around_click():
    r = _rec()
    r.events = [TraceEvent(i=0, kind="click", ts=1.0, x=800, y=450, rel=[0.5, 0.5], region="center", window_before=WIN, window_after=WIN)]
    wf = segmenter.heuristic(r)
    t = wf["apps"]["recorded"]["steps"][0]["target"]
    assert t["kind"] == "window_region" and t["rel"] == [0.44, 0.45, 0.56, 0.55]
    assert wf["apps"]["recorded"]["steps"][0]["expect"] == [{"kind": "manual"}]


def test_empty_recording_yields_no_guide():
    r = _rec()
    r.events = []
    assert segmenter.heuristic(r) is None


def test_backfill_replaces_hallucinated_label_and_missing_expect():
    r = _rec()
    step = {"events": [3], "title": "Finish", "say": "Finish the sketch", "instruction": "Click Finish",
            "target": {"kind": "ui_text", "text": "Finish Sketch"}, "expect": [{"kind": "manual"}]}
    out = segmenter._backfill(step, r)
    assert out["target"]["text"] == "Close"                     # what was really under the cursor
    assert out["expect"][0]["kind"] in ("ocr_contains", "title_contains")


@pytest.mark.asyncio
async def test_segment_falls_back_to_heuristic_without_model():
    wf, method = await segmenter.segment(None, None, _rec())
    assert method == "heuristic" and wf is not None


@pytest.mark.asyncio
async def test_segment_uses_model_output_and_validates_it():
    class R:
        text = json.dumps({"name": "Close sketch quickly", "done_say": "Done.", "steps": [
            {"id": "open", "events": [0, 1], "title": "Open the sketch tools", "say": "Open the Sketch tab and pick Trim.",
             "instruction": "Click Sketch, then press Ctrl+T", "target": {"kind": "ui_text", "text": "Sketch", "region": "top"},
             "expect": [{"kind": "ocr_contains", "text": "Trim edge"}]},
            {"id": "close", "events": [3], "title": "Close the sketch", "say": "Close it.", "instruction": "Click Close",
             "target": {"kind": "ui_text", "text": "Close", "region": "right"}, "expect": []},
            {"id": "bogus", "events": [9], "title": "", "say": "", "instruction": ""},   # invalid -> dropped
        ]})

    class LLM:
        available = True

        async def complete(self, system, turns, tools):
            assert "Trace (event number" in turns[0]["parts"][-1]["text"]
            return R()

    wf, method = await segmenter.segment(LLM(), None, _rec())
    assert method == "model" and wf["name"] == "Close sketch quickly"
    steps = wf["apps"]["recorded"]["steps"]
    assert len(steps) == 2 and steps[0]["events"] == [0, 1]
    assert steps[1]["expect"] and steps[1]["expect"][0]["kind"] != "manual"   # back-filled from the recording


# --- library, registration, lookup ------------------------------------------------------------------------

def test_library_add_find_export_import_delete(tmp_path):
    lib = GuideLibrary(tmp_path)
    wf = segmenter.heuristic(_rec())
    lib.add(wf)
    assert wf["id"] in WORKFLOWS and (tmp_path / "guides" / f"{wf['id']}.json").exists()
    assert find_workflow("guide me through close the sketch")["id"] == wf["id"]
    assert find_workflow("walk me through how to close the sketch and name the part")["id"] == wf["id"]
    assert find_workflow("guide me through merging the holes")["id"] == "merge_holes"   # built-ins still win
    assert lib.find("can you close the sketch for me")["id"] == wf["id"]
    assert lib.find("play some music") is None

    out = lib.export(wf["id"])
    data = json.loads(out.read_text("utf-8"))
    assert data["format"] == "eliguide" and "recording_id" not in data["guide"]
    assert all("events" not in s for s in data["guide"]["apps"]["recorded"]["steps"])

    lib2 = GuideLibrary(tmp_path / "other")
    imported = lib2.import_file(str(out))
    assert imported["id"] == wf["id"] and imported["imported"]
    assert lib2.list()[0]["name"] == "close the sketch"

    assert lib.delete(wf["id"]) and wf["id"] not in lib.guides and wf["id"] not in WORKFLOWS
    lib2.delete(imported["id"])
    assert "merge_holes" in WORKFLOWS and BUILTIN_IDS == {"merge_holes"}


def test_library_reloads_from_disk(tmp_path):
    lib = GuideLibrary(tmp_path)
    wf = segmenter.heuristic(_rec())
    lib.add(wf)
    fresh = GuideLibrary(tmp_path)
    assert fresh.load() == 1 and wf["id"] in fresh.guides
    fresh.delete(wf["id"])


def test_library_rejects_garbage(tmp_path):
    lib = GuideLibrary(tmp_path)
    with pytest.raises(ValueError):
        lib.add({"id": "x", "apps": {}})
    (tmp_path / "guides" / "junk.json").write_text("{not json", "utf-8")
    assert lib.load() == 0


def test_unregister_never_removes_builtins():
    unregister("merge_holes")
    assert "merge_holes" in WORKFLOWS


# --- intents ----------------------------------------------------------------------------------------------

def _teach_intent():
    src = (ROOT / "eli" / "intents.py").read_text("utf-8")
    ns = {"re": re}
    exec(src[src.index("TEACH_START_RE"):src.index("def match(text: str):")], ns)
    return ns["extract_teach_request"]


@pytest.mark.parametrize("text,expected", [
    ("Eli, let me teach you how to export a STEP file", ("teach_start", ["export a STEP file"])),
    ("watch me merge these holes", ("teach_start", ["merge these holes"])),
    ("teach you to add a chamfer", ("teach_start", ["add a chamfer"])),
    ("record this", ("teach_start", [""])),
    ("stop recording", ("teach_stop", [])),
    ("I'm done with the demo", ("teach_stop", [])),
    ("cancel the recording", ("teach_cancel", [])),
    ("what guides do you have?", ("teach_list", [])),
    ("review that guide", ("teach_review", [""])),
    ("export that guide called export step", ("teach_export", ["export step"])),
    ("stop", None),
    ("show me the screen", None),
    ("open notepad", None),
])
def test_teach_intents(text, expected):
    assert _teach_intent()(text) == expected
