# Estatísticas — subset_revisao_lacunas_v4

Findings: 1. Execuções lidas: subset_revisao_lacunas_v4.

## 1. Visão geral

- **Findings:** 1 em **1 apps** (1 especificações).
- **Findings por app:** mínimo 1, mediana 1, máximo 1.

### Findings por especificação

| especificação | n | % |
|---|---|---|
| CipherSpec | 1 | 100.0% |

## 2. Desfecho de cada finding

Cada finding aparece em exatamente uma linha: a etapa mais avançada que alcançou ou o motivo de ter parado.

| desfecho | n | % | IC 95% (Wilson) |
|---|---|---|---|
| violação sem impacto de segurança | 1 | 100.0% | 20.7%–100.0% |

### Detalhe dos desfechos

| desfecho | detalhe | n |
|---|---|---|
| violação sem impacto de segurança | The original AES-GCM encryption initialization is documented Cipher behavior: when encrypting and required parameters are omitted, the provider generates them, and the code persists cipher.getParameters() for decryption. This is also the behavior required by the established AndroidKeyStore key configured for randomized encryption, which rejects caller-supplied GCM parameters. Thus the original provider-managed nonce path is correct API use for that lifecycle; forcing an explicit nonce does not repair a demonstrated security-relevant misuse. | 1 |

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
| not_established | 1 |

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
| CipherSpec | 1 | 0 | 0 | 1 |
| **total** | 1 | 0 | 0 | 1 |

## 5. Correções, PRs e issues

Nenhuma correção gerada.

## 6. Apps

≥1 confirmado: 1 de 1 apps, ≥1 correção proposta: 1 de 1 apps

| app | findings | do app | analisados | confirmados | correção proposta | PR pronto | issues | categoria | MB | instalações |
|---|---|---|---|---|---|---|---|---|---|---|
| org.fedorahosted.freeotp_48 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |  |  |  |

## 7. Decisão do pipeline × rótulos

*Concordância com o veredito humano: forneça --annotations (colunas apk, class, method, spec, verdict com R/W/F/E).*

## 9. Builds

### Builds de validação

| causa | n |
|---|---|

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|

## 10. Custo

| execução | findings | chamadas LLM | falhas | h de modelo | h etapa llm | h validação | h total |
|---|---|---|---|---|---|---|---|
| subset_revisao_lacunas_v4 | 1 | 28 | 0 | 0.4 | 0.4 | 0.0 | 0.4 |
