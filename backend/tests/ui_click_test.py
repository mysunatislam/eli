"""Guarded end-to-end UI test for the overlay.

It touches the real mouse/keyboard, so it refuses to run until the PC has been idle for a while,
restores the cursor afterwards, and only types into Eli's panel after the overlay's own log
confirms the panel opened (so text can never land in another app).

Run the backend and `ELI_DEBUG=1 npm start` (log redirected to backend/data/electron.log), then:
    .venv\\Scripts\\python.exe tests\\ui_click_test.py
"""
import ctypes
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pyautogui

LOG = Path(__file__).resolve().parents[1] / "data" / "electron.log"
IDLE_NEEDED = 45.0
MAX_WAIT = 600.0
pyautogui.FAILSAFE = False


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0


def log_text() -> str:
    try:
        return LOG.read_text("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def main() -> int:
    m = re.search(r"bounds (\d+) (\d+) (\d+) (\d+) scale ([\d.]+)", log_text())
    if not m:
        print("overlay bounds not found in electron.log (start the overlay with ELI_DEBUG=1)")
        return 2
    wx, wy, ww, wh = map(int, m.groups()[:4])
    scale = float(m.group(5))
    heart = (int((wx + ww - 8 - 160 + 80) * scale), int((wy + wh - 8 - 160 + 86) * scale))
    print(f"overlay at {wx},{wy} {ww}x{wh} scale {scale}; heart centre {heart}")

    t0 = time.time()
    while idle_seconds() < IDLE_NEEDED:
        if time.time() - t0 > MAX_WAIT:
            print("PC never went idle; not touching the mouse.")
            return 3
        time.sleep(5)
    print(f"idle for {idle_seconds():.0f}s — running")

    before = log_text()
    origin = pyautogui.position()
    pyautogui.moveTo(heart[0] - 30, heart[1] - 30, duration=0.25)
    pyautogui.moveTo(heart[0], heart[1], duration=0.25)
    time.sleep(0.35)
    pyautogui.click(heart[0], heart[1])
    time.sleep(1.0)
    after = log_text()[len(before):]
    clicked = "[heart] mousedown" in after
    opened = "[panel] open" in after
    print("hit-toggle seen:", "[main] hit true" in after, "| mousedown:", clicked, "| panel opened:", opened)

    calc = False
    if opened:
        pyautogui.typewrite("open calculator", interval=0.03)
        pyautogui.press("enter")
        time.sleep(5)
        out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
        calc = "calculator" in out or "calc.exe" in out
        print("calculator running:", calc)
        pyautogui.press("escape")  # closes the panel
        subprocess.run(["taskkill", "/IM", "CalculatorApp.exe", "/F"], capture_output=True)
        subprocess.run(["taskkill", "/IM", "Calculator.exe", "/F"], capture_output=True)
    pyautogui.moveTo(origin.x, origin.y, duration=0.2)
    ok = clicked and opened and calc
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
