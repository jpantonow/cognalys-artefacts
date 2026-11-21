# Estatísticas — run_ab56

Findings: 2. Execuções lidas: run_v9, run_ab56.

## 1. Visão geral

- **Findings:** 2 em **1 apps** (2 especificações).
- **Findings por app:** mínimo 2, mediana 2, máximo 2.
- **Label do monitor:** platform-default 1 (50.0%), application-manager 1 (50.0%)

### Findings por especificação

| especificação | n | % |
|---|---|---|
| TrustManagerFactorySpec | 1 | 50.0% |
| SSLContextSpec | 1 | 50.0% |

## 2. Desfecho de cada finding

Cada finding aparece em exatamente uma linha: a etapa mais avançada que alcançou ou o motivo de ter parado.

| desfecho | n | % | IC 95% (Wilson) |
|---|---|---|---|
| violação sem impacto de segurança | 2 | 100.0% | 34.2%–100.0% |

### Detalhe dos desfechos

| desfecho | detalhe | n |
|---|---|---|
| violação sem impacto de segurança | `TrustManagerFactory.init(KeyStore)` documents that a null KeyStore is permitted and selects the default trust material (WEB-001). The source deliberately passes `null as KeyStore?` to use that documented platform default (TLS-CLAIM-002, TLS-CLAIM-003). An explicit keystore is not an equivalent replacement for the platform-default trust-store selection. | 1 |
| violação sem impacto de segurança | SSLContext.init is documented to accept a caller-supplied TrustManager[] and use it as the TLS trust policy. The original call supplies the deliberately selected DefaultTrustManager or PinningTrustManager. In particular, PinningTrustManager is the application’s documented-by-code mechanism for accepting its configured certificate while enforcing its pin and hostname checks. Replacing it with the platform-default TrustManagerFactory changes that security-relevant acceptance behavior rather than repairing misuse of SSLContext.init. | 1 |

### Desfecho × label do monitor

| desfecho | application-manager | platform-default | total |
|---|---|---|---|
| violação sem impacto de segurança | 1 | 1 | 2 |
| **total** | 1 | 1 | 2 |

### Desfecho × especificação

| desfecho | SSLContextSpec | TrustManagerFactorySpec | total |
|---|---|---|---|
| violação sem impacto de segurança | 1 | 1 | 2 |
| **total** | 1 | 1 | 2 |

## 3. Funil

### Funil geral

| etapa | n | % dos importados | IC 95% (Wilson) |
|---|---|---|---|
| importado | 2 | 100.0% | 34.2%–100.0% |
| analisado pelo LLM | 2 | 100.0% | 34.2%–100.0% |
| violação confirmada | 2 | 100.0% | 34.2%–100.0% |
| correção proposta | 1 | 50.0% | 9.5%–90.5% |
| solução gerada | 0 | 0.0% | 0.0%–65.8% |
| aplicada na worktree | 0 | 0.0% | 0.0%–65.8% |
| build ok | 0 | 0.0% | 0.0%–65.8% |
| PR pronto (commit) | 0 | 0.0% | 0.0%–65.8% |
| PR pronto (publicável) | 0 | 0.0% | 0.0%–65.8% |
| PR pronto (revisão) | 0 | 0.0% | 0.0%–65.8% |

### Disponibilidade de código (extração)

Não é etapa do funil: um finding de biblioteca pode ser analisado a partir de bytecode e dependências sem código no checkout.

| extraction_status | n |
|---|---|
| success | 2 |

### Funil por label do monitor

| etapa | application-manager (n=1) | platform-default (n=1) |
|---|---|---|
| importado | 1 (100.0%) | 1 (100.0%) |
| analisado pelo LLM | 1 (100.0%) | 1 (100.0%) |
| violação confirmada | 1 (100.0%) | 1 (100.0%) |
| correção proposta | 1 (100.0%) | 0 (0.0%) |
| solução gerada | 0 (0.0%) | 0 (0.0%) |
| aplicada na worktree | 0 (0.0%) | 0 (0.0%) |
| build ok | 0 (0.0%) | 0 (0.0%) |
| PR pronto (commit) | 0 (0.0%) | 0 (0.0%) |
| PR pronto (publicável) | 0 (0.0%) | 0 (0.0%) |
| PR pronto (revisão) | 0 (0.0%) | 0 (0.0%) |

### Funil por especificação

| etapa | SSLContextSpec (n=1) | TrustManagerFactorySpec (n=1) |
|---|---|---|
| importado | 1 (100.0%) | 1 (100.0%) |
| analisado pelo LLM | 1 (100.0%) | 1 (100.0%) |
| violação confirmada | 1 (100.0%) | 1 (100.0%) |
| correção proposta | 1 (100.0%) | 0 (0.0%) |
| solução gerada | 0 (0.0%) | 0 (0.0%) |
| aplicada na worktree | 0 (0.0%) | 0 (0.0%) |
| build ok | 0 (0.0%) | 0 (0.0%) |
| PR pronto (commit) | 0 (0.0%) | 0 (0.0%) |
| PR pronto (publicável) | 0 (0.0%) | 0 (0.0%) |
| PR pronto (revisão) | 0 (0.0%) | 0 (0.0%) |

## 4. Decisões do LLM

### Decisão do LLM × violação do detector

`detector_violation`: *confirmed* = o relato se confirma no código; *not_confirmed* = falso positivo; *unknown* = a evidência não decide.

| decisão | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| NO_SAFE_FIX | 1 | 0 | 0 | 1 |
| MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | 1 | 0 | 0 | 1 |
| **total** | 2 | 0 | 0 | 2 |

### Impacto de segurança em tempo de execução (só analisados)

| runtime_security_impact | n |
|---|---|
| non_security | 2 |

### O que o LLM tinha para ler × análise

| evidência | analisado | não analisado | total |
|---|---|---|---|
| código-fonte | 2 | 0 | 2 |
| **total** | 2 | 0 | 2 |

### Evidência × violação (só analisados)

| evidência | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| código-fonte | 2 | 0 | 0 | 2 |
| **total** | 2 | 0 | 0 | 2 |

### Especificação × violação (só analisados)

| especificação | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| SSLContextSpec | 1 | 0 | 0 | 1 |
| TrustManagerFactorySpec | 1 | 0 | 0 | 1 |
| **total** | 2 | 0 | 0 | 2 |

## 5. Correções, PRs e issues

Nenhuma correção gerada.

## 6. Apps

≥1 confirmado: 1 de 1 apps, ≥1 correção proposta: 1 de 1 apps

| app | findings | do app | analisados | confirmados | correção proposta | PR pronto | issues | categoria | MB | instalações |
|---|---|---|---|---|---|---|---|---|---|---|
| org.cryptomator.lite.debug_3157 | 2 | 2 | 2 | 2 | 1 | 0 | 0 |  |  |  |

## 7. Decisão do pipeline × rótulos

### detector_violation × label do monitor (só findings analisados)

| label do monitor | n | confirmed | not_confirmed | unknown |
|---|---|---|---|---|
| application-manager | 1 | 1 | 0 | 0 |
| platform-default | 1 | 1 | 0 | 0 |

### detector_violation × especificação (só findings analisados)

| especificação | n | confirmed | not_confirmed | unknown |
|---|---|---|---|---|
| SSLContextSpec | 1 | 1 | 0 | 0 |
| TrustManagerFactorySpec | 1 | 1 | 0 | 0 |

*Concordância com o veredito humano: forneça --annotations (colunas apk, class, method, spec, verdict com R/W/F/E).*

## 8. Estabilidade

### Estabilidade entre execuções

| par de execuções | analisados em ambas | mesma decisão | mesmo detector_violation | κ (detector_violation) |
|---|---|---|---|---|
| run_v9 × run_ab56 | 2 | 2 (100.0%) | 2 (100.0%) | – |

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
| run_ab56 | 2 | 17 | 0 | 0.2 | 0.3 | 0.0 | 0.3 |
