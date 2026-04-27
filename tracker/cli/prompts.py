"""
tracker/cli/prompts.py

All user input goes through these typed helpers.
No bare input() calls anywhere else in the codebase.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.prompt import Confirm, Prompt

console = Console()


def ask_single_line(question: str, default: str | None = None) -> str:
    """
    Ask a single-line question. Returns stripped string.
    Raises KeyboardInterrupt if user presses Ctrl-C (propagates up cleanly).
    """
    return Prompt.ask(question, default=default or "")


def ask_multiline(prompt_text: str) -> str:
    """
    Collect multi-line input until user enters a blank line or EOF.
    Returns the full text joined with newlines.
    Never returns an empty string — re-prompts if nothing entered.
    Raises KeyboardInterrupt on Ctrl-C.
    """
    console.print(f"[bold]{prompt_text}[/bold]")
    console.print("[dim](Press Enter twice or Ctrl-D when done)[/dim]")

    lines: list[str] = []
    try:
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line == "" and lines and lines[-1] == "":
                # Two consecutive blank lines = done
                break
            lines.append(line)
    except KeyboardInterrupt:
        raise

    # Strip trailing blank lines
    while lines and lines[-1] == "":
        lines.pop()

    text = "\n".join(lines).strip()
    if not text:
        console.print("[yellow]Nothing entered. Please type your goals.[/yellow]")
        return ask_multiline(prompt_text)

    return text


def ask_confirmation(question: str, default: bool = False) -> bool:
    """
    Ask a yes/no question. Returns bool.
    Raises KeyboardInterrupt on Ctrl-C.
    """
    return Confirm.ask(question, default=default)


def ask_optional_minutes(prompt_text: str) -> int | None:
    """
    Ask for a number of minutes. Returns None if user skips.
    Raises KeyboardInterrupt on Ctrl-C.
    Raises ValueError if input is not a valid integer.
    """
    raw = Prompt.ask(prompt_text, default="")
    if not raw.strip():
        return None
    try:
        minutes = int(raw.strip())
        if minutes <= 0:
            raise ValueError(f"Minutes must be positive, got {minutes}")
        return minutes
    except ValueError:
        console.print(f"[red]Invalid number: {raw!r}[/red]")
        return ask_optional_minutes(prompt_text)
