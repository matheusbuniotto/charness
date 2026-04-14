# Phase 2 — Design (Type II)

**Data:** 2026-04-14

## Decisão Arquitetural Principal

**Runtime: Subprocess (A) + CLAUDE.md/flags/hooks (C)**

Harness controla o fluxo e as transições. Claude Code é o executor dentro de cada estado. Cada estado = processo novo (contexto zerado por design).

> Por que não SDK direto (B): reconstruiria o que Claude Code já faz.
> Por que não C puro: perde controle explícito das transições de estado.

---

## Abstrações Core

| Abstração | Responsabilidade |
|-----------|-----------------|
| `Task` | Unidade de trabalho — texto livre → spec materializada |
| `State` | Fase do pipeline com critérios de entrada/saída |
| `Run` | Execução completa de uma Task pelo pipeline |
| `Harness` | Orquestrador — invoca subprocessos, controla transições |

---

## Fluxo

```
[texto livre] → [spec generation] → [refinamento] → [handshake]
     → [implementação] → [avaliação + testes] → [log]
          ↑                      |
          └──── reprovado ───────┘
```

Transições configuráveis. Retorno é válido e esperado.

---

## Estado Entre Agentes

**Combinação: arquivos materializados + resumo injetado**

- Artefatos grandes vivem no filesystem (spec, código, resultado da avaliação)
- Harness injeta no prompt apenas o resumo relevante para aquele estado
- Os arquivos materializados já são o log — agente de log consolida no final

---

## Entrada

**Texto livre** — o harness passa pelo primeiro agente que estrutura em spec markdown com frontmatter. O usuário valida antes do pipeline continuar.

---

## Critérios de Avaliação (dois níveis)

- **Global** — sempre aplicado: complexidade de código, legibilidade, sem TODOs, cobertura da spec
- **Task-level (DoD)** — definido pelo usuário junto com o texto livre da task

Agente avaliador checa os dois. Falha em qualquer um → gate de retorno ativa.

---

## Human Gates

Configuráveis por run ou por estado. Quando ativo, harness pausa e aguarda aprovação explícita antes de transitar.

---

## Backlog (deferido)

- Agente improver (melhoria de prompts baseada em erros da execução)
- Configuração de fluxo por projeto (estados customizáveis)
- Critérios globais configuráveis (além dos defaults)
- Multi-task / sprint com várias tasks em sequência

---

## MVP — Primeiro Artefato

**Run com 2 estados** — spec generation + stub de implementação.

Objetivo: validar que subprocess + passagem de estado via arquivo funciona antes de construir os demais estados.

---

## Exit Criteria — Type II

- [x] Decisão arquitetural tomada e documentada
- [x] Tradeoffs documentados
- [x] Abstrações principais nomeadas
- [x] Formato de entrada definido
- [x] Estado entre agentes definido
- [x] Critérios de avaliação definidos
- [x] Human gates incluídos
- [x] MVP claro
- [x] Backlog explícito

## Próximo Passo

Type III — Build: implementar o MVP (2 estados) e validar a arquitetura na prática.
