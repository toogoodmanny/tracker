"""
tracker/analysis/screenshot_analyser.py

Vision pipeline for the screenshots flagged by the daemon.

Flow:
1. Pull all pending (unanalysed) screenshots from the DB.
2. For each, send the JPEG + window context to Claude's vision API.
3. Store the short analysis text back on the snapshot row.

This module never raises out; per-screenshot failures are logged and skipped
so a single bad image cannot block the whole daily report.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from tracker.config import Config
from tracker.core.models import Snapshot
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)


@dataclass
class ScreenshotAnalysisResult:
    analysed_count: int
    skipped_count: int
    input_tokens: int
    output_tokens: int


class ScreenshotAnalyser:
    """Runs vision-LLM analysis on pending screenshots."""

    def __init__(self, config: Config, db: Database) -> None:
        self._config = config
        self._db = db
        self._template = self._load_template()

    def _load_template(self) -> str:
        path = self._config.paths.prompts_dir / "screenshot-analysis.txt"
        if not path.exists():
            # Fallback to inline minimal prompt so tracker still works if the
            # template was never bundled.
            return (
                "App: {app_name}\nWindow: {window_title}\nFlagged: {trigger_reason}\n"
                "Describe in 1-3 sentences what the user is doing and whether it "
                "looks like work, drift, or a break."
            )
        return path.read_text(encoding="utf-8")

    def analyse_pending(self, limit: int = 50) -> ScreenshotAnalysisResult:
        """
        Analyse up to `limit` pending screenshots.

        Returns counts + token usage. Never raises.
        """
        if not self._config.api.anthropic_api_key:
            logger.info("Skipping screenshot analysis — no API key configured")
            return ScreenshotAnalysisResult(0, 0, 0, 0)

        pending = self._db.snapshots.get_pending_screenshot_analysis()
        if not pending:
            return ScreenshotAnalysisResult(0, 0, 0, 0)

        pending = pending[:limit]
        analysed = 0
        skipped = 0
        input_tokens = 0
        output_tokens = 0

        for snap in pending:
            try:
                analysis, in_tok, out_tok = self._analyse_one(snap)
            except RuntimeError as exc:
                logger.warning(
                    "Screenshot analysis failed for snapshot %s: %s",
                    snap.id, exc,
                )
                skipped += 1
                continue

            if analysis is None:
                skipped += 1
                continue

            assert snap.id is not None
            self._db.snapshots.mark_screenshot_analysed(snap.id, analysis)
            analysed += 1
            input_tokens += in_tok
            output_tokens += out_tok

        logger.info(
            "Screenshot analysis: %d analysed, %d skipped, %d in / %d out tokens",
            analysed, skipped, input_tokens, output_tokens,
        )
        return ScreenshotAnalysisResult(
            analysed_count=analysed,
            skipped_count=skipped,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _analyse_one(
        self, snap: Snapshot
    ) -> tuple[str | None, int, int]:
        """Returns (analysis_text or None if skipped, input_tokens, output_tokens)."""
        if not snap.screenshot_path:
            return None, 0, 0

        path = Path(snap.screenshot_path)
        if not path.exists():
            logger.debug("Screenshot file missing: %s", path)
            return None, 0, 0

        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            logger.debug("Could not read screenshot %s: %s", path, exc)
            return None, 0, 0

        prompt = self._build_prompt(snap)
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        return self._call_vision(prompt, b64)

    def _build_prompt(self, snap: Snapshot) -> str:
        # Trigger reason isn't stored separately — derive from snapshot fields.
        if snap.app_name is None:
            trigger = "no app detected (signal loss)"
        else:
            trigger = (
                f"flagged for review (unknown app or stalled activity in {snap.app_name})"
            )
        return (
            self._template
            .replace("{app_name}", snap.app_name or "unknown")
            .replace("{window_title}", snap.window_title or "")
            .replace("{timestamp}", snap.timestamp.isoformat(timespec="seconds"))
            .replace("{trigger_reason}", trigger)
        )

    def _call_vision(
        self, prompt: str, image_b64: str
    ) -> tuple[str, int, int]:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc

        client = anthropic.Anthropic(
            api_key=self._config.api.anthropic_api_key,
            max_retries=1,
            timeout=30.0,
        )

        try:
            message = client.messages.create(
                model=self._config.api.anthropic_model,
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
        except anthropic.AuthenticationError as exc:
            raise RuntimeError(f"Invalid Anthropic API key: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError(f"Anthropic rate limit hit: {exc}") from exc
        except anthropic.APIError as exc:
            raise RuntimeError(f"Anthropic API error: {exc}") from exc

        content = message.content[0]
        if content.type != "text":
            raise RuntimeError(f"Unexpected content type: {content.type}")

        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage else 0
        return content.text.strip(), in_tok, out_tok
