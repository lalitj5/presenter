"""
auto-present — Phase 3

Pipeline: microphone → faster-whisper → transcript buffer
              ├─ semantic detector (Claude)  ─┐
              └─ prosodic detector (pitch)  ─┴─ fusion → auto-advance

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

import keyboard
import yaml

from audio_capture import AudioCapture
from display import LiveDisplay
from prosodic_detector import ProsodicDetector
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
    prosodic: ProsodicDetector,
    prosodic_signal_box: list,
    display: LiveDisplay,
    stop_event: asyncio.Event,
) -> None:
    loop = asyncio.get_event_loop()

    while not stop_event.is_set():
        chunk = await loop.run_in_executor(None, capture.get_chunk, 1.0)
        if chunk is None:
            continue

        # Prosodic analysis — runs on every raw chunk
        prosodic_signal = await loop.run_in_executor(None, prosodic.feed, chunk)
        display.update_pitch(prosodic.last_pitch)

        if prosodic_signal is not None:
            prosodic_signal_box[0] = prosodic_signal
            if prosodic_signal.triggered:
                display.mark_prosodic_trigger()

        # Transcription — runs on buffered chunks
        segment = await loop.run_in_executor(None, transcriber.feed, chunk)
        if segment is None:
            continue

        buffer.append(segment)

        if not segment.is_silence:
            display.update_transcript(segment.text)


async def semantic_loop(
    buffer: TranscriptBuffer,
    detector: SemanticDetector,
    prosodic_signal_box: list,
    controller: SlideController,
    display: LiveDisplay,
    config: dict,
    stop_event: asyncio.Event,
) -> None:
    """
    Fusion loop: fires Claude on pause detection, then combines semantic
    confidence with the latest prosodic signal to decide whether to advance.

    Fusion rules:
      - Advance if semantic_confidence >= HIGH_THRESHOLD (0.88) alone
      - Advance if semantic_confidence >= MID_THRESHOLD (0.65) AND prosodic_confidence >= 0.35
      - Never advance within lockout_seconds of the last advance
    """
    sem_cfg = config["semantic"]
    pres_cfg = config["presentation"]

    pause_threshold: float = sem_cfg["pause_threshold"]
    context_window: float = sem_cfg["context_window"]
    confidence_threshold: float = sem_cfg["confidence_threshold"]   # base threshold
    lockout_seconds: float = pres_cfg["lockout_seconds"]

    HIGH_THRESHOLD = confidence_threshold          # semantic alone is sufficient
    MID_THRESHOLD = confidence_threshold - 0.15    # semantic + prosodic=1 together sufficient

    last_advance_time: float = 0.0
    last_check_time: float = 0.0
    min_check_interval: float = 2.0

    loop = asyncio.get_event_loop()

    while not stop_event.is_set():
        await asyncio.sleep(0.2)

        now = time.time()

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
            continue

        last_check_time = now
        print(f"[Semantic] Checking slide {current_slide} — '{transcript_window[-60:]}'...")

        decision = await loop.run_in_executor(
            None, detector.check, transcript_window, current_slide
        )
        if decision is None:
            continue

        # Read prosodic signal — only count it if it fired within the last 8 seconds.
        # Don't clear it; it stays until overwritten by a newer signal.
        prosodic = prosodic_signal_box[0]
        if prosodic is not None and (time.time() - prosodic.timestamp) > 8.0:
            prosodic = None  # too stale to be relevant to this pause

        prosodic_triggered = prosodic.triggered if prosodic is not None else False
        sem_conf = decision.confidence

        display.update_status(
            current_slide,
            controller.total_slides(),
            f"semantic={sem_conf:.2f} prosodic={'1' if prosodic_triggered else '0'}",
        )

        should_advance = (
            decision.advance and (
                sem_conf >= HIGH_THRESHOLD
                or (sem_conf >= MID_THRESHOLD and prosodic_triggered)
            )
        )

        if should_advance:
            controller.advance()
            last_advance_time = time.time()
            display.update_status(
                decision.slide_to,
                controller.total_slides(),
                f"↑ advanced ({sem_conf:.2f})",
            )


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

    # --- Prosodic detector ---
    prosodic = ProsodicDetector(sample_rate=config["audio"]["sample_rate"])
    prosodic_signal_box = [None]

    # --- Live display ---
    display = LiveDisplay()
    display.start()

    stop_event = asyncio.Event()
    capture.start()

    try:
        await asyncio.gather(
            transcription_loop(capture, transcriber, buffer, prosodic, prosodic_signal_box, display, stop_event),
            semantic_loop(buffer, detector, prosodic_signal_box, controller, display, config, stop_event),
            key_listener(controller, stop_event),
        )
    except asyncio.CancelledError:
        pass
    finally:
        capture.stop()
        display.stop()
        final = transcriber.flush()
        if final and not final.is_silence:
            print(f"[final] {final.text}")
        print("[main] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
