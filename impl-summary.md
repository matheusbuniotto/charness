# impl-summary — Refatorar __init__.py em módulos separados

## O que foi feito

O arquivo `__init__.py` monolítico (736 linhas) foi quebrado em três módulos focados, mantendo a interface pública intacta.

## Arquivos criados

- `src/c_harness/git.py` — operações git: `GitContext`, `_run_git`, `collect_git_context`
- `src/c_harness/runner.py` — orquestração principal: `Context`, `Transition`, `StateFn`, `console`, `MAX_RETRIES`, `_extract_json`, `_format_tool_event`, `run_claude`, `run_pipeline`, `main`
- `src/c_harness/states.py` — estados e prompts: todos os `state_*`, `_print_eval_results`, `STATES`, constantes de prompt (`SPEC_PROMPT`, `SPEC_EDIT_PROMPT`, `IMPL_PROMPT`, `EVAL_PROMPT`)

## Arquivos modificados

- `src/c_harness/__init__.py` — reduzido a re-exports da interface pública via `__all__`

## Decisões tomadas

1. **Importação lazy de `states` em `runner.py`**: `run_pipeline` importa `STATES` dentro do corpo da função (`from .states import STATES`) para evitar importação circular. `states.py` importa de `runner.py`, então importar `states` no topo de `runner.py` criaria ciclo.

2. **`_run_git` fica em `git.py`**: A função é usada em `state_human_gate_commit` (estados), mas pertence semanticamente ao módulo git. `states.py` a importa diretamente de `.git`.

3. **`console` definido em `runner.py`**: É o ponto central de output — vive onde vive a orquestração. `states.py` o importa de lá.

4. **`datetime` importado localmente em `state_log`**: Já estava sendo importado no topo de `runner.py`; em `states.py` foi adicionado como import local dentro da função para não duplicar o import no topo do módulo sem necessidade.

5. **Nenhuma abstração nova introduzida**: só movimentação de código, sem criar classes, protocolos ou camadas novas.
