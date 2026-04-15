"""c-harness: CLI para desenvolvimento assistido por agentes com Claude Code."""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

console = Console()

MAX_RETRIES = 2  # padrão "dois strikes" — falhou duas vezes, escala pro humano


# ---------------------------------------------------------------------------
# Prompts por estado
# ---------------------------------------------------------------------------

SPEC_PROMPT = """Você é um agente de estruturação de specs técnicas.

Receberá um texto livre descrevendo uma task de desenvolvimento.
Transforme em uma spec estruturada.

Retorne SOMENTE um JSON válido com esta estrutura (sem markdown, sem explicações):
{
  "title": "título curto da task",
  "summary": "o que precisa ser feito em 2-3 frases",
  "dod": ["critério 1", "critério 2", "critério 3"],
  "out_of_scope": ["o que NÃO deve ser feito"],
  "notes": "contexto adicional relevante (ou null)"
}"""

IMPL_PROMPT = """Você é um agente de implementação.

Sua responsabilidade: implementar a task descrita em spec.json.

Regras:
- Leia spec.json antes de começar
- Implemente exatamente o que o DoD pede — nem mais, nem menos
- Ao terminar, escreva impl-summary.md listando o que foi feito e quais arquivos foram criados/modificados
- Se encontrar algo ambíguo na spec, registre em impl-summary.md na seção "decisões tomadas"

O spec.json está em: {spec_path}"""

EVAL_PROMPT = """Você é um agente de avaliação de código.

Sua responsabilidade: verificar se a implementação atende ao DoD e critérios globais.

Leia:
1. {spec_path} — a spec com o DoD
2. {impl_summary_path} — o que o agente de implementação fez
3. Os arquivos listados no impl-summary como criados/modificados

Avalie cada critério do DoD (pass/fail) e os critérios globais abaixo.

Critérios globais (sempre aplicados):
- Sem TODOs ou placeholders no código
- Funções com responsabilidade única e clara
- Sem código morto ou imports não utilizados
- Nomes descritivos (variáveis, funções, arquivos)

Retorne SOMENTE um JSON válido (sem markdown):
{{
  "verdict": "approved" | "rejected",
  "dod_results": [
    {{"criterion": "texto do critério", "passed": true, "note": "observação ou null"}}
  ],
  "global_results": [
    {{"criterion": "nome do critério global", "passed": true, "note": "observação ou null"}}
  ],
  "rejection_reason": "resumo do que precisa ser corrigido (null se approved)",
  "failed_criteria": ["lista dos critérios que falharam (vazia se approved)"]
}}"""


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
    retries: dict = field(default_factory=dict)  # estado -> contagem de retries


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
    import re
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
    if blocks:
        return blocks[-1].strip()

    # último { ... } no texto
    start = text.rfind("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

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
        "--output-format", "stream-json",
        "--permission-mode", "auto",
        "--no-session-persistence",
        "--system-prompt", system_prompt,
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
    current_msg = ["iniciando..."]

    def render() -> Columns:
        return Columns([
            Spinner("dots", style="cyan"),
            Text(f"  {label}  {current_msg[0]}", style="dim"),
        ])

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
                        msg = _format_tool_event(block.get("name", ""), block.get("input", {}))
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
# Estados
# ---------------------------------------------------------------------------

def state_spec_generation(ctx: Context) -> Transition:
    """Texto livre → spec estruturada."""
    console.print("[cyan]▸ spec-generation[/cyan]")

    raw = run_claude(
        prompt=f"Task: {ctx.task_text}",
        system_prompt=SPEC_PROMPT,
        cwd=ctx.run_dir,
        label="spec",
        allowed_tools=None,  # spec agent não precisa de tools
    )

    try:
        ctx.spec = json.loads(_extract_json(raw))
    except json.JSONDecodeError:
        console.print(f"[red][erro] JSON inválido da spec:[/red]\n{raw}")
        sys.exit(1)

    spec_file = ctx.run_dir / "spec.json"
    spec_file.write_text(json.dumps(ctx.spec, indent=2, ensure_ascii=False))
    console.print(f"[green]✓[/green] spec salva")

    return Transition(next_state="human_gate_spec")


def state_human_gate_spec(ctx: Context) -> Transition:
    """Human gate: confirmar spec antes de implementar."""
    spec = ctx.spec
    console.print(f"\n  [bold]{spec['title']}[/bold]")
    console.print(f"  [dim]{spec['summary']}[/dim]")
    console.print(f"  DoD: {len(spec['dod'])} critérios\n")
    for i, criterion in enumerate(spec['dod'], 1):
        console.print(f"    [dim]{i}.[/dim] {criterion}")

    console.print()
    resposta = console.input("[yellow]aprovar spec e iniciar implementação?[/yellow] [dim][s/N][/dim] ").strip().lower()

    if resposta not in ("s", "sim", "y", "yes"):
        console.print("[dim]run pausada pelo usuário.[/dim]")
        return Transition(next_state="done", reason="pausado pelo usuário na spec")

    return Transition(next_state="implementation")


def state_implementation(ctx: Context) -> Transition:
    """Spec → implementação no projeto."""
    retries = ctx.retries.get("implementation", 0)
    console.print(f"[cyan]▸ implementation[/cyan]" + (f" [dim](tentativa {retries + 1})[/dim]" if retries > 0 else ""))

    spec_path = ctx.run_dir / "spec.json"
    rejection_context = ""

    if ctx.eval_result.get("rejection_reason"):
        rejection_context = f"""
A implementação anterior foi reprovada. Corrija os seguintes problemas:
{ctx.eval_result['rejection_reason']}

Critérios que falharam:
{chr(10).join(f'- {c}' for c in ctx.eval_result.get('failed_criteria', []))}
"""

    run_claude(
        prompt=f"Implemente a task descrita em spec.json.{rejection_context}",
        system_prompt=IMPL_PROMPT.format(spec_path=spec_path),
        cwd=ctx.project_dir,
        label="impl",
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )

    console.print("[green]✓[/green] implementação concluída")
    return Transition(next_state="evaluation")


def state_evaluation(ctx: Context) -> Transition:
    """Avalia implementação contra DoD + critérios globais."""
    console.print("[cyan]▸ evaluation[/cyan]")

    spec_path = ctx.run_dir / "spec.json"
    impl_summary_path = ctx.project_dir / "impl-summary.md"

    if not impl_summary_path.exists():
        console.print("[yellow]⚠ impl-summary.md não encontrado — avaliação com contexto limitado[/yellow]")

    raw = run_claude(
        prompt="Avalie a implementação conforme as instruções.",
        system_prompt=EVAL_PROMPT.format(
            spec_path=spec_path,
            impl_summary_path=impl_summary_path,
        ),
        cwd=ctx.project_dir,
        label="eval",
        allowed_tools=["Read", "Glob", "Grep"],
    )

    try:
        ctx.eval_result = json.loads(_extract_json(raw))
    except json.JSONDecodeError:
        console.print(f"[red][erro] JSON inválido da avaliação:[/red]\n{raw}")
        sys.exit(1)

    eval_file = ctx.run_dir / "eval.json"
    eval_file.write_text(json.dumps(ctx.eval_result, indent=2, ensure_ascii=False))

    verdict = ctx.eval_result.get("verdict")

    if verdict == "approved":
        console.print("[green]✓[/green] avaliação aprovada")
        _print_eval_results(ctx.eval_result)
        return Transition(next_state="log")

    # reprovado — verificar retries
    retries = ctx.retries.get("implementation", 0)
    if retries >= MAX_RETRIES:
        console.print(f"[red]✗[/red] avaliação reprovada após {MAX_RETRIES + 1} tentativas — escalando para humano")
        _print_eval_results(ctx.eval_result)
        return Transition(next_state="human_gate_eval", reason="max retries atingido")

    console.print(f"[yellow]✗[/yellow] avaliação reprovada — voltando para implementação")
    _print_eval_results(ctx.eval_result, only_failed=True)
    ctx.retries["implementation"] = retries + 1
    return Transition(next_state="implementation", reason=ctx.eval_result.get("rejection_reason", ""))


def state_human_gate_eval(ctx: Context) -> Transition:
    """Human gate: avaliação falhou após max retries."""
    console.print("\n[red bold]run requer intervenção humana[/red bold]")
    console.print(f"  Motivo: {ctx.eval_result.get('rejection_reason', 'não especificado')}\n")

    resposta = console.input("[yellow]forçar aprovação mesmo assim?[/yellow] [dim][s/N][/dim] ").strip().lower()
    if resposta in ("s", "sim", "y", "yes"):
        return Transition(next_state="log", reason="aprovado manualmente pelo humano")

    return Transition(next_state="done", reason="run encerrada pelo humano após falha")


def _print_eval_results(eval_result: dict, only_failed: bool = False) -> None:
    """Exibe painel de resultados da avaliação."""
    all_results = eval_result.get("dod_results", []) + eval_result.get("global_results", [])
    items = [r for r in all_results if not r.get("passed")] if only_failed else all_results

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(width=2)
    table.add_column()
    table.add_column(style="dim")

    for r in items:
        icon = "[green]✓[/green]" if r.get("passed") else "[red]✗[/red]"
        note = r.get("note") or ""
        table.add_row(icon, r["criterion"], note)

    verdict = eval_result.get("verdict", "")
    color = "green" if verdict == "approved" else "red"
    title = "avaliação — aprovado" if verdict == "approved" else "avaliação — reprovado"
    console.print(Panel(table, title=f"[{color}]{title}[/{color}]", border_style=color, padding=(0, 1)))


def state_log(ctx: Context) -> Transition:
    """Consolida artefatos da run em run-log.md."""
    console.print("[cyan]▸ log[/cyan]")

    spec = ctx.spec
    eval_result = ctx.eval_result
    retries = ctx.retries.get("implementation", 0)
    verdict = eval_result.get("verdict", "unknown")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    dod_lines = "\n".join(
        f"- {'✓' if r.get('passed') else '✗'} {r['criterion']}" + (f" — {r['note']}" if r.get("note") else "")
        for r in eval_result.get("dod_results", [])
    )
    global_lines = "\n".join(
        f"- {'✓' if r.get('passed') else '✗'} {r['criterion']}" + (f" — {r['note']}" if r.get("note") else "")
        for r in eval_result.get("global_results", [])
    )

    log = f"""# Run Log — {spec.get('title', 'task')}

**Data:** {now}
**Veredicto:** {verdict}
**Tentativas de implementação:** {retries + 1}
**Artefatos:** {ctx.run_dir}

---

## Task

{ctx.task_text}

## Spec

**Resumo:** {spec.get('summary', '')}

**DoD:**
{chr(10).join(f'- {d}' for d in spec.get('dod', []))}

**Fora do escopo:**
{chr(10).join(f'- {o}' for o in spec.get('out_of_scope', []))}

---

## Avaliação

### DoD
{dod_lines}

### Critérios Globais
{global_lines}
"""

    if eval_result.get("rejection_reason"):
        log += f"\n### Motivo de rejeição (última tentativa)\n{eval_result['rejection_reason']}\n"

    log_file = ctx.run_dir / "run-log.md"
    log_file.write_text(log)
    console.print(f"[green]✓[/green] log salvo em [dim]{log_file}[/dim]")

    return Transition(next_state="done")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

STATES: dict[str, StateFn] = {
    "spec_generation": state_spec_generation,
    "human_gate_spec": state_human_gate_spec,
    "implementation": state_implementation,
    "evaluation": state_evaluation,
    "human_gate_eval": state_human_gate_eval,
    "log": state_log,
}


def run_pipeline(ctx: Context) -> None:
    """Executa a state machine até o estado 'done'."""
    current = "spec_generation"

    while current != "done":
        if current not in STATES:
            console.print(f"[red][erro] estado desconhecido: {current}[/red]")
            sys.exit(1)

        transition = STATES[current](ctx)
        current = transition.next_state

        if transition.reason and current != "done":
            console.print(f"  [dim]→ {transition.reason}[/dim]")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Entrypoint do c-harness."""
    if len(sys.argv) < 2:
        console.print("[yellow]uso:[/yellow] c-harness '<descrição da task>'")
        sys.exit(1)

    task_text = " ".join(sys.argv[1:])
    project_dir = Path.cwd()
    run_dir = project_dir / ".harness" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold]c-harness[/bold] [dim]→ {run_dir.relative_to(project_dir)}[/dim]\n")

    ctx = Context(
        task_text=task_text,
        run_dir=run_dir,
        project_dir=project_dir,
    )

    run_pipeline(ctx)

    console.print(f"\n[bold green]✓ run concluída[/bold green] [dim]→ {run_dir.relative_to(project_dir)}[/dim]\n")
