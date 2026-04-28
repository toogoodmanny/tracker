"""
tracker/analysis/screenshot_analyser.py

Vision pipeline for the screenshots flagged by the daemon.

Flow:
1. Pull pending (unanalysed) screenshots from the DB.
2. Deduplicate: max 1 per app (or 2 if >30 min apart) — no value in 5× VS Code shots.
3. Submit them all as a single Anthropic Message Batch (50% cheaper, parallel).
4. Poll until the batch is finished (max BATCH_TIMEOUT_S seconds).
5. Fall back to sequential sync calls if the batch API fails or times out.
6. Write analysis text back to each snapshot row.

Model: uses config.api.screenshot_model (defaults to claude-3-5-haiku-20241022),
which is ~12× cheaper than Sonnet for the same simple "describe what's on screen" task.

This module never raises out; per-screenshot failures are logged and skipped.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from tracker.config import Config
from tracker.core.models import Snapshot
from tracker.db.repositories import Database

logger = logging.getLogger(__name__)

BATCH_TIMEOUT_S: float = 180.0   # 3 min — then fall back to sync
BATCH_POLL_INTERVAL_S: float = 5.0


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
            return (
                "App: {app_name}\nWindow: {window_title}\nFlagged: {trigger_reason}\n"
                "Describe in 1-3 sentences what the user is doing and whether it "
                "looks like work, drift, or a break."
            )
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def analyse_pending(self, limit: int = 15) -> ScreenshotAnalysisResult:
        """
        Analyse up to `limit` pending screenshots using the Batch API.

        At most one screenshot per unique app is analysed — there's no value
        in sending Claude 5 screenshots of VS Code. A second shot is only
        allowed if the first was taken >30 minutes earlier.

        Falls back to sequential sync calls if the batch API fails/times out.
        Returns counts + token usage. Never raises.
        """
        if not self._config.api.anthropic_api_key:
            logger.info("Skipping screenshot analysis — no API key configured")
            return ScreenshotAnalysisResult(0, 0, 0, 0)

        pending = self._db.snapshots.get_pending_screenshot_analysis()
        if not pending:
            return ScreenshotAnalysisResult(0, 0, 0, 0)

        # Deduplicate per app, then cap at limit
        pending = self._dedup_by_app(pending, limit)

        if not pending:
            return ScreenshotAnalysisResult(0, 0, 0, 0)

        try:
            return self._analyse_batch(pending)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Batch API unavailable (%s) — falling back to sequential calls", exc
            )
            return self._analyse_sync(pending)

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _dedup_by_app(self, pending: list[Snapshot], limit: int) -> list[Snapshot]:
        """Keep at most one screenshot per app (or a second one if >30 min later)."""
        deduped: list[Snapshot] = []
        seen_app: dict[str, float] = {}  # app → last kept timestamp (epoch minutes)
        for snap in pending:
            app = snap.app_name or "unknown"
            snap_minutes = snap.timestamp.timestamp() / 60.0
            last_min = seen_app.get(app)
            if last_min is None or (snap_minutes - last_min) > 30:
                deduped.append(snap)
                seen_app[app] = snap_minutes
        return deduped[:limit]

    # ------------------------------------------------------------------
    # Batch API path (50% cheaper, parallel)
    # ------------------------------------------------------------------

    def _analyse_batch(self, snaps: list[Snapshot]) -> ScreenshotAnalysisResult:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc

        client = anthropic.Anthropic(
            api_key=self._config.api.anthropic_api_key,
            max_retries=1,
            timeout=30.0,
        )

        # Build batch requests — skip any snapshot whose image file is missing
        requests: list[dict] = []
        snap_map: dict[str, Snapshot] = {}  # custom_id → snapshot
        for snap in snaps:
            if not snap.screenshot_path:
                continue
            path = Path(snap.screenshot_path)
            if not path.exists():
                logger.debug("Screenshot file missing: %s", path)
                continue
            try:
                image_bytes = path.read_bytes()
            except OSError as exc:
                logger.debug("Could not read screenshot %s: %s", path, exc)
                continue

            b64 = base64.standard_b64encode(image_bytes).decode("ascii")
            prompt = self._build_prompt(snap)
            custom_id = f"ss-{snap.id}"
            snap_map[custom_id] = snap

            requests.append({
                "custom_id": custom_id,
                "params": {
                    "model": self._config.api.screenshot_model,
                    "max_tokens": 300,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }],
                },
            })

        if not requests:
            return ScreenshotAnalysisResult(0, 0, 0, 0)

        # Submit batch
        batch = client.messages.batches.create(requests=requests)
        logger.info(
            "Screenshot batch submitted: %d images, batch_id=%s",
            len(requests), batch.id,
        )

        # Poll until ended or timeout
        deadline = time.monotonic() + BATCH_TIMEOUT_S
        while time.monotonic() < deadline:
            batch = client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            logger.debug("Batch %s status: %s — polling again in %ds",
                         batch.id, batch.processing_status, int(BATCH_POLL_INTERVAL_S))
            time.sleep(BATCH_POLL_INTERVAL_S)
        else:
            raise RuntimeError(
                f"Batch {batch.id} did not finish within {BATCH_TIMEOUT_S:.0f}s"
            )

        # Process results
        analysed = skipped = input_tokens = output_tokens = 0
        for result in client.messages.batches.results(batch.id):
            snap = snap_map.get(result.custom_id)
            if snap is None:
                continue
            if result.result.type != "succeeded":
                logger.warning(
                    "Batch screenshot failed for %s: %s",
                    result.custom_id, result.result,
                )
                skipped += 1
                continue
            message = result.result.message
            content = message.content[0] if message.content else None
            if content is None or content.type != "text":
                skipped += 1
                continue
            assert snap.id is not None
            self._db.snapshots.mark_screenshot_analysed(snap.id, content.text.strip())
            analysed += 1
            usage = getattr(message, "usage", None)
            if usage:
                input_tokens += getattr(usage, "input_tokens", 0)
                output_tokens += getattr(usage, "output_tokens", 0)

        logger.info(
            "Screenshot batch done: %d analysed, %d skipped, "
            "%d in / %d out tokens (50%% batch discount applied)",
            analysed, skipped, input_tokens, output_tokens,
        )
        return ScreenshotAnalysisResult(analysed, skipped, input_tokens, output_tokens)

    # ------------------------------------------------------------------
    # Sequential sync fallback
    # ------------------------------------------------------------------

    def _analyse_sync(self, snaps: list[Snapshot]) -> ScreenshotAnalysisResult:
        """Original one-by-one API calls, used as fallback if batch is unavailable."""
        analysed = skipped = input_tokens = output_tokens = 0
        for snap in snaps:
            try:
                analysis, in_tok, out_tok = self._analyse_one(snap)
            except RuntimeError as exc:
                logger.warning(
                    "Screenshot analysis failed for snapshot %s: %s", snap.id, exc
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
            "Screenshot sync: %d analysed, %d skipped, %d in / %d out tokens",
            analysed, skipped, input_tokens, output_tokens,
        )
        return ScreenshotAnalysisResult(analysed, skipped, input_tokens, output_tokens)

    def _analyse_one(self, snap: Snapshot) -> tuple[str | None, int, int]:
        if not snap.screenshot_path:
            return None, 0, 0
        path = Path(snap.screenshot_path)
        if not path.exists():
            return None, 0, 0
        try:
            image_bytes = path.read_bytes()
        except OSError:
            return None, 0, 0

        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        prompt = self._build_prompt(snap)
        return self._call_vision_sync(prompt, b64)

    def _call_vision_sync(self, prompt: str, image_b64: str) -> tuple[str, int, int]:
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
                model=self._config.api.screenshot_model,
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/jpeg",
                            "data": image_b64,
                        }},
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

    # ------------------------------------------------------------------
    # Prompt helper
    # ------------------------------------------------------------------

    def _build_prompt(self, snap: Snapshot) -> str:
        trigger = (
            "no app detected (signal loss)"
            if snap.app_name is None
            else f"flagged for review (unknown app or stalled activity in {snap.app_name})"
        )
        return (
            self._template
            .replace("{app_name}", snap.app_name or "unknown")
            .replace("{window_title}", snap.window_title or "")
            .replace("{timestamp}", snap.timestamp.isoformat(timespec="seconds"))
            .replace("{trigger_reason}", trigger)
        )
