"""'Hey Eli' wake-word listener. Runs whisper on short utterances and checks for the name,
so no custom wake-word model is needed. Pauses itself while Eli is talking or push-to-talk is active."""
from __future__ import annotations

import logging
import re
import threading
import time

import numpy as np

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

STOP_WORDS = (
    "stop", "eli stop", "stop eli", "ellie stop", "stop ellie",
    "stop talking", "stop speaking", "be quiet", "shut up", "quiet",
    "pause", "cancel", "hush", "silence"
)


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
        self._awake_until = 0.0
        self._was_awake = False
        self._cooldown_until = 0.0

    def extend_conversation(self, seconds: float = 60.0) -> None:
        """Keeps Eli awake for uninterrupted back-and-forth conversation (default 60s)."""
        self._awake_until = time.time() + max(seconds, 60.0)
        self._was_awake = True

    def is_awake(self) -> bool:
        return time.time() < self._awake_until

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()

    def _is_recent_speech_echo(self, text: str) -> bool:
        low = text.lower().strip().strip(".!?,")
        if not low:
            return True
        last = getattr(self.c.tts, "last_spoken", "").lower().strip().strip(".!?,")
        last_time = getattr(self.c.tts, "last_spoken_time", 0.0)
        now = time.time()
        if last and (now - last_time) < 8.0:
            words = set(low.split())
            last_words = set(last.split())
            if words and last_words:
                common = words.intersection(last_words)
                if len(common) / len(words) >= 0.5:
                    return True
        echo_phrases = (
            "take your time", "nothing to be sorry", "sorry, i'm going to",
            "i'm here with you", "opened google chrome", "how can i help",
            "what would you like", "sure, i will", "all clean", "all clear"
        )
        if any(p in low for p in echo_phrases):
            return True
        return False

    def run(self) -> None:
        while not self._stop.is_set():
            if not bool(self.settings.get("wake_enabled")) or self.c.recorder is None or self._paused.is_set():
                time.sleep(0.3)
                continue

            # --- 1. Total Silence & Mic Off While Eli is Speaking ---
            if self.c.tts.speaking:
                self._cooldown_until = time.time() + 0.8
                self.hub.status(mic_live=False)
                time.sleep(0.2)
                continue

            # --- 2. Acoustic Settle Cooldown: Allow room reverberation / speaker echo to die down completely ---
            if time.time() < self._cooldown_until:
                self.hub.status(mic_live=False)
                time.sleep(0.15)
                continue

            # Don't record if push-to-talk or another recording is in progress
            if self.c.recording:
                time.sleep(0.2)
                continue

            now = time.time()
            awake = now < self._awake_until

            # Check for transition: awake -> standby timeout (60 seconds elapsed without user voice)
            if not awake and self._was_awake:
                self._was_awake = False
                log.info("Continuous conversation 60s window timed out. Returning to silent standby.")
                self.hub.toast("Eli is on standby. Say 'Hello Eli' anytime.")
                self.hub.set_state("idle")

            try:
                if awake:
                    # Continuous conversation capture: generous 2.0s silence break, up to 25.0s max for long sentences
                    self.hub.set_state("listening")
                    self.hub.status(mic_live=True)
                    audio = self.c.recorder.record(max_seconds=25.0, silence_seconds=2.0, min_seconds=0.4, wait_timeout=3.0)
                else:
                    # Standby wake-word capture: shorter window, waiting for wake phrase
                    audio = self.c.recorder.record(max_seconds=6.0, silence_seconds=1.2, min_seconds=0.3, wait_timeout=3.0)
            except Exception as e:
                log.warning("wake listener mic error: %s", e)
                time.sleep(0.5)
                continue
            finally:
                if not awake:
                    self.hub.status(mic_live=False)

            if audio.size < 16000 * 0.25:
                continue

            text = self.c.transcriber.transcribe(audio)
            if not text:
                continue

            if self._is_recent_speech_echo(text):
                log.info("Discarded audio echo of Eli's own voice: %r", text)
                continue

            hit, rest = match_wake(text, self.wake_words)

            if awake:
                # While awake, any speech is accepted as a conversational command
                hit = True
                cmd = rest.strip() if (match_wake(text, self.wake_words)[0] and rest.strip()) else text.strip()
                log.info("Awake conversational turn received: %r", cmd)
            else:
                if not hit:
                    # In standby mode, ignore casual room chatter that doesn't start with Eli
                    log.debug("Standby ignored non-wake speech: %r", text)
                    continue
                cmd = rest.strip() if rest.strip() else text.strip()

            # Keep awake for another 60 seconds from this utterance
            self._awake_until = time.time() + 60.0
            self._was_awake = True
            self.c.recording = True

            try:
                self.hub.set_state("listening")
                self.hub.status(mic_live=True)
                log.info("dispatching voice command: %r", cmd)
                self.hub.toast(f'Heard: "{cmd}"')
                self.c.dispatch(cmd, "voice")
            finally:
                self.hub.status(mic_live=False)
                self.c.recording = False
