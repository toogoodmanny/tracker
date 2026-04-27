"""
tracker/collectors/docwatcher.py

Tracks word count changes in document files.
Polls on disk every N seconds to detect writing progress.
Works for: .docx (python-docx), .md/.txt (plain text count).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".docx", ".md", ".txt", ".markdown"}


@dataclass
class DocSnapshot:
    """Word count reading for a single file."""
    file_path: str
    word_count: int
    extension: str


class DocWatcher:
    """
    Reads word counts from document files on disk.
    Maintains previous counts to compute deltas.
    Never raises — returns None on any read error.
    """

    def __init__(self) -> None:
        self._previous_counts: dict[str, int] = {}

    def read_word_count(self, file_path: str) -> DocSnapshot | None:
        """
        Read current word count for a file.
        Returns None if the file doesn't exist, isn't supported, or can't be read.
        """
        path = Path(file_path)

        if not path.exists():
            return None

        ext = path.suffix.lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            return None

        try:
            if ext == ".docx":
                count = _count_words_docx(path)
            else:
                count = _count_words_plaintext(path)
        except PermissionError as exc:
            logger.debug("Permission denied reading %s: %s", file_path, exc)
            return None
        except OSError as exc:
            logger.debug("OS error reading %s: %s", file_path, exc)
            return None

        return DocSnapshot(
            file_path=file_path,
            word_count=count,
            extension=ext,
        )

    def get_delta(self, file_path: str, current_count: int) -> int:
        """
        Return change in word count since last check.
        Positive = words added, negative = words deleted.
        """
        previous = self._previous_counts.get(file_path, current_count)
        delta = current_count - previous
        self._previous_counts[file_path] = current_count
        return delta

    def clear_history(self, file_path: str) -> None:
        """Remove stored word count for a file (e.g. when file closes)."""
        self._previous_counts.pop(file_path, None)


# ---------------------------------------------------------------------------
# File-type specific word count functions
# ---------------------------------------------------------------------------

def _count_words_docx(path: Path) -> int:
    """
    Count words in a .docx file using python-docx.
    Raises OSError if the file is unreadable.
    Raises docx.oxml.exceptions if the file is not a valid docx.
    """
    try:
        import docx  # type: ignore[import-untyped]
    except ImportError as exc:
        raise OSError("python-docx not installed") from exc

    try:
        doc = docx.Document(str(path))
    except Exception as exc:
        raise OSError(f"Cannot parse docx {path}: {exc}") from exc

    text = " ".join(para.text for para in doc.paragraphs)
    return len(text.split())


def _count_words_plaintext(path: Path) -> int:
    """
    Count words in a plain text / markdown file.
    Raises OSError if file can't be read.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise

    return len(text.split())


def extract_last_n_words_plaintext(path: Path, n: int = 100) -> str:
    """
    Read the last N words from a plain text file.
    Used to determine topic for LLM context.
    Returns empty string if file can't be read.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("Cannot read %s for topic extraction: %s", path, exc)
        return ""

    words = text.split()
    return " ".join(words[-n:])
