"""Implementação do agente Cursor usando a CLI agent."""

import json
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def _format_tool_event(tool_call: dict) -> str:
    """Formata evento de tool_call do cursor para exibição."""
    if "readToolCall" in tool_call:
        path = tool_call["readToolCall"].get("args", {}).get("path", "")
        return f"lendo {Path(path).name}"
    if "writeToolCall" in tool_call:
        path = tool_call["writeToolCall"].get("args", {}).get("path", "")
        return f"escrevendo {Path(path).name}"
    if "editToolCall" in tool_call:
        path = tool_call["editToolCall"].get("args", {}).get("path", "")
        return f"editando {Path(path).name}"
    if "runCommandToolCall" in tool_call:
        cmd = tool_call["runCommandToolCall"].get("args", {}).get("command", "")
        return f"bash: {cmd[:50]}{'...' if len(cmd) > 50 else ''}"
    if "function" in tool_call:
        return tool_call["function"].get("name", "tool").lower()
    return "tool"


class CursorAgent:
    """Agente que usa a CLI agent (Cursor) para execução."""

    name = "cursor"

    def run(
        self,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        label: str,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """Executa cursor agent CLI com streaming JSON.

        Args:
            prompt: O prompt principal a ser enviado ao agente.
            system_prompt: Contexto do sistema — concatenado ao prompt,
                pois o cursor agent não suporta --system-prompt nativo.
            cwd: Diretório de trabalho (passado via --workspace e subprocess).
            label: Label para identificar a operação no output.
            allowed_tools: Ignorado — cursor agent não suporta restrição de tools via CLI.

        Returns:
            A resposta completa do agente como string.
        """
        full_prompt = f"{system_prompt}\n\n---\n\n{prompt}" if system_prompt else prompt

        cmd = [
            "agent",
            "--print",
            "--output-format",
            "stream-json",
            "--trust",
            "--force",
            "--workspace",
            str(cwd),
            full_prompt,
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )

        result_text = ""

        assert process.stdout is not None, "stdout deve estar disponível com PIPE"

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

                elif event_type == "tool_call" and obj.get("subtype") == "started":
                    msg = _format_tool_event(obj.get("tool_call", {}))
                    status.update(f"[dim]  {label}  {msg}[/dim]")

                elif event_type == "result":
                    result_text = obj.get("result", "")

        process.wait()

        if process.returncode != 0:
            assert process.stderr is not None, "stderr deve estar disponível com PIPE"
            err = process.stderr.read()
            console.print(f"[red][erro] cursor agent falhou:[/red]\n{err}")
            sys.exit(1)

        return result_text
