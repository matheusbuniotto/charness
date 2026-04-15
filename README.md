# c-harness

CLI para desenvolvimento assistido por agentes — orquestra Claude Code, Cursor ou PI em uma máquina de estados com gates e validação.

## O que é

`c-harness` é um **tiny harness** para agentes de código. Recebe uma task em texto livre ou spec estruturada, roda uma pipeline de estados com contexto isolado por agente, e entrega implementação avaliada contra DoD + critérios globais.

Cada estado é um subprocesso com contexto limpo; transições são explícitas e podem ter loop-back quando a avaliação reprova.

## Pipeline

```
git_check → spec_generation → human_gate_spec → implementation
                                                      ↓
                                              human_gate_commit
                                                      ↓
                                                 evaluation ──┐
                                                      ↓       │ reprovado (até N retries)
                                                     log ←────┘
```

Estados materializam artefatos em `.harness/runs/<timestamp>/`:
- `spec.json` — spec gerada/editada
- `eval.json` — veredicto da avaliação
- `run-log.md` — log consolidado
- `impl-summary.md` — resumo do agente de implementação

## Instalação

```bash
uv pip install -e .
```

Requer `ANTHROPIC_API_KEY` (ou outro backend configurado).

## Uso

**Texto livre** — spec é gerada por agente:
```bash
c-harness "adicionar endpoint /health que retorna 200 ok"
```

**Spec local** — pula a geração:
```bash
c-harness caminho/para/spec.json
```

**Retomar run pausada:**
```bash
c-harness caminho/para/resume.json
```

**Escolher backend:**
```bash
c-harness --agent claude "..."   # default
c-harness --agent cursor "..."
c-harness --agent pi "..."
```

## Estados atuais

| Estado | Função |
|--------|--------|
| `git_check` | Valida working tree limpa antes de iniciar |
| `spec_generation` | Texto livre → spec estruturada (JSON) |
| `spec_edit` | Edita spec com feedback do usuário |
| `human_gate_spec` | Aprova/edita/cancela spec antes da implementação |
| `implementation` | Executa a task no projeto |
| `human_gate_commit` | Revisa diff e decide se commita antes de avaliar |
| `evaluation` | Verifica DoD + critérios globais; loop de retry se reprovar |
| `human_gate_eval` | Escalada humana após max retries |
| `log` | Consolida artefatos em `run-log.md` |

## Documentação

- [`phase1-exploration.md`](phase1-exploration.md) — Exploração do problema (Type I)
- [`phase2-design.md`](phase2-design.md) — Design arquitetural (Type II)
- [`roadmap.md`](roadmap.md) — Backlog priorizado, referências de 2026 e enterprise readiness

## Status

Projeto em desenvolvimento ativo. MVP funcional, roadmap em `roadmap.md`.
