"""Microphone recording with a simple energy VAD, and offline transcription with faster-whisper."""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

log = logging.getLogger("eli.stt")

RATE = 16000


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
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                log.info("whisper ready")
            except Exception as e:
                self.error = f"speech-to-text unavailable: {e}"
                log.warning(self.error)

    @property
    def ready(self) -> bool:
        return self._model is not None

    INITIAL_PROMPT = (
        "Eli, Ellie, Elli, Ili, Ilii, Iliii, Iliiii, Iliiiii, Elii, Eliii, Ilai, Alai. "
        "Hey Eli, Hey Ellie, Hey Elli, Hey Ili, Hey Iliiiii, Hello Ellie, Hello Elli, "
        "Hi Ili, Hi Eli, Iliiii, Iliii, Ilii, Ili. Yes, Eli. Open VS Code, YouTube, Python, Bipolar disorder."
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
            condition_on_previous_text=False,
            initial_prompt=self.INITIAL_PROMPT,
        )
        return " ".join(s.text.strip() for s in segments).strip()


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
