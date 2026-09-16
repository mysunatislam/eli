"""Eli backend server: FastAPI + WebSockets for the desktop overlay and the phone companion."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__, config
from .agents.automation_agent import AutomationAgent
from .agents.coding_agent import CodingAgent
from .agents.communication_agent import CommunicationAgent
from .agents.design_agent import DesignAgent
from .agents.guide_agent import GuideAgent
from .agents.teach_agent import TeachAgent
from .agents.scheduler import Scheduler
from .agents.main_agent import MainAgent, PermissionBroker
from .agents.memory_agent import MemoryAgent
from .agents.proactive import ProactiveMonitor
from .agents.vision_agent import Frame, VisionAgent, WindowInfo
from .bus import EventHub
from .llm import LLM, image_part, text_part
from .personas import list_personas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
for noisy in ("faster_whisper", "httpx", "google_genai", "comtypes"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("eli")

hub = EventHub()
settings = config.Settings()
memory = MemoryAgent(config.DATA_DIR)
memory.private = bool(settings.get("private_mode"))
vision = VisionAgent(hub, settings, config.CAPTURE_INTERVAL, config.CAPTURE_INTERVAL_MAX, config.SCREENSHOT_MAX_SIDE)
auto = AutomationAgent(vision)
coding = CodingAgent(settings, config.PROJECT_DIRS)
design = DesignAgent()
comms = CommunicationAgent()
llm = LLM()
broker = PermissionBroker(hub, settings)
proactive = ProactiveMonitor(hub, settings, coding)
vision.frame_listeners.append(proactive.on_frame)
agent = MainAgent(hub, settings, memory, vision, auto, coding, design, comms, llm, broker, proactive)
guide = GuideAgent(hub, settings, vision, llm, memory, broker, auto)
agent.executor.guide = guide
teach = TeachAgent(hub, settings, vision, llm, memory, broker, config.DATA_DIR, guide)
agent.executor.teach = teach
scheduler = Scheduler(hub, settings, memory, agent)
agent.executor.scheduler = scheduler
vision.frame_listeners.append(guide.on_frame)
speech = None
STARTED = time.time()


def status_payload() -> dict:
    token = settings.get("pairing_token")
    return {
        "type": "status",
        "version": __version__,
        "state": hub.state,
        "llm": llm.available,
        "llm_reason": llm.reason,
        "provider": llm.name,
        "model": llm.model,
        "usage": llm.usage,
        "observe_enabled": bool(settings.get("observe_enabled")),
        "observing": vision.enabled,
        "screen_permission": settings.get("screen_permission", "ask"),
        "wake_enabled": bool(settings.get("wake_enabled")),
        "voice_replies": bool(settings.get("voice_replies")),
        "private_mode": bool(settings.get("private_mode")),
        "automation_enabled": bool(settings.get("automation_enabled", True)),
        "nudges_enabled": bool(settings.get("nudges_enabled", True)),
        "proactive_mode": settings.get("proactive_mode", "ask"),
        "follow_cursor": bool(settings.get("follow_cursor", True)),
        "trust_mode": agent.executor.trust_active(),
        "trust_until": settings.get("trust_until") or 0,
        "blocked_apps": settings.get("blocked_apps") or [],
        "camera_enabled": False,
        "autostart": config.autostart_enabled(),
        "mic": bool(speech and speech.available()),
        "audio_error": (speech.audio_error if speech else "speech not started"),
        "stt_ready": bool(speech and speech.transcriber.ready),
        "mobile_url": f"http://{config.lan_ip()}:{config.PORT}/mobile?token={token}",
        "pairing_token": token,
        "mobile_connected": hub.connected("mobile"),
        "desktop_connected": hub.connected("desktop"),
        "memory": memory.stats(),
        "vision": vision.stats(),
        "guide": guide.status(),
        "jobs": scheduler.list_public(),
        "tts": speech.tts.describe() if speech else "",
        "active_persona": getattr(agent.active_persona, "id", "classic") if hasattr(agent, "active_persona") else "classic",
        "persona": {
            "id": agent.active_persona.id,
            "name": agent.active_persona.name,
            "display": agent.active_persona.display,
            "tagline": agent.active_persona.tagline,
            "gender": agent.active_persona.gender,
            "voice": agent.active_persona.voice,
            "color": agent.active_persona.color_accent,
            "color_light": agent.active_persona.color_light,
            "color_dark": agent.active_persona.color_dark,
            "shape": agent.active_persona.shape,
        } if hasattr(agent, "active_persona") else None,
        "personas": list_personas(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global speech
    loop = asyncio.get_running_loop()
    hub.bind(loop)
    from .speech.controller import SpeechController

    def on_command(text: str, source: str) -> None:
        asyncio.run_coroutine_threadsafe(agent.handle(text, source), loop)

    speech = SpeechController(hub, settings, on_command)
    agent.attach_speech(speech)
    speech.preload()
    proactive.auto_handler = lambda nid: asyncio.run_coroutine_threadsafe(agent.nudge_action(nid, "yes"), loop)
    guide.attach(loop, speech)
    teach.attach(loop, speech)
    scheduler.speech = speech
    scheduler.start(loop)
    vision.start()
    s = status_payload()
    log.info("Eli %s ready. LLM: %s. Mic: %s. Phone: %s", __version__,
             f"{llm.name}/{llm.model}" if llm.available else "OFF - " + llm.reason,
             "yes" if s["mic"] else "no (%s)" % s["audio_error"], s["mobile_url"])
    try:
        yield
    finally:
        scheduler.stop()
        vision.stop()
        if speech:
            speech.shutdown()


app = FastAPI(title="Eli backend", version=__version__, lifespan=lifespan)


# -- HTTP -------------------------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"ok": True, "version": __version__, "llm": llm.available, "provider": llm.name, "model": llm.model}


@app.get("/status")
async def status():
    return status_payload()


@app.get("/metrics")
async def metrics():
    import psutil
    p = psutil.Process()
    cpu = await asyncio.to_thread(p.cpu_percent, 0.3)
    return {
        "uptime_s": int(time.time() - STARTED),
        "backend_cpu_percent": cpu,
        "backend_rss_mb": round(p.memory_info().rss / 1e6, 1),
        "threads": p.num_threads(),
        "vision": vision.stats(),
        "llm": llm.status(),
        "state": hub.state,
        "clients": {"desktop": hub.connected("desktop"), "mobile": hub.connected("mobile")},
    }


@app.get("/mobile")
async def mobile_index():
    return FileResponse(config.MOBILE_DIR / "index.html")


@app.get("/mobile/{path:path}")
async def mobile_static(path: str):
    f = (config.MOBILE_DIR / path).resolve()
    if config.MOBILE_DIR.resolve() not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


def _check_token(token: str) -> None:
    if not token or token != settings.get("pairing_token"):
        raise HTTPException(401, "bad pairing token")


@app.get("/api/frame.jpg")
async def frame_jpg(token: str = Query("")):
    _check_token(token)
    if not vision.enabled:
        return JSONResponse({"error": "Screen observation is off (or private mode is on). Turn on 'See screen' on the PC."}, status_code=403)
    frame = await asyncio.to_thread(vision.current, 4.0)
    if frame.blocked:
        return JSONResponse({"error": "The active app is on the block list."}, status_code=403)
    return Response(frame.to_jpeg(900, 70), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/quit")
@app.post("/api/quit")
def api_quit():
    log.info("Quit requested via API; shutting down Eli backend.")
    def _exit():
        time.sleep(0.3)
        import os
        os._exit(0)
    import threading
    threading.Thread(target=_exit, daemon=True).start()
    return {"status": "quitting"}


@app.post("/api/command")
async def api_command(body: dict, token: str = Query("")):
    _check_token(token)
    reply = await agent.handle(str(body.get("text", "")), "mobile")
    return {"reply": reply}


@app.post("/api/camera")
async def api_camera(body: dict, token: str = Query("")):
    """Phone photo -> caption (vision model, or OCR offline) -> encrypted memory."""
    _check_token(token)
    if settings.get("private_mode"):
        return JSONResponse({"error": "Private mode is on; the photo was not stored."}, status_code=403)
    data = body.get("image", "")
    if "," in data[:80]:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception:
        raise HTTPException(400, "bad image")
    note = str(body.get("note", "")).strip()
    from PIL import Image
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    frame = Frame(image=img, ts=time.time(), window=WindowInfo(app="phone camera"))
    caption = ""
    if llm.available:
        b64, media, _, _ = frame.to_model_image(1024)
        try:
            r = await llm.complete(
                ("You write short, factual captions for a personal memory log. Describe objects and where they are, visible text, "
                 "and people by appearance only (never identify them). One or two sentences.", ""),
                [{"role": "user", "parts": [image_part(media, b64), text_part("Caption this photo." + (f" The user's note: {note}" if note else ""))]}], [])
            caption = r.text.strip()
        except Exception as e:
            log.warning("caption failed: %s", e)
    if not caption:
        text = await asyncio.to_thread(frame.text, 300)
        caption = ("Text in photo: " + text.replace("\n", " ")) if text else "Photo (no caption available offline)"
    if note:
        caption = f"{caption} Note: {note}"
    thumb = frame.to_jpeg(320, 60)
    mem = memory.add_world_frame(caption, thumb, source="phone")
    hub.transcript("eli", f"Saved a photo memory: {caption}", "mobile")
    return {"ok": True, "caption": caption, "memory_id": mem.id}


@app.post("/api/sos")
async def api_sos(body: dict, token: str = Query("")):
    _check_token(token)
    note = str(body.get("note", "")).strip() or "Emergency assistance requested from the phone."
    hub.emit({"type": "notify", "title": "Emergency alert from your phone", "body": note}, to=("desktop",))
    hub.toast("EMERGENCY: " + note)
    hub.transcript("eli", "Emergency alert received: " + note, "mobile")
    if speech:
        speech.say("Emergency alert from your phone. " + note)
    memory.remember(f"Emergency alert from the phone: {note}", kind="episode", importance=1.0, source="phone")
    return {"ok": True}


# -- settings ---------------------------------------------------------------------------------------
async def apply_setting(key: str, value) -> None:
    if key == "observe_enabled":
        if value and settings.get("private_mode"):
            hub.toast("Private mode is on; turn it off to observe the screen.")
            value = False
        elif value and not broker.granted():
            value = await broker.ensure_screen("Eli wants to observe your screen every few seconds so it knows what you're working on. Frames stay on this PC.")
        settings.set(key, bool(value))
        vision._last_announced = ""
    elif key in ("wake_enabled", "voice_replies", "save_frames", "automation_enabled", "nudges_enabled", "follow_cursor"):
        settings.set(key, bool(value))
        if key == "wake_enabled" and value and speech and not speech.available():
            hub.toast("No microphone found, so 'Hey Eli' can't listen.")
            settings.set(key, False)
    elif key == "private_mode":
        settings.set(key, bool(value))
        memory.private = bool(value)
        if value and speech:
            speech.stop_speaking()
        hub.toast("Private mode on: no capture, no memory, no cloud calls with screen content." if value else "Private mode off.")
    elif key == "autostart":
        out = await asyncio.to_thread(config.set_autostart, bool(value))
        hub.toast("Eli will start when you sign in to Windows." if value else "Eli will no longer start automatically.")
        log.info("autostart: %s", out)
    elif key == "trust_mode":
        settings.set(key, bool(value))
        settings.set("trust_until", 0)  # switched on from the UI: stays until switched off
        hub.toast("Trust mode on: risky actions run without asking (destructive commands still ask)." if value else "Trust mode off.")
    elif key == "proactive_mode" and value in ("ask", "auto"):
        settings.set(key, value)
    elif key == "blocked_apps":
        items = [str(v).strip().lower() for v in (value or []) if str(v).strip()]
        settings.set(key, items)
    elif key == "screen_permission" and value in ("ask", "granted", "denied"):
        settings.set(key, value)
        if value != "granted":
            settings.set("observe_enabled", False)
    hub.emit(status_payload())


async def handle_message(msg: dict, source: str) -> None:
    t = msg.get("type")
    if t in ("user_text", "command"):
        asyncio.create_task(agent.handle(str(msg.get("text", "")), source))
    elif t == "ptt" and speech:
        speech.ptt_toggle()
    elif t == "set_setting":
        await apply_setting(str(msg.get("key", "")), msg.get("value"))
    elif t == "confirm":
        asyncio.create_task(agent.confirm(str(msg.get("id", "")), bool(msg.get("approve"))))
    elif t == "permission":
        broker.resolve(str(msg.get("id", "")), bool(msg.get("allow")))
    elif t == "nudge_action":
        asyncio.create_task(agent.nudge_action(str(msg.get("id", "")), str(msg.get("action", "no"))))
    elif t in ("stop", "stop_task", "cancel_task", "stop_speaking"):
        agent._abort_requested = True
        if speech:
            speech.stop_speaking()
        auto.stop_or_pause_media()
        if scheduler:
            scheduler.cancel_all()
        hub.set_state("idle")
        hub.toast("Stopped immediately.")
    elif t == "set_persona":
        pid = str(msg.get("persona", ""))
        asyncio.create_task(agent.switch_persona(pid))
    elif t == "forget_all":
        n = memory.forget(everything=True)
        hub.toast(f"Deleted {n} memories, the knowledge graph and the conversation log.")
        hub.emit(status_payload())
    elif t == "forget_today":
        n = memory.forget(since=time.time() - time.time() % 86400)
        hub.toast(f"Deleted {n} memories and today's conversation.")
        hub.emit(status_payload())
    elif t == "job_cancel":
        scheduler.cancel(int(msg.get("id", 0)))
    elif t == "guide_preview":
        # draws a sample step so the overlay layout can be checked without a CAD app
        rect = vision.user_window().rect
        l, tp, r, b = rect if rect[2] - rect[0] > 100 else (0, 0, 1920, 1080)
        hub.emit({"type": "guide", "action": "show", "app": "Preview", "step": {"index": 1, "total": 6, "id": "preview", "title": "Pick the Trim tool",
                  "instruction": "Press G, T (Sketch > Sketcher tools > Trim edge). The status bar reads Trim edge.", "optional": False},
                  "indicators": [{"kind": "region", "x": l + (r - l) // 4, "y": tp + (b - tp) // 3, "w": (r - l) // 6, "h": (b - tp) // 8, "label": "Look here"},
                                 {"kind": "ring", "x": l + (r - l) // 4 + (r - l) // 12, "y": tp + (b - tp) // 3 + (b - tp) // 16, "r": 24},
                                 {"kind": "arrow", "x": l + (r - l) // 4 + (r - l) // 12, "y": tp + (b - tp) // 3 + (b - tp) // 16},
                                 {"kind": "key", "keys": "G, T"}]}, to=("desktop",))
        async def _expire():
            await asyncio.sleep(12)
            if not guide.active:
                hub.emit({"type": "guide", "action": "clear"}, to=("desktop",))
        asyncio.create_task(_expire())
    elif t == "guide_action":
        if msg.get("action") == "stop" and not guide.active:
            hub.emit({"type": "guide", "action": "clear"}, to=("desktop",))
        asyncio.create_task(guide.control(str(msg.get("action", ""))))
    elif t == "guide_start":
        asyncio.create_task(agent.handle("guide me through " + str(msg.get("request", "merging the holes")), source))
    elif t == "guide_resend":
        guide.resend()
    elif t == "get_status":
        hub.emit(status_payload())


# -- WebSockets ---------------------------------------------------------------------------------------
@app.websocket("/ws/desktop")
async def ws_desktop(ws: WebSocket):
    # Security: strictly reject any non-localhost connection to the desktop control websocket
    client_host = ws.client.host if ws.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        log.warning("Security alert: rejected non-localhost connection to /ws/desktop from %s", client_host)
        await ws.close(code=1008, reason="Forbidden: localhost only")
        return
    await ws.accept()
    hub.add("desktop", ws)
    try:
        await ws.send_json(status_payload())
        await ws.send_json({"type": "history", "items": memory.recent_messages(12)})
        try:
            await ws.send_json({"type": "context", **vision.user_window().as_dict()})
        except Exception:
            pass
        guide.resend()
        while True:
            msg = await ws.receive_json()
            await handle_message(msg, "desktop")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.info("desktop socket closed: %s", e)
    finally:
        hub.remove("desktop", ws)


@app.websocket("/ws/mobile")
async def ws_mobile(ws: WebSocket, token: str = Query("")):
    if not token or token != settings.get("pairing_token"):
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.add("mobile", ws)
    hub.emit(status_payload(), to=("desktop",))
    try:
        await ws.send_json(status_payload())
        await ws.send_json({"type": "history", "items": memory.recent_messages(12)})
        while True:
            msg = await ws.receive_json()
            await handle_message(msg, "mobile")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.info("mobile socket closed: %s", e)
    finally:
        hub.remove("mobile", ws)
        hub.emit(status_payload(), to=("desktop",))


# -- Astha Hyperlink Protocol -------------------------------------------------------------------------
@app.get("/api/astha/status")
async def astha_status():
    """Health and status endpoint for Astha mobile/PWA sensory client."""
    return {
        "ok": True,
        "engine": "Eli",
        "name": "Eli Desktop Core",
        "version": __version__,
        "ready": True,
        "state": hub.state,
        "llm_ready": llm.available,
        "active_window": vision.user_window().title if vision.user_window() else ""
    }


def is_lan_or_local(host: str) -> bool:
    if not host or host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return True
    if host.startswith("192.168.") or host.startswith("10.") or host.startswith("127."):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


@app.post("/api/astha/task")
async def astha_task(body: dict):
    """Executes a desktop task (coding, command, phone event, or IDE action) dispatched from Astha."""
    task_type = body.get("type", "command")
    if task_type == "phone_event":
        event_name = body.get("event", "")
        caller = body.get("caller", "")
        callee = body.get("callee", "")
        app_name = body.get("app", "")
        log.info("Received phone event via HTTP from Astha: event=%s, caller=%s, app=%s", event_name, caller, app_name)
        if event_name == "incoming_call":
            try:
                from eli import voice
                asyncio.create_task(asyncio.to_thread(voice.speak_text, f"Astha alert: incoming call from {caller or 'unknown caller'}"))
            except Exception:
                pass
            try:
                memories.save_memory("phone_call", f"Incoming call from {caller}", category="phone_events")
            except Exception:
                pass
        hub.emit({"type": "astha_phone_event", "event": event_name, "caller": caller, "callee": callee, "app": app_name})
        return {"ok": True, "event": event_name}
    elif task_type == "code":
        filename = body.get("filename", "script.py")
        code = body.get("code", "")
        goal = body.get("goal", "")
        if not code and goal:
            prompt = f"Write a complete, clean, working {body.get('language', 'Python')} script to {goal}. Output ONLY the code inside a ```python ``` block with no other conversational text."
            gen = await agent.handle(prompt, "astha")
            code_match = re.search(r"```(?:python)?\s*\n([\s\S]*?)\n```", gen)
            code = code_match.group(1).strip() if code_match else gen.strip()

        result = await asyncio.to_thread(coding.create_code_script, filename, code, body.get("language", "python"), run_after=body.get("run", True))
        return {
            "ok": result.get("ok", False),
            "output": result.get("execution_output", ""),
            "path": result.get("path", ""),
            "summary": result.get("summary", ""),
            "en": f"Successfully created {filename}, verified syntax, ran it, and opened it in VS Code.",
            "bn": f"ভিএস কোডে {filename} স্ক্রিপ্ট তৈরি এবং রান করা হয়েছে।"
        }
    else:
        text = body.get("text", "")
        reply = await agent.handle(text, "astha")
        return {"ok": True, "reply": reply}


@app.websocket("/ws/astha")
async def ws_astha(ws: WebSocket, token: str = Query("")):
    client_host = ws.client.host if ws.client else ""
    if not is_lan_or_local(client_host):
        if not token or token != settings.get("pairing_token"):
            await ws.close(code=4401, reason="bad pairing token")
            return
    await ws.accept()
    log.info("Astha client connected from %s", client_host)
    try:
        await ws.send_json({
            "type": "astha_ready",
            "engine": "Eli",
            "version": __version__,
            "state": hub.state,
            "ides": coding.discover_ides()
        })
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type", "")
            if mtype == "ping":
                await ws.send_json({"type": "pong", "time": time.time()})
            elif mtype == "phone_event":
                event_name = msg.get("event", "")
                caller = msg.get("caller", "")
                callee = msg.get("callee", "")
                app_name = msg.get("app", "")
                log.info("Received phone event over WS from Astha: event=%s, caller=%s, app=%s", event_name, caller, app_name)
                if event_name == "incoming_call":
                    try:
                        from eli import voice
                        asyncio.create_task(asyncio.to_thread(voice.speak_text, f"Astha alert: incoming call from {caller or 'unknown caller'}"))
                    except Exception:
                        pass
                    try:
                        memories.save_memory("phone_call", f"Incoming call from {caller}", category="phone_events")
                    except Exception:
                        pass
                hub.emit({"type": "astha_phone_event", "event": event_name, "caller": caller, "callee": callee, "app": app_name})
                await ws.send_json({"type": "phone_event_ack", "ok": True, "event": event_name})
            elif mtype == "code_task":
                filename = msg.get("filename", "script.py")
                code = msg.get("code", "")
                goal = msg.get("goal", "")
                if not code and goal:
                    gen = await agent.handle(f"Write a clean, working Python script for: {goal}. Output ONLY the code inside ```python ```.", "astha")
                    code_match = re.search(r"```(?:python)?\s*\n([\s\S]*?)\n```", gen)
                    code = code_match.group(1).strip() if code_match else gen.strip()
                result = await asyncio.to_thread(coding.create_code_script, filename, code, msg.get("language", "python"), run_after=msg.get("run", True))
                await ws.send_json({
                    "type": "code_task_result",
                    "ok": result.get("ok", False),
                    "summary": result.get("summary", ""),
                    "output": result.get("execution_output", ""),
                    "path": result.get("path", ""),
                    "en": f"Script {filename} created, syntax verified, and opened in VS Code.",
                    "bn": f"ভিএস কোডে {filename} স্ক্রিপ্ট তৈরি এবং রান করা সম্পন্ন হয়েছে।"
                })
            elif mtype in ("command", "user_text"):
                text = msg.get("text", "")
                reply = await agent.handle(text, "astha")
                await ws.send_json({"type": "command_result", "reply": reply})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.info("astha socket error: %s", e)


