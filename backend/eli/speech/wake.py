"""'Hey Eli' wake-word listener. Runs whisper on short utterances and checks for the name,
so no custom wake-word model is needed. Pauses itself while Eli is talking or push-to-talk is active."""
from __future__ import annotations

import logging
import re
import threading
import time

log = logging.getLogger("eli.wake")

WAKE_RE = re.compile(
    r"^(?:(?:hello|hey|hi|ok|okay|just)\s+)?"
    r"(?:h?[iea]+l+[ieya]+|allie|ali|ally|i\s+lie|i\s+lay|inlet)"
    r"[,!.]*\s*",
    re.I
)

KNOWN_NAMES = {
    "eli", "ellie", "elli", "elly", "ely", "allie", "ali", "ally",
    "ili", "ilii", "iliii", "iliiii", "iliiiii", "iliiiiii",
    "ilee", "ileee", "elii", "eliii", "eliiii", "eliiiii",
    "eliiiiee", "eliiiie", "eliiie", "eliee",
    "ilai", "alai", "elai", "ilay", "alay", "ilaii",
    "hili", "hilii", "hiliii", "hilee", "heli", "helli",
    "inlet", "i lie", "i lay"
}


def match_wake(text: str, wake_words) -> tuple[bool, str]:
    t = re.sub(r"[^a-z0-9' ]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return False, ""
    words = t.split()

    # 1. Exact or prefix match against explicit wake word list
    for w in sorted(wake_words, key=len, reverse=True):
        if t == w:
            return True, ""
        if t.startswith(w + " "):
            return True, t[len(w):].strip()

    # 2. Regex match against whole string
    m = WAKE_RE.match(t)
    if m:
        return True, t[m.end():].strip()

    # 3. Check individual words or sub-phrases within the first 4 words
    for i in range(min(4, len(words))):
        clean_w = words[i].strip("',.")
        if clean_w in KNOWN_NAMES:
            return True, " ".join(words[i + 1:]).strip()
        if re.match(r"^(?:h?[iea]+l+[ieya]+)$", clean_w) and len(clean_w) >= 3:
            return True, " ".join(words[i + 1:]).strip()

        sub = " ".join(words[i:])
        m_sub = WAKE_RE.match(sub)
        if m_sub:
            return True, sub[m_sub.end():].strip()

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
        self._followup_until = 0.0

    def extend_conversation(self, seconds: float = 12.0) -> None:
        self._followup_until = time.time() + seconds

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
                # Fast wake capture: 6.0s max, 1.2s silence break, 3.5s wait timeout
                audio = self.c.recorder.record(max_seconds=6.0, silence_seconds=1.2, wait_timeout=3.5)
            except Exception as e:
                log.warning("wake listener mic error: %s", e)
                time.sleep(1)
                continue
            if audio.size < 16000 * 0.20 or not self._active():
                continue
            text = self.c.transcriber.transcribe(audio)
            if not text:
                continue
            hit, rest = match_wake(text, self.wake_words)
            if not hit and time.time() < self._followup_until and len(text.split()) >= 1:
                hit = True
                rest = text.strip()
                log.info("Continuous conversational follow-up accepted: %r", text)

            log.info("wake heard: %r -> hit=%s rest=%r", text, hit, rest)
            if not hit:
                continue

            self._followup_until = time.time() + 15.0
            self.c.recording = True
            try:
                # Turn mic ON visually in UI immediately & set state to listening
                self.hub.set_state("listening")
                self.hub.status(mic_live=True)

                cmd = rest.strip() if rest.strip() else text.strip()
                log.info("dispatching voice command: %r", cmd)
                self.hub.toast(f'Heard: "{cmd}"')
                self.c.dispatch(cmd, "voice")
            finally:
                self.hub.status(mic_live=False)
                self.c.recording = False
