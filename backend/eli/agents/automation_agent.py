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
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

if os.name == "nt":
    from ctypes import wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("u", _INPUT_UNION),
        ]

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

log = logging.getLogger("eli.automation")

def get_user_idle_seconds() -> float:
    """Return how many seconds have passed since the user last moved the mouse or pressed a key."""
    if os.name != "nt":
        return 999.0
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis / 1000.0)
    except Exception:
        pass
    return 999.0

def get_foreground_window_info() -> tuple[str, str]:
    """Return (title, exe_path) of current foreground/active window."""
    ensure_interactive_desktop()
    if os.name != "nt":
        return ("", "")
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ("", "")
        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        if length > 0:
            user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pname = ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        hProcess = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if hProcess:
            buf_len = wintypes.DWORD(1024)
            pbuf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(hProcess, 0, pbuf, ctypes.byref(buf_len)):
                pname = pbuf.value
            kernel32.CloseHandle(hProcess)
        return (title, pname)
    except Exception:
        return ("", "")


def get_foreground_window_title() -> str:
    """Return the title of the current foreground/active window."""
    title, _ = get_foreground_window_info()
    return title

def ensure_interactive_desktop() -> bool:
    """Ensure the calling thread is attached to the active user desktop (Default)."""
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
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = False
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
    "matlab": [
        [r"E:\Matlab911Win\bin\matlab.exe", "-desktop"],
        [f"{PROGRAMFILES}\\MATLAB\\R2021b\\bin\\matlab.exe", "-desktop"],
        [f"{PROGRAMFILES}\\MATLAB\\R2022a\\bin\\matlab.exe", "-desktop"],
        [f"{PROGRAMFILES}\\MATLAB\\R2022b\\bin\\matlab.exe", "-desktop"],
        [f"{PROGRAMFILES}\\MATLAB\\R2023a\\bin\\matlab.exe", "-desktop"],
        [f"{PROGRAMFILES}\\MATLAB\\R2023b\\bin\\matlab.exe", "-desktop"],
        [f"{PROGRAMFILES}\\MATLAB\\R2024a\\bin\\matlab.exe", "-desktop"],
        ["matlab", "-desktop"],
    ],
}
APP_ALIASES = {
    "google chrome": "chrome", "browser": "chrome", "the browser": "chrome",
    "vscode": "vs code", "code": "vs code", "visual studio code": "vs code", "the editor": "vs code",
    "explorer": "file explorer", "files": "file explorer", "my files": "file explorer",
    "microsoft edge": "edge", "calc": "calculator", "cmd": "command prompt", "windows terminal": "terminal",
    "ms word": "word", "microsoft word": "word", "ms excel": "excel", "microsoft excel": "excel",
    "ms paint": "paint", "fusion": "fusion 360", "fusion360": "fusion 360",
    "matlab app": "matlab", "matlab r2021b": "matlab", "mathworks matlab": "matlab",
}
# Window-title keywords used to confirm an app came up / to focus it
APP_WINDOW_KEYWORDS = {
    "chrome": "Chrome", "vs code": "Visual Studio Code", "notepad": "Notepad", "file explorer": "File Explorer",
    "edge": "Edge", "calculator": "Calculator", "terminal": "Terminal", "command prompt": "Command Prompt",
    "powershell": "PowerShell", "paint": "Paint", "word": "Word", "excel": "Excel", "outlook": "Outlook",
    "matlab": "MATLAB",
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
    def __init__(self, vision=None, settings=None):
        self.vision = vision  # for OCR-grounded clicks
        self.settings = settings
        self.is_media_playing = False

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
        ensure_interactive_desktop()
        if gw is None:
            time.sleep(min(timeout, 1.5))
            return True
        end = time.time() + timeout
        kw = keyword.lower()
        while time.time() < end:
            try:
                if any(kw in (w.title or "").lower() for w in gw.getAllWindows()):
                    return True
                fg = get_foreground_window_title().lower()
                if kw in fg:
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        # Fallback: check if process is running
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                if kw in (p.info.get("name") or "").lower():
                    return True
        except Exception:
            pass
        return False

    def open_url(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)
        return f"Opened {url}."

    def web_search(self, query: str, engine: str = "google", open_chrome: bool = False, browser: str = "chrome") -> str:
        q = urllib.parse.quote_plus(query)
        urls = {
            "google": f"https://www.google.com/search?q={q}",
            "youtube": f"https://www.youtube.com/results?search_query={q}",
            "bing": f"https://www.bing.com/search?q={q}",
            "duckduckgo": f"https://duckduckgo.com/?q={q}",
        }
        url = urls.get(engine.lower(), urls["google"])

        if browser.lower() in ("edge", "microsoft edge", "msedge"):
            self.visual_open_edge(url, query=query)
            return f"Opened Microsoft Edge in full screen and searched for '{query}' on Google."

        if open_chrome or engine.lower() == "google" or browser.lower() in ("chrome", "google chrome"):
            self.visual_open_chrome(url, query=query)
            return f"Opened Google Chrome in full screen and searched for '{query}' on Google."

        webbrowser.open(url)
        return f"Searched {engine} for '{query}'."

    def visual_open_chrome(self, url: str, query: str = "") -> bool:
        """Visibly moves cursor towards taskbar / Google Chrome, launches Chrome detached from Antigravity,
        brings the browser to the active foreground in full screen (SW_MAXIMIZE), and moves cursor to search bar."""
        ensure_interactive_desktop()
        sw, sh = 1920, 1080
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                sw = user32.GetSystemMetrics(0)
                sh = user32.GetSystemMetrics(1)
            except Exception:
                pass

        # 1. Visual mouse glide to taskbar (NEVER click blindly to avoid minimizing Antigravity/active apps!)
        target_x = sw // 2 - 60
        target_y = sh - 24
        clicked = False
        if self.vision:
            try:
                hit = self.vision.find_text("Chrome")
                if hit:
                    target_x, target_y = hit[0], hit[1]
                    self.move_mouse(target_x, target_y)
                    time.sleep(0.15)
                    self.click(target_x, target_y)
                    clicked = True
            except Exception:
                pass

        if not clicked:
            try:
                self.move_mouse(target_x, target_y)
            except Exception:
                pass

        # 2. Chrome executable path
        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
        ]
        chrome_exe = next((p for p in chrome_candidates if os.path.exists(p)), None)

        opened = False
        if chrome_exe:
            try:
                subprocess.Popen(
                    [chrome_exe, "--no-first-run", "--no-default-browser-check", "--start-maximized", "--new-window", url],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                opened = True
                log.info("Launched Google Chrome: %s", url)
            except Exception as e:
                log.debug("direct chrome launch failed: %s; trying os.startfile", e)
                try:
                    os.startfile(url)
                    opened = True
                except Exception:
                    pass

        if not opened:
            try:
                webbrowser.open(url)
            except Exception:
                pass

        # 3. Bring window to active foreground in full screen (SW_MAXIMIZE)
        time.sleep(1.0)
        self.activate_chrome(maximize=True)

        # 4. Visually glide cursor smoothly to Chrome's search / address bar
        try:
            search_x = sw // 2
            search_y = 130
            self.move_mouse(search_x, search_y)
        except Exception:
            pass

        return True

    def activate_chrome(self, maximize: bool = True) -> bool:
        """Finds only genuine Google Chrome windows (strictly chrome.exe, excluding Antigravity and Electron dummy windows),
        restores/maximizes it into full screen, and brings it to the foreground."""
        ensure_interactive_desktop()
        try:
            import psutil
            user32 = ctypes.windll.user32
            found_hwnds = []

            def enum_cb(hwnd, extra):
                if not user32.IsWindowVisible(hwnd):
                    return True
                cls_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_name, 256)
                if cls_name.value != "Chrome_WidgetWin_1":
                    return True

                # Must be genuine chrome.exe - NEVER Antigravity.exe, Code.exe or electron.exe!
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    proc = psutil.Process(pid.value)
                    if proc.name().lower() != "chrome.exe":
                        return True
                except Exception:
                    return True

                # Must have a non-empty window title
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                t = buff.value.lower()
                if any(bad in t for bad in ("eli", "antigravity", "visual studio")):
                    return True

                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if (rect.right - rect.left) < 200 or (rect.bottom - rect.top) < 200:
                    return True

                found_hwnds.append(hwnd)
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            for _ in range(5):
                user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
                if found_hwnds:
                    break
                time.sleep(0.25)

            if found_hwnds:
                hwnd = found_hwnds[0]
                self._force_foreground(hwnd, maximize=maximize)
                log.info("Force activated genuine Google Chrome window hwnd=%d to foreground (maximized=%s)", hwnd, maximize)
                return True
        except Exception as e:
            log.debug("activate_chrome failed: %s", e)
        try:
            if gw is not None:
                for w in gw.getAllWindows():
                    t = (w.title or "").lower()
                    if ("chrome" in t or "youtube" in t) and not any(bad in t for bad in ("eli", "antigravity", "visual studio")):
                        if w.isMinimized:
                            w.restore()
                        if maximize:
                            try:
                                w.maximize()
                            except Exception:
                                pass
                        w.activate()
                        self._force_foreground(w._hWnd, maximize=maximize)
                        return True
        except Exception:
            pass
        return False

    def visual_open_edge(self, url: str, query: str = "") -> bool:
        """Visibly moves the cursor towards taskbar / Microsoft Edge, launches Edge detached from Antigravity,
        brings the browser to the active foreground in full screen (SW_MAXIMIZE), and moves cursor to search bar."""
        ensure_interactive_desktop()
        sw, sh = 1920, 1080
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                sw = user32.GetSystemMetrics(0)
                sh = user32.GetSystemMetrics(1)
            except Exception:
                pass

        # 1. Visual mouse glide to taskbar (NEVER click blindly to avoid minimizing Antigravity/active apps!)
        target_x = sw // 2 - 20
        target_y = sh - 24
        clicked = False
        if self.vision:
            try:
                hit = self.vision.find_text("Edge")
                if hit:
                    target_x, target_y = hit[0], hit[1]
                    self.move_mouse(target_x, target_y)
                    time.sleep(0.15)
                    self.click(target_x, target_y)
                    clicked = True
            except Exception:
                pass

        if not clicked:
            try:
                self.move_mouse(target_x, target_y)
            except Exception:
                pass

        # 2. Edge executable candidates
        edge_candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe")
        ]
        edge_exe = next((p for p in edge_candidates if os.path.exists(p)), None)

        opened = False
        if edge_exe:
            try:
                subprocess.Popen(
                    [edge_exe, "--no-first-run", "--no-default-browser-check", "--start-maximized", "--new-window", url],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                opened = True
                log.info("Launched Microsoft Edge directly: %s", url)
            except Exception as e:
                log.debug("direct edge launch failed: %s; trying cmd start", e)
                try:
                    subprocess.Popen(
                        ["cmd.exe", "/c", "start", "", edge_exe, "--no-first-run", "--no-default-browser-check", "--start-maximized", "--new-window", url]
                    )
                    opened = True
                except Exception:
                    pass

        if not opened:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", f"microsoft-edge:{url}"])
            except Exception:
                pass

        time.sleep(1.2)
        self.activate_edge(maximize=True)

        try:
            search_x = sw // 2
            search_y = 130
            self.move_mouse(search_x, search_y)
        except Exception:
            pass

        return True

    def activate_edge(self, maximize: bool = True) -> bool:
        """Finds only genuine Microsoft Edge windows (strictly msedge.exe),
        restores/maximizes it into full screen, and brings it to the foreground."""
        ensure_interactive_desktop()
        try:
            import psutil
            user32 = ctypes.windll.user32
            found_hwnds = []

            def enum_cb(hwnd, extra):
                if not user32.IsWindowVisible(hwnd):
                    return True
                cls_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_name, 256)
                if cls_name.value != "Chrome_WidgetWin_1":
                    return True

                # Must be genuine msedge.exe - NEVER Antigravity.exe, Code.exe or electron.exe!
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    proc = psutil.Process(pid.value)
                    if proc.name().lower() != "msedge.exe":
                        return True
                except Exception:
                    return True

                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                t = buff.value.lower()
                if any(bad in t for bad in ("eli", "antigravity", "visual studio")):
                    return True

                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if (rect.right - rect.left) < 200 or (rect.bottom - rect.top) < 200:
                    return True

                found_hwnds.append(hwnd)
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            for _ in range(6):
                user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
                if found_hwnds:
                    break
                time.sleep(0.3)

            if found_hwnds:
                hwnd = found_hwnds[0]
                self._force_foreground(hwnd, maximize=maximize)
                log.info("Force activated genuine Microsoft Edge window hwnd=%d to foreground (maximized=%s)", hwnd, maximize)
                return True
        except Exception as e:
            log.debug("activate_edge failed: %s", e)
        try:
            if gw is not None:
                for w in gw.getAllWindows():
                    t = (w.title or "").lower()
                    if "edge" in t and not any(bad in t for bad in ("eli", "antigravity", "visual studio")):
                        if w.isMinimized:
                            w.restore()
                        if maximize:
                            try:
                                w.maximize()
                            except Exception:
                                pass
                        w.activate()
                        self._force_foreground(w._hWnd, maximize=maximize)
                        return True
        except Exception:
            pass
        return False

    def activate_vscode(self, maximize: bool = True) -> bool:
        """Finds genuine Visual Studio Code windows (code.exe, excluding Antigravity and Eli),
        restores/maximizes it into full screen, and brings it to the foreground."""
        ensure_interactive_desktop()
        try:
            import psutil
            user32 = ctypes.windll.user32
            found_hwnds = []

            def enum_cb(hwnd, extra):
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    proc = psutil.Process(pid.value)
                    pname = proc.name().lower()
                    if pname != "code.exe" and not pname.startswith("code"):
                        return True
                except Exception:
                    return True

                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                t = buff.value.lower()
                if any(bad in t for bad in ("eli", "antigravity")):
                    return True

                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if (rect.right - rect.left) < 200 or (rect.bottom - rect.top) < 200:
                    return True

                found_hwnds.append(hwnd)
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            for _ in range(6):
                user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
                if found_hwnds:
                    break
                time.sleep(0.3)

            if found_hwnds:
                hwnd = found_hwnds[0]
                self._force_foreground(hwnd, maximize=maximize)
                log.info("Force activated Visual Studio Code window hwnd=%d to foreground (maximized=%s)", hwnd, maximize)
                return True
        except Exception as e:
            log.debug("activate_vscode failed: %s", e)
        try:
            if gw is not None:
                for w in gw.getAllWindows():
                    t = (w.title or "").lower()
                    if ("visual studio code" in t or " - code" in t) and not any(bad in t for bad in ("eli", "antigravity")):
                        if w.isMinimized:
                            w.restore()
                        if maximize:
                            try:
                                w.maximize()
                            except Exception:
                                pass
                        w.activate()
                        self._force_foreground(w._hWnd, maximize=maximize)
                        return True
        except Exception:
            pass
        return False

    def play_youtube(self, query: str) -> str:
        clean = re.sub(r"^(?:play|search for|listen to|play music|play the music|the music|music|the song|song|a music|a video of among them|video of among them|video|video of)\s+", "", query, flags=re.I).strip()
        clean = re.sub(r"\s+(?:and play a video of among them|and play a video|and play it|and play|please|video)$", "", clean, flags=re.I).strip()
        clean = clean or query
        if not clean or clean.lower() in ("music", "song", "youtube", "relaxing music", "any music", "some music", "enemy music", "a music", "youtube music"):
            clean = "relaxing music"

        # 1. Resolve direct YouTube watch URL so playback actually starts
        watch_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean)}"
        try:
            req = urllib.request.Request(
                watch_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            html = urllib.request.urlopen(req, timeout=4.0).read().decode("utf-8", errors="ignore")
            vids = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', html)
            if vids:
                watch_url = f"https://www.youtube.com/watch?v={vids[0]}"
                log.info("Resolved direct YouTube watch URL for %r: %s", clean, watch_url)
        except Exception as e:
            log.debug("youtube video id scrape fallback: %s", e)

        # 2. Launch directly in Google Chrome with visual mouse glide
        self.is_media_playing = True
        self.visual_open_chrome(watch_url, query=clean)
        return f"Opened Google Chrome in full screen and started playing '{clean}' on YouTube."

    def _instant_click(self, x: int, y: int) -> None:
        """Instantly click coordinates without slow smooth mouse dragging."""
        ensure_interactive_desktop()
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                user32.SetCursorPos(int(x), int(y))
                time.sleep(0.02)
                inp_down = INPUT(type=0)
                inp_down.u.mi.dwFlags = 0x0002
                inp_up = INPUT(type=0)
                inp_up.u.mi.dwFlags = 0x0004
                inputs = (INPUT * 2)(inp_down, inp_up)
                user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
                return
            except Exception as e:
                log.debug("instant click error: %s", e)
        if pyautogui is not None:
            try:
                pyautogui.click(int(x), int(y))
            except Exception:
                pass

    def stop_or_pause_media(self) -> str:
        ensure_interactive_desktop()
        self.is_media_playing = False
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                VK_MEDIA_PLAY_PAUSE = 0xB3
                user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
                user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)
            except Exception as e:
                log.debug("media key error: %s", e)
        if gw is not None:
            try:
                for w in gw.getAllWindows():
                    t = (w.title or "").lower()
                    if any(k in t for k in ("youtube", "chrome", "edge", "firefox", "spotify")):
                        self.focus_window(w.title)
                        self.press_keys("k")
                        break
            except Exception:
                pass
        return "I stopped and paused playback for you."

    def resume_media(self) -> str:
        ensure_interactive_desktop()
        self.is_media_playing = True
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                VK_MEDIA_PLAY_PAUSE = 0xB3
                user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
                user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)
            except Exception:
                pass
        if gw is not None:
            try:
                for w in gw.getAllWindows():
                    t = (w.title or "").lower()
                    if any(k in t for k in ("youtube", "chrome", "edge", "firefox", "spotify")):
                        self.focus_window(w.title)
                        self.press_keys("k")
                        break
            except Exception:
                pass
        return "Resumed playback."

    def close_app_or_window(self, target: str = "") -> str:
        ensure_interactive_desktop()
        self.is_media_playing = False
        t_low = (target or "").lower().strip()

        PROTECTED_APPS = ("antigravity", "visual studio", "code", "eli", "powershell", "terminal", "cmd")

        # 1. Close active tab
        if t_low in ("tab", "active tab", "current tab"):
            fg_title = self._get_foreground_title().lower()
            if any(p in fg_title for p in PROTECTED_APPS):
                log.info("Refusing to send Ctrl+W to protected foreground window: %r", fg_title)
                return "Kept your active workspace open for safety."
            self.press_keys("ctrl+w")
            return "Closed the active tab."

        # 2. Close everything / all tabs / all windows
        # STRICT SAFETY: ONLY terminate browsers; NEVER send blind Alt+F4 to desktop or Antigravity!
        if any(k in t_low for k in ("everything", "all the tabs", "all tabs", "all windows")):
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    subprocess.run(["taskkill", "/F", "/IM", "firefox.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    subprocess.run(["taskkill", "/F", "/IM", "brave.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                except Exception:
                    pass
            return "Closed all open browser tabs and windows."

        # 3. Close Google Chrome
        if "chrome" in t_low or t_low in ("google chrome", "browser"):
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                except Exception:
                    pass
            return "Closed Google Chrome."

        # 4. Close Microsoft Edge
        if "edge" in t_low or t_low in ("microsoft edge", "msedge"):
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                except Exception:
                    pass
            return "Closed Microsoft Edge."

        # 5. Targeted window focus & close
        if target and target not in ("window", "active window", "current window"):
            if any(p in target.lower() for p in PROTECTED_APPS):
                return f"Cannot close {target} because it is a protected system application."
            res = self.focus_window(target)
            if not res.startswith("No window"):
                time.sleep(0.2)
                fg_title = self._get_foreground_title().lower()
                if not any(p in fg_title for p in PROTECTED_APPS):
                    self.press_keys("alt+f4")
                    return f"Closed {target}."
                return f"Kept {target} open for safety."

        # 6. Generic active window close (strictly protected)
        fg_title = self._get_foreground_title().lower()
        if any(p in fg_title for p in PROTECTED_APPS):
            log.info("Refusing to close protected foreground window: %r", fg_title)
            return "Kept your active workspace open for safety."

        self.press_keys("alt+f4")
        return "Closed the active window."

    def _get_foreground_title(self) -> str:
        ensure_interactive_desktop()
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        return buff.value
            except Exception:
                pass
        return ""


    def click_trust_dialog(self) -> str:
        """Detects and automatically clicks VS Code folder or workspace trust dialogs
        ('Yes, I trust the authors', 'Trust Folder & Continue', 'Trust Folder', 'Trust', 'OK')."""
        ensure_interactive_desktop()
        if not self.vision:
            return "Vision agent not available."

        orig_pos = None
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                orig_pos = (pt.x, pt.y)
            except Exception:
                pass

        try:
            trust_targets = [
                "yes, i trust",
                "trust the authors",
                "trust folder & continue",
                "trust folder",
                "trust workspace",
                "manage workspace trust",
                "trust",
                "ok"
            ]

            def is_target(line_text: str) -> bool:
                lt = line_text.strip().lower()
                return any(tgt in lt for tgt in trust_targets) and len(lt) <= 35 and len(lt.split()) <= 6

            for attempt in range(3):
                frame = self.vision.capture_now()
                lines = frame.ocr()
                has_trust_context = any("trust" in l.text.lower() for l in lines)

                target_hit = None
                for line in lines:
                    if is_target(line.text):
                        bx, by, bw, bh = line.box
                        if bw > 0 and bh > 0:
                            lt = line.text.strip().lower()
                            priority = 10 if ("yes, i trust" in lt or "trust the authors" in lt) else 5 if "trust" in lt else 1
                            if target_hit is None or priority > target_hit[3]:
                                target_hit = (bx + bw // 2, by + bh // 2, line.text.strip(), priority)

                if target_hit:
                    if target_hit[3] == 1 and not has_trust_context:
                        time.sleep(0.4)
                        continue

                    tx, ty, ttext, _ = target_hit
                    log.info("Found trust dialog button '%s' at (%d, %d); clicking", ttext, tx, ty)
                    self._instant_click(tx, ty)
                    time.sleep(0.3)
                    return f"Accepted folder trust dialog ('{ttext}')."

                time.sleep(0.5)

            return "No folder trust prompt was visible."
        finally:
            if orig_pos and os.name == "nt":
                try:
                    ctypes.windll.user32.SetCursorPos(orig_pos[0], orig_pos[1])
                except Exception:
                    pass

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
        ensure_interactive_desktop()
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

    def dismiss_interferences(self) -> str:
        """Dismisses any stuck file dialogs (Create File, Save As) or modal popups by sending Escape or closing."""
        ensure_interactive_desktop()
        dismissed = []
        if gw is not None:
            try:
                for w in gw.getAllWindows():
                    t = (w.title or "").strip()
                    low = t.lower()
                    if any(low.startswith(k) for k in ("create file", "save as", "open file", "built-in")):
                        try:
                            w.close()
                            dismissed.append(t)
                        except Exception:
                            pass
            except Exception:
                pass
        if pyautogui is not None:
            try:
                pyautogui.press("escape")
                time.sleep(0.05)
                pyautogui.press("escape")
            except Exception:
                pass
        return f"Dismissed modal interferences: {', '.join(dismissed)}" if dismissed else "Cleaned modal state with Escape."

    def focus_window(self, title: str) -> str:
        ensure_interactive_desktop()
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
            self._force_foreground(w._hWnd)
            try:
                w.activate()
            except Exception:
                pass
            time.sleep(0.3)
            return f"Switched to '{w.title}'."
        except Exception as e:
            return f"Couldn't focus '{w.title}': {e}"

    def _force_foreground(self, hwnd: int, maximize: bool = False) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cur_tid = kernel32.GetCurrentThreadId()
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)

        user32.AttachThreadInput(cur_tid, fg_tid, True)
        user32.AttachThreadInput(cur_tid, target_tid, True)

        cmd_show = 3 if maximize else 9  # SW_MAXIMIZE = 3, SW_RESTORE = 9
        user32.ShowWindow(hwnd, cmd_show)
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)

        user32.AttachThreadInput(cur_tid, fg_tid, False)
        user32.AttachThreadInput(cur_tid, target_tid, False)

    # -- keyboard / mouse ----------------------------------------------------------------------
    def type_text(self, text: str, press_enter: bool = False) -> str:
        ensure_interactive_desktop()
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                for ch in text:
                    if ch == "\n":
                        down = INPUT(type=1)
                        down.u.ki.wVk = 0x0D
                        up = INPUT(type=1)
                        up.u.ki.wVk = 0x0D
                        up.u.ki.dwFlags = 0x0002  # KEYEVENTF_KEYUP
                    else:
                        down = INPUT(type=1)
                        down.u.ki.wScan = ord(ch)
                        down.u.ki.dwFlags = 0x0004  # KEYEVENTF_UNICODE
                        up = INPUT(type=1)
                        up.u.ki.wScan = ord(ch)
                        up.u.ki.dwFlags = 0x0004 | 0x0002  # KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
                    evs = (INPUT * 2)(down, up)
                    user32.SendInput(2, evs, ctypes.sizeof(INPUT))
                    time.sleep(0.02)
                if press_enter:
                    down = INPUT(type=1)
                    down.u.ki.wVk = 0x0D
                    up = INPUT(type=1)
                    up.u.ki.wVk = 0x0D
                    up.u.ki.dwFlags = 0x0002
                    evs = (INPUT * 2)(down, up)
                    user32.SendInput(2, evs, ctypes.sizeof(INPUT))
            except Exception as e:
                log.debug("SendInput typing failed: %s", e)
                if pyautogui is not None:
                    try:
                        pyautogui.write(text, interval=0.02)
                        if press_enter:
                            pyautogui.press("enter")
                    except Exception:
                        pass
        elif pyautogui is not None:
            try:
                pyautogui.write(text, interval=0.02)
                if press_enter:
                    pyautogui.press("enter")
            except Exception:
                pass
        preview = text if len(text) <= 60 else text[:57] + "..."
        return f"Typed: {preview}" + (" and pressed Enter." if press_enter else "")

    def press_keys(self, keys: str) -> str:
        ensure_interactive_desktop()
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
        ensure_interactive_desktop()
        x, y = int(x), int(y)
        self.move_mouse(x, y)
        time.sleep(0.08)
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                if button == "right":
                    down_flag, up_flag = 0x0008, 0x0010
                elif button == "middle":
                    down_flag, up_flag = 0x0020, 0x0040
                else:
                    down_flag, up_flag = 0x0002, 0x0004
                inp_down = INPUT(type=0)
                inp_down.u.mi.dwFlags = down_flag
                inp_up = INPUT(type=0)
                inp_up.u.mi.dwFlags = up_flag
                inputs = (INPUT * 2)(inp_down, inp_up)
                user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
                if double:
                    time.sleep(0.08)
                    user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
            except Exception as e:
                log.debug("SendInput click failed: %s", e)
                try:
                    user32.mouse_event(down_flag, 0, 0, 0, 0)
                    user32.mouse_event(up_flag, 0, 0, 0, 0)
                except Exception:
                    pass
        elif pyautogui is not None:
            try:
                if double:
                    pyautogui.doubleClick(x, y, button=button)
                else:
                    pyautogui.click(x, y, button=button)
            except Exception:
                pass
        return f"{'Double-clicked' if double else 'Clicked'} at ({x}, {y})."

    def click_text(self, text: str, double: bool = False) -> str:
        ensure_interactive_desktop()
        if self.vision is None:
            return "click_text needs the vision agent."
        hit = self.vision.find_text(text)
        if not hit:
            return f"I couldn't find '{text}' on the screen."
        x, y, matched = hit
        self.click(x, y, double=double)
        return f"Clicked '{matched}' at ({x}, {y})."

    def move_mouse(self, x: int, y: int) -> str:
        ensure_interactive_desktop()
        x, y = int(x), int(y)
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                curr_x, curr_y = pt.x, pt.y
                steps = 35
                for i in range(1, steps + 1):
                    ix = int(curr_x + (x - curr_x) * (i / steps))
                    iy = int(curr_y + (y - curr_y) * (i / steps))
                    user32.SetCursorPos(ix, iy)
                    time.sleep(0.015)
                user32.SetCursorPos(x, y)
            except Exception as e:
                log.debug("SetCursorPos failed: %s", e)
        if pyautogui is not None:
            try:
                pyautogui.moveTo(x, y, duration=0.1)
            except Exception:
                pass
        return f"Moved the mouse to ({x}, {y})."

    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None) -> str:
        ensure_interactive_desktop()
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
