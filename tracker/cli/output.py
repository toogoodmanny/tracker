"""
tracker/cli/output.py

All user-facing terminal output goes through these helpers.
No print() calls in command handlers.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
error_console = Console(stderr=True)


def print_success(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    error_console.print(f"[red]✗[/red] {message}")


def print_warning(message: str) -> None:
    console.print(f"[yellow]⚠[/yellow] {message}")


def print_info(message: str) -> None:
    console.print(f"[dim]→[/dim] {message}")


def print_header(title: str) -> None:
    console.print(Panel(f"[bold]{title}[/bold]", expand=False))


def print_key_value(key: str, value: str) -> None:
    console.print(f"  [dim]{key}:[/dim] {value}")


def print_table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    table = Table(title=title, show_header=True)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_separator() -> None:
    console.print("[dim]" + "─" * 50 + "[/dim]")
