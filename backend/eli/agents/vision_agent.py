"""Vision Agent: screen capture, active-window detection, OCR, screenshot encoding for the
vision model, adaptive observation and app-level privacy.

Pipeline:  screen capture (mss) -> active window (Win32) -> change detection -> OCR (Windows.Media.Ocr)
           -> Frame -> (on request) JPEG for the vision-language model.

Privacy: frames live in RAM only. Apps on the block list are never captured, private mode stops
capture entirely, and nothing is sent to the model unless the user asks about the screen.
"""
from __future__ import annotations

import base64
import ctypes
import io
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

import psutil
from PIL import Image, ImageOps

log = logging.getLogger("eli.vision")

def ensure_interactive_desktop() -> bool:
    """Ensure the calling thread is attached to the active user desktop (Default)."""
    import os
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        DESKTOP_ALL = 0x1FF
        h = user32.OpenInputDesktop(0, False, DESKTOP_ALL)
        if not h:
            h = user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL)
        if h:
            return bool(user32.SetThreadDesktop(h))
    except Exception as e:
        log.debug("ensure_interactive_desktop error: %s", e)
    return False

try:
    import mss  # type: ignore
except Exception:  # pragma: no cover
    mss = None

try:
    import winocr  # type: ignore
except Exception:
    winocr = None

import re

APP_NAMES = {
    "code.exe": "VS Code", "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox",
    "notepad.exe": "Notepad", "notepad++.exe": "Notepad++", "explorer.exe": "File Explorer",
    "windowsterminal.exe": "Windows Terminal", "cmd.exe": "Command Prompt", "powershell.exe": "PowerShell",
    "pwsh.exe": "PowerShell", "sldworks.exe": "SolidWorks", "fusion360.exe": "Fusion 360", "blender.exe": "Blender",
    "acad.exe": "AutoCAD", "freecad.exe": "FreeCAD", "winword.exe": "Word", "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint", "outlook.exe": "Outlook", "olk.exe": "Outlook", "slack.exe": "Slack",
    "discord.exe": "Discord", "teams.exe": "Teams", "ms-teams.exe": "Teams", "whatsapp.exe": "WhatsApp",
    "telegram.exe": "Telegram", "spotify.exe": "Spotify", "idea64.exe": "IntelliJ IDEA", "pycharm64.exe": "PyCharm",
    "python.exe": "Python", "mspaint.exe": "Paint", "calc.exe": "Calculator", "calculatorapp.exe": "Calculator",
    "claude.exe": "Claude", "electron.exe": "Eli overlay", "figma.exe": "Figma", "obsidian.exe": "Obsidian",
    "onshape.exe": "Onshape", "inventor.exe": "Inventor", "cura.exe": "Cura", "prusa-slicer.exe": "PrusaSlicer",
}

ERROR_RE = re.compile(
    r"(traceback|error|exception|failed|fatal|cannot find|is not defined|undefined|unexpected token|"
    r"syntaxerror|nameerror|typeerror|indexerror|keyerror|modulenotfounderror|importerror|attributeerror|"
    r"valueerror|filenotfounderror|zerodivisionerror|panic|segmentation fault|npm err|enoent|eaddrinuse|"
    r"warning|exit code [1-9])",
    re.I,
)


@dataclass
class WindowInfo:
    title: str = ""
    process: str = ""
    app: str = ""
    pid: int = 0
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    def describe(self) -> str:
        app = self.app or self.process or "Unknown app"
        return f"{app} — {self.title}" if self.title else app

    def as_dict(self) -> dict:
        return {"title": self.title, "process": self.process, "app": self.app, "pid": self.pid, "rect": list(self.rect)}


@dataclass
class OcrLine:
    text: str
    box: tuple[int, int, int, int]  # x, y, w, h in screen pixels


def active_window() -> WindowInfo:
    """Foreground window title + owning process, via Win32 (no extra deps)."""
    ensure_interactive_desktop()
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        proc = ""
        try:
            proc = psutil.Process(pid.value).name()
        except Exception:
            pass
        return WindowInfo(
            title=buf.value, process=proc,
            app=APP_NAMES.get(proc.lower(), proc.replace(".exe", "").title() if proc else ""),
            pid=pid.value, rect=(rect.left, rect.top, rect.right, rect.bottom),
        )
    except Exception as e:  # pragma: no cover
        log.debug("active_window failed: %s", e)
        return WindowInfo()


def window_rect_by_title(substr: str) -> Optional[tuple[int, int, int, int]]:
    """Screen rectangle of the first visible window whose title contains substr."""
    try:
        import pygetwindow as gw  # type: ignore
        for w in gw.getAllWindows():
            if substr.lower() in (w.title or "").lower() and w.title and not w.isMinimized:
                return (w.left, w.top, w.left + w.width, w.top + w.height)
    except Exception:
        pass
    return None


def run_ocr(img: Image.Image) -> list[OcrLine]:
    """OCR with the Windows built-in engine. Runs offline. Call from a worker thread."""
    if winocr is None:
        return []
    try:
        res = winocr.recognize_pil_sync(img, "en")
    except Exception as e:
        log.debug("OCR failed: %s", e)
        return []
    lines: list[OcrLine] = []
    for line in _get(res, "lines") or []:
        words = _get(line, "words") or []
        box = (0, 0, 0, 0)
        if words:
            try:
                rects = [_get(w, "bounding_rect") for w in words]
                xs = [int(_get(r, "x")) for r in rects]
                ys = [int(_get(r, "y")) for r in rects]
                x2 = [int(_get(r, "x") + _get(r, "width")) for r in rects]
                y2 = [int(_get(r, "y") + _get(r, "height")) for r in rects]
                box = (min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys))
            except Exception:
                pass
        text = _get(line, "text") or ""
        if text.strip():
            lines.append(OcrLine(text=text.strip(), box=box))
    return lines


def _get(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


@dataclass
class Frame:
    image: Image.Image
    ts: float
    window: WindowInfo
    blocked: bool = False
    _ocr: Optional[list[OcrLine]] = None

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    def ocr(self) -> list[OcrLine]:
        if self._ocr is None:
            self._ocr = [] if self.blocked else run_ocr(self.image)
        return self._ocr

    def text(self, max_chars: int = 6000) -> str:
        return "\n".join(l.text for l in self.ocr())[:max_chars]

    def window_lines(self) -> list[OcrLine]:
        l, t, r, b = self.window.rect
        if r - l < 50 or b - t < 50:
            return self.ocr()
        inside = [ln for ln in self.ocr() if ln.box[2] and l <= ln.box[0] + ln.box[2] // 2 <= r and t <= ln.box[1] + ln.box[3] // 2 <= b]
        return inside or self.ocr()

    def window_text(self, max_chars: int = 4000) -> str:
        return "\n".join(l.text for l in self.window_lines())[:max_chars]

    def other_text(self, max_chars: int = 1500) -> str:
        win = set(id(l) for l in self.window_lines())
        return "\n".join(l.text for l in self.ocr() if id(l) not in win)[:max_chars]

    def error_snippet(self, context: int = 4, max_lines: int = 24) -> str:
        lines = [l.text for l in self.window_lines()]
        if not any(ERROR_RE.search(t) for t in lines):
            lines = [l.text for l in self.ocr()]
        hits = [i for i, t in enumerate(lines) if ERROR_RE.search(t)]
        if not hits:
            return ""
        keep: set[int] = set()
        for i in hits:
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
        out = [lines[i] for i in sorted(keep)]
        return "\n".join(out[:max_lines])

    def signature(self) -> bytes:
        """Tiny grayscale thumbnail used for change detection."""
        return ImageOps.grayscale(self.image).resize((24, 14), Image.BILINEAR).tobytes()

    def to_model_image(self, max_side: int = 1568) -> tuple[str, str, float, tuple[int, int]]:
        img = self.image
        w, h = img.size
        scale = max(w, h) / float(max_side)
        if scale > 1.0:
            img = img.resize((int(w / scale), int(h / scale)), Image.LANCZOS)
        else:
            scale = 1.0
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg", scale, img.size

    def to_jpeg(self, max_side: int = 900, quality: int = 70) -> bytes:
        img = self.image
        w, h = img.size
        s = max(w, h) / float(max_side)
        if s > 1.0:
            img = img.resize((int(w / s), int(h / s)), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()


def _sig_diff(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


class VisionAgent:
    """Observes the screen (when allowed) adaptively and answers 'what is on screen' questions."""

    def __init__(self, hub, settings, interval: float = 3.0, interval_max: float = 15.0, max_side: int = 1568):
        self.hub = hub
        self.settings = settings
        self.base_interval = interval
        self.interval_max = interval_max
        self.interval = interval
        self.max_side = max_side
        self.latest: Optional[Frame] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_announced = ""
        self._last_real: Optional[WindowInfo] = None
        self._last_sig: bytes = b""
        self.frame_listeners: list[Callable] = []   # e.g. the proactive monitor
        self.captures = 0
        self.ocr_runs = 0
        self.last_model_scale = 1.0
        self.last_model_size = (0, 0)

    # -- policy ----------------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("observe_enabled")) and not self.settings.get("private_mode") \
            and self.settings.get("screen_permission") == "granted"

    def blocked(self, w: WindowInfo) -> bool:
        hay = f"{w.title} {w.process} {w.app}".lower()
        return any(b and b in hay for b in (self.settings.get("blocked_apps") or []))

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self) -> None:
        if mss is None:
            log.warning("mss not installed; screen capture disabled")
            return
        self._thread = threading.Thread(target=self._loop, name="eli-vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        sct = mss.mss()
        while not self._stop.is_set():
            if not self.enabled:
                self.interval = self.base_interval
                time.sleep(0.5)
                continue
            try:
                frame = self._grab(sct)
                if frame.blocked:
                    with self._lock:
                        self.latest = None
                    self._announce(frame)
                    time.sleep(self.base_interval)
                    continue
                sig = frame.signature()
                changed = _sig_diff(sig, self._last_sig) > 2.5
                self._last_sig = sig
                if changed:
                    self.interval = self.base_interval
                    frame.ocr()
                    self.ocr_runs += 1
                    with self._lock:
                        self.latest = frame
                    for fn in list(self.frame_listeners):
                        try:
                            fn(frame)
                        except Exception as e:
                            log.debug("frame listener failed: %s", e)
                    if self.settings.get("save_frames"):
                        self._save(frame)
                else:
                    # screen is static: keep the old OCR, slow down
                    with self._lock:
                        if self.latest is not None:
                            self.latest.ts = frame.ts
                    self.interval = min(self.interval * 1.5, self.interval_max)
                self._announce(frame)
            except Exception as e:
                log.warning("capture failed: %s", e)
                time.sleep(2)
            time.sleep(self.interval)

    def _grab(self, sct) -> Frame:
        ensure_interactive_desktop()
        win = self.user_window()
        if self.blocked(win):
            return Frame(image=Image.new("RGB", (8, 8), "black"), ts=time.time(), window=win, blocked=True)
        try:
            mon = sct.monitors[1]
            shot = sct.grab(mon)
            self.captures += 1
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            return Frame(image=img, ts=time.time(), window=win)
        except Exception as e:
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                self.captures += 1
                return Frame(image=img, ts=time.time(), window=win)
            except Exception:
                return Frame(image=Image.new("RGB", (1920, 1080), "white"), ts=time.time(), window=win)

    def user_window(self) -> WindowInfo:
        """The window the user is working in: ignores Eli's own overlay (which takes focus when clicked)."""
        w = active_window()
        if w.process.lower() == "electron.exe" and w.title.strip() == "Eli":
            return self._last_real or WindowInfo(title="", process="", app="Desktop")
        self._last_real = w
        return w

    def _announce(self, frame: Frame) -> None:
        desc = frame.window.describe() + (" [blocked]" if frame.blocked else "")
        if desc != self._last_announced:
            self._last_announced = desc
            self.hub.emit({"type": "context", **frame.window.as_dict(), "blocked": frame.blocked}, to=("desktop",))

    def _save(self, frame: Frame) -> None:
        from .. import config
        d = config.DATA_DIR / "frames"
        d.mkdir(exist_ok=True)
        frame.image.save(d / f"{int(frame.ts)}.jpg", "JPEG", quality=60)

    # -- queries ---------------------------------------------------------------------------------
    def capture_now(self) -> Frame:
        """Fresh capture regardless of the background loop. Honors the block list and private mode."""
        ensure_interactive_desktop()
        if self.settings.get("private_mode"):
            return Frame(image=Image.new("RGB", (8, 8), "black"), ts=time.time(), window=self.user_window(), blocked=True)
        with mss.mss() as sct:
            frame = self._grab(sct)
        if not frame.blocked:
            with self._lock:
                self.latest = frame
        return frame

    def current(self, max_age: float = 6.0) -> Frame:
        with self._lock:
            latest = self.latest
        if latest is not None and (time.time() - latest.ts) <= max_age:
            return latest
        return self.capture_now()

    def model_image(self, frame: Frame) -> tuple[str, str]:
        b64, media, scale, size = frame.to_model_image(self.max_side)
        self.last_model_scale = scale
        self.last_model_size = size
        return b64, media

    def to_screen_coords(self, x: float, y: float) -> tuple[int, int]:
        return int(x * self.last_model_scale), int(y * self.last_model_scale)

    def describe_locally(self, frame: Frame, max_chars: int = 500, max_lines: int = 8) -> str:
        if frame.blocked:
            return f"{frame.window.app or 'That app'} is on your block list, so I didn't look."
        lines = [l.text for l in frame.window_lines() if len(l.text) > 2][:max_lines]
        head = f"I can see {frame.window.describe()}."
        if not lines:
            return head + (" I couldn't read any text in it." if winocr else " (Windows OCR is not available, so I can't read the text.)")
        body = "\n".join(lines)[:max_chars]
        return f"{head} Text I can read in it:\n{body}"

    def find_text_box(self, query: str, frame: Optional[Frame] = None, region: Optional[str] = None) -> Optional[tuple[int, int, int, int, str]]:
        """Locate visible text; returns (x, y, w, h, text) in screen pixels. `region` (top/left/right/bottom)
        restricts the search to that part of the active window, which disambiguates toolbar labels."""
        frame = frame or self.current(max_age=2.0)
        q = query.strip().lower()
        if not q or frame.blocked:
            return None
        lines = [l for l in frame.ocr() if l.box[2] and l.box[3]]
        l0, t0, r0, b0 = frame.window.rect
        if region and r0 - l0 > 100:
            W, H = r0 - l0, b0 - t0
            def inside(ln):
                cx, cy = ln.box[0] + ln.box[2] // 2, ln.box[1] + ln.box[3] // 2
                if region == "top":
                    return cy < t0 + 0.2 * H
                if region == "bottom":
                    return cy > t0 + 0.8 * H
                if region == "left":
                    return cx < l0 + 0.3 * W
                if region == "right":
                    return cx > l0 + 0.7 * W
                return True
            scoped = [l for l in lines if inside(l)]
            lines = scoped or lines
        exact = [l for l in lines if l.text.lower() == q]
        words = [l for l in lines if q in set(l.text.lower().split())]
        contains = [l for l in lines if q in l.text.lower()]
        for cands in (exact, words, contains):
            if cands:
                l = min(cands, key=lambda c: c.box[2] * c.box[3])
                x, y, w, h = l.box
                return (x, y, w, h, l.text)
        return None

    def find_text(self, query: str, frame: Optional[Frame] = None) -> Optional[tuple[int, int, str]]:
        hit = self.find_text_box(query, frame)
        if not hit:
            return None
        x, y, w, h, text = hit
        return (x + w // 2, y + h // 2, text)

    def stats(self) -> dict:
        return {"captures": self.captures, "ocr_runs": self.ocr_runs, "interval": round(self.interval, 1), "observing": self.enabled}
