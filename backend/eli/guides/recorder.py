"""Teach-me mode, part 1: record what an expert does once.

The recorder listens to the user's own clicks and keystrokes (Windows low-level hooks, never
pyautogui), takes a screenshot around each meaningful event, and enriches every event with what
the guide engine will later need to point at and verify:

    - the OCR label under the cursor at the moment of the click ("FINISH SKETCH", "Trim", ...)
    - the active window (title, process, rect) before and after
    - which OCR lines appeared / disappeared inside the app window because of the action
    - keyboard shortcuts as chords ("Ctrl+E"), navigation keys by name ("Enter", "Escape")

Plain typing is recorded as a *count of characters* by default, never the characters themselves,
unless the user explicitly turns `teach_capture_text` on. Password managers, banking and anything
else on the vision block list are never captured, private mode refuses to record at all, and
events injected by software (Eli's own automation, macros) are ignored so Eli never records itself.

The output is a `Recording`: a plain-JSON trace plus optional JPEG thumbnails, saved under
DATA_DIR/recordings/<id>/. The segmenter (recorder.py's sibling) turns it into a guide.

Everything Windows-specific is isolated behind `Recorder.available`, so the trace model and the
segmenter run (and are tested) on any OS.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("eli.guide.record")

IS_WINDOWS = os.name == "nt"

# --- trace model (pure Python, OS-independent) ----------------------------------------------------------


@dataclass
class TraceEvent:
    """One meaningful user action, enriched with what the screen looked like around it."""
    i: int
    kind: str                         # click | rclick | dblclick | shortcut | key | type | scroll
    ts: float                         # seconds since recording start
    x: int = 0
    y: int = 0
    keys: str = ""                    # "Ctrl+E", "Enter", "F2" ... (shortcut/key)
    chars: int = 0                    # for kind == "type": how many printable characters
    text: str = ""                    # typed text, only when teach_capture_text is on
    label: str = ""                   # OCR text under the cursor before the click
    label_box: list[int] = field(default_factory=list)   # x, y, w, h (screen px)
    region: str = ""                  # top | left | right | bottom | center (relative to the window)
    rel: list[float] = field(default_factory=list)       # click position as fractions of the window
    window_before: dict = field(default_factory=dict)
    window_after: dict = field(default_factory=dict)
    appeared: list[str] = field(default_factory=list)    # OCR lines new after the action
    disappeared: list[str] = field(default_factory=list) # OCR lines gone after the action
    before_image: str = ""            # relative path of a JPEG thumbnail (before), if saved
    after_image: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TraceEvent":
        known = {f for f in TraceEvent.__dataclass_fields__}
        return TraceEvent(**{k: v for k, v in d.items() if k in known})


@dataclass
class Recording:
    id: str
    name: str
    goal: str
    app: dict = field(default_factory=dict)      # window of the app the recording started in
    started: float = 0.0
    ended: float = 0.0
    events: list[TraceEvent] = field(default_factory=list)
    dir: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, (self.ended or time.time()) - self.started)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "goal": self.goal, "app": self.app, "started": self.started,
                "ended": self.ended, "duration": round(self.duration, 1), "events": [e.as_dict() for e in self.events]}

    @staticmethod
    def from_dict(d: dict, rec_dir: str = "") -> "Recording":
        r = Recording(id=str(d.get("id", "")), name=str(d.get("name", "")), goal=str(d.get("goal", "")),
                      app=dict(d.get("app") or {}), started=float(d.get("started") or 0), ended=float(d.get("ended") or 0),
                      dir=rec_dir)
        r.events = [TraceEvent.from_dict(e) for e in (d.get("events") or []) if isinstance(e, dict)]
        return r

    def save(self, base_dir: Path) -> Path:
        d = base_dir / self.id
        d.mkdir(parents=True, exist_ok=True)
        (d / "trace.json").write_text(json.dumps(self.as_dict(), indent=1), "utf-8")
        self.dir = str(d)
        return d

    @staticmethod
    def load(rec_dir: Path) -> "Recording":
        data = json.loads((rec_dir / "trace.json").read_text("utf-8"))
        return Recording.from_dict(data, str(rec_dir))


# --- enrichment helpers (pure Python; unit-tested) ------------------------------------------------------

def label_under(point: tuple[int, int], lines, window_rect=None, max_dist: int = 40) -> tuple[str, list[int]]:
    """Best OCR line for a click at `point`: containing box wins, else nearest box centre within max_dist px."""
    px, py = point
    best, best_d = None, float("inf")
    for ln in lines or []:
        x, y, w, h = ln.box
        if w <= 0 or h <= 0:
            continue
        if window_rect and not _inside(window_rect, x + w // 2, y + h // 2):
            continue
        if x <= px <= x + w and y <= py <= y + h:
            return ln.text.strip(), [x, y, w, h]
        # distance from the point to the box (not to its centre, so long labels still match)
        dx = max(x - px, 0, px - (x + w))
        dy = max(y - py, 0, py - (y + h))
        d = (dx * dx + dy * dy) ** 0.5
        if d < best_d:
            best, best_d = ln, d
    if best is not None and best_d <= max_dist:
        x, y, w, h = best.box
        return best.text.strip(), [x, y, w, h]
    return "", []


def _inside(rect, x: int, y: int) -> bool:
    l, t, r, b = rect
    return l <= x <= r and t <= y <= b


def region_of(point: tuple[int, int], window_rect) -> tuple[str, list[float]]:
    """Coarse region name and fractional position of a point inside a window."""
    l, t, r, b = window_rect or (0, 0, 0, 0)
    w, h = max(1, r - l), max(1, b - t)
    fx, fy = (point[0] - l) / w, (point[1] - t) / h
    fx, fy = min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy))
    if fy < 0.16:
        reg = "top"
    elif fy > 0.86:
        reg = "bottom"
    elif fx < 0.2:
        reg = "left"
    elif fx > 0.8:
        reg = "right"
    else:
        reg = "center"
    return reg, [round(fx, 3), round(fy, 3)]


def ocr_delta(before_lines, after_lines, min_len: int = 3, limit: int = 12) -> tuple[list[str], list[str]]:
    """Lines that appeared / disappeared, ignoring short fragments and pure numbers (coordinates, timers)."""
    def keyset(lines):
        out = set()
        for ln in lines or []:
            t = " ".join(str(ln.text).split())
            if len(t) >= min_len and not t.replace(".", "").replace(",", "").replace("-", "").isdigit():
                out.add(t)
        return out
    b, a = keyset(before_lines), keyset(after_lines)
    appeared = sorted(a - b, key=lambda s: (-len(s), s))[:limit]
    disappeared = sorted(b - a, key=lambda s: (-len(s), s))[:limit]
    return appeared, disappeared


# --- key names --------------------------------------------------------------------------------------------

VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Escape", 0x20: "Space", 0x21: "PageUp", 0x22: "PageDown",
    0x23: "End", 0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Win", 0x5C: "Win", 0x6A: "*", 0x6B: "+", 0x6D: "-", 0x6E: ".", 0x6F: "/",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
for _i in range(0x70, 0x88):
    VK_NAMES[_i] = f"F{_i - 0x6F}"
MODIFIER_VKS = {0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift", 0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl",
                0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt", 0x5B: "Win", 0x5C: "Win"}
NAV_VKS = {0x08, 0x09, 0x0D, 0x1B, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E} | set(range(0x70, 0x88))


def vk_name(vk: int) -> str:
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num{vk - 0x60}"
    return f"VK{vk:02X}"


def chord(mods: set[str], vk: int) -> str:
    order = [m for m in ("Ctrl", "Alt", "Shift", "Win") if m in mods]
    return "+".join(order + [vk_name(vk)])


# --- the recorder -------------------------------------------------------------------------------------------

@dataclass
class _Raw:
    kind: str
    ts: float
    x: int = 0
    y: int = 0
    vk: int = 0
    mods: set = field(default_factory=set)
    delta: int = 0


class Recorder:
    """Records the user's actions with screen context. One recording at a time.

    vision: VisionAgent (capture_now / current / user_window / blocked)
    settings: Settings (private_mode, teach_capture_text)
    """

    WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL = 0x0201, 0x0204, 0x0207, 0x020A
    WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP = 0x0100, 0x0104, 0x0101, 0x0105
    WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
    LLMHF_INJECTED = 0x01
    DOUBLE_CLICK_S = 0.35
    AFTER_DELAY_S = 0.8
    TYPE_GAP_S = 1.5
    SNAPSHOT_EVERY_S = 1.0
    MAX_EVENTS = 400

    def __init__(self, vision, settings, data_dir: Path, on_event: Optional[Callable[[TraceEvent], None]] = None,
                 save_images: bool = True, thumb_side: int = 1000):
        self.vision, self.settings = vision, settings
        self.base_dir = Path(data_dir) / "recordings"
        self.on_event = on_event
        self.save_images = save_images
        self.thumb_side = thumb_side
        self.available = IS_WINDOWS
        self.recording: Optional[Recording] = None
        self._q: "queue.Queue[_Raw]" = queue.Queue()
        self._stop = threading.Event()
        self._hook_thread: Optional[threading.Thread] = None
        self._worker: Optional[threading.Thread] = None
        self._hook_tid = 0
        self._mods: set[str] = set()
        self._last_click: tuple[float, int, int] = (0.0, -1, -1)
        self._latest = None           # last Frame we captured ourselves
        self._typing: list[str] = []
        self._typing_started = 0.0
        self._typing_last = 0.0
        self._typing_window: dict = {}
        self._procs = []              # keep ctypes callbacks alive
        self.error = ""

    # -- public ------------------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.recording is not None and not self._stop.is_set()

    def start(self, name: str, goal: str = "") -> str:
        if not self.available:
            return "Recording needs Windows (low-level input hooks)."
        if self.settings.get("private_mode"):
            return "Private mode is on, so I won't record anything. Turn it off and ask again."
        if self.active:
            return f"I'm already recording “{self.recording.name}”. Say “stop recording” first."
        win = self.vision.user_window()
        if self.vision.blocked(win):
            return f"{win.app or 'That app'} is on your block list; I won't record it."
        rid = time.strftime("%Y%m%d-%H%M%S")
        self.recording = Recording(id=rid, name=(name or goal or "untitled").strip()[:80], goal=goal.strip()[:300],
                                   app=win.as_dict(), started=time.time())
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._mods.clear()
        self._typing, self._typing_started, self._typing_last = [], 0.0, 0.0
        self.error = ""
        self._latest = self._safe_capture()
        self._worker = threading.Thread(target=self._work, name="eli-teach-worker", daemon=True)
        self._worker.start()
        self._hook_thread = threading.Thread(target=self._hook_loop, name="eli-teach-hooks", daemon=True)
        self._hook_thread.start()
        time.sleep(0.15)
        if self.error:
            self._stop.set()
            self.recording = None
            return f"I couldn't install the input hooks: {self.error}"
        log.info("recording %s started in %s", rid, win.describe())
        return f"Recording “{self.recording.name}”. Do it once at your normal pace; say “stop recording” when you're done."

    def stop(self) -> Optional[Recording]:
        if self.recording is None:
            return None
        self._stop.set()
        self._flush_typing(final=True)
        if IS_WINDOWS and self._hook_tid:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._hook_tid, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        if self._worker:
            self._worker.join(timeout=3.0)
        rec = self.recording
        rec.ended = time.time()
        rec.save(self.base_dir)
        self.recording = None
        log.info("recording %s stopped: %d events, %.0fs", rec.id, len(rec.events), rec.duration)
        return rec

    def status(self) -> dict:
        r = self.recording
        return {"active": self.active, "id": r.id if r else "", "name": r.name if r else "",
                "events": len(r.events) if r else 0, "seconds": round(r.duration) if r else 0}

    # -- hooks (Windows) ---------------------------------------------------------------------------------

    def _hook_loop(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as e:  # pragma: no cover
            self.error = str(e)
            return
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._hook_tid = kernel32.GetCurrentThreadId()
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        t0 = self.recording.started if self.recording else time.time()

        def mouse_proc(n_code, w_param, l_param):
            if n_code >= 0 and not self._stop.is_set():
                try:
                    s = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    if not (s.flags & self.LLMHF_INJECTED):
                        now = time.time() - t0
                        if w_param == self.WM_LBUTTONDOWN:
                            self._q.put(_Raw("click", now, s.pt.x, s.pt.y, mods=set(self._mods)))
                        elif w_param == self.WM_RBUTTONDOWN:
                            self._q.put(_Raw("rclick", now, s.pt.x, s.pt.y, mods=set(self._mods)))
                        elif w_param == self.WM_MBUTTONDOWN:
                            self._q.put(_Raw("mclick", now, s.pt.x, s.pt.y))
                        elif w_param == self.WM_MOUSEWHEEL:
                            delta = ctypes.c_short(s.mouseData >> 16).value
                            self._q.put(_Raw("scroll", now, s.pt.x, s.pt.y, delta=delta))
                except Exception as e:  # never let an exception escape a hook
                    log.debug("mouse hook: %s", e)
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        def kbd_proc(n_code, w_param, l_param):
            if n_code >= 0 and not self._stop.is_set():
                try:
                    s = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if not (s.flags & 0x10):  # LLKHF_INJECTED
                        vk = int(s.vkCode)
                        down = w_param in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN)
                        if vk in MODIFIER_VKS:
                            (self._mods.add if down else self._mods.discard)(MODIFIER_VKS[vk])
                        elif down:
                            self._q.put(_Raw("key", time.time() - t0, vk=vk, mods=set(self._mods)))
                except Exception as e:
                    log.debug("kbd hook: %s", e)
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._procs = [HOOKPROC(mouse_proc), HOOKPROC(kbd_proc)]
        hmod = kernel32.GetModuleHandleW(None)
        h_mouse = user32.SetWindowsHookExW(self.WH_MOUSE_LL, self._procs[0], hmod, 0)
        h_kbd = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._procs[1], hmod, 0)
        if not h_mouse or not h_kbd:
            self.error = f"SetWindowsHookEx failed (mouse={bool(h_mouse)}, keyboard={bool(h_kbd)})"
            for h in (h_mouse, h_kbd):
                if h:
                    user32.UnhookWindowsHookEx(h)
            return
        msg = wintypes.MSG()
        try:
            while not self._stop.is_set():
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(h_mouse)
            user32.UnhookWindowsHookEx(h_kbd)
            self._procs = []

    # -- worker: enrich events with screen context -------------------------------------------------------

    def _safe_capture(self):
        try:
            f = self.vision.capture_now()
            return None if getattr(f, "blocked", False) else f
        except Exception as e:
            log.debug("capture failed: %s", e)
            return None

    def _work(self) -> None:
        last_snap = time.time()
        while not self._stop.is_set() or not self._q.empty():
            try:
                raw = self._q.get(timeout=0.25)
            except queue.Empty:
                if self._typing and time.time() - self._typing_last > self.TYPE_GAP_S:
                    self._flush_typing()
                if time.time() - last_snap >= self.SNAPSHOT_EVERY_S and not self._stop.is_set():
                    f = self._safe_capture()
                    if f is not None:
                        self._latest = f
                    last_snap = time.time()
                continue
            try:
                self._handle(raw)
            except Exception as e:
                log.warning("recorder event failed: %s", e)
            last_snap = time.time()

    def _handle(self, raw: _Raw) -> None:
        rec = self.recording
        if rec is None or len(rec.events) >= self.MAX_EVENTS:
            return
        if raw.kind == "key":
            if raw.mods - {"Shift"} or raw.vk in NAV_VKS:
                self._flush_typing()
                keys = chord(raw.mods, raw.vk) if raw.mods - {"Shift"} else vk_name(raw.vk)
                kind = "shortcut" if raw.mods - {"Shift"} else "key"
                self._add_event(kind, raw, keys=keys)
            else:
                # printable typing: aggregate
                ch = vk_name(raw.vk)
                if len(ch) == 1 or ch.startswith("Num"):
                    if not self._typing:
                        self._typing_started = raw.ts
                        self._typing_window = self._window_dict(self._latest)
                    self._typing.append(ch if raw.mods & {"Shift"} else ch.lower())
                    self._typing_last = time.time()
            return
        self._flush_typing()
        if raw.kind in ("click", "rclick", "mclick"):
            t, lx, ly = self._last_click
            if raw.kind == "click" and (raw.ts - t) < self.DOUBLE_CLICK_S and abs(raw.x - lx) < 6 and abs(raw.y - ly) < 6 and rec.events:
                rec.events[-1].kind = "dblclick"
                self._last_click = (raw.ts, raw.x, raw.y)
                return
            self._last_click = (raw.ts, raw.x, raw.y)
            self._add_event(raw.kind, raw)
        elif raw.kind == "scroll":
            if rec.events and rec.events[-1].kind == "scroll" and raw.ts - rec.events[-1].ts < 1.0:
                return  # collapse a wheel burst into one event
            self._add_event("scroll", raw, keys="down" if raw.delta < 0 else "up")

    def _window_dict(self, frame) -> dict:
        try:
            return frame.window.as_dict() if frame is not None else self.vision.user_window().as_dict()
        except Exception:
            return {}

    def _flush_typing(self, final: bool = False) -> None:
        if not self._typing or self.recording is None:
            return
        text = "".join(self._typing)
        ev = TraceEvent(i=len(self.recording.events), kind="type", ts=round(self._typing_started, 2), chars=len(text),
                        text=text if self.settings.get("teach_capture_text") else "", window_before=self._typing_window)
        self._typing = []
        self.recording.events.append(ev)
        self._notify(ev)

    def _add_event(self, kind: str, raw: _Raw, keys: str = "") -> None:
        rec = self.recording
        before = self._latest
        ev = TraceEvent(i=len(rec.events), kind=kind, ts=round(raw.ts, 2), x=raw.x, y=raw.y, keys=keys)
        ev.window_before = self._window_dict(before)
        rect = tuple(ev.window_before.get("rect") or (0, 0, 0, 0))
        if kind in ("click", "rclick", "mclick", "dblclick") and before is not None:
            try:
                ev.label, ev.label_box = label_under((raw.x, raw.y), before.ocr(), rect if rect[2] > rect[0] else None)
            except Exception as e:
                log.debug("ocr label: %s", e)
            ev.region, ev.rel = region_of((raw.x, raw.y), rect)
        if self.save_images and before is not None:
            ev.before_image = self._save_thumb(before, f"{ev.i:03d}_before.jpg", (raw.x, raw.y) if raw.x or raw.y else None)
        # what changed because of the action
        time.sleep(self.AFTER_DELAY_S)
        after = self._safe_capture()
        if after is not None:
            self._latest = after
            ev.window_after = after.window.as_dict()
            try:
                b_lines = before.window_lines() if before is not None else []
                a_lines = after.window_lines()
                ev.appeared, ev.disappeared = ocr_delta(b_lines, a_lines)
            except Exception as e:
                log.debug("ocr delta: %s", e)
            if self.save_images and (ev.appeared or ev.disappeared or kind != "scroll"):
                ev.after_image = self._save_thumb(after, f"{ev.i:03d}_after.jpg")
        rec.events.append(ev)
        self._notify(ev)

    def _save_thumb(self, frame, name: str, marker: Optional[tuple[int, int]] = None) -> str:
        try:
            from PIL import Image, ImageDraw
            img = frame.image
            if marker:
                img = img.copy()
                d = ImageDraw.Draw(img)
                x, y = marker
                d.ellipse((x - 14, y - 14, x + 14, y + 14), outline=(255, 40, 90), width=4)
                d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 40, 90))
            w, h = img.size
            s = max(w, h) / float(self.thumb_side)
            if s > 1.0:
                img = img.resize((int(w / s), int(h / s)), Image.BILINEAR)
            d = self.base_dir / self.recording.id
            d.mkdir(parents=True, exist_ok=True)
            img.save(d / name, "JPEG", quality=72)
            return name
        except Exception as e:
            log.debug("thumb failed: %s", e)
            return ""

    def _notify(self, ev: TraceEvent) -> None:
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass
