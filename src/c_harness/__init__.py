"""c-harness: CLI para desenvolvimento assistido por agentes com Claude Code."""

from .git import GitContext, collect_git_context
from .runner import (
    Context,
    StateFn,
    Transition,
    console,
    main,
    run_claude,
    run_pipeline,
    validate_spec,
)
from .states import STATES

__all__ = [
    "GitContext",
    "collect_git_context",
    "Context",
    "Transition",
    "StateFn",
    "console",
    "run_claude",
    "run_pipeline",
    "validate_spec",
    "main",
    "STATES",
]
