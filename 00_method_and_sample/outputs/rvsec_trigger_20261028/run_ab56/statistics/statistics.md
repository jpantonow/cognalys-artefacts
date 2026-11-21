# Estatísticas — run_ab56

Findings: 1. Execuções lidas: run_v6, run_ab56.

## 1. Visão geral

- **Findings:** 1 em **1 apps** (1 especificações).
- **Findings por app:** mínimo 1, mediana 1, máximo 1.

### Findings por especificação

| especificação | n | % |
|---|---|---|
| KeyPairGeneratorSpec | 1 | 100.0% |

## 2. Desfecho de cada finding

Cada finding aparece em exatamente uma linha: a etapa mais avançada que alcançou ou o motivo de ter parado.

| desfecho | n | % | IC 95% (Wilson) |
|---|---|---|---|
| violação sem impacto de segurança | 1 | 100.0% | 20.7%–100.0% |

### Detalhe dos desfechos

| desfecho | detalhe | n |
|---|---|---|
| violação sem impacto de segurança | The reported factory use requests "ED25519", which is a documented Java KeyPairGenerator standard algorithm name (WEB-002, WEB-009). KeyPairGenerator is expressly provider-based and its factory/initialization behavior is documented by the JCA API (WEB-001). The CrySL allowlist excludes this documented algorithm, but replacing it with RSA, EC, DSA, DH, or DiffieHellman would change the selected SSH identity algorithm, its encoded key material, and the application's advertised ED25519 support; it would not preserve what this code generates and later supplies for SSH authentication (SRC-001, SRC-008, SRC-009, SRC-010, CALLEE-003). | 1 |

## 3. Funil

### Funil geral

| etapa | n | % dos importados | IC 95% (Wilson) |
|---|---|---|---|
| importado | 1 | 100.0% | 20.7%–100.0% |
| analisado pelo LLM | 1 | 100.0% | 20.7%–100.0% |
| violação confirmada | 1 | 100.0% | 20.7%–100.0% |
| correção proposta | 1 | 100.0% | 20.7%–100.0% |
| solução gerada | 0 | 0.0% | 0.0%–79.3% |
| aplicada na worktree | 0 | 0.0% | 0.0%–79.3% |
| build ok | 0 | 0.0% | 0.0%–79.3% |
| PR pronto (commit) | 0 | 0.0% | 0.0%–79.3% |
| PR pronto (publicável) | 0 | 0.0% | 0.0%–79.3% |
| PR pronto (revisão) | 0 | 0.0% | 0.0%–79.3% |

### Disponibilidade de código (extração)

Não é etapa do funil: um finding de biblioteca pode ser analisado a partir de bytecode e dependências sem código no checkout.

| extraction_status | n |
|---|---|
| success | 1 |

## 4. Decisões do LLM

### Decisão do LLM × violação do detector

`detector_violation`: *confirmed* = o relato se confirma no código; *not_confirmed* = falso positivo; *unknown* = a evidência não decide.

| decisão | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | 1 | 0 | 0 | 1 |
| **total** | 1 | 0 | 0 | 1 |

### Impacto de segurança em tempo de execução (só analisados)

| runtime_security_impact | n |
|---|---|
| non_security | 1 |

### O que o LLM tinha para ler × análise

| evidência | analisado | não analisado | total |
|---|---|---|---|
| código-fonte | 1 | 0 | 1 |
| **total** | 1 | 0 | 1 |

### Evidência × violação (só analisados)

| evidência | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| código-fonte | 1 | 0 | 0 | 1 |
| **total** | 1 | 0 | 0 | 1 |

### Especificação × violação (só analisados)

| especificação | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| KeyPairGeneratorSpec | 1 | 0 | 0 | 1 |
| **total** | 1 | 0 | 0 | 1 |

## 5. Correções, PRs e issues

Nenhuma correção gerada.

## 6. Apps

≥1 confirmado: 1 de 1 apps, ≥1 correção proposta: 1 de 1 apps

| app | findings | do app | analisados | confirmados | correção proposta | PR pronto | issues | categoria | MB | instalações |
|---|---|---|---|---|---|---|---|---|---|---|
| com.example.trigger.debug_408 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |  |  |  |

## 7. Decisão do pipeline × rótulos

*Concordância com o veredito humano: forneça --annotations (colunas apk, class, method, spec, verdict com R/W/F/E).*

## 8. Estabilidade

### Estabilidade entre execuções

| par de execuções | analisados em ambas | mesma decisão | mesmo detector_violation | κ (detector_violation) |
|---|---|---|---|---|
| run_v6 × run_ab56 | 1 | 1 (100.0%) | 1 (100.0%) | – |

Atenção: se o código do pipeline mudou entre as execuções, a diferença mistura o efeito das mudanças com a variação do LLM. Para medir só a variação, compare execuções com o código congelado.

## 9. Builds

### Builds de validação

| causa | n |
|---|---|

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|

## 10. Custo

| execução | findings | chamadas LLM | falhas | h de modelo | h etapa llm | h validação | h total |
|---|---|---|---|---|---|---|---|
| run_ab56 | 1 | 8 | 0 | 0.1 | 0.1 | 0.0 | 0.1 |
