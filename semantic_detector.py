"""
Semantic slide detector. On each call, sends a rolling transcript window +
slide manifest context to Claude and asks whether to advance.

Prompt caching strategy:
  - System prompt contains the full slide manifest (never changes per session)
    → marked cache_control: ephemeral so Anthropic caches it for 5 minutes
  - User message contains only the rolling transcript window + current position
    → sent fresh each call, cheap because system prompt is cached

Response format: a single JSON object streamed back by the model:
  {"advance": true/false, "confidence": 0.0-1.0, "reason": "..."}
"""

import json
import os
import re
import time
from dataclasses import dataclass

import anthropic

from slide_manifest import load_manifest

from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("ANTHROPIC_API_KEY")

@dataclass
class SlideDecision:
    advance: bool
    confidence: float
    reason: str
    slide_from: int
    slide_to: int


class SemanticDetector:
    def __init__(self, manifest_path: str, model: str = "claude-sonnet-4-6"):
        self.manifest = load_manifest(manifest_path)
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key)
        self._system_prompt = self._build_system_prompt()
        print(f"[SemanticDetector] Loaded {len(self.manifest)} slides, model={model}")

    def _build_system_prompt(self) -> str:
        """
        Build the system prompt containing the full slide manifest.
        This is sent with cache_control so Anthropic caches it — subsequent
        calls only pay tokens for the user message (transcript window).
        """
        lines = [
            "You are a presentation assistant. Your only job is to decide whether "
            "a presenter should advance to the next slide based on what they are saying.\n",
            "Here is the full slide deck content:\n",
        ]
        for slide in self.manifest:
            lines.append(f"--- Slide {slide['slide']} ---")
            if slide["title"]:
                lines.append(f"Title: {slide['title']}")
            if slide["body"]:
                lines.append(f"Content: {slide['body']}")
            if slide["notes"]:
                lines.append(f"Speaker notes: {slide['notes']}")
            lines.append("")

        lines += [
            "Rules:",
            "- Advance only when the presenter has clearly finished discussing the current slide's content.",
            "- Do NOT advance if the presenter is mid-sentence or still on the current topic.",
            "- Do NOT advance if there is no clear signal the topic has shifted.",
            "- Respond ONLY with a JSON object — no explanation outside the JSON.",
            'Response format: {"advance": true/false, "confidence": 0.0-1.0, "reason": "one sentence"}',
        ]
        return "\n".join(lines)

    def check(self, transcript_window: str, current_slide: int) -> SlideDecision | None:
        """
        Ask Claude whether to advance from current_slide.
        Returns a SlideDecision, or None if the call fails.
        current_slide is 1-indexed.
        """
        total = len(self.manifest)
        if current_slide >= total:
            return None  # already on last slide

        next_slide = current_slide + 1

        user_message = (
            f"Currently on slide {current_slide} of {total}.\n\n"
            f"Recent speech transcript:\n\"{transcript_window}\"\n\n"
            f"Should the presenter advance to slide {next_slide}?"
        )

        try:
            decision = self._call_claude(user_message, current_slide, next_slide)
            return decision
        except Exception as e:
            print(f"[SemanticDetector] API call failed: {e}")
            return None

    def _call_claude(self, user_message: str, current_slide: int, next_slide: int) -> SlideDecision:
        """
        Stream a response from Claude. The system prompt is sent with
        cache_control so it's only tokenised once per 5-minute cache window.
        """
        full_response = ""

        with self.client.messages.stream(
            model=self.model,
            max_tokens=150,
            system=[
                {
                    "type": "text",
                    "text": self._system_prompt,
                    "cache_control": {"type": "ephemeral"},  # cache the slide manifest
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                full_response += text

        return self._parse_response(full_response, current_slide, next_slide)

    def _parse_response(self, raw: str, current_slide: int, next_slide: int) -> SlideDecision:
        """Extract JSON from the streamed response."""
        # Strip markdown code fences if present
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON found in response: {raw!r}")

        data = json.loads(match.group())
        return SlideDecision(
            advance=bool(data.get("advance", False)),
            confidence=float(data.get("confidence", 0.0)),
            reason=data.get("reason", ""),
            slide_from=current_slide,
            slide_to=next_slide,
        )
