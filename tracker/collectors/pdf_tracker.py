"""
tracker/collectors/pdf_tracker.py

Detects whether a PDF (or any file) was received from outside
(downloaded, sent via WhatsApp/Telegram) vs created by the user.

Uses the macOS quarantine extended attribute:
  com.apple.quarantine
Any file downloaded from the internet or received via a messaging app
gets this attribute set automatically by the OS, regardless of where
the user saves it.

macOS-only. Returns CREATED on other platforms.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_MACOS = platform.system() == "Darwin"


class FileOrigin(str, Enum):
    RECEIVED = "received"    # Downloaded / sent by someone else
    CREATED = "created"      # Made by the user
    UNKNOWN = "unknown"      # Could not determine


@dataclass
class FileOriginResult:
    path: str
    origin: FileOrigin
    quarantine_value: str | None  # Raw xattr value if present


def detect_file_origin(file_path: str) -> FileOriginResult:
    """
    Determine whether a file was received or created.
    Never raises — returns UNKNOWN on any error.
    """
    path = Path(file_path)

    if not path.exists():
        return FileOriginResult(
            path=file_path, origin=FileOrigin.UNKNOWN, quarantine_value=None
        )

    if not _IS_MACOS:
        return FileOriginResult(
            path=file_path, origin=FileOrigin.UNKNOWN, quarantine_value=None
        )

    quarantine = _read_quarantine_xattr(file_path)

    if quarantine is not None:
        return FileOriginResult(
            path=file_path,
            origin=FileOrigin.RECEIVED,
            quarantine_value=quarantine,
        )

    return FileOriginResult(
        path=file_path,
        origin=FileOrigin.CREATED,
        quarantine_value=None,
    )


def _read_quarantine_xattr(file_path: str) -> str | None:
    """
    Read the com.apple.quarantine extended attribute.
    Returns the attribute value if present, None otherwise.
    Raises nothing — subprocess errors return None.
    """
    try:
        result = subprocess.run(
            ["xattr", "-p", "com.apple.quarantine", file_path],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        # returncode 1 = attribute not present — normal for user-created files
        return None
    except FileNotFoundError:
        logger.debug("xattr command not found")
        return None
    except subprocess.TimeoutExpired:
        logger.debug("xattr check timed out for %s", file_path)
        return None
    except OSError as exc:
        logger.debug("xattr OS error for %s: %s", file_path, exc)
        return None


def classify_pdf_session(
    file_path: str,
    time_on_file_minutes: float,
) -> dict[str, str]:
    """
    Return classification metadata for a PDF viewing session.
    Used by the LLM analysis to understand what kind of work was happening.
    """
    origin = detect_file_origin(file_path)
    path = Path(file_path)

    return {
        "file_name": path.name,
        "origin": origin.origin.value,
        "work_type": "reviewing_received_doc" if origin.origin == FileOrigin.RECEIVED else "reading_own_doc",
        "time_minutes": str(round(time_on_file_minutes, 1)),
    }
