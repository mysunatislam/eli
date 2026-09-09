"""Eli Bridge — Blender add-on.

Lets the Eli desktop companion inspect and fix the *running* Blender session. It listens on
127.0.0.1:8791 (localhost only) for newline-terminated JSON {"code": "..."} requests, runs the code
on Blender's main thread (bpy is only safe there), and answers with {"output": "..."}.

Install: Blender > Edit > Preferences > Add-ons > Install... > pick this file > enable "Eli Bridge".
Eli asks for your confirmation before executing code here unless you switch on trust mode.
"""
bl_info = {
    "name": "Eli Bridge",
    "author": "Eli",
    "version": (0, 2, 0),
    "blender": (3, 0, 0),
    "location": "Background service (127.0.0.1:8791)",
    "description": "Lets the Eli desktop companion inspect and fix this Blender session over localhost",
    "category": "System",
}

import contextlib
import io
import json
import queue
import socket
import threading
import traceback

import bpy

PORT = 8791
_requests: "queue.Queue[tuple[str, socket.socket]]" = queue.Queue()
_running = False
_server: socket.socket | None = None


def _execute(code: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            exec(compile(code, "<eli>", "exec"), {"bpy": bpy, "__name__": "__eli__"})
        except Exception:
            buf.write(traceback.format_exc())
    return buf.getvalue()


def _timer():
    """Runs on Blender's main thread every 0.2 s and drains queued requests."""
    while not _requests.empty():
        code, conn = _requests.get()
        out = _execute(code)
        try:
            conn.sendall((json.dumps({"output": out}) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
    return 0.2 if _running else None


def _serve():
    global _server
    _server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _server.bind(("127.0.0.1", PORT))
    _server.listen(4)
    _server.settimeout(1.0)
    while _running:
        try:
            conn, _ = _server.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            conn.settimeout(10)
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data.decode("utf-8", "replace") or "{}")
            _requests.put((str(req.get("code", "")), conn))
        except Exception:
            try:
                conn.close()
            except OSError:
                pass


def register():
    global _running
    _running = True
    threading.Thread(target=_serve, name="eli-bridge", daemon=True).start()
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, persistent=True)
    print(f"[Eli Bridge] listening on 127.0.0.1:{PORT}")


def unregister():
    global _running, _server
    _running = False
    try:
        if _server:
            _server.close()
    except OSError:
        pass
    _server = None
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)


if __name__ == "__main__":
    register()
