"""
auto-present — Phase 0

Pipeline: microphone → faster-whisper → terminal transcript
Slide control: manual only (→ / ← arrow keys, q to quit)

Run:
    python main.py

Requirements:
    - PowerPoint open with Slide Show running (F5), OR
    - Any focused window (fallback mode uses arrow keys via pyautogui)
"""

import asyncio
import time
from datetime import datetime

import keyboard
import yaml

from audio_capture import AudioCapture
from slide_controller import SlideController
from transcript_buffer import TranscriptBuffer
from transcriber import Transcriber

CONFIG_PATH = "config.yaml"


def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


async def transcription_loop(
    capture: AudioCapture,
    transcriber: Transcriber,
    buffer: TranscriptBuffer,
    stop_event: asyncio.Event,
) -> None:
    """
    Continuously pulls audio chunks from AudioCapture, feeds them to the
    Transcriber, and prints non-silence segments to the terminal.
    """
    loop = asyncio.get_event_loop()

    while not stop_event.is_set():
        # get_chunk() is blocking — run it in a thread so we don't stall the loop
        chunk = await loop.run_in_executor(None, capture.get_chunk, 1.0)
        if chunk is None:
            continue

        # feed() returns a segment only when enough audio has been buffered
        segment = await loop.run_in_executor(None, transcriber.feed, chunk)
        if segment is None:
            continue

        buffer.append(segment)

        if not segment.is_silence:
            ts = datetime.fromtimestamp(segment.timestamp).strftime("%H:%M:%S")
            print(f"[{ts}] {segment.text}")


async def key_listener(
    controller: SlideController,
    stop_event: asyncio.Event,
) -> None:
    """
    Listens for keyboard input to manually control slides and quit.
      →  advance slide
      ←  go back
      q  quit
    """
    print("\n[Controls]  →  next slide   ←  prev slide   q  quit\n")

    def on_right(_):
        current = controller.current_slide()
        total = controller.total_slides()
        label = f"(slide {current}/{total})" if current > 0 else ""
        print(f"[Slide] → advance {label}")
        controller.advance()

    def on_left(_):
        current = controller.current_slide()
        total = controller.total_slides()
        label = f"(slide {current}/{total})" if current > 0 else ""
        print(f"[Slide] ← go back {label}")
        controller.go_back()

    def on_quit(_):
        print("\n[main] Quit requested.")
        stop_event.set()

    keyboard.on_press_key("right", on_right)
    keyboard.on_press_key("left", on_left)
    keyboard.on_press_key("q", on_quit)

    # Keep the coroutine alive until stop_event fires
    while not stop_event.is_set():
        await asyncio.sleep(0.1)

    keyboard.unhook_all()


async def main() -> None:
    config = load_config(CONFIG_PATH)

    # --- Slide controller ---
    controller = SlideController()
    controller.connect()
    if controller.is_fallback():
        print("[main] Running in fallback mode — arrow keys sent to focused window")

    # --- Transcription stack ---
    transcriber = Transcriber(
        model_size=config["transcription"]["model"],
        device=config["transcription"]["device"],
        compute_type=config["transcription"]["compute_type"],
        buffer_seconds=config["transcription"]["buffer_seconds"],
    )

    capture = AudioCapture(
        sample_rate=config["audio"]["sample_rate"],
        chunk_duration=config["audio"]["chunk_duration"],
        channels=config["audio"]["channels"],
    )

    buffer = TranscriptBuffer()

    stop_event = asyncio.Event()

    capture.start()
    print("[main] Listening... speak into your microphone.\n")

    try:
        await asyncio.gather(
            transcription_loop(capture, transcriber, buffer, stop_event),
            key_listener(controller, stop_event),
        )
    except asyncio.CancelledError:
        pass
    finally:
        capture.stop()
        # Flush any remaining buffered audio
        final = transcriber.flush()
        if final and not final.is_silence:
            print(f"[final] {final.text}")
        print("[main] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
