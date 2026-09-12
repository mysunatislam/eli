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
                # Fast wake capture: 5.0s max, 0.8s silence break, 3.5s wait timeout
                audio = self.c.recorder.record(max_seconds=5.0, silence_seconds=0.8, wait_timeout=3.5)
            except Exception as e:
                log.warning("wake listener mic error: %s", e)
                time.sleep(1)
                continue
            if audio.size < 16000 * 0.25 or not self._active():
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

                if rest.strip():
                    log.info("dispatching direct voice command: %r", rest.strip())
                    self.hub.toast(f'Heard: "{rest.strip()}"')
                    self.c.dispatch(rest.strip(), "voice")
                    continue

                # Just the wake name was spoken: acknowledge and turn mic on for follow-up
                self.hub.toast("Listening... (Mic ON)")
                self.c.tts.say("Yes?")
                while self.c.tts.speaking or not self.c.tts.q.empty():
                    time.sleep(0.04)
                # Acoustic settle to prevent speaker echo
                time.sleep(0.3)

                self.hub.set_state("listening")
                self.hub.status(mic_live=True)
                follow = self.c.recorder.record(max_seconds=25.0, silence_seconds=2.0, wait_timeout=10.0)
                self.hub.status(mic_live=False)

                if follow.size < 16000 * 0.35:
                    log.info("follow-up audio too short, returning to idle")
                    self.hub.toast("Mic timed out.")
                    self.hub.set_state("idle")
                    continue

                self.hub.set_state("thinking")
                said = self.c.transcriber.transcribe(follow)
                log.info("follow-up transcribed: %r", said)
                if said:
                    self.hub.toast(f'Heard: "{said}"')
                    self.c.dispatch(said, "voice")
                else:
                    self.hub.toast("I didn't catch that.")
                    self.hub.set_state("idle")
            finally:
                self.hub.status(mic_live=False)
                self.c.recording = False
