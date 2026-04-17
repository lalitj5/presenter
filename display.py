"""
Live terminal display for auto-present.

Layout:
  ┌─────────────────────────────────────────────┐
  │  auto-present   Slide 3/21   semantic=0.72  │  ← status header
  ├─────────────────────────────────────────────┤
  │  Pitch (Hz)  [last 30s]                     │
  │   400 ·                                     │
  │   300 ·  ·╭──╮  ╭╮  ╭──────╮               │  ← scrolling pitch graph
  │   200 ·──╯  ╰──╯╰──╯       ╰──             │     red marker = prosodic trigger
  │   100 ·                                     │
  ├─────────────────────────────────────────────┤
  │  "...and that's why the market opportunity" │  ← rolling subtitle
  │  "is particularly significant going forward"│
  └─────────────────────────────────────────────┘

Runs in a daemon thread — call start() once, feed data via update_* methods,
call stop() on shutdown.
"""

import threading
import time
from collections import deque

import plotext as ptx
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text


PITCH_WINDOW_SECONDS = 30.0
SUBTITLE_LINES = 3          # how many transcript segments to keep visible
REFRESH_HZ = 10             # display refresh rate


class LiveDisplay:
    def __init__(self):
        # Pitch time series — (relative_time, hz)
        self._pitch_times: deque[float] = deque()
        self._pitch_values: deque[float] = deque()
        self._prosodic_markers: deque[float] = deque()   # times of prosodic triggers

        # Subtitle — rolling window of transcript segments
        self._subtitle: deque[str] = deque(maxlen=SUBTITLE_LINES)

        # Status line fields
        self._slide_label: str = "Slide --/--"
        self._last_decision: str = ""

        self._lock = threading.Lock()
        self._running = False
        self._start_time = time.time()

    # ------------------------------------------------------------------
    # Public feed methods — called from asyncio tasks via run_in_executor
    # ------------------------------------------------------------------

    def update_pitch(self, pitch_hz: float) -> None:
        """Feed a pitch value. Pass 0.0 for silence/unvoiced frames."""
        with self._lock:
            t = time.time() - self._start_time
            self._pitch_times.append(t)
            self._pitch_values.append(pitch_hz)
            cutoff = t - PITCH_WINDOW_SECONDS
            while self._pitch_times and self._pitch_times[0] < cutoff:
                self._pitch_times.popleft()
                self._pitch_values.popleft()

    def update_transcript(self, text: str) -> None:
        """Append a new transcript segment (subtitle line)."""
        with self._lock:
            self._subtitle.append(text)

    def update_status(self, slide: int, total: int, decision_str: str = "") -> None:
        """Update the header status line."""
        with self._lock:
            self._slide_label = f"Slide {slide}/{total}" if slide > 0 else "Slide --/--"
            self._last_decision = decision_str

    def mark_prosodic_trigger(self) -> None:
        """Place a red vertical marker on the pitch graph at the current time."""
        with self._lock:
            t = time.time() - self._start_time
            self._prosodic_markers.append(t)
            # Keep only markers within the visible window
            cutoff = t - PITCH_WINDOW_SECONDS
            while self._prosodic_markers and self._prosodic_markers[0] < cutoff:
                self._prosodic_markers.popleft()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Rendering — runs entirely inside the display thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        console = Console()
        with Live(
            self._render(),
            console=console,
            refresh_per_second=REFRESH_HZ,
            screen=True,
        ) as live:
            while self._running:
                live.update(self._render())
                time.sleep(1.0 / REFRESH_HZ)

    def _render(self) -> Layout:
        with self._lock:
            times = list(self._pitch_times)
            values = list(self._pitch_values)
            markers = list(self._prosodic_markers)
            subtitle = list(self._subtitle)
            slide_label = self._slide_label
            last_decision = self._last_decision

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="pitch", size=16),
            Layout(name="subtitle", size=6),
        )

        # --- Header ---
        header_text = Text()
        header_text.append("auto-present", style="bold cyan")
        header_text.append(f"   {slide_label}", style="bold white")
        if last_decision:
            header_text.append(f"   {last_decision}", style="dim")
        layout["header"].update(Panel(header_text))

        # --- Pitch graph ---
        layout["pitch"].update(
            Panel(
                self._render_pitch(times, values, markers),
                title="[blue]Pitch (Hz)[/blue]",
                border_style="blue",
            )
        )

        # --- Subtitle ---
        layout["subtitle"].update(
            Panel(
                self._render_subtitle(subtitle),
                title="[green]Transcript[/green]",
                border_style="green",
            )
        )

        return layout

    def _render_pitch(self, times, values, markers) -> str:
        voiced_t = [t for t, v in zip(times, values) if v > 0]
        voiced_v = [v for t, v in zip(times, values) if v > 0]

        if not voiced_t:
            return "[dim]Waiting for speech...[/dim]"

        ptx.clear_figure()
        ptx.plot_size(90, 12)
        ptx.theme("dark")
        ptx.scatter(voiced_t, voiced_v, marker="dot", color="cyan", label="pitch Hz")

        for m in markers:
            if times and m >= times[0]:
                ptx.vertical_line(m, color="red")

        ptx.xlabel("seconds")
        ptx.ylim(50, 450)
        ptx.yfrequency(4)
        ptx.xfrequency(6)

        return ptx.build()

    def _render_subtitle(self, segments: list[str]) -> Text:
        if not segments:
            return Text("Waiting for speech...", style="dim")

        text = Text()
        for i, seg in enumerate(segments):
            is_latest = i == len(segments) - 1
            style = "bold white" if is_latest else "dim white"
            text.append(seg.strip(), style=style)
            if not is_latest:
                text.append("  ", style="dim white")
        return text
