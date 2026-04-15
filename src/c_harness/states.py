"""Definições e lógica de estados da máquina de estados do c-harness."""

import json
import subprocess
import sys

from rich.panel import Panel
from rich.table import Table

from .git import _run_git, collect_git_context
from .config import load_skills
from .runner import (
    MAX_RETRIES,
    Context,
    StateFn,
    Transition,
    _extract_json,
    console,
    run_agent,
)

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

SPEC_EDIT_PROMPT = """Você é um agente de estruturação de specs técnicas.

Receberá uma spec existente em JSON e instruções de edição do usuário.
Revise a spec conforme as instruções e retorne a versão atualizada.

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
# Estados
# ---------------------------------------------------------------------------


def state_git_check(ctx: Context) -> Transition:
    """Valida estado do git antes de iniciar a run."""
    console.print("[cyan]▸ git-check[/cyan]")

    git = collect_git_context(ctx.project_dir)

    if git is None:
        console.print("[dim]  não é repositório git — pulando verificação[/dim]")
        return Transition(next_state="spec_generation")

    ctx.git = git
    console.print(f"[dim]  branch: {git.branch}[/dim]")

    if git.is_clean:
        console.print("[green]✓[/green] working tree limpa")
        return Transition(next_state="spec_generation")

    # tem mudanças — bloqueia com sugestão
    console.print("[red]✗[/red] working tree com mudanças não commitadas:\n")
    for line in git.dirty_summary.splitlines()[:10]:
        console.print(f"  [dim]{line}[/dim]")

    console.print(
        "\n[yellow]sugestão:[/yellow] commit ou stash antes de iniciar uma run.\n"
        "  [dim]git add -p && git commit -m 'wip'[/dim]   — commita o que está pronto\n"
        "  [dim]git stash[/dim]                            — guarda temporariamente"
    )
    sys.exit(1)


def state_spec_generation(ctx: Context) -> Transition:
    """Texto livre → spec estruturada."""
    console.print("[cyan]▸ spec-generation[/cyan]")

    raw, usage = run_agent(
        prompt=f"Task: {ctx.task_text}",
        system_prompt=SPEC_PROMPT + load_skills('spec_generation'),
        cwd=ctx.run_dir,
        label="spec",
        allowed_tools=None,  # spec agent não precisa de tools
    )

    
    ctx.metrics.total_input_tokens += usage.input_tokens
    ctx.metrics.total_output_tokens += usage.output_tokens
    ctx.metrics.total_cache_creation_tokens += usage.cache_creation_tokens
    ctx.metrics.total_cache_read_tokens += usage.cache_read_tokens
    ctx.metrics.total_cost_usd += usage.cost_usd
    ctx.metrics.steps.append({
        "state": "spec_generation",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation": usage.cache_creation_tokens,
        "cache_read": usage.cache_read_tokens,
        "cost_usd": usage.cost_usd
    })
    try:
        ctx.spec = json.loads(_extract_json(raw))
    except json.JSONDecodeError:
        console.print(f"[red][erro] JSON inválido da spec:[/red]\n{raw}")
        sys.exit(1)

    spec_file = ctx.run_dir / "spec.json"
    spec_file.write_text(json.dumps(ctx.spec, indent=2, ensure_ascii=False))
    console.print("[green]✓[/green] spec salva")

    return Transition(next_state="human_gate_spec")


def state_spec_edit(ctx: Context) -> Transition:
    """Edita a spec existente com base no feedback do usuário."""
    console.print("[cyan]▸ spec-edit[/cyan]")

    current_spec_json = json.dumps(ctx.spec, indent=2, ensure_ascii=False)
    feedback = ctx.spec_edit_feedback  # type: ignore[attr-defined]

    prompt = f"Spec atual:\n{current_spec_json}\n\nInstruções de edição:\n{feedback}"

    raw, usage = run_agent(
        prompt=prompt,
        system_prompt=SPEC_EDIT_PROMPT + load_skills('spec_edit'),
        cwd=ctx.run_dir,
        label="spec-edit",
        allowed_tools=None,
    )

    
    ctx.metrics.total_input_tokens += usage.input_tokens
    ctx.metrics.total_output_tokens += usage.output_tokens
    ctx.metrics.total_cache_creation_tokens += usage.cache_creation_tokens
    ctx.metrics.total_cache_read_tokens += usage.cache_read_tokens
    ctx.metrics.total_cost_usd += usage.cost_usd
    ctx.metrics.steps.append({
        "state": "spec_edit",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation": usage.cache_creation_tokens,
        "cache_read": usage.cache_read_tokens,
        "cost_usd": usage.cost_usd
    })
    try:
        ctx.spec = json.loads(_extract_json(raw))
    except json.JSONDecodeError:
        console.print(f"[red][erro] JSON inválido da spec editada:[/red]\n{raw}")
        sys.exit(1)

    spec_file = ctx.run_dir / "spec.json"
    spec_file.write_text(json.dumps(ctx.spec, indent=2, ensure_ascii=False))
    console.print("[green]✓[/green] spec atualizada")

    return Transition(next_state="human_gate_spec")


def state_human_gate_spec(ctx: Context) -> Transition:
    """Human gate: confirmar spec antes de implementar, com opção de edição."""
    spec = ctx.spec
    console.print(f"\n  [bold]{spec['title']}[/bold]")
    console.print(f"  [dim]{spec['summary']}[/dim]")
    console.print(f"  DoD: {len(spec['dod'])} critérios\n")
    for i, criterion in enumerate(spec["dod"], 1):
        console.print(f"    [dim]{i}.[/dim] {criterion}")
    if spec.get("out_of_scope"):
        console.print(f"\n  Fora do escopo: {len(spec['out_of_scope'])} itens")
    if spec.get("notes"):
        console.print(f"  [dim]Notas: {spec['notes']}[/dim]")

    console.print()
    resposta = (
        console.input(
            "[yellow]aprovar spec?[/yellow] [dim][s=aprovar / e=editar / N=cancelar][/dim] "
        )
        .strip()
        .lower()
    )

    if resposta in ("e", "editar", "edit"):
        feedback = console.input(
            "[yellow]descreva as alterações desejadas:[/yellow] "
        ).strip()
        if not feedback:
            console.print(
                "[dim]nenhuma alteração informada — mantendo spec atual.[/dim]"
            )
            return Transition(next_state="human_gate_spec")
        ctx.spec_edit_feedback = feedback  # type: ignore[attr-defined]
        return Transition(next_state="spec_edit")

    if resposta not in ("s", "sim", "y", "yes"):
        console.print("[dim]run pausada pelo usuário.[/dim]")
        return Transition(next_state="done", reason="pausado pelo usuário na spec")

    return Transition(next_state="implementation")


def state_implementation(ctx: Context) -> Transition:
    """Spec → implementação no projeto."""
    retries = ctx.retries.get("implementation", 0)
    console.print(
        "[cyan]▸ implementation[/cyan]"
        + (f" [dim](tentativa {retries + 1})[/dim]" if retries > 0 else "")
    )

    spec_path = ctx.run_dir / "spec.json"
    rejection_context = ""

    if ctx.eval_result.get("rejection_reason"):
        rejection_context = f"""
A implementação anterior foi reprovada. Corrija os seguintes problemas:
{ctx.eval_result["rejection_reason"]}

Critérios que falharam:
{chr(10).join(f"- {c}" for c in ctx.eval_result.get("failed_criteria", []))}
"""

    git_context = ""
    if ctx.git:
        git_context = f"""
Contexto git do projeto:
- Branch: {ctx.git.branch}
- Histórico recente:
{ctx.git.log}
"""

    raw, usage = run_agent(
        prompt=f"Implemente a task descrita em spec.json.{rejection_context}{git_context}",
        system_prompt=IMPL_PROMPT.format(spec_path=spec_path) + load_skills('implementation'),
        cwd=ctx.project_dir,
        label="impl",
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    )

    
    ctx.metrics.total_input_tokens += usage.input_tokens
    ctx.metrics.total_output_tokens += usage.output_tokens
    ctx.metrics.total_cache_creation_tokens += usage.cache_creation_tokens
    ctx.metrics.total_cache_read_tokens += usage.cache_read_tokens
    ctx.metrics.total_cost_usd += usage.cost_usd
    ctx.metrics.steps.append({
        "state": "implementation",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation": usage.cache_creation_tokens,
        "cache_read": usage.cache_read_tokens,
        "cost_usd": usage.cost_usd
    })
    console.print("[green]✓[/green] implementação concluída")
    return Transition(next_state="human_gate_commit")


def state_human_gate_commit(ctx: Context) -> Transition:
    """Human gate: revisar diff e decidir se commita antes da avaliação."""
    if ctx.git is None:
        return Transition(next_state="evaluation")

    diff = _run_git(["diff", "--stat", "HEAD"], ctx.project_dir)
    new_files = _run_git(
        ["ls-files", "--others", "--exclude-standard"], ctx.project_dir
    )

    if not diff and not new_files:
        console.print(
            "[dim]  nenhuma mudança detectada no git — pulando gate de commit[/dim]"
        )
        return Transition(next_state="evaluation")

    console.print("\n[cyan]▸ human-gate: commit[/cyan]")
    if diff:
        console.print(f"\n[dim]{diff}[/dim]")
    if new_files:
        console.print("\n[dim]novos arquivos:[/dim]")
        for f in new_files.splitlines():
            console.print(f"  [dim]+ {f}[/dim]")

    resposta = (
        console.input(
            "\n[yellow]commitar implementação antes de avaliar?[/yellow] [dim][s/N][/dim] "
        )
        .strip()
        .lower()
    )

    if resposta not in ("s", "sim", "y", "yes"):
        return Transition(next_state="evaluation", reason="commit pulado pelo usuário")

    # gera mensagem de commit baseada na spec
    title = ctx.spec.get("title", "implementação via c-harness")
    commit_msg = f"feat: {title}\n\ngerado por c-harness · run {ctx.run_dir.name}"

    _run_git(["add", "-A"], ctx.project_dir)
    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        capture_output=True,
        text=True,
        cwd=ctx.project_dir,
    )

    if result.returncode == 0:
        console.print("[green]✓[/green] commit criado")
        hash_short = _run_git(["rev-parse", "--short", "HEAD"], ctx.project_dir)
        console.print(f"  [dim]{hash_short} {title}[/dim]")
    else:
        console.print(f"[red]✗[/red] commit falhou:\n{result.stderr}")

    return Transition(next_state="evaluation")


def state_evaluation(ctx: Context) -> Transition:
    """Avalia implementação contra DoD + critérios globais."""
    console.print("[cyan]▸ evaluation[/cyan]")

    spec_path = ctx.run_dir / "spec.json"
    impl_summary_path = ctx.project_dir / "impl-summary.md"

    if not impl_summary_path.exists():
        console.print(
            "[yellow]⚠ impl-summary.md não encontrado — avaliação com contexto limitado[/yellow]"
        )

    raw, usage = run_agent(
        prompt="Avalie a implementação conforme as instruções.",
        system_prompt=EVAL_PROMPT.format(
            spec_path=spec_path,
            impl_summary_path=impl_summary_path,
        ) + load_skills('evaluation'),
        cwd=ctx.project_dir,
        label="eval",
        allowed_tools=["Read", "Glob", "Grep"],
    )

    
    ctx.metrics.total_input_tokens += usage.input_tokens
    ctx.metrics.total_output_tokens += usage.output_tokens
    ctx.metrics.total_cache_creation_tokens += usage.cache_creation_tokens
    ctx.metrics.total_cache_read_tokens += usage.cache_read_tokens
    ctx.metrics.total_cost_usd += usage.cost_usd
    ctx.metrics.steps.append({
        "state": "evaluation",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation": usage.cache_creation_tokens,
        "cache_read": usage.cache_read_tokens,
        "cost_usd": usage.cost_usd
    })
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
        console.print(
            f"[red]✗[/red] avaliação reprovada após {MAX_RETRIES + 1} tentativas — escalando para humano"
        )
        _print_eval_results(ctx.eval_result)
        return Transition(next_state="human_gate_eval", reason="max retries atingido")

    console.print(
        "[yellow]✗[/yellow] avaliação reprovada — voltando para implementação"
    )
    _print_eval_results(ctx.eval_result, only_failed=True)
    ctx.retries["implementation"] = retries + 1
    return Transition(
        next_state="implementation", reason=ctx.eval_result.get("rejection_reason", "")
    )


def state_human_gate_eval(ctx: Context) -> Transition:
    """Human gate: avaliação falhou após max retries."""
    console.print("\n[red bold]run requer intervenção humana[/red bold]")
    console.print(
        f"  Motivo: {ctx.eval_result.get('rejection_reason', 'não especificado')}\n"
    )

    resposta = (
        console.input(
            "[yellow]forçar aprovação mesmo assim?[/yellow] [dim][s/N][/dim] "
        )
        .strip()
        .lower()
    )
    if resposta in ("s", "sim", "y", "yes"):
        return Transition(next_state="log", reason="aprovado manualmente pelo humano")

    return Transition(next_state="done", reason="run encerrada pelo humano após falha")


def _print_eval_results(eval_result: dict, only_failed: bool = False) -> None:
    """Exibe painel de resultados da avaliação."""
    all_results = eval_result.get("dod_results", []) + eval_result.get(
        "global_results", []
    )
    items = (
        [r for r in all_results if not r.get("passed")] if only_failed else all_results
    )

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
    console.print(
        Panel(
            table,
            title=f"[{color}]{title}[/{color}]",
            border_style=color,
            padding=(0, 1),
        )
    )


def state_log(ctx: Context) -> Transition:
    """Consolida artefatos da run em run-log.md."""
    from datetime import datetime

    console.print("[cyan]▸ log[/cyan]")

    spec = ctx.spec
    eval_result = ctx.eval_result
    retries = ctx.retries.get("implementation", 0)
    verdict = eval_result.get("verdict", "unknown")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    dod_lines = "\n".join(
        f"- {'✓' if r.get('passed') else '✗'} {r['criterion']}"
        + (f" — {r['note']}" if r.get("note") else "")
        for r in eval_result.get("dod_results", [])
    )
    global_lines = "\n".join(
        f"- {'✓' if r.get('passed') else '✗'} {r['criterion']}"
        + (f" — {r['note']}" if r.get("note") else "")
        for r in eval_result.get("global_results", [])
    )

    log = f"""# Run Log — {spec.get("title", "task")}

**Data:** {now}
**Veredicto:** {verdict}
**Tentativas de implementação:** {retries + 1}
**Artefatos:** {ctx.run_dir}

---

## Task

{ctx.task_text}

## Spec

**Resumo:** {spec.get("summary", "")}

**DoD:**
{chr(10).join(f"- {d}" for d in spec.get("dod", []))}

**Fora do escopo:**
{chr(10).join(f"- {o}" for o in spec.get("out_of_scope", []))}

---

## Avaliação

### DoD
{dod_lines}

### Critérios Globais
{global_lines}
"""


    if ctx.metrics.total_input_tokens > 0 or ctx.metrics.total_output_tokens > 0:
        log += "\n## Uso de Tokens\n"
        log += f"- **Input Tokens:** {ctx.metrics.total_input_tokens}\n"
        log += f"- **Output Tokens:** {ctx.metrics.total_output_tokens}\n"
        log += f"- **Cache Read:** {ctx.metrics.total_cache_read_tokens}\n"
        log += f"- **Cache Creation:** {ctx.metrics.total_cache_creation_tokens}\n"
        log += f"- **Custo Total:** ${ctx.metrics.total_cost_usd:.4f}\n"
        
        # Save metrics JSON
        metrics_file = ctx.run_dir / "metrics.json"
        metrics_data = {
            "total_input_tokens": ctx.metrics.total_input_tokens,
            "total_output_tokens": ctx.metrics.total_output_tokens,
            "total_cache_creation_tokens": ctx.metrics.total_cache_creation_tokens,
            "total_cache_read_tokens": ctx.metrics.total_cache_read_tokens,
            "total_cost_usd": ctx.metrics.total_cost_usd,
            "steps": ctx.metrics.steps
        }
        import json
        metrics_file.write_text(json.dumps(metrics_data, indent=2))
        console.print(f"[green]✓[/green] métricas salvas em [dim]{metrics_file}[/dim]")

    if eval_result.get("rejection_reason"):
        log += f"\n### Motivo de rejeição (última tentativa)\n{eval_result['rejection_reason']}\n"

    log_file = ctx.run_dir / "run-log.md"
    log_file.write_text(log)
    console.print(f"[green]✓[/green] log salvo em [dim]{log_file}[/dim]")

    return Transition(next_state="done")


# ---------------------------------------------------------------------------
# Mapa de estados
# ---------------------------------------------------------------------------

STATES: dict[str, StateFn] = {
    "git_check": state_git_check,
    "spec_generation": state_spec_generation,
    "spec_edit": state_spec_edit,
    "human_gate_spec": state_human_gate_spec,
    "implementation": state_implementation,
    "human_gate_commit": state_human_gate_commit,
    "evaluation": state_evaluation,
    "human_gate_eval": state_human_gate_eval,
    "log": state_log,
}
