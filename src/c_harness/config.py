import yaml
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class Config:
    skills_dir: Path = Path("skills")
    save_tokens: bool = True
    metrics_file: str = "metrics.json"
    global_skills: list[str] = field(default_factory=list)
    
    @classmethod
    def load(cls, config_path: Path) -> "Config":
        if not config_path.exists():
            return cls()
            
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
            
        harness = data.get("harness", {})
        metrics = harness.get("metrics", {})
        
        return cls(
            skills_dir=Path(harness.get("skills_dir", "skills")),
            save_tokens=metrics.get("save_tokens", True),
            metrics_file=metrics.get("log_file", "metrics.json"),
            global_skills=harness.get("global_skills", [])
        )

# Instância global carregada em tempo de inicialização
config = Config.load(Path("config.yml"))

def _load_claude_skills(base_dir: Path, allowed_skills: list[str]) -> list[str]:
    """Carrega skills da pasta .claude/skills (project ou geral)."""
    skills = []
    if not base_dir.exists() or not base_dir.is_dir():
        return skills
        
    for item in sorted(base_dir.iterdir()):
        # Se allowed_skills estiver definido, filtra pelo nome
        if allowed_skills and item.stem not in allowed_skills and item.name not in allowed_skills:
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

def load_skills(state_name: str = "") -> str:
    """Carrega skills globais e específicas do estado."""
    skills = []
    
    # 1. Global Skills: .claude/skills (Geral e Project)
    general_claude_skills = Path.home() / ".claude" / "skills"
    project_claude_skills = Path.cwd() / ".claude" / "skills"
    
    # Se global_skills vazio na config, carrega tudo (pode ficar grande)
    allowed = config.global_skills
    
    skills.extend(_load_claude_skills(general_claude_skills, allowed))
    if project_claude_skills != general_claude_skills:
        skills.extend(_load_claude_skills(project_claude_skills, allowed))

    # 2. State-Specific Skills: da pasta skills/ do projeto
    if config.skills_dir.exists() and config.skills_dir.is_dir():
        valid_states = {"spec_generation", "spec_edit", "implementation", "evaluation"}
        
        def _load_state_path(p: Path):
            if p.is_file() and p.suffix == ".md":
                content = p.read_text().strip()
                skills.append(f"--- SKILL STATE: {state_name.upper()} ---\n{content}\n")
            elif p.is_dir():
                for f in sorted(p.glob("*.md")):
                    content = f.read_text().strip()
                    skills.append(f"--- SKILL STATE: {state_name.upper()} ({f.stem}) ---\n{content}\n")

        if state_name in valid_states:
            _load_state_path(config.skills_dir / f"{state_name}.md")
            _load_state_path(config.skills_dir / state_name)

    if not skills:
        return ""
        
    return "\n# SKILLS\n" + "\n".join(skills)
