"""Implementação do agente Pi usando a CLI pi."""

import subprocess
import sys
from pathlib import Path

from rich.console import Console
from . import TokenUsage

console = Console()

class PiAgent:
    name = "pi"

    def run(
        self,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        label: str,
        allowed_tools: list[str] | None = None,
    ) -> tuple[str, TokenUsage]:
        cmd = [
            "pi",
            "--print",
            "--no-session",
            "--system-prompt",
            system_prompt,
        ]

        if allowed_tools:
            tool_map = {
                "Read": "read",
                "Write": "write",
                "Edit": "edit",
                "Bash": "bash",
                "Glob": "find",
                "Grep": "grep",
            }
            pi_tools = [tool_map.get(t, t.lower()) for t in allowed_tools]
            cmd += ["--tools", ",".join(pi_tools)]

        cmd.append(prompt)

        with console.status(f"[dim]  {label}  executando...[/dim]", spinner="dots"):
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=cwd,
                    timeout=300,
                )
            except subprocess.TimeoutExpired:
                console.print("[red][erro] pi travado (timeout)[/red]")
                sys.exit(1)

        if result.returncode != 0:
            console.print(f"[red][erro] pi falhou:[/red]\n{result.stderr}")
            sys.exit(1)

        return result.stdout, TokenUsage() # pi currently does not output usage easily
