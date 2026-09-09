"""Automation Agent: mouse, keyboard, app launching, window switching.

Everything here acts on the real desktop, so every public method returns a short
human-readable result string and never raises for expected failures.
Risk classification lives in `risk_of()`; the executor uses it to gate actions
behind a user confirmation.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

log = logging.getLogger("eli.automation")

try:
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = True   # slam the mouse into the top-left corner to abort any automation
    pyautogui.PAUSE = 0.04
except Exception as e:  # pragma: no cover
    pyautogui = None
    log.warning("pyautogui unavailable: %s", e)

try:
    import pyperclip  # type: ignore
except Exception:
    pyperclip = None

try:
    import pygetwindow as gw  # type: ignore
except Exception:
    gw = None

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
PROGRAMFILES = os.environ.get("PROGRAMFILES", r"C:\Program Files")
PROGRAMFILES86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")

# name -> list of candidate launch commands (first that exists/launches wins)
APP_COMMANDS: dict[str, list[list[str]]] = {
    "chrome": [[f"{PROGRAMFILES}\\Google\\Chrome\\Application\\chrome.exe"], [f"{PROGRAMFILES86}\\Google\\Chrome\\Application\\chrome.exe"], ["chrome"]],
    "vs code": [[f"{LOCALAPPDATA}\\Programs\\Microsoft VS Code\\Code.exe"], [f"{PROGRAMFILES}\\Microsoft VS Code\\Code.exe"], ["code"]],
    "notepad": [["notepad"]],
    "file explorer": [["explorer"]],
    "edge": [["msedge"]],
    "firefox": [["firefox"]],
    "calculator": [["calc"]],
    "terminal": [["wt"], ["cmd"]],
    "command prompt": [["cmd"]],
    "powershell": [["powershell"]],
    "paint": [["mspaint"]],
    "word": [["winword"]],
    "excel": [["excel"]],
    "powerpoint": [["powerpnt"]],
    "outlook": [["outlook"]],
    "settings": [["ms-settings:"]],
    "task manager": [["taskmgr"]],
    "snipping tool": [["snippingtool"]],
    "spotify": [["spotify"]],
    "discord": [[f"{LOCALAPPDATA}\\Discord\\Update.exe", "--processStart", "Discord.exe"], ["discord"]],
    "slack": [["slack"]],
    "blender": [["blender"]],
    "fusion 360": [[f"{LOCALAPPDATA}\\Autodesk\\webdeploy\\production\\Autodesk Fusion 360.exe"], ["fusion360"]],
}
APP_ALIASES = {
    "google chrome": "chrome", "browser": "chrome", "the browser": "chrome",
    "vscode": "vs code", "code": "vs code", "visual studio code": "vs code", "the editor": "vs code",
    "explorer": "file explorer", "files": "file explorer", "my files": "file explorer",
    "microsoft edge": "edge", "calc": "calculator", "cmd": "command prompt", "windows terminal": "terminal",
    "ms word": "word", "microsoft word": "word", "ms excel": "excel", "microsoft excel": "excel",
    "ms paint": "paint", "fusion": "fusion 360", "fusion360": "fusion 360",
}
# Window-title keywords used to confirm an app came up / to focus it
APP_WINDOW_KEYWORDS = {
    "chrome": "Chrome", "vs code": "Visual Studio Code", "notepad": "Notepad", "file explorer": "File Explorer",
    "edge": "Edge", "calculator": "Calculator", "terminal": "Terminal", "command prompt": "Command Prompt",
    "powershell": "PowerShell", "paint": "Paint", "word": "Word", "excel": "Excel", "outlook": "Outlook",
}
URL_SHORTCUTS = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
    "github": "https://github.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "reddit": "https://www.reddit.com", "twitter": "https://x.com", "x": "https://x.com",
    "linkedin": "https://www.linkedin.com", "netflix": "https://www.netflix.com", "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com", "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "google docs": "https://docs.google.com", "docs": "https://docs.google.com", "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com", "stack overflow": "https://stackoverflow.com", "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.com", "notion": "https://www.notion.so", "figma": "https://www.figma.com",
    "google calendar": "https://calendar.google.com", "calendar": "https://calendar.google.com",
}
KEY_ALIASES = {"return": "enter", "cmd": "win", "windows": "win", "super": "win", "esc": "escape", "control": "ctrl",
               "option": "alt", "del": "delete", "pgup": "pageup", "pgdn": "pagedown", "spacebar": "space"}

MESSAGING_TITLES = re.compile(r"(gmail|mail|outlook|whatsapp|slack|discord|teams|telegram|messenger|signal|inbox)", re.I)
DANGEROUS_CLICK = re.compile(r"^(send|submit|pay|buy( now)?|purchase|place order|delete|confirm|remove|uninstall|format|reply all)$", re.I)


def normalize_app(name: str) -> str:
    key = name.strip().lower().rstrip(".!?")
    key = re.sub(r"^(the |my |up )", "", key)
    return APP_ALIASES.get(key, key)


def _exists(cmd: list[str]) -> bool:
    return bool(cmd) and (Path(cmd[0]).is_file() or not cmd[0].endswith(".exe"))


class AutomationAgent:
    def __init__(self, vision=None):
        self.vision = vision  # for OCR-grounded clicks

    # -- risk ----------------------------------------------------------------------------
    def risk_of(self, tool: str, args: dict, active_title: str = "") -> str:
        """'low' runs immediately; 'high' waits for the user's OK."""
        if tool == "run_command":
            return "high"
        if tool == "press_keys":
            keys = str(args.get("keys", "")).lower()
            if "enter" in keys and MESSAGING_TITLES.search(active_title or ""):
                return "high"
        if tool == "type_text" and args.get("press_enter") and MESSAGING_TITLES.search(active_title or ""):
            return "high"
        if tool == "click_text" and DANGEROUS_CLICK.match(str(args.get("text", "")).strip()):
            return "high"
        return "low"

    # -- apps ---------------------------------------------------------------------------
    def open_app(self, name: str) -> str:
        key = normalize_app(name)
        if key in URL_SHORTCUTS:
            webbrowser.open(URL_SHORTCUTS[key])
            return f"Opened {key} in your browser."
        if key.startswith(("http://", "https://")) or re.match(r"^[\w.-]+\.(com|org|net|io|ai|dev|edu|gov)(/.*)?$", key):
            url = key if key.startswith("http") else "https://" + key
            webbrowser.open(url)
            return f"Opened {url}."
        cmds = APP_COMMANDS.get(key)
        if cmds:
            for cmd in cmds:
                if not _exists(cmd):
                    continue
                if self._launch(cmd):
                    seen = self._wait_for_window(APP_WINDOW_KEYWORDS.get(key, key), timeout=5.0)
                    return f"Opened {key}." if seen else f"Launched {key}; its window should appear in a moment."
            return f"I couldn't launch {key}. It may not be installed."
        # Unknown app: let the shell resolve it (App Paths, PATH, Start-menu names)
        if self._launch([name]):
            seen = self._wait_for_window(name, timeout=3.0)
            return f"Opened {name}." if seen else f"Launched {name}; I didn't see a window with that name yet."
        return f"I don't know how to open '{name}'. Try the exact program name."

    def _launch(self, cmd: list[str]) -> bool:
        """Fire-and-forget launch. Never wait on the child's stdio: GUI apps inherit pipes and would block us."""
        quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            if Path(cmd[0]).is_file():
                subprocess.Popen(cmd, **quiet)
                return True
            if len(cmd) == 1:
                try:
                    os.startfile(cmd[0])  # ShellExecute: resolves App Paths (chrome), PATH (notepad), protocols (ms-settings:)
                    return True
                except OSError as e:
                    log.info("startfile %s failed: %s", cmd[0], e)
            # `start` handles arguments and Start-menu style names
            subprocess.Popen(["cmd", "/c", "start", "", *cmd], **quiet)
            return True
        except Exception as e:
            log.info("launch %s failed: %s", cmd, e)
            return False

    def _wait_for_window(self, keyword: str, timeout: float = 3.0) -> bool:
        if gw is None:
            time.sleep(min(timeout, 1.5))
            return True
        end = time.time() + timeout
        kw = keyword.lower()
        while time.time() < end:
            try:
                if any(kw in (w.title or "").lower() for w in gw.getAllWindows()):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def open_url(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)
        return f"Opened {url}."

    def web_search(self, query: str, engine: str = "google") -> str:
        q = urllib.parse.quote_plus(query)
        urls = {
            "google": f"https://www.google.com/search?q={q}",
            "youtube": f"https://www.youtube.com/results?search_query={q}",
            "bing": f"https://www.bing.com/search?q={q}",
            "duckduckgo": f"https://duckduckgo.com/?q={q}",
        }
        url = urls.get(engine.lower(), urls["google"])
        webbrowser.open(url)
        return f"Searched {engine} for '{query}'."

    def compose_email(self, to: str = "", subject: str = "", body: str = "") -> str:
        """Opens a Gmail compose window with fields pre-filled. Never sends."""
        params = {"view": "cm", "fs": "1"}
        if to:
            params["to"] = to
        if subject:
            params["su"] = subject
        if body:
            params["body"] = body
        webbrowser.open("https://mail.google.com/mail/?" + urllib.parse.urlencode(params))
        who = f" to {to}" if to else ""
        return f"Opened a Gmail draft{who}. I stopped before sending — review it and press Send yourself."

    # -- windows --------------------------------------------------------------------------
    def list_windows(self, limit: int = 25) -> list[str]:
        if gw is None:
            return []
        titles = []
        try:
            for w in gw.getAllWindows():
                t = (w.title or "").strip()
                if t and t not in titles:
                    titles.append(t + (" (minimized)" if w.isMinimized else ""))
        except Exception:
            pass
        return titles[:limit]

    def focus_window(self, title: str) -> str:
        if gw is None:
            return "Window switching isn't available (pygetwindow missing)."
        needle = title.lower().strip()
        try:
            wins = [w for w in gw.getAllWindows() if needle in (w.title or "").lower() and w.title]
        except Exception as e:
            return f"Couldn't list windows: {e}"
        if not wins:
            return f"No window matching '{title}'. Open windows: {', '.join(self.list_windows(10)) or 'none'}"
        w = wins[0]
        try:
            if w.isMinimized:
                w.restore()
            try:
                w.activate()
            except Exception:
                # pygetwindow sometimes raises even when activation worked; use the Win32 fallback
                self._force_foreground(w._hWnd)
            time.sleep(0.3)
            return f"Switched to '{w.title}'."
        except Exception as e:
            return f"Couldn't focus '{w.title}': {e}"

    def _force_foreground(self, hwnd: int) -> None:
        user32 = ctypes.windll.user32
        if pyautogui:
            pyautogui.press("alt")  # unlocks SetForegroundWindow for this process
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)

    # -- keyboard / mouse ----------------------------------------------------------------------
    def type_text(self, text: str, press_enter: bool = False) -> str:
        if pyautogui is None:
            return "Typing isn't available (pyautogui missing)."
        if text.isascii():
            pyautogui.write(text, interval=0.012)
        elif pyperclip is not None:
            old = None
            try:
                old = pyperclip.paste()
            except Exception:
                pass
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.2)
            if old is not None:
                try:
                    pyperclip.copy(old)
                except Exception:
                    pass
        else:
            pyautogui.write(text.encode("ascii", "ignore").decode(), interval=0.012)
        if press_enter:
            pyautogui.press("enter")
        preview = text if len(text) <= 60 else text[:57] + "..."
        return f"Typed: {preview}" + (" and pressed Enter." if press_enter else "")

    def press_keys(self, keys: str) -> str:
        if pyautogui is None:
            return "Key presses aren't available (pyautogui missing)."
        results = []
        for combo in re.split(r"[,\s]+then[,\s]+|;", keys.strip()):
            parts = [KEY_ALIASES.get(p.strip().lower(), p.strip().lower()) for p in combo.split("+") if p.strip()]
            if not parts:
                continue
            if len(parts) == 1:
                pyautogui.press(parts[0])
            else:
                pyautogui.hotkey(*parts)
            results.append("+".join(parts))
            time.sleep(0.15)
        return "Pressed " + ", ".join(results) + "." if results else "No keys given."

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> str:
        if pyautogui is None:
            return "Clicking isn't available."
        if double:
            pyautogui.doubleClick(x, y, button=button)
        else:
            pyautogui.click(x, y, button=button)
        return f"{'Double-clicked' if double else 'Clicked'} at ({x}, {y})."

    def click_text(self, text: str, double: bool = False) -> str:
        if self.vision is None:
            return "click_text needs the vision agent."
        hit = self.vision.find_text(text)
        if not hit:
            return f"I couldn't find '{text}' on the screen."
        x, y, matched = hit
        self.click(x, y, double=double)
        return f"Clicked '{matched}' at ({x}, {y})."

    def move_mouse(self, x: int, y: int) -> str:
        if pyautogui is None:
            return "Mouse control isn't available."
        pyautogui.moveTo(x, y, duration=0.2)
        return f"Moved the mouse to ({x}, {y})."

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None) -> str:
        if pyautogui is None:
            return "Scrolling isn't available."
        # pyautogui on Windows sends the raw wheel delta; 120 = one notch
        clicks = int(amount) * 120
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x=x, y=y)
        else:
            pyautogui.scroll(clicks)
        return f"Scrolled {'up' if amount > 0 else 'down'} {abs(int(amount))} notches."

    # -- clipboard / shell ------------------------------------------------------------------
    def get_clipboard(self) -> str:
        if pyperclip is None:
            return ""
        try:
            return pyperclip.paste() or ""
        except Exception:
            return ""

    def set_clipboard(self, text: str) -> str:
        if pyperclip is None:
            return "Clipboard isn't available."
        pyperclip.copy(text)
        return f"Copied {len(text)} characters to the clipboard."

    def run_command(self, command: str, timeout: int = 30, cwd: Optional[str] = None) -> str:
        """Runs a user-approved shell command (always confirmation-gated by the executor)."""
        if cwd and not Path(cwd).is_dir():
            return f"Folder not found: {cwd}"
        try:
            r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd or None,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            out = out.strip()
            return f"exit {r.returncode}\n{out[-4000:]}" if out else f"exit {r.returncode} (no output)"
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s."
        except Exception as e:
            return f"Command failed: {e}"

    def wait(self, seconds: float) -> str:
        s = max(0.0, min(float(seconds), 5.0))
        time.sleep(s)
        return f"Waited {s:.1f}s."
