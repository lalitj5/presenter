import queue
import numpy as np
import sounddevice as sd


class AudioCapture:
    """
    Captures microphone audio in real-time and pushes float32 numpy chunks
    into a thread-safe queue. Runs entirely via a sounddevice callback so it
    never blocks the main thread.
    """

    def __init__(self, sample_rate: int = 16000, chunk_duration: float = 0.5, channels: int = 1):
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration)  # samples per chunk
        self.channels = channels
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        if status:
            print(f"[AudioCapture] Warning: {status}")
        # Always mono float32; squeeze to 1-D
        chunk = indata[:, 0].copy() if indata.ndim > 1 else indata.flatten().copy()
        self._queue.put(chunk)

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=np.float32,
            blocksize=self.chunk_size,
            callback=self._callback,
        )
        self._stream.start()
        print(f"[AudioCapture] Started — {self.sample_rate}Hz, {self.chunk_size} samples/chunk")

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        print("[AudioCapture] Stopped")

    def get_chunk(self, timeout: float = 2.0) -> np.ndarray | None:
        """
        Block until a chunk is available and return it.
        Returns None on timeout (e.g. during shutdown).
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def list_devices(self) -> None:
        """Print available audio input devices — useful for debugging mic selection."""
        print(sd.query_devices())
