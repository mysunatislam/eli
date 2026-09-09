"""'Hey Eli' wake-word listener. Runs whisper on short utterances and checks for the name,
so no custom wake-word model is needed. Pauses itself while Eli is talking or push-to-talk is active."""
from __future__ import annotations

import logging
import re
import threading
import time

log = logging.getLogger("eli.wake")


def match_wake(text: str, wake_words) -> tuple[bool, str]:
    t = re.sub(r"[^a-z0-9' ]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    for w in sorted(wake_words, key=len, reverse=True):
        if t == w:
            return True, ""
        if t.startswith(w + " "):
            return True, t[len(w):].strip()
    words = t.split()
    for i in range(min(3, len(words))):
        if words[i].strip("',.") in ("eli", "ellie", "elly", "ely"):
            return True, " ".join(words[i + 1:]).strip()
    return False, ""


class WakeListener(threading.Thread):
    def __init__(self, controller, hub, settings, wake_words):
        super().__init__(name="eli-wake", daemon=True)
        self.c = controller
        self.hub = hub
        self.settings = settings
        self.wake_words = wake_words
        self._stop = threading.Event()
        self._paused = threading.Event()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()

    def _active(self) -> bool:
        return bool(self.settings.get("wake_enabled")) and self.c.recorder is not None \
            and not self._paused.is_set() and not self.c.tts.speaking and not self.c.recording

    def run(self) -> None:
        while not self._stop.is_set():
            if not self._active():
                time.sleep(0.3)
                continue
            try:
                audio = self.c.recorder.record(max_seconds=7.0, silence_seconds=0.9, wait_timeout=1.5)
            except Exception as e:
                log.warning("wake listener mic error: %s", e)
                time.sleep(2)
                continue
            if audio.size < 16000 * 0.4 or not self._active():
                continue
            text = self.c.transcriber.transcribe(audio)
            if not text:
                continue
            hit, rest = match_wake(text, self.wake_words)
            log.debug("heard: %r -> wake=%s rest=%r", text, hit, rest)
            if not hit:
                continue
            self.c.recording = True
            try:
                if len(rest.split()) >= 2:
                    self.hub.set_state("listening")
                    self.c.dispatch(rest, "voice")
                    continue
                # Just the name: acknowledge and listen for the actual request
                self.hub.set_state("listening")
                self.hub.status(mic=True)
                self.c.tts.say("Yes?")
                while self.c.tts.speaking or not self.c.tts.q.empty():
                    time.sleep(0.05)
                follow = self.c.recorder.record(max_seconds=12.0, silence_seconds=1.3, wait_timeout=4.0)
                self.hub.status(mic=False)
                if follow.size < 16000 * 0.4:
                    self.hub.set_state("idle")
                    continue
                self.hub.set_state("thinking")
                said = self.c.transcriber.transcribe(follow)
                if said:
                    self.c.dispatch(said, "voice")
                else:
                    self.hub.toast("I didn't catch that.")
                    self.hub.set_state("idle")
            finally:
                self.c.recording = False
