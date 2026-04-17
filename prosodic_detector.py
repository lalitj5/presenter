"""
Prosodic slide detector. Analyses raw audio chunks in real-time for acoustic
signals that indicate a slide transition:

  - Falling pitch at the end of an utterance (sentence-final declination)
  - Energy drop from speech to silence
  - Utterance duration long enough to be a complete thought

A calibration phase (first ~30s of speech) establishes per-speaker baselines
for pitch and energy so thresholds are not hard-coded.

Signal output: ProsodicSignal with a confidence score 0.0–1.0.
"""

import time
from collections import deque
from dataclasses import dataclass, field

import librosa
import numpy as np


@dataclass
class ProsodicSignal:
    confidence: float       # 0.0 – 1.0
    reason: str             # human-readable explanation
    timestamp: float = field(default_factory=time.time)


class ProsodicDetector:
    def __init__(
        self,
        sample_rate: int = 16000,
        calibration_seconds: float = 30.0,
    ):
        self.sample_rate = sample_rate
        self.calibration_seconds = calibration_seconds

        # Calibration accumulators
        self._calibrated = False
        self._calib_pitches: list[float] = []
        self._calib_energies: list[float] = []
        self._calib_speech_seconds: float = 0.0

        # Baselines set after calibration
        self._mean_pitch: float = 0.0
        self._mean_energy: float = 0.0

        # Rolling pitch history for trend detection (last ~1s of frames)
        self._pitch_window: deque[float] = deque(maxlen=20)

        # Track the most recent signal so main.py can poll it
        self._last_signal: ProsodicSignal | None = None
        self._last_signal_consumed: bool = True

        # Speech/silence tracking
        self._in_speech: bool = False
        self._speech_start: float = 0.0
        self._energy_threshold: float = 0.01   # updated post-calibration

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def feed(self, chunk: np.ndarray) -> ProsodicSignal | None:
        """
        Process one audio chunk. Returns a ProsodicSignal at the moment a
        speech→silence boundary is detected with transition-like acoustics,
        or None otherwise.
        """
        energy = self._rms(chunk)
        pitch = self._estimate_pitch(chunk)

        if not self._calibrated:
            self._calibrate(chunk, energy, pitch)
            return None

        is_speech = energy > self._energy_threshold

        if is_speech and pitch > 0:
            self._pitch_window.append(pitch)

        signal = None

        if is_speech and not self._in_speech:
            # Silence → speech: start tracking utterance
            self._in_speech = True
            self._speech_start = time.time()
            self._pitch_window.clear()

        elif not is_speech and self._in_speech:
            # Speech → silence: evaluate the just-finished utterance
            self._in_speech = False
            utterance_duration = time.time() - self._speech_start
            signal = self._evaluate_utterance(utterance_duration)

        return signal

    def is_calibrated(self) -> bool:
        return self._calibrated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rms(self, chunk: np.ndarray) -> float:
        return float(np.sqrt(np.mean(chunk ** 2)))

    def _estimate_pitch(self, chunk: np.ndarray) -> float:
        """
        Estimate fundamental frequency using librosa.yin.
        Returns 0.0 if the chunk is too short or pitch is unvoiced.
        """
        min_samples = int(self.sample_rate / 50)  # need at least 20ms
        if len(chunk) < min_samples:
            return 0.0
        try:
            f0 = librosa.yin(
                chunk,
                fmin=librosa.note_to_hz("C2"),   # ~65 Hz
                fmax=librosa.note_to_hz("C6"),   # ~1047 Hz
                sr=self.sample_rate,
            )
            voiced = f0[f0 > 0]
            return float(np.mean(voiced)) if len(voiced) > 0 else 0.0
        except Exception:
            return 0.0

    def _calibrate(self, chunk: np.ndarray, energy: float, pitch: float) -> None:
        """Collect stats during the calibration window."""
        is_speech = energy > 0.01
        if is_speech:
            chunk_duration = len(chunk) / self.sample_rate
            self._calib_speech_seconds += chunk_duration
            self._calib_energies.append(energy)
            if pitch > 0:
                self._calib_pitches.append(pitch)

        if self._calib_speech_seconds >= self.calibration_seconds:
            self._finish_calibration()

    def _finish_calibration(self) -> None:
        self._mean_pitch = float(np.mean(self._calib_pitches)) if self._calib_pitches else 180.0
        self._mean_energy = float(np.mean(self._calib_energies)) if self._calib_energies else 0.05
        # Energy threshold = 30% of mean speech energy
        self._energy_threshold = self._mean_energy * 0.30
        self._calibrated = True
        print(
            f"[ProsodicDetector] Calibrated — "
            f"mean pitch: {self._mean_pitch:.0f}Hz, "
            f"mean energy: {self._mean_energy:.4f}, "
            f"silence threshold: {self._energy_threshold:.4f}"
        )

    def _evaluate_utterance(self, duration: float) -> ProsodicSignal | None:
        """
        Analyse the pitch window from the just-finished utterance.
        Returns a signal if it looks like a slide-transition boundary.
        """
        # Utterance must be long enough to be a real sentence
        if duration < 1.5:
            return None

        pitches = list(self._pitch_window)
        if len(pitches) < 4:
            return None

        confidence = 0.0
        reasons = []

        # --- Signal 1: falling pitch trend ---
        # Compare mean pitch of last 25% of frames vs first 75%
        split = max(1, int(len(pitches) * 0.75))
        early_mean = np.mean(pitches[:split])
        late_mean = np.mean(pitches[split:])
        pitch_drop_ratio = (early_mean - late_mean) / (early_mean + 1e-6)

        if pitch_drop_ratio > 0.10:   # pitch fell by >10% at end of utterance
            drop_confidence = min(pitch_drop_ratio / 0.30, 1.0) * 0.6
            confidence += drop_confidence
            reasons.append(f"pitch drop {pitch_drop_ratio:.0%}")

        # --- Signal 2: final pitch below speaker mean ---
        below_mean_ratio = (self._mean_pitch - late_mean) / (self._mean_pitch + 1e-6)
        if below_mean_ratio > 0.05:
            below_confidence = min(below_mean_ratio / 0.20, 1.0) * 0.3
            confidence += below_confidence
            reasons.append(f"final pitch {below_mean_ratio:.0%} below mean")

        # --- Signal 3: utterance duration bonus ---
        if duration > 4.0:
            confidence = min(confidence + 0.10, 1.0)
            reasons.append(f"long utterance {duration:.1f}s")

        if confidence < 0.25:
            return None

        return ProsodicSignal(
            confidence=min(confidence, 1.0),
            reason=", ".join(reasons),
        )
