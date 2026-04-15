"""Orquestração principal do c-harness."""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from rich.console import Console

from .git import GitContext

console = Console()

MAX_RETRIES = 2  # padrão "dois strikes" — falhou duas vezes, escala pro humano


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Estado compartilhado entre estados da run."""

    task_text: str
    run_dir: Path
    project_dir: Path
    spec: dict = field(default_factory=dict)
    eval_result: dict = field(default_factory=dict)
    retries: dict = field(default_factory=dict)
    git: GitContext | None = None


@dataclass
class Transition:
    """Resultado de um estado — para onde ir e por quê."""

    next_state: str
    reason: str = ""


StateFn = Callable[[Context], Transition]


# ---------------------------------------------------------------------------
# Runner de subprocess
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> str:
    """Extrai JSON do output — trata JSON puro, markdown code block, ou texto com JSON embutido."""
    text = text.strip()

    # JSON puro
    if text.startswith("{"):
        return text

    # último bloco ```json ... ``` ou ``` ... ```
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
    if blocks:
        return blocks[-1].strip()

    # último { ... } no texto
    start = text.rfind("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    return text


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


def run_claude(
    prompt: str,
    system_prompt: str,
    cwd: Path,
    label: str = "",
    allowed_tools: list[str] | None = None,
) -> str:
    """Executa claude CLI como subprocess com feedback visual em tempo real."""
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
        # com --allowedTools, prompt via stdin (argumento posicional some)
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
        process.stdin.write(stdin_input)
        process.stdin.close()

    result_text = ""

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

    process.wait()

    if process.returncode != 0:
        err = process.stderr.read()
        console.print(f"[red][erro] claude falhou:[/red]\n{err}")
        sys.exit(1)

    return result_text


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def run_pipeline(ctx: Context, start_state: str = "git_check") -> None:
    """Executa a state machine até o estado 'done'."""
    from .states import STATES  # importação lazy para evitar importação circular

    current = start_state

    while current != "done":
        if current not in STATES:
            console.print(f"[red][erro] estado desconhecido: {current}[/red]")
            sys.exit(1)

        transition = STATES[current](ctx)
        current = transition.next_state

        if transition.reason and current != "done":
            console.print(f"  [dim]→ {transition.reason}[/dim]")


# ---------------------------------------------------------------------------
# Validação e roteamento de entrada
# ---------------------------------------------------------------------------

SPEC_REQUIRED_FIELDS = ("title", "summary", "dod", "out_of_scope", "notes")


def validate_spec(data: dict) -> list[str]:
    """Valida campos obrigatórios de uma spec local.

    Returns:
        Lista de erros encontrados. Vazia se a spec for válida.
    """
    errors = []
    for key in SPEC_REQUIRED_FIELDS:
        if key not in data:
            errors.append(f"campo ausente: '{key}'")
        elif key in ("dod", "out_of_scope") and not isinstance(data[key], list):
            errors.append(f"campo '{key}' deve ser uma lista")
    return errors


def _detect_input_mode(args: list[str]) -> tuple[str, str | None]:
    """Detecta o modo de entrada a partir dos argumentos.

    Returns:
        Tupla (modo, caminho_ou_none) onde modo é 'free_text', 'local_spec' ou 'resume'.
    """
    if len(args) == 1:
        candidate = Path(args[0])
        if candidate.suffix == ".json" and candidate.exists():
            return "json_file", str(candidate)
    return "free_text", None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Entrypoint do c-harness."""
    args = sys.argv[1:]

    if not args:
        console.print("[yellow]uso:[/yellow] c-harness '<descrição da task>'")
        console.print("       c-harness <caminho/para/spec.json>     [dim]# spec local[/dim]")
        console.print("       c-harness <caminho/para/resume.json>   [dim]# retomada[/dim]")
        console.print("       c-harness --edit <caminho/para/spec.json>")
        sys.exit(1)

    project_dir = Path.cwd()
    run_dir = (
        project_dir / ".harness" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    console.print(
        f"\n[bold]c-harness[/bold] [dim]→ {run_dir.relative_to(project_dir)}[/dim]\n"
    )

    # Modo de edição de spec existente: --edit <spec-path>
    if args[0] == "--edit":
        if len(args) < 2:
            console.print("[red][erro][/red] --edit requer o caminho para um spec.json")
            sys.exit(1)

        spec_path = Path(args[1])
        if not spec_path.exists():
            console.print(f"[red][erro][/red] spec não encontrada: {spec_path}")
            sys.exit(1)

        try:
            spec_data = json.loads(spec_path.read_text())
        except json.JSONDecodeError as exc:
            console.print(f"[red][erro][/red] JSON inválido em {spec_path}: {exc}")
            sys.exit(1)

        # Copia a spec original para o run_dir como ponto de partida
        (run_dir / "spec.json").write_text(
            json.dumps(spec_data, indent=2, ensure_ascii=False)
        )

        ctx = Context(
            task_text=spec_data.get("summary", ""),
            run_dir=run_dir,
            project_dir=project_dir,
            spec=spec_data,
        )

        run_pipeline(ctx, start_state="human_gate_spec")

    else:
        mode, json_path = _detect_input_mode(args)

        if mode == "json_file":
            json_file = Path(json_path)  # type: ignore[arg-type]
            try:
                data = json.loads(json_file.read_text())
            except json.JSONDecodeError as exc:
                console.print(f"[red][erro][/red] JSON inválido em {json_file}: {exc}")
                sys.exit(1)

            if "resume_from" in data:
                # --- Modo retomada ---
                resume = data["resume_from"]
                resume_state = resume.get("state", "implementation")
                resume_spec = resume.get("spec")
                resume_retries = resume.get("retries", {})
                resume_eval = resume.get("eval_result", {})
                original_run_dir = resume.get("run_dir")

                # Valida campos mínimos do resume
                resume_errors = []
                if not resume_spec:
                    resume_errors.append("campo 'spec' ausente ou vazio em 'resume_from'")
                if resume_state not in (
                    "git_check", "spec_generation", "spec_edit", "human_gate_spec",
                    "implementation", "human_gate_commit", "evaluation",
                    "human_gate_eval", "log",
                ):
                    resume_errors.append(
                        f"campo 'state' inválido em 'resume_from': '{resume_state}'"
                    )
                if resume_errors:
                    console.print("[red][erro][/red] resume inválido:")
                    for err in resume_errors:
                        console.print(f"  [red]•[/red] {err}")
                    sys.exit(1)

                console.print(
                    f"[bold]modo:[/bold] retomada → continuando de [cyan]{resume_state}[/cyan]"
                )
                if original_run_dir:
                    console.print(f"  [dim]run original: {original_run_dir}[/dim]")

                # Salva cópia da spec na nova run_dir
                (run_dir / "spec.json").write_text(
                    json.dumps(resume_spec, indent=2, ensure_ascii=False)
                )

                ctx = Context(
                    task_text=resume_spec.get("summary", ""),
                    run_dir=run_dir,
                    project_dir=project_dir,
                    spec=resume_spec,
                    eval_result=resume_eval,
                    retries=resume_retries,
                )

                run_pipeline(ctx, start_state=resume_state)

            else:
                # --- Modo spec local ---
                errors = validate_spec(data)
                if errors:
                    console.print("[red][erro][/red] spec inválida:")
                    for err in errors:
                        console.print(f"  [red]•[/red] {err}")
                    sys.exit(1)

                console.print(
                    f"[bold]modo:[/bold] spec local → [dim]{json_file}[/dim]"
                )

                # Salva cópia da spec na run_dir (sem re-estruturar)
                (run_dir / "spec.json").write_text(
                    json.dumps(data, indent=2, ensure_ascii=False)
                )

                ctx = Context(
                    task_text=data.get("summary", ""),
                    run_dir=run_dir,
                    project_dir=project_dir,
                    spec=data,
                )

                run_pipeline(ctx, start_state="human_gate_spec")

        else:
            # --- Modo texto livre ---
            task_text = " ".join(args)
            console.print("[bold]modo:[/bold] texto livre → gerando spec")

            ctx = Context(
                task_text=task_text,
                run_dir=run_dir,
                project_dir=project_dir,
            )

            run_pipeline(ctx)

    console.print(
        f"\n[bold green]✓ run concluída[/bold green] [dim]→ {run_dir.relative_to(project_dir)}[/dim]\n"
    )
