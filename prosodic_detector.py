"""
Prosodic slide detector — deterministic binary signal.

Tracks pitch in real-time across each utterance. At every speech→silence
boundary, computes the percentage drop from the utterance's peak pitch to
its final pitch. If the drop exceeds DROP_THRESHOLD, fires signal=1.

No confidence scores — either the pitch dropped enough or it didn't.

Tune DROP_THRESHOLD and MIN_UTTERANCE_SECONDS in config.yaml or directly
below to match your speaking style.
"""

import time
from dataclasses import dataclass, field

import librosa
import numpy as np


# --- Tunable constants ---
DROP_THRESHOLD = 0.15          # pitch must drop by this % from peak to fire
MIN_UTTERANCE_SECONDS = 1.5    # ignore utterances shorter than this
MIN_PITCH_SAMPLES = 5          # need at least this many voiced frames
CALIBRATION_SECONDS = 30.0     # seconds of speech needed to calibrate


@dataclass
class ProsodicSignal:
    triggered: bool             # True = pitch dropped enough, advance signal
    drop_pct: float             # actual percentage drop observed
    peak_hz: float              # utterance peak pitch
    final_hz: float             # utterance final pitch
    timestamp: float = field(default_factory=time.time)


class ProsodicDetector:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate

        # Calibration
        self._calibrated = False
        self._calib_energies: list[float] = []
        self._calib_speech_seconds: float = 0.0
        self._energy_threshold: float = 0.01   # updated after calibration

        # Per-utterance state
        self._in_speech: bool = False
        self._speech_start: float = 0.0
        self._utterance_pitches: list[float] = []   # all voiced pitch values this utterance

        # Exposed for display
        self.last_pitch: float = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def feed(self, chunk: np.ndarray) -> ProsodicSignal | None:
        """
        Process one audio chunk. Returns a ProsodicSignal at each
        speech→silence boundary, or None while still in utterance / silence.
        """
        energy = self._rms(chunk)
        pitch = self._estimate_pitch(chunk)

        if not self._calibrated:
            self._calibrate(energy, chunk)
            return None

        is_speech = energy > self._energy_threshold

        self.last_pitch = pitch  # always expose for display

        if is_speech:
            if not self._in_speech:
                # Silence → speech: start new utterance
                self._in_speech = True
                self._speech_start = time.time()
                self._utterance_pitches = []

            if pitch > 0:
                self._utterance_pitches.append(pitch)

        else:
            if self._in_speech:
                # Speech → silence: evaluate and reset
                self._in_speech = False
                return self._evaluate()

        return None

    def is_calibrated(self) -> bool:
        return self._calibrated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rms(self, chunk: np.ndarray) -> float:
        return float(np.sqrt(np.mean(chunk ** 2)))

    def _estimate_pitch(self, chunk: np.ndarray) -> float:
        """Return mean voiced F0 for this chunk, or 0.0 if unvoiced/too short."""
        if len(chunk) < int(self.sample_rate / 50):
            return 0.0
        try:
            f0 = librosa.yin(
                chunk,
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C6"),
                sr=self.sample_rate,
            )
            voiced = f0[f0 > 0]
            return float(np.mean(voiced)) if len(voiced) > 0 else 0.0
        except Exception:
            return 0.0

    def _calibrate(self, energy: float, chunk: np.ndarray) -> None:
        if energy > 0.01:
            self._calib_energies.append(energy)
            self._calib_speech_seconds += len(chunk) / self.sample_rate

        if self._calib_speech_seconds >= CALIBRATION_SECONDS:
            mean_energy = float(np.mean(self._calib_energies))
            self._energy_threshold = mean_energy * 0.30
            self._calibrated = True
            print(
                f"[ProsodicDetector] Calibrated — "
                f"mean energy: {mean_energy:.4f}, "
                f"silence threshold: {self._energy_threshold:.4f}"
            )

    def _evaluate(self) -> ProsodicSignal | None:
        """
        Compare peak pitch to final pitch across the utterance.
        Returns None if utterance is too short or too few voiced frames.
        """
        duration = time.time() - self._speech_start
        pitches = self._utterance_pitches

        if duration < MIN_UTTERANCE_SECONDS:
            return None
        if len(pitches) < MIN_PITCH_SAMPLES:
            return None

        peak_hz = max(pitches)

        # "Final pitch" = mean of last 20% of voiced frames
        tail_start = max(0, int(len(pitches) * 0.80))
        final_hz = float(np.mean(pitches[tail_start:]))

        drop_pct = (peak_hz - final_hz) / (peak_hz + 1e-6)

        triggered = drop_pct >= DROP_THRESHOLD

        signal = ProsodicSignal(
            triggered=triggered,
            drop_pct=drop_pct,
            peak_hz=peak_hz,
            final_hz=final_hz,
        )

        if triggered:
            print(
                f"[ProsodicDetector] ↓ pitch drop {drop_pct:.0%} "
                f"({peak_hz:.0f}Hz → {final_hz:.0f}Hz) — signal=1"
            )

        return signal
