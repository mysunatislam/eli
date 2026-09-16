"""Offline smoke test for the agents (no server, no API key, no microphone needed).
Run:  .venv\\Scripts\\python.exe tests\\smoke_agents.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ELI_DATA_DIR"] = tempfile.mkdtemp(prefix="eli-test-")

from eli import intents  # noqa: E402
from eli.agents.memory_agent import MemoryAgent  # noqa: E402
from eli.agents.vision_agent import VisionAgent, active_window  # noqa: E402
from eli.agents.automation_agent import AutomationAgent, normalize_app  # noqa: E402
from eli.fallback import diagnose  # noqa: E402
from eli import config  # noqa: E402


class FakeHub:
    def __init__(self):
        self.events = []
    def emit(self, e, to=None):
        self.events.append(e)
    def set_state(self, s):
        self.events.append({"type": "state", "state": s})
    def status(self, **kw):
        self.events.append({"type": "status", **kw})
    def tool(self, n, d=""):
        self.events.append({"type": "tool", "name": n})
    def notify(self, *a):
        pass
    def toast(self, t):
        self.events.append({"type": "toast", "text": t})
    def connected(self, k):
        return 0


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        global failures
        failures += 1


failures = 0

print("[intents]")
check(intents.match("Eli, remember that my favorite coding language is Python")[0] == "remember", "remember")
check(intents.match("open chrome")[0] == "open", "open")
check(intents.match("Eli open YouTube") == ("open", ["YouTube"]), "open youtube")
check(intents.match("search youtube for biomedical AI") == ("youtube", ["biomedical AI"]), "youtube search")
check(intents.match("write hello in notepad") == ("type_in", ["hello", "notepad"]), "type_in")
check(intents.match("what do you remember about me?")[0] == "what_remember", "what_remember")
check(intents.match("forget everything")[0] == "forget_all", "forget_all")
check(intents.match("how do I sort a list in python") is None, "free text falls through")
check(intents.wants_screen("what am I looking at?"), "wants_screen")
check(intents.wants_screen("Eli, what is wrong?"), "wants_screen (error)")
check(intents.third_person("my favorite coding language is Python") == "The user's favorite coding language is Python.", "third person: " + intents.third_person("my favorite coding language is Python"))
check(intents.classify_kind("my favorite coding language is Python") == "preference", "classify preference")

print("[memory]")
mem = MemoryAgent(config.DATA_DIR)
m1 = mem.remember("The user's favorite coding language is Python.", kind="preference", importance=0.9)
mem.remember("The user is building a ventilator enclosure in Fusion 360.", kind="fact")
mem.remember("Fixed an off-by-one in build_holes.py by using range(len(holes)).", kind="solution")
r = mem.recall("what language should I write this in?")
check(r and "Python" in r[0].content, f"recall language -> {r[0].content if r else None} ({r[0].score if r else 0})")
r2 = mem.recall("holes loop crash")
check(r2 and "off-by-one" in r2[0].content, f"recall solution -> {r2[0].content if r2 else None}")
check(mem.preferences()[0].content.endswith("Python."), "preferences()")
dup = mem.remember("The user's favorite coding language is Python.", kind="preference")
check(dup.id == m1.id, "dedupe near-identical memory")
raw = mem.db.execute("SELECT content FROM memories LIMIT 1").fetchone()[0]
check(b"Python" not in bytes(raw), "content encrypted at rest")
mem.log_message("user", "hello there")
check(mem.recent_messages(1)[0]["text"] == "hello there", "conversation log round-trip")
n = mem.forget(query="ventilator enclosure")
check(n == 1, f"forget by query removed {n}")
print("  stats", mem.stats())

print("[vision]")
hub = FakeHub()
class S:
    def __init__(self): self.d = {"observe_enabled": False}
    def get(self, k, d=None): return self.d.get(k, d)
    def set(self, k, v): self.d[k] = v
vis = VisionAgent(hub, S())
t0 = time.time()
frame = vis.capture_now()
check(frame.image.size[0] > 100, f"captured {frame.image.size} in {time.time()-t0:.2f}s; active window: {frame.window.describe()!r}")
t0 = time.time()
lines = frame.ocr()
print(f"  OCR: {len(lines)} lines in {time.time()-t0:.2f}s; first: {[l.text for l in lines[:3]]}")
check(isinstance(lines, list), "ocr returns list")
b64, media, scale, size = frame.to_model_image(1568)
check(media == "image/jpeg" and size[0] <= 1568, f"model image {size} scale={scale:.2f} b64={len(b64)//1024}KB")
print("  local description:", vis.describe_locally(frame, 160).replace("\n", " | ")[:200])

print("[automation]")
auto = AutomationAgent(vis)
check(normalize_app("Google Chrome") == "chrome" and normalize_app("VSCode") == "vs code", "app aliases")
check(auto.risk_of("run_command", {"command": "dir"}) == "high", "run_command is high risk")
check(auto.risk_of("press_keys", {"keys": "ctrl+enter"}, "Inbox - Gmail") == "high", "enter in Gmail is high risk")
check(auto.risk_of("press_keys", {"keys": "ctrl+s"}, "main.py - VS Code") == "low", "ctrl+s in VS Code is low risk")
check(auto.risk_of("click_text", {"text": "Send"}) == "high", "clicking 'Send' is high risk")
wins = auto.list_windows()
check(isinstance(wins, list), f"list_windows -> {len(wins)} windows")
check(auto.set_clipboard("eli clipboard test").startswith("Copied") and auto.get_clipboard() == "eli clipboard test", "clipboard round-trip")

print("[fallback diagnosis]")
snippet = 'Traceback (most recent call last):\n  File "build_holes.py", line 42, in <module>\n    place_hole(holes[i])\nIndexError: list index out of range'
d = diagnose(snippet)
check("line 42" in d and "off-by-one" in d.lower() or "range(len" in d, "IndexError diagnosis: " + d[:120])
d2 = diagnose("NameError: name 'x' is not defined")
check("'x'" in d2, "NameError diagnosis")
d3 = diagnose("ModuleNotFoundError: No module named 'requests'")
check("pip install requests" in d3, "ModuleNotFound diagnosis")

print("\nFAILURES:", failures)
sys.exit(1 if failures else 0)
