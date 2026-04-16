"""Implementação mock do agente Cursor."""

import time
from pathlib import Path

from rich.console import Console
from . import TokenUsage

console = Console()


class CursorAgent:
    name = "cursor"

    def run(
        self,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        label: str,
        allowed_tools: list[str] | None = None,
        state: str = "",
    ) -> tuple[str, TokenUsage]:
        with console.status(f"[dim]  {label}  executando no cursor (mock)...[/dim]"):
            time.sleep(1)
        return (
            '{"title": "mock cursor spec", "summary": "mock summary", "dod": ["mock dod"], "out_of_scope": []}',
            TokenUsage(),
        )
