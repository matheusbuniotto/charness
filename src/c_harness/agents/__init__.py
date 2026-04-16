"""Módulo de agents - interface base e factory."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: list[str] = field(default_factory=list)


@runtime_checkable
class Agent(Protocol):
    name: str

    def run(
        self,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        label: str,
        allowed_tools: list[str] | None = None,
        state: str = "",
    ) -> tuple[str, TokenUsage]: ...


def create_agent(backend: Literal["claude", "cursor", "pi"]) -> Agent:
    if backend == "claude":
        from .claude import ClaudeAgent

        return ClaudeAgent()
    if backend == "cursor":
        from .cursor import CursorAgent

        return CursorAgent()
    if backend == "pi":
        from .pi import PiAgent

        return PiAgent()
    raise ValueError(f"backend de agente desconhecido: '{backend}'")


__all__ = [
    "Agent",
    "TokenUsage",
    "create_agent",
]
