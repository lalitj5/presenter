import time
from dataclasses import dataclass, field

import numpy as np
from faster_whisper import WhisperModel


@dataclass
class TranscriptSegment:
    text: str
    timestamp: float        # wall-clock time when this segment was produced
    is_silence: bool = False


class Transcriber:
    """
    Wraps faster-whisper. Accepts raw float32 audio chunks via feed() and
    returns a TranscriptSegment once enough audio has been accumulated.

    Strategy: buffer audio until `buffer_seconds` have accumulated, then
    transcribe the full buffer in one call. VAD filtering inside faster-whisper
    automatically skips silent regions so silence → is_silence=True segment.

    Latency = buffer_seconds + model inference time.
    Tune buffer_seconds in config.yaml: lower = faster response, lower accuracy.
    """

    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        sample_rate: int = 16000,
        buffer_seconds: float = 2.0,
    ):
        print(f"[Transcriber] Loading model '{model_size}' on {device} ({compute_type})...")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.sample_rate = sample_rate
        self.buffer_seconds = buffer_seconds
        self._buffer: np.ndarray = np.array([], dtype=np.float32)
        print("[Transcriber] Model ready.")

    def feed(self, chunk: np.ndarray) -> TranscriptSegment | None:
        """
        Append chunk to the internal buffer. When the buffer reaches
        buffer_seconds of audio, transcribe and return a segment.
        Returns None while still accumulating.
        """
        self._buffer = np.concatenate([self._buffer, chunk])
        buffered_seconds = len(self._buffer) / self.sample_rate

        if buffered_seconds >= self.buffer_seconds:
            return self._transcribe_and_flush()
        return None

    def _transcribe_and_flush(self) -> TranscriptSegment:
        audio = self._buffer.copy()
        self._buffer = np.array([], dtype=np.float32)

        segments, _info = self.model.transcribe(
            audio,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
        )

        # faster-whisper returns a generator — consume it
        text = " ".join(seg.text.strip() for seg in segments).strip()
        is_silence = len(text) == 0

        return TranscriptSegment(
            text=text,
            timestamp=time.time(),
            is_silence=is_silence,
        )

    def flush(self) -> TranscriptSegment | None:
        """
        Force-transcribe whatever is left in the buffer (e.g. on shutdown).
        Returns None if the buffer is empty.
        """
        if len(self._buffer) == 0:
            return None
        return self._transcribe_and_flush()
