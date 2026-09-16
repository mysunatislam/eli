"""Drive the running backend over the desktop WebSocket, the way the overlay does.
Run the backend first (python run.py), then:  .venv\\Scripts\\python.exe tests\\ws_client.py [--llm]
--llm adds scenarios that need a configured model (screen questions, file search, project memory).
"""
import asyncio
import json
import sys
import time

import httpx
import websockets

URL = "ws://127.0.0.1:8790/ws/desktop"
AUTO_ALLOW_SCREEN = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main(with_llm: bool) -> int:
    async with httpx.AsyncClient() as http:
        st = (await http.get("http://127.0.0.1:8790/status")).json()
        print("status:", json.dumps({k: st.get(k) for k in ("llm", "provider", "model", "mic", "observe_enabled", "screen_permission", "private_mode", "automation_enabled")}))
        if with_llm and not st.get("llm"):
            print("no LLM configured; dropping --llm scenarios")
            with_llm = False

    async with websockets.connect(URL, max_size=None) as ws:
        async def pump(seconds, until_reply=False):
            end = time.time() + seconds
            replies, live = [], ""
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.time()))
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                t = m.get("type")
                if t == "transcript":
                    print(f"   [{m['role']}] {m['text'][:400]}")
                    if m["role"] == "eli":
                        replies.append(m["text"])
                        if until_reply:
                            end = min(end, time.time() + 1.5)
                elif t == "transcript_delta":
                    live += m.get("text", "")
                elif t == "state":
                    print(f"   (state: {m['state']})")
                elif t == "tool":
                    print(f"   (tool: {m.get('name')} — {m.get('detail')})")
                elif t == "attention":
                    print(f"   (attention -> {m.get('rect')})")
                elif t == "permission_request":
                    print(f"   (permission request) -> {'ALLOW' if AUTO_ALLOW_SCREEN else 'DENY'}")
                    await ws.send(json.dumps({"type": "permission", "id": m["id"], "allow": AUTO_ALLOW_SCREEN}))
                elif t == "confirm_request":
                    print(f"   (confirm request: {m['description']}) -> DECLINE (test)")
                    await ws.send(json.dumps({"type": "confirm", "id": m["id"], "approve": False}))
                elif t == "nudge":
                    print(f"   (nudge: {m['text']})")
                elif t == "toast":
                    print(f"   (toast: {m['text']})")
            if live:
                print(f"   (streamed {len(live)} chars live)")
            return replies

        await pump(1.0)
        scenarios = [
            ("remember that my favorite coding language is Python", 4),
            ("what do you remember about me?", 4),
            ("open notepad", 8),
            ("write hello from Eli in notepad", 10),
            ("private mode on", 3),
            ("what am I looking at?", 5),
            ("private mode off", 3),
        ]
        if with_llm:
            scenarios += [
                ("what am I looking at?", 30),
                ("Eli, what is wrong?", 30),
                ("find files named README", 30),
                ("remember that my CPAP project lives in my Documents folder", 20),
                ("open my CPAP project", 25),
                ("write me a two-line python function that reverses a string", 30),
            ]
        else:
            scenarios += [("what am I looking at?", 8), ("Eli, what is wrong?", 8)]
        scenarios += [("search youtube for biomedical AI", 6)]
        for text, wait in scenarios:
            print(f"\n>> {text}")
            await ws.send(json.dumps({"type": "user_text", "text": text}))
            await pump(wait, until_reply=True)

        print("\n>> set_setting observe_enabled=true")
        await ws.send(json.dumps({"type": "set_setting", "key": "observe_enabled", "value": True}))
        await pump(8)
        print(">> set_setting observe_enabled=false")
        await ws.send(json.dumps({"type": "set_setting", "key": "observe_enabled", "value": False}))
        await pump(2)
        print(">> forget_all")
        await ws.send(json.dumps({"type": "forget_all"}))
        await pump(2)
    async with httpx.AsyncClient() as http:
        m = (await http.get("http://127.0.0.1:8790/metrics")).json()
        print("\nmetrics:", json.dumps({k: m[k] for k in ("backend_cpu_percent", "backend_rss_mb", "vision", "state")}))
        print("llm:", json.dumps(m["llm"]))
    print("\nclient done")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main("--llm" in sys.argv)))
    except (ConnectionRefusedError, OSError) as e:
        print("backend not reachable:", e)
        sys.exit(1)
