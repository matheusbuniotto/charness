# Implantação: Desacoplar providers de agente do runner.py

## Resumo
Refatoração da arquitetura do c-harness para isolar as implementações específicas de cada agente (Claude e Pi) em módulos separados, criando uma interface comum. O runner.py agora apenas orquestra, sem conhecer detalhes de implementação de cada CLI.

## Arquivos Criados

### `src/c_harness/agents/__init__.py`
- Define o protocolo/interface base `Agent` usando `typing.Protocol`
- Implementa a factory function `create_agent()` que seleciona o agente baseado no backend
- Exporta os tipos e funções públicos do módulo

### `src/c_harness/agents/claude.py`
- Contém a implementação `ClaudeAgent` da interface `Agent`
- Move a lógica de streaming JSON da CLI claude do runner.py
- Inclui função auxiliar `_format_tool_event()` para exibição de progresso

### `src/c_harness/agents/pi.py`
- Contém a implementação `PiAgent` da interface `Agent`
- Move a lógica de execução da CLI pi do runner.py
- Inclui mapeamento de nomes de ferramentas do claude para pi

## Arquivos Modificados

### `src/c_harness/runner.py`
- **Removido**: Variável global `AGENT_BACKEND`
- **Removido**: Funções `_run_claude_streaming()` e `_run_pi_streaming()`
- **Adicionado**: Variável privada `_agent_instance` para armazenar a instância do agente
- **Adicionado**: Função `configure_agent()` para configurar o agente global
- **Adicionado**: Função `get_agent_backend()` para obter o nome do backend atual
- **Modificado**: `run_agent()` agora delega para a instância do agente configurada
- **Modificado**: `main()` usa `configure_agent()` e `get_agent_backend()` ao invés da variável global

### `src/c_harness/__init__.py`
- **Removido**: Export de `AGENT_BACKEND`
- **Adicionado**: Export de `Agent`, `ClaudeAgent`, `PiAgent`, `create_agent` do módulo agents
- **Adicionado**: Export de `configure_agent` e `get_agent_backend` do runner

## Decisões Tomadas

1. **Uso de Protocol vs ABC**: Optei por `typing.Protocol` ao invés de ABC porque:
   - É mais leve e não requer herança explícita
   - Permite duck typing - qualquer classe com o método `run` compatível funciona
   - É o padrão moderno para interfaces em Python (structural subtyping)

2. **Instância global privada**: Mantive o padrão de ter uma instância global (`_agent_instance`) mas encapsulada através de funções, mantendo a compatibilidade com o código existente em `states.py` que chama `run_agent()` sem passar a instância.

3. **Método `name` na interface**: Adicionei o atributo `name` à interface `Agent` para permitir identificar qual backend está sendo usado sem precisar de introspeção de tipo.

4. **Verificações com assertions**: No `claude.py`, usei `assert` para garantir ao type checker que `stdout`, `stdin`, `stderr` não são None quando usamos `PIPE`. Isso é mais limpo que verificações `if` em runtime já que sabemos que esses valores sempre serão definidos quando usamos `Popen` com `PIPE`.

## Princípio OCP Aplicado

A arquitetura agora segue o Princípio Aberto/Fechado (OCP):
- **Aberto para extensão**: Novos agents podem ser adicionados criando uma nova classe que implemente o protocolo `Agent` e registrando-a na factory
- **Fechado para modificação**: O `runner.py` não precisa ser alterado para adicionar novos agents - apenas o módulo `agents` precisa ser estendido

## Interface Pública Mantida

A função `run_agent()` mantém a mesma assinatura e comportamento, garantindo que todo o código existente (especialmente em `states.py`) continue funcionando sem modificações.
