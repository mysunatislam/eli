"""Microphone recording with a simple energy VAD, and offline transcription with faster-whisper."""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Optional

import numpy as np

log = logging.getLogger("eli.stt")

RATE = 16000


def normalize_speech_text(text: str) -> str:
    """Corrects common Whisper phonetic mishearings and accent artifacts."""
    if not text:
        return ""
    t = text.strip()
    # 1. Fix "meal code", "meal file", "meal script", "meal project", etc. -> "new ..."
    t = re.sub(r"\b(?:meal|nail|kneel|milk|mail|neat|deal)\s+(code|script|file|folder|project|python|program|window)\b", r"new \1", t, flags=re.I)
    t = re.sub(r"\b(write|create|make|open)\s+(?:a\s+)?(?:meal)\s+(code|script|file|program)\b", r"\1 a new \2", t, flags=re.I)
    t = re.sub(r"\bmeal\s+file\b", "new file", t, flags=re.I)
    t = re.sub(r"\bmeal\s+code\b", "new code", t, flags=re.I)
    t = re.sub(r"\bmeal\s+script\b", "new script", t, flags=re.I)

    # 2. Fix "the vs code" / "the vscode" -> "VS Code"
    t = re.sub(r"\bthe\s+vs\s+code\b", "VS Code", t, flags=re.I)
    t = re.sub(r"\bthe\s+vscode\b", "VS Code", t, flags=re.I)

    # 3. Fix "happy elli" / "happy eli" -> "hello eli"
    t = re.sub(r"\bhappy\s+el+[ieya]+\b", "hello eli", t, flags=re.I)

    # 4. Fix "skip and" -> "skip ad"
    t = re.sub(r"\bskip\s+and\b", "skip ad", t, flags=re.I)

    # 5. Fix "Antigravity us" -> "Antigravity asks"
    t = re.sub(r"\bantigravity\s+(?:us|is)\b", "Antigravity asks", t, flags=re.I)

    # 6. Fix "google pro" / "google crow" / "google comb" / "google grown" -> "Google Chrome"
    t = re.sub(r"\bgoogle\s+(?:pro|crow|comb|grown|grow|home|com)\b", "Google Chrome", t, flags=re.I)

    # 7. Fix ChatGPT acoustic mishearings
    t = re.sub(r"\b(?:chat\s*gpt|chat\s*gbt|cat\s*gpt|chad\s*gpt|check\s*gpt|chat\s*pt|jet\s*gpt)\b", "ChatGPT", t, flags=re.I)
    # Fix "search for t" or "search for tea" when in search / browser context
    t = re.sub(r"\b(search(?:\s+for)?)\s+(?:t|tea|tee)\b", r"\1 ChatGPT", t, flags=re.I)

    # 8. Fix VS Code acoustic mishearings ("js code", "j s code", "just code", "ds code")
    t = re.sub(r"\b(?:js|j\s+s|just|ds)\s+code\b", "VS Code", t, flags=re.I)
    t = re.sub(r"\bvs\s+code\b", "VS Code", t, flags=re.I)

    t = re.sub(r"\s+", " ", t).strip()
    return t


class Transcriber:
    def __init__(self, model_name: str = "base.en"):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        self.error = ""

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel  # type: ignore
                log.info("loading whisper model '%s' (first run downloads it)...", self.model_name)
                threads = min(4, max(1, (os.cpu_count() or 4) // 2))
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=threads)
                log.info("whisper ready (cpu_threads=%d)", threads)
            except Exception as e:
                self.error = f"speech-to-text unavailable: {e}"
                log.warning(self.error)

    @property
    def ready(self) -> bool:
        return self._model is not None

    INITIAL_PROMPT = (
        "Eli, Ellie, Elli. Hey Eli, Hello Eli, Hi Eli. "
        "Open Google Chrome, Chrome, search on Google, YouTube, ChatGPT, Chat GPT, OpenAI, Claude, "
        "open browser, website, URL, open VS Code, write code, new code, new script, new file, python script, "
        "check errors, diagnose code, Antigravity, submit, allow, terminal."
    )

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        if self._model is None or audio.size == 0:
            return ""
        segments, _ = self._model.transcribe(
            audio,
            beam_size=1,
            language="en",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400, threshold=0.4),
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
            initial_prompt=self.INITIAL_PROMPT,
        )
        valid_texts = []
        for s in segments:
            # Strictly filter out non-speech device audio, music, and background noise
            if getattr(s, "no_speech_prob", 0.0) > 0.45:
                continue
            if getattr(s, "avg_logprob", 0.0) < -1.15:
                continue
            txt = s.text.strip()
            if txt:
                valid_texts.append(txt)
        raw_text = " ".join(valid_texts).strip()
        return normalize_speech_text(raw_text)


class Recorder:
    """Blocking microphone capture. Speech starts when energy rises above an adaptive noise floor and
    ends after `silence_seconds` of quiet."""
    MIN_THRESHOLD = 0.0025

    def __init__(self):
        import sounddevice as sd  # type: ignore
        self.sd = sd
        sd.check_input_settings(samplerate=RATE, channels=1, dtype="float32")

    def record(self, max_seconds: float = 15.0, silence_seconds: float = 2.0, min_seconds: float = 0.5,
               wait_timeout: Optional[float] = None, stop_flag: Optional[threading.Event] = None) -> np.ndarray:
        block = RATE // 10  # 100 ms
        frames: list[np.ndarray] = []
        started = False
        silent = 0.0
        total = 0.0
        floor: Optional[float] = None
        with self.sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=block) as stream:
            while total < max_seconds:
                data, _ = stream.read(block)
                chunk = data[:, 0].copy()
                total += 0.1
                rms = float(np.sqrt(np.mean(chunk ** 2) + 1e-12))
                if floor is None:
                    floor = min(rms, 0.015)
                elif not started:
                    floor = 0.92 * floor + 0.08 * min(rms, 0.015)
                threshold = max(self.MIN_THRESHOLD, (floor or 0.002) * 1.5)
                if rms > threshold:
                    if not started:
                        started = True
                        frames = frames[-8:]  # keep 800 ms of pre-roll
                    silent = 0.0
                elif started:
                    silent += 0.1
                frames.append(chunk)
                if started and silent >= silence_seconds and total >= min_seconds:
                    break
                if not started and wait_timeout is not None and total >= wait_timeout:
                    return np.zeros(0, dtype=np.float32)
                if stop_flag is not None and stop_flag.is_set():
                    break
        if frames and started:
            return np.concatenate(frames)
        return np.zeros(0, dtype=np.float32)
