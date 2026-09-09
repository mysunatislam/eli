"""Eli backend configuration. Local-first: every default keeps data on this PC."""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

# Installed-app layout: the app bundle (BACKEND_DIR, under Program Files) is read-only, so user data
# and secrets live under %APPDATA%\Eli instead. ELI_USER_DIR is set by the desktop app when packaged;
# a .env there is loaded on top of (and overrides) the bundled one, and DATA_DIR defaults into it.
USER_DIR = Path(os.getenv("ELI_USER_DIR")) if os.getenv("ELI_USER_DIR") else None
if USER_DIR:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    load_dotenv(USER_DIR / ".env", override=True)

DATA_DIR = Path(os.getenv("ELI_DATA_DIR", str(USER_DIR / "data" if USER_DIR else BACKEND_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = DATA_DIR / "settings.json"
MOBILE_DIR = Path(__file__).resolve().parent / "mobile"

HOST = os.getenv("ELI_HOST", "0.0.0.0")  # 0.0.0.0 so the phone on your LAN can reach it
PORT = int(os.getenv("ELI_PORT", "8790"))

# LLM (provider selection lives in eli/llm.py: ELI_LLM_PROVIDER, GEMINI_API_KEY, ANTHROPIC_API_KEY)
CONTEXT_BUDGET = int(os.getenv("ELI_CONTEXT_BUDGET", "30000"))   # approx tokens of history kept per conversation
HISTORY_TURNS = int(os.getenv("ELI_HISTORY_TURNS", "16"))

# Vision: adaptive capture between the two bounds. Fast while the screen changes, slow when static.
CAPTURE_INTERVAL = float(os.getenv("ELI_CAPTURE_INTERVAL", "3"))
CAPTURE_INTERVAL_MAX = float(os.getenv("ELI_CAPTURE_INTERVAL_MAX", "15"))
SCREENSHOT_MAX_SIDE = int(os.getenv("ELI_SCREENSHOT_MAX_SIDE", "1568"))

# Speech
WHISPER_MODEL = os.getenv("ELI_WHISPER_MODEL", "base.en")
TTS_RATE = int(os.getenv("ELI_TTS_RATE", "185"))
TTS_VOICE = os.getenv("ELI_TTS_VOICE", "")
TTS_ENGINE = os.getenv("ELI_TTS_ENGINE", "auto")   # auto | neural (Edge neural female voice, online) | sapi (offline)
WAKE_WORDS = ("hey eli", "hey ellie", "hey elly", "hey ely", "hey ali", "ellie", "elly", "eli")

# Extra project folders for the coding agent (semicolon separated)
PROJECT_DIRS = [p for p in os.getenv("ELI_PROJECT_DIRS", "").split(";") if p.strip()]

DEFAULT_SETTINGS = {
    "observe_enabled": False,      # continuous screen capture (needs screen_permission == granted)
    "screen_permission": "ask",    # ask | granted | denied
    "wake_enabled": False,         # always-listening "Hey Eli"
    "voice_replies": True,         # speak replies aloud
    "private_mode": False,         # no capture, no memory writes, no cloud calls with screen content
    "automation_enabled": True,    # kill switch for mouse/keyboard control
    "nudges_enabled": True,        # proactive suggestions
    "proactive_mode": "ask",       # ask (nudge with Yes/No) | auto (look and propose the fix directly)
    "follow_cursor": True,         # the heart trails the mouse pointer across the screen
    "trust_mode": False,           # auto-approve confirmations (shell, tests, file writes, sends)
    "trust_until": 0,              # unix time; 0 = until switched off. Voice-granted trust expires after 30 min
    "blocked_apps": ["password", "1password", "bitwarden", "keepass", "lastpass", "bank", "banking", "wallet", "authenticator"],
    "camera_enabled": False,       # the desktop never opens a camera; phone photos are explicit uploads
    "save_frames": False,          # screenshots stay in RAM unless the user opts in
    "pairing_token": "",           # shared secret for the mobile companion
}


class Settings:
    """Small JSON-backed settings store with thread-safe writes."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.data = dict(DEFAULT_SETTINGS)
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text("utf-8")))
            except Exception:
                pass
        if not self.data.get("pairing_token"):
            self.data["pairing_token"] = secrets.token_urlsafe(8)
        self.data["camera_enabled"] = False
        self.save()

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self.data[key] = value
        self.save()

    def save(self) -> None:
        with self._lock:
            self.path.write_text(json.dumps(self.data, indent=2), "utf-8")

    def public(self) -> dict:
        return dict(self.data)


AUTOSTART_SCRIPT = BACKEND_DIR.parent / "scripts" / "autostart.ps1"


def autostart_enabled() -> bool:
    """True when the Windows Startup folder holds Eli's shortcut."""
    appdata = os.getenv("APPDATA", "")
    return bool(appdata) and (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Eli.lnk").exists()


def set_autostart(enabled: bool) -> str:
    import subprocess
    if not AUTOSTART_SCRIPT.exists():
        return "autostart script missing"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(AUTOSTART_SCRIPT),
                        "-Enable" if enabled else "-Disable"], capture_output=True, text=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (r.stdout or r.stderr).strip()


def lan_ip() -> str:
    """Best-effort LAN address so the phone can find this PC."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
