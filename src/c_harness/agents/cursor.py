"""Agente Cursor usando `cursor agent` CLI.

Diferenças em relação ao ClaudeAgent:
- Não tem --system-prompt: system prompt é embutido no início do user prompt.
- Não tem --allowedTools: controle de acesso via --mode (ask/plan) ou --force.
- Não reporta total_cost_usd: cost_usd sempre 0.
- Eventos de tool use chegam como type=tool_call (não dentro do bloco assistant).
- Modelos usam namespace próprio (ex: auto, composer-2, gpt-5.3-codex).
"""

import json
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from . import TokenUsage
from ..config import config

console = Console()

_WRITE_TOOLS = {"Write", "Edit"}


def _format_tool_event(tool_call: dict) -> str:
    """Formata evento tool_call do cursor para exibição."""
    for key in tool_call:
        name = key.replace("ToolCall", "").lower()
        inner = tool_call[key]
        args = inner.get("args", {})
        if name == "shell":
            cmd = args.get("command", "")
            return f"bash: {cmd[:50]}{'...' if len(cmd) > 50 else ''}"
        if name in ("editfile", "createfile", "writefile"):
            path = args.get("path", "") or inner.get("description", "")
            return f"editando {Path(path).name}" if path else "editando arquivo"
        if name == "readfile":
            path = args.get("path", "")
            return f"lendo {Path(path).name}" if path else "lendo arquivo"
        return name
    return "tool"


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
        model = config.cursor_state_models.get(state) or config.models.get(
            "cursor", "auto"
        )

        # cursor agent não tem --system-prompt; embute no início do prompt
        full_prompt = (
            f"<system>\n{system_prompt}\n</system>\n\n{prompt}"
            if system_prompt
            else prompt
        )

        cmd = [
            "cursor",
            "agent",
            "--print",
            "--output-format",
            "stream-json",
            "--trust",
            "--workspace",
            str(cwd),
            "--model",
            model,
        ]

        # Cursor não tem --allowedTools; usa --mode como aproximação:
        # - allowed_tools=None  → geração de texto puro (spec/edit) → --mode ask
        # - só ferramentas de leitura → evaluation → --mode plan
        # - ferramentas de escrita → implementation → --force
        if allowed_tools is None:
            cmd += ["--mode", "ask"]
        elif _WRITE_TOOLS.isdisjoint(allowed_tools):
            cmd += ["--mode", "plan"]
        else:
            cmd += ["--force"]

        cmd.append(full_prompt)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )

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

                elif event_type == "tool_call" and obj.get("subtype") == "started":
                    msg = _format_tool_event(obj.get("tool_call", {}))
                    usage.tool_calls.append(msg)
                    status.update(f"[dim]  {label}  {msg}[/dim]")

                elif event_type == "result":
                    result_text = obj.get("result", "")
                    u = obj.get("usage", {})
                    # cursor usa camelCase; claude usa snake_case
                    usage.input_tokens = u.get("inputTokens", 0)
                    usage.output_tokens = u.get("outputTokens", 0)
                    usage.cache_creation_tokens = u.get("cacheWriteTokens", 0)
                    usage.cache_read_tokens = u.get("cacheReadTokens", 0)
                    # cursor não reporta custo — cost_usd permanece 0.0

        process.wait()

        if process.returncode != 0:
            assert process.stderr is not None
            err = process.stderr.read()
            console.print(f"[red][erro] cursor agent falhou:[/red]\n{err}")
            sys.exit(1)

        return result_text, usage
