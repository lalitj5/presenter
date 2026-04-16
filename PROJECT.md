# auto-present

Automatically advances presentation slides based on what the presenter is saying, without manual input.

## What it does

Listens to the presenter's microphone in real-time, transcribes speech locally, and decides when to advance to the next slide using two independent detection methods:

- **Semantic detection** — an LLM reads a rolling transcript window and compares it against the known content of each slide. When the presenter's speech has clearly moved past the current slide's content, it advances.
- **Prosodic detection** — real-time pitch and energy analysis detects delivery signals that indicate a transition: sentence-final falling intonation, trailing pauses, and drop in speech energy.

Both signals feed into a fusion layer that weighs confidence scores before issuing an advance command. Neither signal alone triggers an advance; the system errs toward caution to avoid disrupting a live presentation.

## Design goals

- **Minimal latency** — transcription runs locally via `faster-whisper`. LLM calls use prompt caching on slide content so only the new transcript window is evaluated each time.
- **Speaker-agnostic** — prosodic detection runs a calibration pass on the first 60 seconds of speech to establish per-speaker baselines before making any decisions.
- **Safe by default** — a configurable lockout window prevents back-to-back advances. A hold key lets the presenter veto any pending decision.
- **Readable and iterable** — flat module structure, all thresholds externalized to `config.yaml`, no magic.

## Target software

PowerPoint (primary), Google Slides (planned extension).

## Detection pipeline

```
Microphone
  └─ audio_capture.py       raw float32 chunks at 16kHz
      └─ transcriber.py     faster-whisper, VAD-filtered segments
          └─ transcript_buffer.py   rolling 20s window, pause detection
              ├─ semantic_detector.py   Claude API (streaming, cached prompt)
              ├─ prosodic_detector.py   pitch + energy via librosa/parselmouth
              └─ fusion.py              confidence-weighted decision
                  └─ slide_controller.py   win32com (PowerPoint) / pyautogui
```
