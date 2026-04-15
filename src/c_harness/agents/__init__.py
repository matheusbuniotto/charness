"""Módulo de agents - interface base e factory."""

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from .claude import ClaudeAgent
from .cursor import CursorAgent
from .pi import PiAgent


@runtime_checkable
class Agent(Protocol):
    """Interface base para agents de CLI.

    Todos os agents devem implementar este protocolo para serem
    usados pelo runner. O runner trabalha apenas com esta interface,
    sem conhecer detalhes de implementação específicos.
    """

    name: str

    def run(
        self,
        prompt: str,
        system_prompt: str,
        cwd: Path,
        label: str,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """Executa o agente com o prompt e configuração fornecidos.

        Args:
            prompt: O prompt principal a ser enviado ao agente.
            system_prompt: O system prompt/contexto do agente.
            cwd: Diretório de trabalho para execução.
            label: Label para identificar a operação no output.
            allowed_tools: Lista de ferramentas permitidas, ou None.

        Returns:
            A resposta completa do agente como string.
        """
        ...


def create_agent(backend: Literal["claude", "cursor", "pi"]) -> Agent:
    """Factory function que cria o agente apropriado baseado no backend.

    Args:
        backend: Nome do backend de agente desejado.

    Returns:
        Instância do agente configurado.

    Raises:
        ValueError: Se o backend for desconhecido.
    """
    if backend == "claude":
        return ClaudeAgent()
    if backend == "cursor":
        return CursorAgent()
    if backend == "pi":
        return PiAgent()
    raise ValueError(f"backend de agente desconhecido: '{backend}'")


__all__ = [
    "Agent",
    "ClaudeAgent",
    "CursorAgent",
    "PiAgent",
    "create_agent",
]
