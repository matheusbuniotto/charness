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
) -> tuple[str, TokenUsage]:
    """Executa o agente configurado (claude ou pi).

    Usa a instância global do agente configurada via configure_agent().

    Raises:
        RuntimeError: Se o agente não foi configurado antes da chamada.
    """
    if _agent_instance is None:
        raise RuntimeError("agente não configurado — chame configure_agent() primeiro")
    return _agent_instance.run(prompt, system_prompt, cwd, label, allowed_tools)


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
        (eval_dir / "strict-checks.md").write_text("# Avaliação Estrita\n- Verifique nomenclatura clara.\n- Aponte falhas se a complexidade for alta e não houver testes.\n")
        (skills_dir / "implementation.md").write_text("# Regras de Implementação\n- Escreva código limpo e siga o style guide do projeto.\n")
        console.print(f"  [dim]↳ adicionados templates em {skills_dir}/[/dim]")
    else:
        console.print(f"[yellow]⚠ {skills_dir}/ já existe.[/yellow]")

    if not rules_dir.exists():
        rules_dir.mkdir(parents=True)
        console.print(f"[green]✓[/green] criado diretório {rules_dir}/")
        (rules_dir / "project-rules.md").write_text("# Regras do Projeto\n- Respeite o style guide.\n- Priorize simplicidade.\n")
        console.print(f"  [dim]↳ adicionado template em {rules_dir}/[/dim]")
    else:
        console.print(f"[yellow]⚠ {rules_dir}/ já existe.[/yellow]")
        
    console.print("\n[bold green]Setup concluído![/bold green] Você já pode usar o c-harness neste projeto.\n")

def main() -> None:

    """Entrypoint do c-harness."""
    raw_args = sys.argv[1:]
    flags, args = _parse_args(raw_args)


    if args and args[0] == "setup":
        _run_setup()
        sys.exit(0)

    # Configura agente backend

    agent = flags.get("agent", "claude")
    if agent not in ("claude", "cursor", "pi"):
        console.print(f"[red][erro][/red] agente desconhecido: '{agent}'")
        console.print("  use: --agent claude  |  --agent cursor  |  --agent pi")
        sys.exit(1)
    configure_agent(agent)

    if not args:
        console.print("[yellow]uso:[/yellow] c-harness '<descrição da task>'")
        console.print(
            "       c-harness setup                        [dim]# configura o projeto atual[/dim]"
        )
        console.print(
            "       c-harness <caminho/para/spec.json>     [dim]# spec local[/dim]"
        )
        console.print(
            "       c-harness <caminho/para/resume.json>   [dim]# retomada[/dim]"
        )
        console.print("       c-harness --edit <caminho/para/spec.json>")
        console.print("")
        console.print("[dim]flags:[/dim]")
        console.print(
            "       --agent claude|pi    [dim]# seleciona o agente (padrão: claude)[/dim]"
        )
        sys.exit(1)

    project_dir = Path.cwd()
    run_dir = (
        project_dir / ".harness" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold]c-harness[/bold] ({get_agent_backend()}) → ", end="")
    console.print(str(run_dir.relative_to(project_dir)), style="dim")
    console.print()

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
                    resume_errors.append(
                        "campo 'spec' ausente ou vazio em 'resume_from'"
                    )
                if resume_state not in (
                    "git_check",
                    "spec_generation",
                    "spec_edit",
                    "human_gate_spec",
                    "implementation",
                    "human_gate_commit",
                    "evaluation",
                    "human_gate_eval",
                    "log",
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

                console.print(f"[bold]modo:[/bold] spec local → [dim]{json_file}[/dim]")

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
