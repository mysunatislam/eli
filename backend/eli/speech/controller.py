"""Ties recording, transcription, TTS and the wake listener together.
Also turns a streaming reply into speech sentence by sentence so Eli starts talking early."""
from __future__ import annotations

import logging
import re
import threading

from .. import config, intents
from .stt import RATE, Recorder, Transcriber
from .tts import TTSWorker
from .wake import WakeListener

log = logging.getLogger("eli.speech")

SENTENCE_END = re.compile(r"([.!?])(\s|$)")


class SpeechController:
    def __init__(self, hub, settings, on_command):
        """on_command(text, source) is called from worker threads; the caller schedules it on the event loop."""
        self.hub = hub
        self.settings = settings
        self.on_command = on_command
        self.tts = TTSWorker(hub, config.TTS_RATE, config.TTS_VOICE, config.TTS_ENGINE)
        self.tts.start()
        self.transcriber = Transcriber(config.WHISPER_MODEL)
        self.recorder = None
        self.audio_error = ""
        try:
            self.recorder = Recorder()
        except Exception as e:
            self.audio_error = f"No microphone: {e}"
            log.warning(self.audio_error)
        self.recording = False
        self._ptt_stop = threading.Event()
        self._ptt_thread: threading.Thread | None = None
        self._buf = ""
        self._in_code = False
        self.wake = WakeListener(self, hub, settings, config.WAKE_WORDS)
        self.wake.start()

    # -- helpers -----------------------------------------------------------------------------------
    def available(self) -> bool:
        return self.recorder is not None

    def preload(self) -> None:
        if self.recorder is not None:
            threading.Thread(target=self.transcriber.load, name="eli-whisper-load", daemon=True).start()

    def dispatch(self, text: str, source: str) -> None:
        text = intents.strip_wake(text).strip()
        if text:
            self.on_command(text, source)
        else:
            self.hub.set_state("idle")

    def say(self, text: str) -> None:
        self.tts.say(text)

    def say_now(self, text: str) -> None:
        """Interrupt whatever is playing and speak this (guide narration, alerts)."""
        self._buf = ""
        self.tts.say(text, priority=True)

    # -- streaming speech ----------------------------------------------------------------------------
    def stream_text(self, delta: str) -> None:
        """Feed reply text as it streams; complete sentences are spoken immediately."""
        self._buf += delta
        # keep code fences out of speech
        if "```" in self._buf:
            self._in_code = not self._in_code if self._buf.count("```") % 2 else self._in_code
        while True:
            m = SENTENCE_END.search(self._buf)
            if not m or self._in_code:
                break
            sentence, self._buf = self._buf[: m.end()], self._buf[m.end():]
            if len(sentence.strip()) > 1:
                self.tts.say(sentence)
        if len(self._buf) > 240 and not self._in_code:
            self.tts.say(self._buf)
            self._buf = ""

    def flush_stream(self) -> None:
        if self._buf.strip() and not self._in_code:
            self.tts.say(self._buf)
        self._buf = ""
        self._in_code = False

    def stop_speaking(self) -> None:
        self._buf = ""
        self.tts.stop()

    def shutdown(self) -> None:
        self.wake.stop()
        self.tts.shutdown()

    # -- push to talk -----------------------------------------------------------------------------
    def ptt_toggle(self) -> str:
        if self.recorder is None:
            self.hub.toast("No microphone found." if not self.audio_error else self.audio_error)
            return "no-mic"
        if self.recording and self._ptt_thread is not None:
            self._ptt_stop.set()
            return "stopping"
        self._ptt_stop.clear()
        self._ptt_thread = threading.Thread(target=self._ptt_run, name="eli-ptt", daemon=True)
        self._ptt_thread.start()
        return "recording"

    def _ptt_run(self) -> None:
        self.recording = True
        self.wake.pause()
        self.tts.stop()
        self.hub.set_state("listening")
        self.hub.status(mic_live=True)
        try:
            audio = self.recorder.record(max_seconds=20.0, silence_seconds=1.5, wait_timeout=6.0, stop_flag=self._ptt_stop)
            self.hub.status(mic_live=False)
            if audio.size < RATE * 0.4:
                self.hub.toast("I didn't hear anything.")
                self.hub.set_state("idle")
                return
            self.hub.set_state("thinking")
            text = self.transcriber.transcribe(audio)
            if not text:
                self.hub.toast("I didn't catch that." if not self.transcriber.error else self.transcriber.error)
                self.hub.set_state("idle")
                return
            self.dispatch(text, "voice")
        except Exception as e:
            log.exception("push-to-talk failed")
            self.hub.toast(f"Mic error: {e}")
            self.hub.set_state("idle")
        finally:
            self.hub.status(mic_live=False)
            self.recording = False
            self._ptt_stop.clear()
            self._ptt_thread = None
            self.wake.resume()
