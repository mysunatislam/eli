"""Global Win32 Hotkey Listener for Eli.

Registers Ctrl+Space (and Alt+Space) as system-wide two-button listening hotkeys.
Pressing the hotkey toggles Push-To-Talk / Listening mode from anywhere in Windows,
with instant audio tone feedback.
"""
import ctypes
from ctypes import wintypes
import logging
import os
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("eli.speech.hotkey")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20

HOTKEY_ID_CTRL_SPACE = 8791
HOTKEY_ID_ALT_SPACE = 8792


class GlobalHotkeyListener:
    def __init__(self, on_trigger: Callable[[], None]):
        self.on_trigger = on_trigger
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if os.name != "nt":
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="eli-global-hotkey", daemon=True)
        self._thread.start()
        log.info("Global hotkey listener started (Ctrl+Space / Alt+Space)")

    def stop(self) -> None:
        if os.name != "nt":
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            try:
                user32 = ctypes.windll.user32
                user32.PostThreadMessageW(self._thread.ident, 0x0012, 0, 0)
            except Exception:
                pass
            self._thread.join(timeout=1.0)
        self._thread = None
        log.info("Global hotkey listener stopped")

    def _run_loop(self) -> None:
        user32 = ctypes.windll.user32

        flags_ctrl = MOD_CONTROL | getattr(ctypes, "MOD_NOREPEAT", 0x4000)
        flags_alt = MOD_ALT | getattr(ctypes, "MOD_NOREPEAT", 0x4000)

        ok_ctrl = user32.RegisterHotKey(None, HOTKEY_ID_CTRL_SPACE, flags_ctrl, VK_SPACE)
        ok_alt = user32.RegisterHotKey(None, HOTKEY_ID_ALT_SPACE, flags_alt, VK_SPACE)

        if not ok_ctrl and not ok_alt:
            log.warning("Could not register Win32 global hotkeys (Ctrl+Space / Alt+Space may already be registered by Electron)")
            return

        log.info("Registered Win32 global hotkeys: Ctrl+Space (ok=%s), Alt+Space (ok=%s)", bool(ok_ctrl), bool(ok_alt))

        msg = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                has_msg = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if has_msg <= 0:
                    break
                if msg.message == 0x0312:  # WM_HOTKEY
                    hotkey_id = msg.wParam
                    if hotkey_id in (HOTKEY_ID_CTRL_SPACE, HOTKEY_ID_ALT_SPACE):
                        log.info("Global hotkey triggered (id=%d) -> toggle listening", hotkey_id)
                        try:
                            if self.on_trigger:
                                self.on_trigger()
                        except Exception as e:
                            log.exception("Error in hotkey callback: %s", e)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if ok_ctrl:
                user32.UnregisterHotKey(None, HOTKEY_ID_CTRL_SPACE)
            if ok_alt:
                user32.UnregisterHotKey(None, HOTKEY_ID_ALT_SPACE)
            log.info("Unregistered Win32 global hotkeys")
