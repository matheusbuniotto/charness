# Phase 1 — Exploration (Type I)

**Data:** 2026-04-14

## Definição em uma frase

> Estou construindo uma aplicação CLI para usuários desenvolvedores usando Claude Code, porque a implementação de código por agentes tende a fugir do controle em projetos maiores que têm pontos críticos e precisam de validações claras.

## Arquitetura Mental

State machine — não pipeline linear.

```
[Refinamento] ←────────────────────┐
     ↓                              │
[Handshake/Consenso]                │ critério reprovado
     ↓                              │
[Implementação]                     │
     ↓                              │
[Avaliação + Testes] ───────────────┘
     ↓ aprovado
[Log + Improver]
```

- Cada estado = agente com contexto zerado
- Transições são gates com critérios explícitos
- Fluxo configurável por projeto (não hardcoded)
- Retorno é válido e esperado

## Escopo

**Dentro:**
- CLI terminal com Claude Code por baixo
- Pipeline multi-agente com states configuráveis
- Refinamento de spec com handshake entre agentes
- Avaliação por critérios + testes determinísticos
- Log de run com reviews, agreements, harness executados
- Agente improver que melhora prompts com base nos erros da execução
- Visual e claro no terminal

**Fora:**
- Outros LLMs (só Claude)
- Interface web / dashboard
- Integração com issue trackers (Jira, Linear, GitHub Issues)
- Deploy automático após aprovação
- Multi-repo orchestration

## Parte Mais Difícil

Abstração genérica do fluxo que funcione para N tasks diferentes (ex: "adicionar OAuth" e "migrar schema") sem perder clareza de uso.

## Risco Principal

Complexidade de uso no dia a dia. Se levar mais de ~30s pra iniciar uma run, o projeto morre na prática.

## Complexidade Estimada

Na medida.

## Exit Criteria — Type I

- [x] Definição em uma frase escrita
- [x] Escopo definido (dentro e fora)
- [x] Parte mais difícil nomeada
- [x] Risco identificado
- [x] Decisão arquitetural chave tomada (state machine, não pipeline)
- [x] Próximo passo claro: Type II — Design

## Próximo Passo

Type II — Design: definir os estados, critérios de transição, formato de spec, e como o fluxo é configurado por projeto.
