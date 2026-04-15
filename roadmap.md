# Roadmap — c-harness

## Prioridade 1 — Bug de segurança

### `git add -A` → `git add -u`
- **Arquivo:** `src/c_harness/states.py` — `state_human_gate_commit`
- **Problema:** `git add -A` stageará `.env`, credenciais, qualquer arquivo não rastreado
- **Fix:** trocar por `git add -u` (só arquivos já rastreados) ou exibir exatamente o que será staged antes de confirmar

---

## Prioridade 2 — Specs como entidades de primeira classe + CLI de subcomandos

Hoje specs são artefatos de run — cada run copia uma spec para `runs/<ts>/spec.json`.
Isso precisa mudar: **a spec é a fonte de verdade, a run referencia a spec por ID.**

### Mudança de modelo de dados

```
.harness/
├── specs/           # spec-0001.json, spec-0002.json ...
├── runs/            # artefatos de execução (referencia spec_id, não duplica)
├── memory/          # padrões de erro, sugestões do estado improve
└── config.yml       # configuração por projeto
```

`runs/<id>/run-log.md` passa a ter `spec_id: spec-0001` em vez de uma cópia do JSON.
Uma spec pode ser usada em múltiplas runs (re-run após falha, re-spec sem reimplementar).

### CLI de subcomandos

Hoje: `c-harness "<texto>"` ou `c-harness <arquivo.json>`

Com subcomandos:
```
c-harness spec new "<texto>"       # gera spec-NNNN via agente, salva em .harness/specs/
c-harness spec new --template      # abre template editável no editor
c-harness spec list                # lista specs com status (draft / active / done)
c-harness spec edit spec-0001      # edita spec existente via agente ou editor
c-harness spec show spec-0001      # exibe spec formatada no terminal

c-harness run spec-0001            # executa a pipeline com spec já existente
c-harness run "<texto>"            # texto livre → gera spec e já roda
c-harness run --resume <run-id>    # retoma run pausada

c-harness list                     # histórico de runs com veredicto
c-harness show <run-id>            # exibe run-log de uma run
```

**Impacto:** o entrypoint `main()` precisa de roteamento por subcomando (antes da refatoração atual de flags `--`).

### Template de spec

```json
{
  "id": "spec-0001",
  "title": "",
  "summary": "",
  "dod": [],
  "out_of_scope": [],
  "notes": null,
  "created": "2026-04-15",
  "status": "draft"
}
```

Status: `draft` → `active` (quando a run começa) → `done` (quando run aprova).

---

## Prioridade 3 — Sensores determinísticos

Avaliação hoje é 100% LLM. Falta a camada determinística descrita na visão original.

Novo estado `sensors` entre `implementation` e `evaluation`:
- Roda `ruff check` (linter)
- Roda `pytest` se houver testes
- Comandos configuráveis via `.harness/config.yml`
- Se falhar → volta para `implementation` antes do LLM avaliar

```
implementation → sensors → evaluation
                    ↓ (falha)
               implementation
```

---

## Prioridade 4 — Config por projeto

Hoje hardcoded: `MAX_RETRIES`, tools por estado, critérios globais de avaliação.

`.harness/config.yml`:
```yaml
max_retries: 2

sensors:
  lint: "ruff check ."
  test: "pytest"          # omitir para desabilitar

allowed_tools:
  implementation: [Read, Write, Edit, Bash, Glob, Grep]
  evaluation: [Read, Glob, Grep]

eval_criteria:
  - "Sem TODOs ou placeholders"
  - "Funções com responsabilidade única"
  - "Sem código morto ou imports não utilizados"
```

`c-harness init` cria o `.harness/config.yml` com defaults ao inicializar um projeto.

---

## Prioridade 5 — Estado `improve`

Estava na visão original, nunca construído.

Após `log` aprovado: agente lê `run-log.md` + artefatos e gera sugestões para próximas runs.
Output vai para `.harness/memory/improve-<run-id>.md`, não para a raiz do projeto.

Conteúdo esperado:
- Onde os prompts falharam e como melhorar
- Critérios do DoD que foram difíceis de atender (padrões de rejeição)
- Sugestões de ajuste no `config.yml` (ex: adicionar critério de avaliação que faltou)

---

## Prioridade 6 — Human gates interativos

Hoje os gates são `console.input()` simples — sem contexto visual, sem opções além de s/N.

### O que melhorar por gate

**`human_gate_spec`** — exibe a spec, mas sem comparação com a anterior se foi editada. Adicionar:
- Diff visual quando vem de `spec_edit` (o que mudou em relação à versão anterior)
- Opção `v` para ver spec completa em pager antes de decidir

**`human_gate_commit`** — mostra `git diff --stat` mas não o diff completo. Adicionar:
- Opção `d` para expandir diff completo por arquivo
- Opção `e` para abrir editor antes de commitar (ajustar mensagem de commit)
- Listar arquivos novos com tamanho e tipo, não só nome

**`human_gate_eval`** — só mostra motivo de rejeição após max retries. Adicionar:
- Exibir histórico das tentativas anteriores (o que mudou entre cada uma)
- Opção `r` para reiniciar do zero com nova spec em vez de forçar aprovação

### Padrão de interação dos gates

```
[gate] <nome>
  ─────────────────────────────────────
  <contexto visual relevante>
  ─────────────────────────────────────
  [s] aprovar   [e] editar   [d] detalhes   [N] cancelar
```

Tecla única sem Enter (usando `readchar` ou similar) — sem necessidade de pressionar Enter para resposta simples.

---

## Prioridade 7 — Skills e subagentes

Hoje cada estado chama um agente monolítico com um system prompt fixo. Dois níveis de melhoria:

### Skills (prompts reutilizáveis)

`.harness/skills/` — arquivos `.md` injetáveis no system prompt de qualquer estado:

```
.harness/skills/
├── python-conventions.md   # padrões de código Python do projeto
├── architecture.md         # decisões arquiteturais relevantes
└── testing.md              # como testar neste projeto
```

Referenciados no `config.yml`:
```yaml
states:
  implementation:
    skills: [python-conventions, architecture]
  evaluation:
    skills: [python-conventions, testing]
```

O agente de implementação recebe as skills concatenadas no system prompt — sem precisar ler arquivos manualmente.

### Subagentes especializados

A visão original previa builder separado de validator. Extensão natural:

```yaml
# .harness/config.yml
agents:
  spec:      { backend: claude, model: haiku }   # spec não precisa de modelo pesado
  impl:      { backend: claude, model: sonnet }
  eval:      { backend: claude, model: sonnet }
  improve:   { backend: claude, model: haiku }
```

Permite usar modelos menores (mais baratos) em estados que não precisam de raciocínio pesado — spec e improve são candidatos claros.

---

## Prioridade 8 — Rules e leitura de CLAUDE.md

### CLAUDE.md (já parcialmente funciona)

O Claude Code lê automaticamente o `CLAUDE.md` do projeto ao ser invocado como subprocesso — isso já acontece para o backend `claude`. Problema: outros backends (pi, cursor) não fazem isso, e o harness não tem controle explícito sobre *o que* do CLAUDE.md é relevante para cada estado.

Melhoria: o harness lê o `CLAUDE.md` do projeto e injeta explicitamente no system prompt de estados configurados:
```yaml
# .harness/config.yml
context:
  inject_claude_md: true          # default true para backend claude (já acontece)
  inject_claude_md_states: [implementation, evaluation]
```

### Rules por projeto

Complementam o DoD — são invariantes que o agente deve respeitar em qualquer implementação:

```yaml
# .harness/config.yml
rules:
  - "Nunca usar print() — sempre usar logger"
  - "Toda função pública precisa de docstring Google style"
  - "Testes ficam em tests/, não junto com o código"
```

As rules são injetadas no system prompt de `implementation` e usadas como critérios extras em `evaluation` (além dos critérios globais hardcoded).

---

## Prioridade 9 — Trajectory logging (observabilidade da run)

Hoje quando uma run falha, você tem `eval.json` com o veredicto mas não tem como debugar *por quê* o agente de implementação saiu do rumo. Faltam os rastros intermediários.

### O que capturar

`.harness/runs/<run-id>/trace.jsonl` — um evento por linha:
```json
{"ts": "...", "state": "implementation", "event": "prompt_sent", "tokens_in": 1240, "preview": "..."}
{"ts": "...", "state": "implementation", "event": "tool_call", "tool": "Edit", "file": "src/..."}
{"ts": "...", "state": "implementation", "event": "tool_result", "ok": true}
{"ts": "...", "state": "implementation", "event": "response", "tokens_out": 850}
```

O output `stream-json` do Claude Code já fornece boa parte desses eventos — basta persistir em vez de descartar.

### Comandos para consumir

```
c-harness trace <run-id>              # timeline visual da run
c-harness trace <run-id> --state impl # só eventos de um estado
c-harness trace <run-id> --tokens     # breakdown de tokens por estado
```

**Padrão de 2026:** "Capture every prompt, tool call, and internal thought. Without full visibility, debugging a failed agent test is nearly impossible." (QubitTool, 2026)

---

## Prioridade 10 — Execution guardrails

Proteção contra runs que saem do controle — agente que entra em loop, que queima tokens sem progredir, que trava.

### Guardrails por estado (via `config.yml`)

```yaml
guardrails:
  max_tokens_per_call: 50000     # termina a chamada se estourar
  max_duration_seconds: 600      # timeout por chamada do agente
  max_tool_calls_per_state: 50   # previne loop infinito de Read/Grep
  max_total_cost_usd: 2.00       # abort se a run passar disso

  per_state:
    implementation:
      max_duration_seconds: 900  # impl pode demorar mais
    spec_generation:
      max_tokens_per_call: 5000  # spec não precisa de muito
```

### Comportamento ao disparar

- Logar no `trace.jsonl` o motivo do abort
- Marcar run como `aborted` no log
- Sair com código != 0 para integração com CI/scripts

**Padrão de 2026:** "Hardcode maximum reasoning steps to prevent runaway costs. If the agent exceeds this threshold without producing a final answer, the harness should forcibly terminate the execution." (QubitTool, 2026)

---

## Prioridade 11 — Otimização de tokens

### Problema atual

Cada estado passa contexto mais amplo do que precisa:
- `state_implementation` injeta o git log completo mesmo que só o branch importe
- `state_evaluation` usa `allowed_tools=[Read, Glob, Grep]` mas o agente lê arquivos inteiros que podia ler parcialmente
- Prompts repetem instrução de formato JSON em todo estado que gera JSON

### Estratégias

**Contexto mínimo por estado:**
```
git_check       → só branch + is_clean (sem log)
spec_generation → só task_text (sem git)
implementation  → spec + rejection_reason (sem git log completo se não relevante)
evaluation      → spec + impl-summary (sem re-ler tudo do projeto)
```

**Prompts concisos:**
- Separar "instrução" de "formato de saída" — instrução no system prompt, formato como exemplo mínimo
- Evitar repetição de regras que já estão no CLAUDE.md injetado

**Model routing** (integra com prioridade 7):
- Estados que só geram JSON estruturado (spec, improve) → modelo menor
- Estados que escrevem código (impl, eval) → modelo completo

**Métricas de consumo:**
- Logar tokens usados por estado no `run-log.md` (disponível via `stream-json` output)
- `c-harness list` mostra custo estimado por run

---

## Prioridade 12 — Fixes menores

| Item | Arquivo | Fix |
|------|---------|-----|
| `impl-summary.md` vai para raiz do projeto | `states.py` | mover para `run_dir` |
| `ctx.spec_edit_feedback` é atributo dinâmico (`type: ignore`) | `runner.py` | adicionar campo no dataclass `Context` |
| Resume JSON não é gerado automaticamente | `states.py` — `state_log` | emitir `resume.json` ao pausar/reprovar |

---

# Enterprise Readiness (Futuro)

> **Nota:** os itens abaixo são baixa prioridade para uso pessoal — entram em cena quando o harness for usado por um time ou em ambiente corporativo. São mapeados aqui como referência para aplicação futura. **Não implementar antes de fechar as prioridades 1–12.**

Princípio 80/20: os 4 itens abaixo cobrem ~80% do que um harness precisa para rodar em ambiente enterprise. Os demais (chaos engineering, SLO monitoring, multi-tenant) são incrementais sobre essa base.

## E1 — Audit log imutável

Registro append-only de toda execução — quem rodou o quê, quando, com qual spec, em qual branch, com qual veredicto.

- `.harness/audit.jsonl` — linha por run, nunca editada, só adicionada
- Campos mínimos: `run_id`, `user`, `ts_start`, `ts_end`, `spec_id`, `branch`, `commit_sha`, `verdict`, `tokens_total`, `cost_usd`
- Permite compliance (SOC2, ISO 27001) e investigação post-mortem

**Por quê 80/20:** é a base para qualquer outra feature de enterprise (observabilidade, billing, governança).

## E2 — Policy gates (pre-run validation)

Bloqueios duros configuráveis antes da run iniciar, além do `git_check`.

```yaml
# .harness/policy.yml
pre_run:
  require_branch_pattern: "^(feat|fix|chore)/.+"
  forbidden_files: [".env", "secrets/*", "*.pem"]
  require_ci_passing: true          # consulta status do CI na branch
  require_reviewer_approval: false  # para runs em main

post_run:
  forbidden_diff_patterns:
    - "password\\s*="              # regex — bloqueia se detectar no diff
    - "api_key\\s*="
  max_files_changed: 20
```

**Por quê 80/20:** políticas programáveis substituem a confiança implícita no desenvolvedor — necessário em time com mix de seniors/juniors.

## E3 — Secrets management

Hoje o harness usa variáveis de ambiente diretas (`ANTHROPIC_API_KEY`). Em enterprise:

- Integração com cofres: 1Password CLI, HashiCorp Vault, AWS Secrets Manager
- Nunca persistir chaves em arquivo — só resolver via referência no `config.yml`:
  ```yaml
  secrets:
    anthropic_key: "op://vault/claude/api-key"    # 1Password
    # ou: "vault://secret/data/anthropic#api_key"  # Vault
  ```
- Logs nunca mostram valores resolvidos — só a referência

**Por quê 80/20:** chave vazada é o risco mais comum e de maior impacto. Resolve antes de qualquer outra coisa de segurança.

## E4 — Telemetria agregada

Exportar métricas para observabilidade centralizada — não só `run-log.md` local.

- Exporters para OpenTelemetry (traces), Prometheus (métricas), ou webhook customizado
- Métricas-chave: runs por dia, taxa de aprovação, tokens/custo por projeto, tempo médio por estado, taxa de retry
- Permite ver drift de qualidade ao longo do tempo (ex: "a taxa de rejeição do evaluator subiu 30% na última semana")

```yaml
# .harness/config.yml
telemetry:
  exporter: otlp
  endpoint: "https://otel.company.internal:4317"
  tags:
    team: platform
    environment: prod
```

**Por quê 80/20:** sem telemetria agregada não existe melhoria contínua em escala — cada desenvolvedor otimiza isoladamente sem ver o padrão do time.

---

## Além do 80/20 (20% que dá 20%)

Mapeados para completude, mas valor marginal:

- **Chaos engineering** — injeção de falhas (500s, JSON malformado) para testar recuperação
- **SLO monitoring** — alertas automáticos de degradação
- **Multi-tenant isolation** — um harness servindo múltiplos projetos/times com quotas separadas
- **Role-based permissions** — quem pode rodar, quem pode aprovar, quem pode ver traces
- **Compliance export** — relatórios automatizados para auditoria (SOC2, HIPAA)

Só entrar nisso se o harness estiver sendo usado por >10 desenvolvedores ou em contexto regulado (financeiro, saúde).

---

# Sources

Pesquisa de 2026 que embasou este roadmap:

- [Martin Fowler — Harness engineering for coding agent users](https://martinfowler.com/articles/harness-engineering.html)
- [Anthropic — Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic — Scaling Managed Agents](https://www.anthropic.com/engineering/managed-agents)
- [QubitTool — Agent Harness Engineering Guide 2026](https://qubittool.com/blog/agent-harness-evaluation-guide)
- [n1n.ai — The Anatomy of an Agent Harness (2026)](https://explore.n1n.ai/blog/anatomy-of-an-agent-harness-ai-systems-2026-03-11)
- [Kong — Governing Claude Code: Secure Agent Harness Rollouts](https://konghq.com/blog/engineering/claude-code-governance-with-an-ai-gateway)
- [LangChain — AI Agent Observability: Tracing, Testing, and Improving Agents](https://www.langchain.com/articles/agent-observability)
- [HumanLayer — Skill Issue: Harness Engineering for Coding Agents](https://www.humanlayer.dev/blog/skill-issue-harness-engineering-for-coding-agents)
