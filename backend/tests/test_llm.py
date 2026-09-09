"""Live check of the LLM layer with whatever provider .env configures (Gemini or Anthropic).
Sends one screenshot of the current screen to the model (that is what Eli does when you ask about it).
Run:  .venv\\Scripts\\python.exe tests\\test_llm.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from eli.llm import LLM, image_part, text_part, tool_result_part  # noqa: E402

TOOLS = [{
    "name": "open_app",
    "description": "Launch an application by name.",
    "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
}, {
    "name": "get_active_window",
    "description": "Return the title of the foreground window.",
    "input_schema": {"type": "object", "properties": {}},
}]
SYSTEM = ("You are Eli, a terse desktop assistant. Use tools to act. Never narrate tool calls.", "Now: test run")


async def main() -> int:
    llm = LLM()
    print("provider:", llm.status())
    if not llm.available:
        return 2

    t0 = time.time()
    deltas = []
    r = await llm.complete(SYSTEM, [{"role": "user", "parts": [text_part("Say hello in exactly five words.")]}], [], on_text=deltas.append)
    print(f"[chat] {time.time()-t0:.1f}s stop={r.stop} deltas={len(deltas)} usage={r.usage}\n   -> {r.text!r}")

    turns = [{"role": "user", "parts": [text_part("Open notepad for me, then tell me it's done.")]}]
    t0 = time.time()
    r = await llm.complete(SYSTEM, turns, TOOLS)
    print(f"[tool] {time.time()-t0:.1f}s stop={r.stop} calls={[(c.name, c.args) for c in r.tool_calls]} text={r.text!r}")
    if r.tool_calls:
        turns.append({"role": "assistant", "parts": ([text_part(r.text)] if r.text else []) +
                      [{"type": "tool_call", "id": c.id, "name": c.name, "args": c.args} for c in r.tool_calls],
                      "raw": r.raw, "raw_provider": llm.name})
        turns.append({"role": "user", "parts": [tool_result_part(c, "Opened notepad.") for c in r.tool_calls]})
        t0 = time.time()
        r2 = await llm.complete(SYSTEM, turns, TOOLS)
        print(f"[tool-followup] {time.time()-t0:.1f}s stop={r2.stop} text={r2.text!r}")

    from eli.agents.vision_agent import Frame, active_window  # noqa: E402
    import mss  # noqa: E402
    from PIL import Image  # noqa: E402
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    frame = Frame(image=img, ts=time.time(), window=active_window())
    b64, media, scale, size = frame.to_model_image(1280)
    t0 = time.time()
    r = await llm.complete(SYSTEM, [{"role": "user", "parts": [image_part(media, b64),
                                     text_part("In one sentence: which application is in front and what is the user doing?")]}], [])
    print(f"[vision] {time.time()-t0:.1f}s ({size[0]}x{size[1]}) -> {r.text!r}")

    # screenshot returned inside a tool result (look_at_screen style)
    turns = [{"role": "user", "parts": [text_part("What app is on my screen right now? Use get_active_window.")]}]
    r = await llm.complete(SYSTEM, turns, TOOLS)
    if r.tool_calls:
        turns.append({"role": "assistant", "parts": [{"type": "tool_call", "id": c.id, "name": c.name, "args": c.args} for c in r.tool_calls],
                      "raw": r.raw, "raw_provider": llm.name})
        turns.append({"role": "user", "parts": [tool_result_part(r.tool_calls[0], [text_part("Active window: " + frame.window.describe()), image_part(media, b64)])]})
        t0 = time.time()
        r3 = await llm.complete(SYSTEM, turns, TOOLS)
        print(f"[tool+image] {time.time()-t0:.1f}s stop={r3.stop} -> {r3.text!r}")
    print("usage totals:", llm.usage)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
