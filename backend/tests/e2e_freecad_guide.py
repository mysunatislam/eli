"""End-to-end guide test with a real CAD app: launches FreeCAD with the simulated user
(tests/assets/five_holes_demo.py), starts the merge-holes guide through the desktop socket, and records
what Eli sees, says and detects. Screenshots of the overlay are saved to backend/data/e2e_*.png.
Needs: backend + overlay running, FreeCAD 1.x installed, screen permission granted.
    .venv\\Scripts\\python.exe tests\\e2e_freecad_guide.py
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import websockets

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
FREECAD = r"C:\Program Files\FreeCAD 1.1\bin\freecad.exe"
DEMO = HERE / "assets" / "five_holes_demo.py"
URL = "ws://127.0.0.1:8790/ws/desktop"


def screenshot(name):
    import mss
    from PIL import Image
    with mss.mss() as sct:
        s = sct.grab(sct.monitors[1])
        Image.frombytes("RGB", s.size, s.bgra, "raw", "BGRX").save(DATA / f"e2e_{name}.png")
    print(f"   (screenshot e2e_{name}.png)")


async def main() -> int:
    # a private config folder makes FreeCAD start a second instance instead of handing the file to an open one
    cfg = DATA / "freecad-test-config"
    cfg.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([FREECAD, "-u", str(cfg / "user.cfg"), "-s", str(cfg / "system.cfg"), str(DEMO)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("FreeCAD launched, pid", proc.pid)
    t_launch = time.time()
    await asyncio.sleep(18)   # window up, document open, demo timers armed
    events, steps_done, complete = [], [], False
    try:
        async with websockets.connect(URL, max_size=None) as ws:
            await asyncio.sleep(0.4)
            await ws.send(json.dumps({"type": "user_text", "text": "guide me through merging the five holes in FreeCAD but keep the curves between them"}))
            deadline = time.time() + 170
            shots = set()
            while time.time() < deadline:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline - time.time())))
                except asyncio.TimeoutError:
                    break
                t = m.get("type")
                stamp = f"{time.time() - t_launch:5.1f}s"
                if t == "permission_request":
                    await ws.send(json.dumps({"type": "permission", "id": m["id"], "allow": True}))
                elif t == "guide":
                    a = m.get("action")
                    step = m.get("step") or {}
                    n = len(m.get("indicators") or [])
                    if a in ("recognized", "show", "step_done", "complete", "paused", "resumed", "stuck", "hint"):
                        print(f"{stamp} guide {a}: {step.get('title', m.get('text', ''))} ({n} indicators, {m.get('reason', '')})")
                        events.append(a)
                    if a == "step_done":
                        steps_done.append(m.get("index"))
                    if a == "recognized" and "rec" not in shots:
                        shots.add("rec"); await asyncio.sleep(1.5); screenshot("recognized")
                    if a == "show" and step.get("id") in ("edit_sketch", "bridge", "trim", "close") and step["id"] not in shots:
                        shots.add(step["id"]); await asyncio.sleep(2.5); screenshot(step["id"])
                    if a == "complete":
                        complete = True; await asyncio.sleep(1.5); screenshot("complete"); break
                elif t == "transcript" and m["role"] == "eli":
                    print(f"{stamp} [eli] {m['text'][:140]}")
                elif t == "nudge":
                    print(f"{stamp} (nudge: {m['text'][:100]})")
            if not complete:
                await ws.send(json.dumps({"type": "user_text", "text": "stop the guide"}))
                await asyncio.sleep(2)
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
            print("FreeCAD test instance closed")
    print("\nsteps auto-completed:", steps_done, "| complete:", complete)
    ok = complete or len(steps_done) >= 3
    print("E2E", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
