"""c-harness: CLI para desenvolvimento assistido por agentes."""

from .agents import Agent, create_agent
from .git import GitContext, collect_git_context
from .runner import (
    Context,
    StateFn,
    Transition,
    configure_agent,
    console,
    get_agent_backend,
    main,
    run_agent,
    run_pipeline,
    validate_spec,
)
from .states import STATES

__all__ = [
    "Agent",
    "configure_agent",
    "create_agent",
    "GitContext",
    "collect_git_context",
    "Context",
    "Transition",
    "StateFn",
    "console",
    "get_agent_backend",
    "run_agent",
    "run_pipeline",
    "validate_spec",
    "main",
    "STATES",
]
