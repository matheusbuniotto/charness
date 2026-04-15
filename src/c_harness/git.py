"""Operações git do c-harness."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitContext:
    """Informações git do projeto."""

    branch: str
    log: str
    is_clean: bool
    dirty_summary: str  # resumo do que está sujo (se houver)


def _run_git(args: list[str], cwd: Path) -> str:
    """Executa comando git e retorna stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.stdout.strip()


def collect_git_context(project_dir: Path) -> GitContext | None:
    """Coleta contexto git do projeto. Retorna None se não for repositório git."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    if result.returncode != 0:
        return None

    branch = _run_git(["branch", "--show-current"], project_dir) or "detached"
    log = _run_git(["log", "--oneline", "-10"], project_dir)
    status = _run_git(["status", "--porcelain"], project_dir)
    is_clean = not bool(status)
    dirty_summary = status if status else ""

    return GitContext(
        branch=branch,
        log=log,
        is_clean=is_clean,
        dirty_summary=dirty_summary,
    )
