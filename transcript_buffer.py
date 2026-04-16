import time
from collections import deque
from dataclasses import dataclass

from transcriber import TranscriptSegment


@dataclass
class _Entry:
    text: str
    timestamp: float
    is_silence: bool


class TranscriptBuffer:
    """
    Rolling window of transcript segments. Keeps a configurable maximum of
    history (default 5 minutes) so memory stays bounded during long presentations.

    Used by:
      - main.py: print recent speech to terminal
      - semantic_detector.py (Phase 2): get_window() feeds the LLM context
      - main.py (Phase 2): is_paused() triggers LLM checks
    """

    def __init__(self, max_seconds: float = 300.0):
        self.max_seconds = max_seconds
        self._entries: deque[_Entry] = deque()
        self._last_silence_start: float | None = None

    def append(self, segment: TranscriptSegment) -> None:
        entry = _Entry(
            text=segment.text,
            timestamp=segment.timestamp,
            is_silence=segment.is_silence,
        )
        self._entries.append(entry)

        # Track when silence began (used by is_paused)
        if segment.is_silence and self._last_silence_start is None:
            self._last_silence_start = segment.timestamp
        elif not segment.is_silence:
            self._last_silence_start = None

        self._evict_old()

    def get_window(self, seconds: float) -> str:
        """
        Return the last `seconds` of non-silence transcript text as a single
        joined string. Used as the transcript context for LLM calls.
        """
        cutoff = time.time() - seconds
        words = [
            e.text
            for e in self._entries
            if not e.is_silence and e.timestamp >= cutoff
        ]
        return " ".join(words).strip()

    def is_paused(self, threshold: float) -> bool:
        """
        Returns True when the presenter has been silent for at least
        `threshold` seconds. Used to trigger a semantic check (Phase 2).
        """
        if self._last_silence_start is None:
            return False
        return (time.time() - self._last_silence_start) >= threshold

    def last_speech_ago(self) -> float:
        """Seconds since the last non-silence segment was appended."""
        for entry in reversed(self._entries):
            if not entry.is_silence:
                return time.time() - entry.timestamp
        return float("inf")

    def _evict_old(self) -> None:
        cutoff = time.time() - self.max_seconds
        while self._entries and self._entries[0].timestamp < cutoff:
            self._entries.popleft()
