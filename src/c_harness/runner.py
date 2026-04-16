"""Orquestração principal do c-harness."""

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from rich.console import Console

from .agents import Agent, create_agent, TokenUsage
from .git import GitContext

console = Console()

MAX_RETRIES = 2  # padrão "dois strikes" — falhou duas vezes, escala pro humano

# Configuração global do agente (setado no main)
_agent_instance: Agent | None = None


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------


@dataclass
class RunMetrics:
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float = 0.0
    steps: list[dict] = field(default_factory=list)


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
    metrics: RunMetrics = field(default_factory=RunMetrics)
    spec_id: str | None = None
    spec_edit_feedback: str = ""


@dataclass
class Transition:
    """Resultado de um estado — para onde ir e por quê."""

    next_state: str
    reason: str = ""


StateFn = Callable[[Context], Transition]


# ---------------------------------------------------------------------------
# Funções utilitárias
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


# ---------------------------------------------------------------------------
# Interface pública de execução de agentes
# ---------------------------------------------------------------------------


def run_agent(
    prompt: str,
    system_prompt: str,
    cwd: Path,
    label: str = "",
    allowed_tools: list[str] | None = None,
    state: str = "",
) -> tuple[str, TokenUsage]:
    """Executa o agente configurado (claude ou pi).

    Usa a instância global do agente configurada via configure_agent().

    Raises:
        RuntimeError: Se o agente não foi configurado antes da chamada.
    """
    if _agent_instance is None:
        raise RuntimeError("agente não configurado — chame configure_agent() primeiro")
    return _agent_instance.run(prompt, system_prompt, cwd, label, allowed_tools, state)


def configure_agent(backend: Literal["claude", "cursor", "pi"]) -> None:
    """Configura o agente global a ser usado pelas chamadas run_agent().

    Deve ser chamado uma vez no início do programa antes de usar run_agent().

    Args:
        backend: Nome do backend de agente ("claude" ou "pi").
    """
    global _agent_instance
    _agent_instance = create_agent(backend)


def get_agent_backend() -> str:
    """Retorna o nome do backend do agente atualmente configurado.

    Returns:
        Nome do backend ("claude" ou "pi").

    Raises:
        RuntimeError: Se o agente não foi configurado.
    """
    if _agent_instance is None:
        raise RuntimeError("agente não configurado")
    return _agent_instance.name


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


def _detect_json_file(args: list[str]) -> Path | None:
    """Retorna o arquivo JSON se o único argumento for um .json existente, senão None."""
    if len(args) == 1:
        candidate = Path(args[0])
        if candidate.suffix == ".json" and candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(args: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse simples de flags --key value ou --key=value.

    Retorna (flags_dict, positional_args).
    """
    flags: dict[str, str] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                key, value = key.split("=", 1)
                flags[key] = value
            elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                i += 1
            else:
                flags[key] = "true"
        else:
            positional.append(arg)
        i += 1
    return flags, positional


def _spec_to_md(
    spec_id: str,
    title: str,
    summary: str,
    dod: list[str],
    out_of_scope: list[str],
    notes: str,
) -> str:
    lines = [f"# {title}\n", f"**Summary:** {summary}\n", "## DoD (Definition of Done)"]
    lines += [f"- [ ] {d}" for d in dod] or ["- critério 1"]
    lines += ["", "## Out of Scope"]
    lines += [f"- {o}" for o in out_of_scope] or ["- nenhum"]
    lines += ["", "## Notes", notes or "- contexto extra", ""]
    return "\n".join(lines)


def _wizard_collect() -> tuple[str, str, list[str], list[str], str]:
    """Coleta campos da spec interativamente com suporte a undo."""
    console.print(
        "\n  [dim]listas: Enter vazio para terminar · 'u' + Enter para desfazer o último item[/dim]\n"
    )

    title = console.input("título (ex: Adicionar login OAuth): ").strip()
    summary = console.input("summary (o que precisa ser feito, 2-3 frases): ").strip()

    console.print("DoD — critérios de done:")
    dod: list[str] = []
    while True:
        item = console.input(f"  {len(dod) + 1}. ").strip()
        if not item:
            break
        if item.lower() == "u" and dod:
            console.print(f"  ↩ removido: {dod.pop()}")
        elif item.lower() != "u":
            dod.append(item)

    console.print("out of scope — o que NÃO deve ser feito:")
    out_of_scope: list[str] = []
    while True:
        item = console.input(f"  {len(out_of_scope) + 1}. ").strip()
        if not item:
            break
        if item.lower() == "u" and out_of_scope:
            console.print(f"  ↩ removido: {out_of_scope.pop()}")
        elif item.lower() != "u":
            out_of_scope.append(item)

    notes = console.input("notes (contexto extra, Enter para pular): ").strip()

    return title, summary, dod, out_of_scope, notes


def _run_new_spec(spec_id: str) -> None:
    """Cria uma nova spec — IA, wizard ou template."""
    if not spec_id.startswith("spec-"):
        spec_id = f"spec-{spec_id}"

    specs_dir = Path(".harness/specs")
    specs_dir.mkdir(parents=True, exist_ok=True)

    spec_path = specs_dir / f"{spec_id}.md"
    if spec_path.exists():
        console.print(f"[yellow]⚠ {spec_path} já existe.[/yellow]")
        return

    console.print(f"\n[bold]nova spec:[/bold] {spec_id}\n")
    console.print(
        "  [green]a[/green]  gerar com IA  [dim](descreva a task, o agente estrutura)[/dim]"
    )
    console.print(
        "  [green]m[/green]  wizard manual  [dim](preencher campo a campo)[/dim]"
    )
    console.print(
        "  [green]t[/green]  só template    [dim](abre arquivo em branco para editar)[/dim]"
    )
    console.print()
    choice = console.input("[yellow]modo [a/m/t]:[/yellow] ").strip().lower()

    if choice in ("a", "ia", "ai"):
        description = console.input("descreva a task: ").strip()
        if not description:
            console.print("[dim]nenhuma descrição informada — abortando.[/dim]")
            return

        from .states import SPEC_PROMPT

        raw, _ = run_agent(
            prompt=f"Task: {description}",
            system_prompt=SPEC_PROMPT,
            cwd=specs_dir,
            label="spec-new",
            allowed_tools=None,
            state="spec_generation",
        )

        try:
            data = json.loads(_extract_json(raw))
        except Exception:
            console.print(f"[red]erro ao parsear spec gerada:[/red]\n{raw}")
            return

        title = data.get("title", spec_id)
        summary = data.get("summary", "")
        dod = data.get("dod", [])
        out_of_scope = data.get("out_of_scope", [])
        notes = data.get("notes") or ""

    elif choice in ("m", "manual", "wizard"):
        title, summary, dod, out_of_scope, notes = _wizard_collect()
        if not title:
            title = spec_id

    else:
        # template vazio
        template = f"""# {spec_id}

**Summary:** 
Descreva o que precisa ser feito em 2-3 frases.

## DoD (Definition of Done)
- critério 1
- critério 2

## Out of Scope
- o que NÃO deve ser feito

## Notes
- contexto extra
"""
        spec_path.write_text(template)
        console.print(f"[green]✓[/green] template criado em [bold]{spec_path}[/bold]")
        console.print(
            f"  [dim]edite o arquivo e depois execute: c-harness run {spec_id}[/dim]"
        )
        return

    md = _spec_to_md(spec_id, title, summary, dod, out_of_scope, notes)

    console.print(f"\n[dim]{'─' * 50}[/dim]")
    console.print(f"[bold]{title}[/bold]")
    console.print(f"[dim]{summary}[/dim]")
    if dod:
        console.print(f"  DoD: {len(dod)} critérios")
        for d in dod:
            console.print(f"    [dim]• {d}[/dim]")
    console.print(f"[dim]{'─' * 50}[/dim]\n")

    confirm = console.input("salvar spec? [s/N]: ").strip().lower()
    if confirm not in ("s", "sim", "y", "yes"):
        console.print("[dim]abortado.[/dim]")
        return

    spec_path.write_text(md)
    console.print(f"[green]✓[/green] spec salva em [bold]{spec_path}[/bold]")
    console.print(f"  [dim]execute: c-harness run {spec_id}[/dim]")


def _run_setup() -> None:
    """Configura o c-harness no projeto atual."""
    harness_dir = Path(".harness")
    harness_dir.mkdir(exist_ok=True)
    config_file = harness_dir / "config.yml"
    skills_dir = harness_dir / "skills"
    rules_dir = harness_dir / "rules"

    console.print("\n[bold cyan]c-harness setup[/bold cyan]\n")

    if config_file.exists():
        console.print(f"[yellow]⚠ {config_file} já existe. Pulando criação.[/yellow]")
    else:
        config_content = """# Configuração do c-harness
harness:
  # Diretório de skills (aplicadas por estado)
  skills_dir: ".harness/skills"
  
  # Diretório de regras (aplicadas a todos os estados)
  rules_dir: ".harness/rules"

  # Skills globais de ~/.claude/skills a injetar (descomente para filtrar)
  # global_skills:
  #   - "grug"
  #   - "harness"
  
  # Comandos automatizados executados antes da avaliação LLM
  # Se algum falhar, o pipeline volta imediatamente para a implementação
  checks:
    commands:
      # - "uv run ruff check ."
      # - "uv run pytest"
      
  # Critérios de avaliação globais (aplicados pelo agente de evaluation)
  evaluation:
    global_checks:
      - "Sem TODOs ou placeholders no código"
      - "Funções com responsabilidade única e clara"
      - "Sem código morto ou imports não utilizados"
      - "Nomes descritivos (variáveis, funções, arquivos)"
  
  # Rastreio de uso de tokens e contexto
  metrics:
    save_tokens: true
    log_file: "metrics.json"

agents:
  claude:
    model: "claude-3-5-sonnet-latest"
  pi:
    model: "default"
"""
        config_file.write_text(config_content)
        console.print(f"[green]✓[/green] criado {config_file}")

    if not skills_dir.exists():
        skills_dir.mkdir(parents=True)
        console.print(f"[green]✓[/green] criado diretório {skills_dir}/")

        eval_dir = skills_dir / "evaluation"
        eval_dir.mkdir()
        (eval_dir / "strict-checks.md").write_text(
            "# Avaliação Estrita\n- Verifique nomenclatura clara.\n- Aponte falhas se a complexidade for alta e não houver testes.\n"
        )
        (skills_dir / "implementation.md").write_text(
            "# Regras de Implementação\n- Escreva código limpo e siga o style guide do projeto.\n"
        )
        console.print(f"  [dim]↳ adicionados templates em {skills_dir}/[/dim]")
    else:
        console.print(f"[yellow]⚠ {skills_dir}/ já existe.[/yellow]")

    if not rules_dir.exists():
        rules_dir.mkdir(parents=True)
        console.print(f"[green]✓[/green] criado diretório {rules_dir}/")
        (rules_dir / "project-rules.md").write_text(
            "# Regras do Projeto\n- Respeite o style guide.\n- Priorize simplicidade.\n"
        )
        console.print(f"  [dim]↳ adicionado template em {rules_dir}/[/dim]")
    else:
        console.print(f"[yellow]⚠ {rules_dir}/ já existe.[/yellow]")

    console.print(
        "\n[bold green]Setup concluído![/bold green] Você já pode usar o c-harness neste projeto.\n"
    )


def main() -> None:
    """Entrypoint do c-harness."""
    raw_args = sys.argv[1:]
    flags, args = _parse_args(raw_args)

    if args and args[0] == "setup":
        _run_setup()
        sys.exit(0)

    # Configura agente backend (necessário antes de 'new' pois pode usar IA)
    agent = flags.get("agent", "claude")
    if agent not in ("claude", "cursor", "pi"):
        console.print(f"[red][erro][/red] agente desconhecido: '{agent}'")
        console.print("  use: --agent claude  |  --agent cursor  |  --agent pi")
        sys.exit(1)
    configure_agent(agent)

    if args and args[0] == "new":
        if len(args) < 2:
            console.print("[red]Uso:[/red] c-harness new <spec-id>")
            sys.exit(1)
        _run_new_spec(args[1])
        sys.exit(0)

    if not args:
        console.print("""
[bold]uso:[/bold]
  [cyan]c-harness[/cyan] [green]setup[/green]                     [dim]inicializa .harness/ no projeto atual[/dim]
  [cyan]c-harness[/cyan] [green]new[/green] [yellow]<id>[/yellow]                  [dim]cria template de spec em .harness/specs/[/dim]
  [cyan]c-harness[/cyan] [green]run[/green] [yellow]<id>[/yellow]                  [dim]executa pipeline com spec existente[/dim]
  [cyan]c-harness[/cyan] [green]'<texto>'[/green]                 [dim]texto livre → gera spec e executa[/dim]
  [cyan]c-harness[/cyan] [green]--edit[/green] [yellow]<spec.json>[/yellow]        [dim]edita spec JSON via agente[/dim]
  [cyan]c-harness[/cyan] [green]<spec.json>[/green]               [dim]executa a partir de spec JSON local[/dim]
  [cyan]c-harness[/cyan] [green]<resume.json>[/green]             [dim]retoma run pausada a partir de ponto salvo[/dim]

[bold]flags:[/bold]
  [cyan]--agent[/cyan] [yellow]claude|pi|cursor[/yellow]    [dim]seleciona o backend de agente (padrão: claude)[/dim]
""")
        sys.exit(1)

    project_dir = Path.cwd()
    run_dir = (
        project_dir / ".harness" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold]c-harness[/bold] ({get_agent_backend()}) → ", end="")
    console.print(str(run_dir.relative_to(project_dir)), style="dim")
    console.print()

    if args[0] == "run":
        if len(args) < 2:
            console.print("[red]Uso:[/red] c-harness run <spec-id>")
            sys.exit(1)
        spec_id = args[1]
        if not spec_id.startswith("spec-"):
            spec_id = f"spec-{spec_id}"

        ctx = Context(
            task_text="", run_dir=run_dir, project_dir=project_dir, metrics=RunMetrics()
        )
        ctx.spec_id = spec_id
        console.print(f"[bold]modo:[/bold] spec-driven → {spec_id}")
        run_pipeline(ctx)
        sys.exit(0)

    if args[0] == "--edit":
        _run_edit_spec(args, run_dir, project_dir)
    elif json_file := _detect_json_file(args):
        _run_from_json(json_file, run_dir, project_dir)
    else:
        _run_free_text(args, run_dir, project_dir)

    console.print(
        f"\n[bold green]✓ run concluída[/bold green] [dim]→ {run_dir.relative_to(project_dir)}[/dim]\n"
    )


VALID_RESUME_STATES = frozenset(
    {
        "git_check",
        "spec_generation",
        "spec_edit",
        "human_gate_spec",
        "implementation",
        "human_gate_commit",
        "evaluation",
        "human_gate_eval",
        "log",
    }
)


def _run_edit_spec(args: list[str], run_dir: Path, project_dir: Path) -> None:
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


def _run_from_json(json_file: Path, run_dir: Path, project_dir: Path) -> None:
    try:
        data = json.loads(json_file.read_text())
    except json.JSONDecodeError as exc:
        console.print(f"[red][erro][/red] JSON inválido em {json_file}: {exc}")
        sys.exit(1)

    if "resume_from" in data:
        _run_resume(data["resume_from"], run_dir, project_dir)
    else:
        _run_local_spec(data, json_file, run_dir, project_dir)


def _run_resume(resume: dict, run_dir: Path, project_dir: Path) -> None:
    resume_state = resume.get("state", "implementation")
    resume_spec: dict | None = resume.get("spec")
    errors = []
    if not resume_spec:
        errors.append("campo 'spec' ausente ou vazio em 'resume_from'")
    if resume_state not in VALID_RESUME_STATES:
        errors.append(f"campo 'state' inválido em 'resume_from': '{resume_state}'")
    if errors:
        console.print("[red][erro][/red] resume inválido:")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
        sys.exit(1)

    assert resume_spec is not None  # validated above
    console.print(
        f"[bold]modo:[/bold] retomada → continuando de [cyan]{resume_state}[/cyan]"
    )
    if original_run_dir := resume.get("run_dir"):
        console.print(f"  [dim]run original: {original_run_dir}[/dim]")

    (run_dir / "spec.json").write_text(
        json.dumps(resume_spec, indent=2, ensure_ascii=False)
    )
    ctx = Context(
        task_text=resume_spec.get("summary", ""),
        run_dir=run_dir,
        project_dir=project_dir,
        spec=resume_spec,
        eval_result=resume.get("eval_result", {}),
        retries=resume.get("retries", {}),
    )
    run_pipeline(ctx, start_state=resume_state)


def _run_local_spec(
    data: dict, json_file: Path, run_dir: Path, project_dir: Path
) -> None:
    errors = validate_spec(data)
    if errors:
        console.print("[red][erro][/red] spec inválida:")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
        sys.exit(1)
    console.print(f"[bold]modo:[/bold] spec local → [dim]{json_file}[/dim]")
    (run_dir / "spec.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    ctx = Context(
        task_text=data.get("summary", ""),
        run_dir=run_dir,
        project_dir=project_dir,
        spec=data,
    )
    run_pipeline(ctx, start_state="human_gate_spec")


def _run_free_text(args: list[str], run_dir: Path, project_dir: Path) -> None:
    task_text = " ".join(args)
    console.print("[bold]modo:[/bold] texto livre → gerando spec")
    ctx = Context(task_text=task_text, run_dir=run_dir, project_dir=project_dir)
    run_pipeline(ctx)
