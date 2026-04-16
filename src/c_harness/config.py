import yaml
from pathlib import Path
from dataclasses import dataclass, field

VALID_STATES = {"spec_generation", "spec_edit", "implementation", "evaluation"}


@dataclass
class StateSkills:
    """Skills configuradas para um estado específico."""

    global_skills: list[str] = field(default_factory=list)  # ~/.claude/skills/
    local_files: list[str] = field(default_factory=list)  # .harness/skills/
    rule_files: list[str] = field(default_factory=list)  # .harness/rules/


@dataclass
class Config:
    skills_dir: Path = Path(".harness/skills")
    rules_dir: Path = Path(".harness/rules")
    save_tokens: bool = True
    metrics_file: str = "metrics.json"

    # Comandos automatizados
    checks_commands: list[str] = field(default_factory=list)

    # Critérios de avaliação globais
    global_checks: list[str] = field(
        default_factory=lambda: [
            "Sem TODOs ou placeholders no código",
            "Funções com responsabilidade única e clara",
            "Sem código morto ou imports não utilizados",
            "Nomes descritivos (variáveis, funções, arquivos)",
        ]
    )

    # Model config: global default per backend, then per-state overrides
    models: dict[str, str] = field(default_factory=dict)
    # state_models: overrides globais de estado (usados pelo ClaudeAgent)
    state_models: dict[str, str] = field(default_factory=dict)
    # cursor_state_models: overrides de estado específicos para o CursorAgent
    cursor_state_models: dict[str, str] = field(default_factory=dict)

    # NOVO: Per-state explicit skills (replaces global_skills whitelist)
    state_skills: dict[str, StateSkills] = field(default_factory=dict)

    # File reading controls
    read_excludes: list[str] = field(default_factory=list)

    # Token/cost limits
    max_input_tokens: int = 0  # 0 = unlimited
    max_cost_usd: float = 0.0  # 0.0 = unlimited

    @classmethod
    def load(cls, config_path: Path) -> "Config":
        if not config_path.exists():
            return cls()

        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}

        harness = data.get("harness", {})
        metrics = harness.get("metrics", {})
        evaluation = harness.get("evaluation", {})
        checks = harness.get("checks", {})
        limits = harness.get("limits", {})

        agents_cfg = data.get("agents", {})
        models = {}
        for backend, cfg in agents_cfg.items():
            if isinstance(cfg, dict) and "model" in cfg:
                models[backend] = cfg["model"]

        state_models = {}
        for state_name, cfg in agents_cfg.items():
            if state_name in VALID_STATES and isinstance(cfg, dict) and "model" in cfg:
                state_models[state_name] = cfg["model"]
        states_cfg = agents_cfg.get("states", {})
        for state_name, cfg in states_cfg.items():
            if isinstance(cfg, dict) and "model" in cfg:
                state_models[state_name] = cfg["model"]

        cursor_state_models = {}
        cursor_states_cfg = agents_cfg.get("cursor", {}).get("states", {})
        for state_name, cfg in cursor_states_cfg.items():
            if isinstance(cfg, dict) and "model" in cfg:
                cursor_state_models[state_name] = cfg["model"]

        # Parse NEW per-state skills config
        state_skills: dict[str, StateSkills] = {}
        skills_cfg = data.get("skills", {})
        for state_name in VALID_STATES:
            if state_name not in skills_cfg:
                continue
            cfg = skills_cfg[state_name]
            if isinstance(cfg, dict):
                state_skills[state_name] = StateSkills(
                    global_skills=cfg.get("global", []),
                    local_files=cfg.get("local", []),
                    rule_files=cfg.get("rules", []),
                )

        return cls(
            skills_dir=Path(harness.get("skills_dir", ".harness/skills")),
            rules_dir=Path(harness.get("rules_dir", ".harness/rules")),
            save_tokens=metrics.get("save_tokens", True),
            metrics_file=metrics.get("log_file", "metrics.json"),
            checks_commands=checks.get("commands", []),
            global_checks=evaluation.get(
                "global_checks",
                [
                    "Sem TODOs ou placeholders no código",
                    "Funções com responsabilidade única e clara",
                    "Sem código morto ou imports não utilizados",
                    "Nomes descritivos (variáveis, funções, arquivos)",
                ],
            ),
            models=models,
            state_models=state_models,
            cursor_state_models=cursor_state_models,
            state_skills=state_skills,
            read_excludes=harness.get("read_excludes", []),
            max_input_tokens=limits.get("max_input_tokens", 0),
            max_cost_usd=limits.get("max_cost_usd", 0.0),
        )


# Instância global carregada em tempo de inicialização
config = Config.load(Path(".harness/config.yml"))

# Cache de skills para evitar re-leitura de disco
_skills_cache: dict[str, str] = {}
_rules_cache: str | None = None


def _load_claude_skills(base_dir: Path, allowed_skills: list[str]) -> list[str]:
    """Carrega skills da pasta .claude/skills (project ou geral)."""
    skills = []
    if not base_dir.exists() or not base_dir.is_dir():
        return skills

    for item in sorted(base_dir.iterdir()):
        if allowed_skills and item.stem not in allowed_skills:
            continue

        if item.is_file() and item.suffix == ".md":
            content = item.read_text().strip()
            skills.append(f"--- SKILL GLOBAL ({item.stem}) ---\n{content}\n")
        elif item.is_dir():
            skill_file = item / "SKILL.md"
            if skill_file.is_file():
                content = skill_file.read_text().strip()
                skills.append(f"--- SKILL GLOBAL ({item.name}) ---\n{content}\n")
    return skills


def _load_local_skill_files(files: list[str], base_dir: Path) -> list[str]:
    """Carrega arquivos de skill locais específicos."""
    skills = []
    for fname in files:
        path = base_dir / fname
        if path.is_file():
            content = path.read_text().strip()
            skills.append(f"--- SKILL LOCAL ({path.stem}) ---\n{content}\n")
    return skills


def _load_rule_files(files: list[str], base_dir: Path) -> list[str]:
    """Carrega arquivos de regras específicos como skills."""
    skills = []
    for fname in files:
        path = base_dir / fname
        if path.is_file():
            content = path.read_text().strip()
            skills.append(f"--- RULE SKILL ({path.stem}) ---\n{content}\n")
        elif path.is_dir():
            for f in sorted(path.glob("*.md")):
                content = f.read_text().strip()
                skills.append(f"--- RULE SKILL ({f.stem}) ---\n{content}\n")
    return skills


def load_skills(state_name: str = "") -> str:
    """Carrega skills configuradas explicitamente para o estado (com cache)."""
    global _skills_cache

    # Build cache key from state + config
    state_cfg = config.state_skills.get(state_name, StateSkills())
    cache_key = (
        f"{state_name}:"
        f"{','.join(sorted(state_cfg.global_skills))}:"
        f"{','.join(sorted(state_cfg.local_files))}:"
        f"{','.join(sorted(state_cfg.rule_files))}"
    )
    if cache_key in _skills_cache:
        return _skills_cache[cache_key]

    skills: list[str] = []

    # 1. Global skills (from ~/.claude/skills)
    if state_cfg.global_skills:
        general_claude = Path.home() / ".claude" / "skills"
        project_claude = Path.cwd() / ".claude" / "skills"
        skills.extend(_load_claude_skills(general_claude, state_cfg.global_skills))
        if project_claude != general_claude:
            skills.extend(_load_claude_skills(project_claude, state_cfg.global_skills))

    # 2. Local skill files (from .harness/skills/)
    if state_cfg.local_files:
        skills.extend(_load_local_skill_files(state_cfg.local_files, config.skills_dir))

    # 3. Rule files as skills (from .harness/rules/)
    if state_cfg.rule_files:
        skills.extend(_load_rule_files(state_cfg.rule_files, config.rules_dir))

    # 4. Fallback: se nada configurado e é estado válido, carrega padrão do diretório state/
    if not skills and state_name in VALID_STATES:
        if config.skills_dir.exists():
            state_path = config.skills_dir / state_name
            if state_path.is_dir():
                for f in sorted(state_path.glob("*.md")):
                    content = f.read_text().strip()
                    skills.append(
                        f"--- SKILL STATE: {state_name.upper()} ({f.stem}) ---\n{content}\n"
                    )
            elif (state_path := config.skills_dir / f"{state_name}.md").exists():
                content = state_path.read_text().strip()
                skills.append(f"--- SKILL STATE: {state_name.upper()} ---\n{content}\n")

    result = "\n# SKILLS\n" + "\n".join(skills) if skills else ""
    _skills_cache[cache_key] = result
    return result


def load_rules() -> str:
    """Carrega regras globais da pasta rules/ (com cache)."""
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache

    if not config.rules_dir.exists() or not config.rules_dir.is_dir():
        _rules_cache = ""
        return ""

    rules = []
    for item in sorted(config.rules_dir.glob("*.md")):
        content = item.read_text().strip()
        rules.append(f"--- RULE: {item.stem} ---\n{content}\n")

    result = "\n# REGRAS DO PROJETO\n" + "\n".join(rules) if rules else ""
    _rules_cache = result
    return result


def check_limits(current_metrics: dict) -> tuple[bool, str]:
    """Verifica se os limites de tokens/custo foram atingidos.

    Retorna (ok, mensagem). Se não ok, o caller deve abortar.
    """
    if config.max_input_tokens > 0:
        total_input = current_metrics.get("total_input_tokens", 0)
        total_cache_read = current_metrics.get("total_cache_read_tokens", 0)
        total = total_input + total_cache_read
        if total >= config.max_input_tokens:
            return (
                False,
                f"Limite de tokens atingido: {total:,} / {config.max_input_tokens:,}",
            )

    if config.max_cost_usd > 0.0:
        cost = current_metrics.get("total_cost_usd", 0.0)
        if cost >= config.max_cost_usd:
            return (
                False,
                f"Limite de custo atingido: ${cost:.4f} / ${config.max_cost_usd:.4f}",
            )

    return True, ""


def should_exclude_file(file_path: str | Path) -> bool:
    """Verifica se um arquivo deve ser excluído da leitura baseado em padrões.

    Suporta glob patterns: **/dir/**, *.ext, etc.
    """
    from fnmatch import fnmatch

    path_str = str(file_path)

    for pattern in config.read_excludes:
        # Normalize pattern
        pat = pattern.strip()
        if not pat:
            continue

        # Match direto ou com glob
        if fnmatch(path_str, pat) or fnmatch(Path(path_str).name, pat):
            return True

        # Match parcial para patterns tipo .venv/**
        if pat.endswith("/**"):
            prefix = pat[:-3]
            if prefix in path_str.split("/"):
                return True

    return False
