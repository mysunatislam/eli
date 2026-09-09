"""Protocol test for confirmations: voice approval, trust mode auto-approval, and the destructive-command
guard. Needs the backend running with an LLM configured.
    .venv\\Scripts\\python.exe tests\\test_trust.py
"""
import asyncio
import json
import sys
import time

import websockets

URL = "ws://127.0.0.1:8790/ws/desktop"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main() -> int:
    ok = True
    async with websockets.connect(URL, max_size=None) as ws:
        async def pump(seconds, auto_confirm=None):
            end = time.time() + seconds
            seen = {"confirm": [], "eli": [], "toast": []}
            while time.time() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.time())))
                except asyncio.TimeoutError:
                    break
                t = m.get("type")
                if t == "confirm_request":
                    seen["confirm"].append(m["description"])
                    print(f"   (confirm request: {m['description']})")
                    if auto_confirm is not None:
                        await ws.send(json.dumps({"type": "confirm", "id": m["id"], "approve": auto_confirm}))
                elif t == "transcript" and m["role"] == "eli":
                    seen["eli"].append(m["text"])
                    print(f"   [eli] {m['text'][:200]}")
                elif t == "toast":
                    seen["toast"].append(m["text"])
                    print(f"   (toast: {m['text']})")
                elif t == "tool":
                    print(f"   (tool: {m.get('detail')})")
            return seen

        async def say(text, wait, auto_confirm=None):
            print(f"\n>> {text}")
            await ws.send(json.dumps({"type": "user_text", "text": text}))
            return await pump(wait, auto_confirm)

        await pump(1)
        await say("trust mode off", 3)

        # 1. risky action without trust -> confirm request -> approve by voice
        s = await say("run the command: echo hello-from-eli", 25)
        if not s["confirm"]:
            print("FAIL: expected a confirmation request"); ok = False
        s2 = await say("approve", 25)
        if not any("hello-from-eli" in r for r in s2["eli"] + s["eli"]):
            print("FAIL: approved command output not reported"); ok = False

        # 2. trust mode: same action runs without asking
        await say("trust mode on", 3)
        s = await say("run the command: echo trusted-run", 25)
        if s["confirm"]:
            print("FAIL: trust mode should not ask"); ok = False
        if not any("Auto-approved" in t for t in s["toast"]):
            print("FAIL: expected an Auto-approved toast"); ok = False

        # 3. destructive command still asks even in trust mode (declined by the test). The model may also
        #    refuse outright; what must never happen is an auto-approved run.
        s = await say("run the command: rmdir /s /q C:\\eli-test-folder-that-does-not-exist", 25, auto_confirm=False)
        if any("Auto-approved" in t for t in s["toast"]):
            print("FAIL: destructive command was auto-approved"); ok = False
        elif s["confirm"]:
            print("   guard asked for confirmation (correct)")
        else:
            print("   model refused without calling the tool (also acceptable)")
        await say("trust mode off", 3)

    # 4. the guard itself, independent of the model
    from eli.agents.main_agent import DANGEROUS_CMD
    for cmd, dangerous in [("rmdir /s /q C:\\x", True), ("rm -rf ./build", True), ("format D:", True), ("git push --force", True),
                           ("shutdown /s", True), ("echo hi", False), ("pytest -q", False), ("dir", False), ("git status", False)]:
        if bool(DANGEROUS_CMD.search(cmd)) != dangerous:
            print(f"FAIL: guard misclassified {cmd!r}"); ok = False
    print("   guard regex checks done")
    print("\nTRUST TESTS", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
