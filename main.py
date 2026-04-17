"""
auto-present — Phase 2

Pipeline: microphone → faster-whisper → transcript buffer
          → semantic detector (Claude) → auto-advance slides

Manual controls still active:
  →  next slide   ←  prev slide   q  quit

Setup:
  1. Run slide_manifest.py against your deck to generate a .manifest.json
  2. Set presentation.manifest_path in config.yaml
  3. Set ANTHROPIC_API_KEY in your environment
  4. Open PowerPoint, press F5 to start Slide Show
  5. python main.py
"""

import asyncio
import os
import time
from datetime import datetime

import keyboard
import yaml

from audio_capture import AudioCapture
from semantic_detector import SemanticDetector
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
    """Pulls audio chunks, transcribes, appends to buffer, prints to terminal."""
    loop = asyncio.get_event_loop()

    while not stop_event.is_set():
        chunk = await loop.run_in_executor(None, capture.get_chunk, 1.0)
        if chunk is None:
            continue

        segment = await loop.run_in_executor(None, transcriber.feed, chunk)
        if segment is None:
            continue

        buffer.append(segment)

        if not segment.is_silence:
            ts = datetime.fromtimestamp(segment.timestamp).strftime("%H:%M:%S")
            print(f"[{ts}] {segment.text}")


async def semantic_loop(
    buffer: TranscriptBuffer,
    detector: SemanticDetector,
    controller: SlideController,
    config: dict,
    stop_event: asyncio.Event,
) -> None:
    """
    Watches the transcript buffer. When a pause is detected, fires a Claude
    call with the recent transcript window. If confidence exceeds the threshold
    and the lockout has expired, advances the slide automatically.
    """
    sem_cfg = config["semantic"]
    pres_cfg = config["presentation"]

    pause_threshold: float = sem_cfg["pause_threshold"]
    context_window: float = sem_cfg["context_window"]
    confidence_threshold: float = sem_cfg["confidence_threshold"]
    lockout_seconds: float = pres_cfg["lockout_seconds"]

    last_advance_time: float = 0.0
    last_check_time: float = 0.0
    min_check_interval: float = 2.0   # don't fire LLM calls faster than every 2s

    loop = asyncio.get_event_loop()

    while not stop_event.is_set():
        await asyncio.sleep(0.2)

        now = time.time()

        # Only trigger when there's a real pause and enough time since last check
        if not buffer.is_paused(pause_threshold):
            continue
        if now - last_check_time < min_check_interval:
            continue
        if now - last_advance_time < lockout_seconds:
            continue

        transcript_window = buffer.get_window(context_window)
        if not transcript_window:
            continue

        current_slide = controller.current_slide()
        if current_slide <= 0:
            continue  # couldn't read slide position

        last_check_time = now
        print(f"[Semantic] Checking slide {current_slide} — '{transcript_window[-60:]}'...")

        # Run blocking API call in thread so we don't stall the event loop
        decision = await loop.run_in_executor(
            None, detector.check, transcript_window, current_slide
        )

        if decision is None:
            continue

        print(
            f"[Semantic] advance={decision.advance} "
            f"confidence={decision.confidence:.2f} — {decision.reason}"
        )

        if decision.advance and decision.confidence >= confidence_threshold:
            print(f"[Semantic] ✓ Advancing {decision.slide_from} → {decision.slide_to}")
            controller.advance()
            last_advance_time = time.time()


async def key_listener(
    controller: SlideController,
    stop_event: asyncio.Event,
) -> None:
    """Manual controls: → next, ← back, q quit."""
    print("\n[Controls]  →  next slide   ←  prev slide   q  quit\n")

    def on_right(_):
        current = controller.current_slide()
        total = controller.total_slides()
        label = f"(slide {current}/{total})" if current > 0 else ""
        print(f"[Slide] → manual advance {label}")
        controller.advance()

    def on_left(_):
        current = controller.current_slide()
        total = controller.total_slides()
        label = f"(slide {current}/{total})" if current > 0 else ""
        print(f"[Slide] ← manual go back {label}")
        controller.go_back()

    def on_quit(_):
        print("\n[main] Quit requested.")
        stop_event.set()

    keyboard.on_press_key("right", on_right)
    keyboard.on_press_key("left", on_left)
    keyboard.on_press_key("q", on_quit)

    while not stop_event.is_set():
        await asyncio.sleep(0.1)

    keyboard.unhook_all()


async def main() -> None:
    config = load_config(CONFIG_PATH)

    # --- Validate API key ---
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[main] ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("       Set it with: set ANTHROPIC_API_KEY=sk-ant-...")
        return

    # --- Validate manifest ---
    manifest_path = config["presentation"].get("manifest_path", "")
    if not manifest_path:
        print("[main] ERROR: presentation.manifest_path not set in config.yaml.")
        print("       Run: python slide_manifest.py path/to/deck.pptx")
        return

    # --- Slide controller ---
    controller = SlideController()
    controller.connect()
    if controller.is_fallback():
        print("[main] Running in fallback mode — WScript.Shell SendKeys")

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

    # --- Semantic detector ---
    detector = SemanticDetector(
        manifest_path=manifest_path,
        model=config["semantic"]["model"],
    )

    stop_event = asyncio.Event()
    capture.start()
    print("[main] Listening... speak into your microphone.\n")

    try:
        await asyncio.gather(
            transcription_loop(capture, transcriber, buffer, stop_event),
            semantic_loop(buffer, detector, controller, config, stop_event),
            key_listener(controller, stop_event),
        )
    except asyncio.CancelledError:
        pass
    finally:
        capture.stop()
        final = transcriber.flush()
        if final and not final.is_silence:
            print(f"[final] {final.text}")
        print("[main] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
