# impl-summary

## O que foi feito

Corrigida a condição de saída do loop em `pi.py`. O `pi` CLI permanecia bloqueado porque
`subprocess.run()` herdava o stdin do processo pai (terminal), fazendo com que `pi` ficasse
aguardando mais entrada do usuário após entregar a resposta — nunca recebendo EOF e, portanto,
nunca encerrando.

A correção passa `stdin=subprocess.DEVNULL`, garantindo que `pi` receba EOF imediato no stdin e
encerre sozinho após completar a tarefa.

O timeout também foi ajustado de 60s para 300s para acomodar tasks de implementação mais longas,
e a mensagem de erro foi atualizada para remover a referência a "modo interativo" (que é o
comportamento corrigido, não mais uma causa provável de travamento).

## Arquivos modificados

- `src/c_harness/agents/pi.py`

## Decisões tomadas

- **`capture_output=True` → `stdout=PIPE + stderr=PIPE + stdin=DEVNULL`**: `capture_output=True`
  é açúcar sintático para `stdout=PIPE, stderr=PIPE`, mas não define stdin. Explicitar os três
  parâmetros torna a condição de saída visível no código, atendendo ao critério do DoD
  "explícita e clara".

- **Timeout de 60s → 300s**: o timeout original de 60s era explicitamente marcado com o comentário
  "para testar", sinalizando que era provisório. Tasks reais de implementação podem levar mais
  tempo; 300s é consistente com o `DEFAULT_TIMEOUT = 600` do `cursor.py`.
