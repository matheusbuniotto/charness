"""Implementação do agente Claude usando a CLI claude."""

import json
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from . import TokenUsage

console = Console()

def _format_tool_event(tool_name: str, tool_input: dict) -> str:
    """Formata evento de tool use para exibição."""
    if tool_name == "Read":
        return f"lendo {Path(tool_input.get('file_path', '')).name}"
    if tool_name in ("Write", "Edit"):
        return f"{'escrevendo' if tool_name == 'Write' else 'editando'} {Path(tool_input.get('file_path', '')).name}"
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return f"bash: {cmd[:50]}{'...' if len(cmd) > 50 else ''}"
    if tool_name == "Glob":
        return f"glob: {tool_input.get('pattern', '')}"
    if tool_name == "Grep":
        return f"grep: {tool_input.get('pattern', '')}"
    return tool_name.lower()

class ClaudeAgent:
    name = "claude"

    def run(
        self,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        label: str,
        allowed_tools: list[str] | None = None,
    ) -> tuple[str, TokenUsage]:
        cmd = [
            "claude",
            "--print",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "auto",
            "--no-session-persistence",
            "--system-prompt",
            system_prompt,
        ]

        if allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]
            stdin_input = prompt
        else:
            cmd.append(prompt)
            stdin_input = None

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_input else None,
            text=True,
            cwd=cwd,
        )

        if stdin_input:
            assert process.stdin is not None
            process.stdin.write(stdin_input)
            process.stdin.close()

        result_text = ""
        usage = TokenUsage()

        assert process.stdout is not None, "stdout deve estar disponível"

        with console.status("", spinner="dots") as status:
            status.update(f"[dim]  {label}  iniciando...[/dim]")

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = obj.get("type")

                if event_type == "assistant":
                    for block in obj.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                short = text[:70] + "..." if len(text) > 70 else text
                                status.update(f"[dim]  {label}  {short}[/dim]")
                        elif block.get("type") == "tool_use":
                            msg = _format_tool_event(
                                block.get("name", ""), block.get("input", {})
                            )
                            status.update(f"[dim]  {label}  {msg}[/dim]")

                elif event_type == "result":
                    result_text = obj.get("result", "")
                    u = obj.get("usage", {})
                    usage.input_tokens = u.get("input_tokens", 0)
                    usage.output_tokens = u.get("output_tokens", 0)
                    usage.cache_creation_tokens = u.get("cache_creation_input_tokens", 0)
                    usage.cache_read_tokens = u.get("cache_read_input_tokens", 0)
                    usage.cost_usd = obj.get("total_cost_usd", 0.0)

        process.wait()

        if process.returncode != 0:
            assert process.stderr is not None
            err = process.stderr.read()
            console.print(f"[red][erro] claude falhou:[/red]\n{err}")
            sys.exit(1)

        return result_text, usage
