"""Text-to-speech with a clear female voice.

Engines (ELI_TTS_ENGINE = auto | neural | sapi):
- neural: Microsoft Edge neural voices via edge-tts (online, e.g. en-US-AriaNeural / en-US-JennyNeural),
  decoded with miniaudio and played through sounddevice. Natural, professional; ~0.5-1 s to first audio.
- sapi:   Windows built-in voices via pyttsx3 (offline). A female voice (Zira/Hazel/…) is preferred automatically.
auto tries neural and falls back to SAPI when offline or when playback fails.

The worker speaks a queue; `say(text, priority=True)` interrupts whatever is playing (used for guide steps so
narration lines up with the on-screen indicator). `on_start` / `on_done` callbacks let the guide overlay pulse
while Eli talks.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import re
import tempfile
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("eli.tts")

FEMALE_HINTS = ("zira", "aria", "jenny", "hazel", "susan", "heera", "sonia", "natasha", "emma", "michelle", "ana", "female")
CODE_FENCE = re.compile(r"```.*?```", re.S)
URL_RE = re.compile(r"https?://\S+")
MD_RE = re.compile(r"[*_`#>]+")


def speakable(text: str) -> str:
    """Strip code, URLs and markdown so speech sounds natural."""
    t = CODE_FENCE.sub(" (code is in the panel) ", text)
    t = URL_RE.sub(" a link ", t)
    t = MD_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:900]


class TTSWorker(threading.Thread):
    def __init__(self, hub, rate: int = 185, voice: str = "", engine: str = ""):
        super().__init__(name="eli-tts", daemon=True)
        self.hub = hub
        self.rate = rate
        self.voice_pref = voice
        self.mode = (engine or os.getenv("ELI_TTS_ENGINE", "auto")).lower()
        self.neural_voice = os.getenv("ELI_TTS_NEURAL_VOICE", "en-US-AriaNeural")
        self.q: "queue.Queue[Optional[tuple[str, bool]]]" = queue.Queue()
        self.speaking = False
        self.current = ""
        self.last_spoken = ""
        self.last_spoken_time = 0.0
        self._abort = threading.Event()
        self._engine = None
        self._engine_lock = threading.Lock()
        self._neural_failed_at = 0.0
        self.on_start: list[Callable[[str], None]] = []
        self.on_done: list[Callable[[str], None]] = []
        self.voice_name = ""

    # -- public ----------------------------------------------------------------------------------
    def say(self, text: str, priority: bool = False) -> None:
        t = speakable(text)
        if not t:
            if self.q.empty() and not self.speaking:
                self.hub.set_state("idle")
            return
        if priority:
            self.stop()
        self.q.put((t, priority))

    def stop(self) -> None:
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        self._abort.set()
        try:
            import sounddevice as sd  # type: ignore
            sd.stop()
        except Exception:
            pass
        with self._engine_lock:
            if self._engine is not None:
                try:
                    self._engine.stop()
                except Exception:
                    pass

    def shutdown(self) -> None:
        self.stop()
        self.q.put(None)

    def describe(self) -> str:
        return f"{'neural ' + self.neural_voice if self._use_neural() else 'sapi ' + (self.voice_name or 'default')}"

    # -- worker ----------------------------------------------------------------------------------
    def run(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                break
            text, _priority = item
            self._abort.clear()
            self.speaking = True
            self.current = text
            self.hub.set_state("talking")
            for cb in list(self.on_start):
                try:
                    cb(text)
                except Exception:
                    pass
            try:
                spoken = False
                if self._use_neural():
                    spoken = self._speak_neural(text)
                if not spoken and not self._abort.is_set():
                    self._speak_sapi(text)
            except Exception as e:
                log.warning("TTS failed: %s", e)
            finally:
                self.speaking = False
                self.last_spoken = text
                self.last_spoken_time = time.time()
                self.current = ""
                for cb in list(self.on_done):
                    try:
                        cb(text)
                    except Exception:
                        pass
                if self.q.empty():
                    self.hub.set_state("idle")

    def _use_neural(self) -> bool:
        if self.mode == "sapi":
            return False
        if self.mode == "neural":
            return True
        return time.time() - self._neural_failed_at > 15  # auto: retry neural 15 seconds after a failure

    # -- neural (edge-tts) -------------------------------------------------------------------------
    def _speak_neural(self, text: str) -> bool:
        try:
            import edge_tts  # type: ignore
            import miniaudio  # type: ignore
            import numpy as np  # type: ignore
            import sounddevice as sd  # type: ignore
        except Exception as e:
            log.info("neural TTS unavailable: %s", e)
            self._neural_failed_at = time.time()
            return False
        path = os.path.join(tempfile.gettempdir(), f"eli-tts-{threading.get_ident()}.mp3")
        try:
            pct = int((self.rate - 175) / 175 * 100)
            rate = f"{'+' if pct >= 0 else ''}{pct}%"

            async def synth():
                await edge_tts.Communicate(text, self.neural_voice, rate=rate).save(path)

            asyncio.run(asyncio.wait_for(synth(), timeout=12))
            if self._abort.is_set():
                return True
            dec = miniaudio.decode_file(path)
            data = np.frombuffer(dec.samples, dtype=np.int16).reshape(-1, dec.nchannels)
            sd.play(data, dec.sample_rate, latency='high')
            while True:
                s = sd.get_stream()
                if s is None or not s.active or self._abort.is_set():
                    break
                time.sleep(0.05)
            sd.stop()
            return True
        except Exception as e:
            log.info("neural TTS failed (%s); falling back to Windows voice", str(e)[:120])
            self._neural_failed_at = time.time()
            return False
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # -- Windows SAPI (pyttsx3) -----------------------------------------------------------------------
    def _engine_get(self):
        with self._engine_lock:
            if self._engine is None:
                import pyttsx3  # type: ignore
                eng = pyttsx3.init()
                eng.setProperty("rate", self.rate)
                self._pick_voice(eng)
                self._engine = eng
            return self._engine

    def _pick_voice(self, eng) -> None:
        try:
            voices = eng.getProperty("voices")
        except Exception:
            return
        chosen = None
        if self.voice_pref:
            for v in voices:
                if self.voice_pref.lower() in (v.name or "").lower():
                    chosen = v
                    break
        if chosen is None:
            for v in voices:
                name = (v.name or "").lower()
                gender = str(getattr(v, "gender", "") or "").lower()
                if any(h in name for h in FEMALE_HINTS) or "female" in gender:
                    chosen = v
                    break
        if chosen is not None:
            eng.setProperty("voice", chosen.id)
            self.voice_name = chosen.name
            log.info("SAPI voice: %s", chosen.name)

    def _speak_sapi(self, text: str) -> None:
        try:
            eng = self._engine_get()
            eng.say(text)
            eng.runAndWait()
        except Exception as e:
            log.warning("pyttsx3 failed (%s); trying PowerShell speech", e)
            self._engine = None
            self._speak_powershell(text)

    def _speak_powershell(self, text: str) -> None:
        import subprocess
        safe = text.replace("'", "''")
        cmd = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
               "try { $s.SelectVoiceByHints('Female') } catch {}; "
               f"$s.Rate = {max(-10, min(10, (self.rate - 175) // 10))}; $s.Speak('{safe}')")
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=120,
                       creationflags=subprocess.CREATE_NO_WINDOW)
