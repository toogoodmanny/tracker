"""
tracker/collectors/screenshot.py

Captures screenshots on macOS, compresses them, stores locally.
LLM analysis is NOT triggered here — the daemon decides when to call it.

Screenshot strategy:
- Capture every N seconds (default 90)
- Compress to JPEG at low quality (~50KB per screenshot)
- Store in screenshots_dir named by ISO timestamp
- Return path — daemon stores it in the snapshot row
- LLM analysis happens separately, triggered by conditions in daemon

macOS-only. Returns None on other platforms.
Uses screencapture (built-in macOS tool) — no extra dependencies.
Falls back to Pillow + Quartz if screencapture unavailable.
"""

from __future__ import annotations

import datetime
import logging
import platform
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_MACOS = platform.system() == "Darwin"

# JPEG quality — lower = smaller file, still readable by vision models
_JPEG_QUALITY = 40
# Max dimension in pixels — resize before storage to cut token cost
_MAX_DIMENSION = 1280


class ScreenshotCollector:
    """
    Captures and stores compressed screenshots.

    Never raises — all errors return None.
    """

    def __init__(self, screenshots_dir: Path) -> None:
        self._dir = screenshots_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._permission_warned = False

    def capture(self) -> str | None:
        """
        Capture the current screen and save as compressed JPEG.
        Returns the file path, or None on failure.
        """
        if not _IS_MACOS:
            return None

        timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        output_path = self._dir / f"{timestamp}.jpg"

        try:
            return self._capture_via_screencapture(output_path)
        except PermissionError as exc:
            if not self._permission_warned:
                logger.warning(
                    "Screen Recording permission not granted. "
                    "Grant in System Settings → Privacy → Screen Recording → Terminal. "
                    "Error: %s",
                    exc,
                )
                self._permission_warned = True
            return None
        except FileNotFoundError as exc:
            logger.debug("screencapture not found: %s", exc)
            return self._capture_via_pillow(output_path)
        except subprocess.CalledProcessError as exc:
            logger.debug("screencapture failed: %s", exc)
            return None
        except OSError as exc:
            logger.debug("Screenshot OS error: %s", exc)
            return None

    def _capture_via_screencapture(self, output_path: Path) -> str | None:
        """
        Use macOS built-in screencapture command.
        Captures main display only (-m flag).
        -x = no sound, -t jpg = JPEG format.
        """
        # Capture to temp file first, then compress with Pillow
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            result = subprocess.run(
                ["screencapture", "-x", "-m", str(tmp_path)],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.debug(
                    "screencapture returned %d: %s",
                    result.returncode,
                    result.stderr.decode(errors="replace"),
                )
                return None

            return _compress_image(tmp_path, output_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _capture_via_pillow(self, output_path: Path) -> str | None:
        """
        Fallback: use Pillow + Quartz to capture the screen.
        Only available on macOS with pyobjc-framework-Quartz.
        """
        try:
            import Quartz  # type: ignore[import-untyped]
            from PIL import Image  # type: ignore[import-untyped]
        except ImportError as exc:
            logger.debug("Pillow/Quartz not available for screenshot fallback: %s", exc)
            return None

        try:
            image_ref = Quartz.CGWindowListCreateImage(
                Quartz.CGRectInfinite,
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
                Quartz.kCGWindowImageDefault,
            )
            if image_ref is None:
                return None

            width = Quartz.CGImageGetWidth(image_ref)
            height = Quartz.CGImageGetHeight(image_ref)

            # Convert to PIL image
            data_provider = Quartz.CGImageGetDataProvider(image_ref)
            data = Quartz.CGDataProviderCopyData(data_provider)
            img = Image.frombuffer("RGBA", (width, height), bytes(data), "raw", "BGRA", 0, 1)
            img = img.convert("RGB")

            return _save_compressed(img, output_path)
        except Exception as exc:
            logger.debug("Pillow screenshot failed: %s", exc)
            return None

    def prune_old_screenshots(self, keep_days: int = 30) -> int:
        """
        Delete screenshots older than keep_days.
        Returns number of files deleted.
        Raises nothing — prune failures are non-critical.
        """
        cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
        deleted = 0
        try:
            for f in self._dir.glob("*.jpg"):
                try:
                    mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < cutoff:
                        f.unlink()
                        deleted += 1
                except OSError as exc:
                    logger.debug("Could not delete screenshot %s: %s", f, exc)
        except OSError as exc:
            logger.debug("Could not prune screenshots dir: %s", exc)
        return deleted


# ---------------------------------------------------------------------------
# Image compression helpers
# ---------------------------------------------------------------------------

def _compress_image(source_path: Path, output_path: Path) -> str | None:
    """
    Load source image, resize if needed, save as compressed JPEG.
    Returns output path string, or None on failure.
    """
    try:
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("Pillow not available — storing uncompressed screenshot")
        try:
            import shutil
            shutil.copy2(source_path, output_path)
            return str(output_path)
        except OSError:
            return None

    try:
        with Image.open(source_path) as img:
            img = img.convert("RGB")
            return _save_compressed(img, output_path)
    except OSError as exc:
        logger.debug("Cannot open screenshot for compression: %s", exc)
        return None


def _save_compressed(img: object, output_path: Path) -> str | None:
    """Resize and save a PIL image as compressed JPEG. Returns path or None."""
    try:
        from PIL import Image  # type: ignore[import-untyped]

        # Resize if too large — keeps LLM token cost low
        w, h = img.size  # type: ignore[union-attr]
        if max(w, h) > _MAX_DIMENSION:
            ratio = _MAX_DIMENSION / max(w, h)
            new_size = (int(w * ratio), int(h * ratio))
            img = img.resize(new_size, Image.LANCZOS)  # type: ignore[union-attr]

        img.save(str(output_path), "JPEG", quality=_JPEG_QUALITY, optimize=True)  # type: ignore[union-attr]
        return str(output_path)
    except OSError as exc:
        logger.debug("Failed to save compressed screenshot: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Screenshot trigger logic
# ---------------------------------------------------------------------------

def should_trigger_llm_analysis(
    app_name: str | None,
    window_title: str | None,
    word_count: int | None,
    previous_word_count: int | None,
    minutes_since_title_change: float,
    known_apps: set[str],
) -> tuple[bool, str]:
    """
    Pure function. Decides whether to send a screenshot to the LLM.
    Returns (should_trigger, reason).

    Trigger conditions:
    1. Unknown app (not in known_apps)
    2. Figma active, window title unchanged > 10 minutes
    3. Doc word count flat > 20 minutes while window active
    4. app_name is None (total signal loss)
    """
    if app_name is None:
        return True, "no_app_detected"

    if app_name not in known_apps:
        return True, f"unknown_app:{app_name}"

    if app_name == "Figma" and minutes_since_title_change > 10:
        return True, "figma_idle_10min"

    if (
        word_count is not None
        and previous_word_count is not None
        and word_count == previous_word_count
        and minutes_since_title_change > 20
        and app_name in {"Word", "Microsoft Word", "Pages", "Obsidian", "Notion"}
    ):
        return True, "doc_stalled_20min"

    return False, ""


# Known apps that do NOT need screenshot fallback
KNOWN_APPS: set[str] = {
    "Google Chrome",
    "Chrome",
    "Figma",
    "Obsidian",
    "Code",
    "Visual Studio Code",
    "Terminal",
    "iTerm2",
    "Warp",
    "Zoom",
    "Slack",
    "WhatsApp",
    "Telegram",
    "Microsoft Word",
    "Word",
    "Pages",
    "Numbers",
    "Keynote",
    "Microsoft Excel",
    "Excel",
    "Safari",
    "Arc",
    "Claude",
    "ChatGPT",
    "Notion",
    "Linear",
    "Finder",
    "System Preferences",
    "System Settings",
    "Music",
    "Spotify",
    "Calendar",
    "Mail",
}
