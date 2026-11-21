# Estatísticas — consolidado de 13 execuções (até subset_bitbanana_scope6)

Findings: 34. Execuções lidas: run2, subset_gates, subset_policy, subset_policy2, subset_bitbanana, subset_bitbanana_readers, subset_bitbanana_readers2, subset_bitbanana_scope, subset_bitbanana_scope2, subset_bitbanana_scope3, subset_bitbanana_scope4, subset_bitbanana_scope5, subset_bitbanana_scope6.

> Cada finding vem da execução mais recente que o processou. Execuções diferentes podem ter usado versões diferentes do pipeline; para uma medição homogênea, rode todos os findings numa mesma execução.

## 1. Visão geral

- **Findings:** 34 em **23 apps** (13 especificações).
- **Findings por app:** mínimo 1, mediana 1, máximo 3.
- **Origem do código:** app 33 (97.1%), library 1 (2.9%)
- **Outcome do pacote:** primary 23 (67.6%), severe-constant 5 (14.7%), severe-trust-all 3 (8.8%), undecided 3 (8.8%)
- **Label do monitor:** violation 20 (58.8%), not-observed 8 (23.5%), sequence 3 (8.8%), application-manager 3 (8.8%)
- **Execução de onde vem cada finding:** run2 28, subset_gates 2, subset_bitbanana_scope6 1, subset_bitbanana 1, subset_policy2 1, subset_policy 1

### Findings por especificação

| especificação | n | % |
|---|---|---|
| MessageDigestSpec | 12 | 35.3% |
| SSLContextSpec | 4 | 11.8% |
| SecretKeySpecSpec | 4 | 11.8% |
| KeyStoreSpec | 3 | 8.8% |
| IvParameterSpecSpec | 3 | 8.8% |
| PBEKeySpecSpec | 1 | 2.9% |
| SecretKeyFactorySpec | 1 | 2.9% |
| MacSpec | 1 | 2.9% |
| AlgorithmParametersSpec | 1 | 2.9% |
| CipherSpec | 1 | 2.9% |
| KeyPairGeneratorSpec | 1 | 2.9% |
| SecureRandomSpec | 1 | 2.9% |
| GCMParameterSpecSpec | 1 | 2.9% |

## 2. Desfecho de cada finding

Cada finding aparece em exatamente uma linha: a etapa mais avançada que alcançou ou o motivo de ter parado.

| desfecho | n | % | IC 95% (Wilson) |
|---|---|---|---|
| PR pronto | 7 | 20.6% | 10.3%–36.8% |
| correção gerada, sem PR | 1 | 2.9% | 0.5%–14.9% |
| correção não aplicável: código mudou upstream | 3 | 8.8% | 3.0%–23.0% |
| issue para o mantenedor do app | 16 | 47.1% | 31.5%–63.3% |
| violação confirmada sem correção segura | 1 | 2.9% | 0.5%–14.9% |
| falso positivo reconhecido | 3 | 8.8% | 3.0%–23.0% |
| evidência insuficiente | 3 | 8.8% | 3.0%–23.0% |

### Detalhe dos desfechos

| desfecho | detalhe | n |
|---|---|---|
| correção não aplicável: código mudou upstream | o branch de destino já não tem a violação | 2 |
| issue para o mantenedor do app | StellarSand/Password-Monitor | 2 |
| issue para o mantenedor do app | VREMSoftwareDevelopment/WifiAnalyzer | 2 |
| issue para o mantenedor do app | chrismiceli/motp | 2 |
| issue para o mantenedor do app | QuantumBadger/RedReader | 2 |
| issue para o mantenedor do app | AEFeinstein/mtg-familiar | 2 |
| issue para o mantenedor do app | michaelWuensch/BitBanana | 1 |
| issue para o mantenedor do app | SchweGELBin/OpenBible2 | 1 |
| issue para o mantenedor do app | CriticalMaps/criticalmaps-android | 1 |
| issue para o mantenedor do app | M66B/FairEmail | 1 |
| issue para o mantenedor do app | paroj/DSub2000 | 1 |
| issue para o mantenedor do app | schorschii/Droid_SCEP | 1 |
| correção não aplicável: código mudou upstream | ORIGINAL_CODE_ABSENT_FROM_BASE_BRANCH | 1 |
| correção gerada, sem PR | build: failed (verified_preexisting_build_failure) | 1 |

### Desfecho × origem

| desfecho | app | library | total |
|---|---|---|---|
| issue para o mantenedor do app | 16 | 0 | 16 |
| PR pronto | 6 | 1 | 7 |
| falso positivo reconhecido | 3 | 0 | 3 |
| correção não aplicável: código mudou upstream | 3 | 0 | 3 |
| evidência insuficiente | 3 | 0 | 3 |
| correção gerada, sem PR | 1 | 0 | 1 |
| violação confirmada sem correção segura | 1 | 0 | 1 |
| **total** | 33 | 1 | 34 |

### Desfecho × outcome do pacote

| desfecho | primary | severe-constant | severe-trust-all | undecided | total |
|---|---|---|---|---|---|
| issue para o mantenedor do app | 13 | 3 | 0 | 0 | 16 |
| PR pronto | 5 | 0 | 2 | 0 | 7 |
| falso positivo reconhecido | 2 | 0 | 0 | 1 | 3 |
| correção não aplicável: código mudou upstream | 2 | 0 | 1 | 0 | 3 |
| evidência insuficiente | 0 | 1 | 0 | 2 | 3 |
| correção gerada, sem PR | 0 | 1 | 0 | 0 | 1 |
| violação confirmada sem correção segura | 1 | 0 | 0 | 0 | 1 |
| **total** | 23 | 5 | 3 | 3 | 34 |

### Desfecho × label do monitor

| desfecho | application-manager | not-observed | sequence | violation | total |
|---|---|---|---|---|---|
| issue para o mantenedor do app | 0 | 3 | 0 | 13 | 16 |
| PR pronto | 2 | 0 | 3 | 2 | 7 |
| falso positivo reconhecido | 0 | 1 | 0 | 2 | 3 |
| correção não aplicável: código mudou upstream | 1 | 0 | 0 | 2 | 3 |
| evidência insuficiente | 0 | 3 | 0 | 0 | 3 |
| correção gerada, sem PR | 0 | 1 | 0 | 0 | 1 |
| violação confirmada sem correção segura | 0 | 0 | 0 | 1 | 1 |
| **total** | 3 | 8 | 3 | 20 | 34 |

## 3. Funil

### Funil geral

| etapa | n | % dos importados | IC 95% (Wilson) |
|---|---|---|---|
| importado | 34 | 100.0% | 89.8%–100.0% |
| analisado pelo LLM | 34 | 100.0% | 89.8%–100.0% |
| violação confirmada | 28 | 82.4% | 66.5%–91.7% |
| correção proposta | 17 | 50.0% | 34.1%–65.9% |
| solução gerada | 11 | 32.4% | 19.1%–49.2% |
| aplicada na worktree | 8 | 23.5% | 12.4%–40.0% |
| build ok | 7 | 20.6% | 10.3%–36.8% |
| PR pronto (commit) | 7 | 20.6% | 10.3%–36.8% |

### Disponibilidade de código (extração)

Não é etapa do funil: um finding de biblioteca pode ser analisado a partir de bytecode e dependências sem código no checkout.

| extraction_status | n |
|---|---|
| success | 34 |

### Funil por origem

| etapa | app (n=33) | library (n=1) |
|---|---|---|
| importado | 33 (100.0%) | 1 (100.0%) |
| analisado pelo LLM | 33 (100.0%) | 1 (100.0%) |
| violação confirmada | 27 (81.8%) | 1 (100.0%) |
| correção proposta | 16 (48.5%) | 1 (100.0%) |
| solução gerada | 10 (30.3%) | 1 (100.0%) |
| aplicada na worktree | 7 (21.2%) | 1 (100.0%) |
| build ok | 6 (18.2%) | 1 (100.0%) |
| PR pronto (commit) | 6 (18.2%) | 1 (100.0%) |

### Funil por outcome do pacote

| etapa | primary (n=23) | severe-constant (n=5) | severe-trust-all (n=3) | undecided (n=3) |
|---|---|---|---|---|
| importado | 23 (100.0%) | 5 (100.0%) | 3 (100.0%) | 3 (100.0%) |
| analisado pelo LLM | 23 (100.0%) | 5 (100.0%) | 3 (100.0%) | 3 (100.0%) |
| violação confirmada | 21 (91.3%) | 4 (80.0%) | 3 (100.0%) | 0 (0.0%) |
| correção proposta | 12 (52.2%) | 2 (40.0%) | 3 (100.0%) | 0 (0.0%) |
| solução gerada | 7 (30.4%) | 1 (20.0%) | 3 (100.0%) | 0 (0.0%) |
| aplicada na worktree | 5 (21.7%) | 1 (20.0%) | 2 (66.7%) | 0 (0.0%) |
| build ok | 5 (21.7%) | 0 (0.0%) | 2 (66.7%) | 0 (0.0%) |
| PR pronto (commit) | 5 (21.7%) | 0 (0.0%) | 2 (66.7%) | 0 (0.0%) |

### Funil por label do monitor

| etapa | application-manager (n=3) | not-observed (n=8) | sequence (n=3) | violation (n=20) |
|---|---|---|---|---|
| importado | 3 (100.0%) | 8 (100.0%) | 3 (100.0%) | 20 (100.0%) |
| analisado pelo LLM | 3 (100.0%) | 8 (100.0%) | 3 (100.0%) | 20 (100.0%) |
| violação confirmada | 3 (100.0%) | 4 (50.0%) | 3 (100.0%) | 18 (90.0%) |
| correção proposta | 3 (100.0%) | 2 (25.0%) | 3 (100.0%) | 9 (45.0%) |
| solução gerada | 3 (100.0%) | 1 (12.5%) | 3 (100.0%) | 4 (20.0%) |
| aplicada na worktree | 2 (66.7%) | 1 (12.5%) | 3 (100.0%) | 2 (10.0%) |
| build ok | 2 (66.7%) | 0 (0.0%) | 3 (100.0%) | 2 (10.0%) |
| PR pronto (commit) | 2 (66.7%) | 0 (0.0%) | 3 (100.0%) | 2 (10.0%) |

## 4. Decisões do LLM

### Decisão do LLM × violação do detector

`detector_violation`: *confirmed* = o relato se confirma no código; *not_confirmed* = falso positivo; *unknown* = a evidência não decide.

| decisão | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | 17 | 0 | 0 | 17 |
| NO_SAFE_FIX | 7 | 3 | 3 | 13 |
| EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX | 4 | 0 | 0 | 4 |
| **total** | 28 | 3 | 3 | 34 |

### Impacto de segurança em tempo de execução (só analisados)

| runtime_security_impact | n |
|---|---|
| not_established | 18 |
| non_security | 9 |
| confirmed | 6 |
| unknown | 1 |

### O que o LLM tinha para ler × análise

| evidência | analisado | não analisado | total |
|---|---|---|---|
| código-fonte | 34 | 0 | 34 |
| **total** | 34 | 0 | 34 |

### Evidência × violação (só analisados)

| evidência | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| código-fonte | 28 | 3 | 3 | 34 |
| **total** | 28 | 3 | 3 | 34 |

### Especificação × violação (só analisados)

| especificação | confirmed | not_confirmed | unknown | total |
|---|---|---|---|---|
| MessageDigestSpec | 12 | 0 | 0 | 12 |
| SSLContextSpec | 4 | 0 | 0 | 4 |
| SecretKeySpecSpec | 1 | 1 | 2 | 4 |
| IvParameterSpecSpec | 2 | 0 | 1 | 3 |
| KeyStoreSpec | 3 | 0 | 0 | 3 |
| CipherSpec | 1 | 0 | 0 | 1 |
| GCMParameterSpecSpec | 0 | 1 | 0 | 1 |
| SecureRandomSpec | 1 | 0 | 0 | 1 |
| PBEKeySpecSpec | 1 | 0 | 0 | 1 |
| AlgorithmParametersSpec | 0 | 1 | 0 | 1 |
| SecretKeyFactorySpec | 1 | 0 | 0 | 1 |
| KeyPairGeneratorSpec | 1 | 0 | 0 | 1 |
| MacSpec | 1 | 0 | 0 | 1 |
| **total** | 28 | 3 | 3 | 34 |

## 5. Correções, PRs e issues

### Correções geradas

| app | método | spec | decisão | aplicação | build | PR pronto | arquivos | linhas | título | avaliação manual |
|---|---|---|---|---|---|---|---|---|---|---|
| de.lukasneugebauer.nextcloudcookbook_62 | configureTrustAllCertificates | SSLContextSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | NOT_APPLICABLE_TO_BASE | – | não | – | – |  |  |
| app.michaelwuensch.bitbanana_79 | encodePbkdf2 | PBEKeySpecSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | success | sim | 7 | +306/-49 | Upgrade app-lock verifiers to PBKDF2-HMAC-SHA256 |  |
| com.craxiom.networksurvey_114 | getOrCreateKey | KeyStoreSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | success | sim | 1 | +2/-2 | Use KeyStore.getKey when reading the encryption key |  |
| com.leekleak.trafficlight_38 | getSecretKey | KeyStoreSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | success | sim | 1 | +1/-1 | Use KeyStore.getKey to read secret keys |  |
| com.sapuseven.untis_29198884 | getCacheFile | MessageDigestSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | success | sim | 1 | +1/-1 | Use SHA-256 for disk-cache file names |  |
| me.diamondforge.tokn_19 | getOrCreateKey | KeyStoreSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | success | sim | 1 | +1/-1 | Read Android keystore keys with getKey |  |
| net.gaast.giggity_769 | hashCode | MessageDigestSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | NOT_APPLICABLE_TO_BASE | – | não | – | – |  |  |
| com.ivanovsky.passnotes_11700 | createHttpClient | SSLContextSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | success | sim | 1 | +13/-28 | Use a factory-provided trust manager for WebDAV clients |  |
| com.nononsenseapps.feeder.play_4025 | trustAllCerts | SSLContextSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | success_app_module_only | sim | 1 | +10/-18 | Use platform trust managers for TLS setup |  |
| github.paroj.dsub2000_217 | <init> | SSLContextSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | NOT_APPLICABLE_TO_BASE | – | não | – | – |  |  |
| rocks.poopjournal.metadataremover_20020 | <clinit> | SecureRandomSpec | MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | SUCCESS | failed | não | 1 | +1/-1 | Use an unseeded SecureRandom for filename selection |  |

*Avaliação manual dos PRs: acrescente a coluna `pr_quality` (correto / cosmético / prejudicial) ao arquivo de --annotations.*

### Candidatos a issue

| app | método | spec | destino | repositório | tipo |
|---|---|---|---|---|---|
| app.michaelwuensch.bitbanana_79 | encodePbkdf2 | SecretKeyFactorySpec | app | michaelWuensch/BitBanana | generation_blocked |
| com.password.monitor_102 | appModule$lambda$0$3 | MessageDigestSpec | app | StellarSand/Password-Monitor | no_safe_fix |
| com.password.monitor_102 | invokeSuspend | MessageDigestSpec | app | StellarSand/Password-Monitor | no_safe_fix |
| com.schwegelbin.openbible_46 | getChecksum | MessageDigestSpec | app | SchweGELBin/OpenBible2 | no_safe_fix |
| com.vrem.wifianalyzer_71 | calculateChildType | MessageDigestSpec | app | VREMSoftwareDevelopment/WifiAnalyzer | no_safe_fix |
| com.vrem.wifianalyzer_71 | calculateGraphType | MessageDigestSpec | app | VREMSoftwareDevelopment/WifiAnalyzer | no_safe_fix |
| de.stephanlindauer.criticalmaps_104 | SHA1 | MessageDigestSpec | app | CriticalMaps/criticalmaps-android | no_safe_fix |
| eu.faircode.email_2322 | getFingerprint | MessageDigestSpec | app | M66B/FairEmail | no_safe_fix |
| github.paroj.dsub2000_217 | md5Hex | MessageDigestSpec | app | paroj/DSub2000 | no_safe_fix |
| org.cry.otp_31 | <init> | MessageDigestSpec | app | chrismiceli/motp | generation_blocked |
| org.cry.otp_31 | hmac_sha | MacSpec | app | chrismiceli/motp | generation_blocked |
| org.quantumbadger.redreader_117 | sha1 | MessageDigestSpec | app | QuantumBadger/RedReader | generation_blocked |
| systems.sieber.droid_scep_7 | CertReq | KeyPairGeneratorSpec | app | schorschii/Droid_SCEP | generation_blocked |
| com.gelakinetic.mtgfam_99 | fetch | IvParameterSpecSpec | app | AEFeinstein/mtg-familiar | no_safe_fix |
| com.gelakinetic.mtgfam_99 | fetch | SecretKeySpecSpec | app | AEFeinstein/mtg-familiar | generation_blocked |
| org.quantumbadger.redreader_117 | parseConfig | IvParameterSpecSpec | app | QuantumBadger/RedReader | no_safe_fix |

## 6. Apps

≥1 confirmado: 21 de 23 apps, ≥1 correção proposta: 15 de 23 apps, ≥1 issue: 11 de 23 apps, ≥1 PR pronto: 7 de 23 apps

| app | findings | do app | analisados | confirmados | correção proposta | PR pronto | issues | categoria | MB | instalações |
|---|---|---|---|---|---|---|---|---|---|---|
| org.cry.otp_31 | 3 | 3 | 3 | 2 | 2 | 0 | 2 |  |  |  |
| org.quantumbadger.redreader_117 | 3 | 3 | 3 | 2 | 1 | 0 | 2 |  |  |  |
| app.michaelwuensch.bitbanana_79 | 2 | 2 | 2 | 2 | 2 | 1 | 1 |  |  |  |
| com.password.monitor_102 | 2 | 2 | 2 | 2 | 0 | 0 | 2 |  |  |  |
| com.vrem.wifianalyzer_71 | 2 | 2 | 2 | 2 | 0 | 0 | 2 |  |  |  |
| github.paroj.dsub2000_217 | 2 | 2 | 2 | 2 | 1 | 0 | 1 |  |  |  |
| org.fedorahosted.freeotp_48 | 2 | 2 | 2 | 1 | 0 | 0 | 0 |  |  |  |
| com.gelakinetic.mtgfam_99 | 2 | 2 | 2 | 2 | 1 | 0 | 2 |  |  |  |
| io.treehouses.remote_6098 | 2 | 2 | 2 | 0 | 0 | 0 | 0 |  |  |  |
| de.lukasneugebauer.nextcloudcookbook_62 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |  |  |  |
| com.craxiom.networksurvey_114 | 1 | 1 | 1 | 1 | 1 | 1 | 0 |  |  |  |
| com.leekleak.trafficlight_38 | 1 | 1 | 1 | 1 | 1 | 1 | 0 |  |  |  |
| com.sapuseven.untis_29198884 | 1 | 1 | 1 | 1 | 1 | 1 | 0 |  |  |  |
| com.schwegelbin.openbible_46 | 1 | 1 | 1 | 1 | 0 | 0 | 1 |  |  |  |
| de.stephanlindauer.criticalmaps_104 | 1 | 1 | 1 | 1 | 0 | 0 | 1 |  |  |  |
| eu.faircode.email_2322 | 1 | 1 | 1 | 1 | 0 | 0 | 1 |  |  |  |
| me.diamondforge.tokn_19 | 1 | 1 | 1 | 1 | 1 | 1 | 0 |  |  |  |
| net.gaast.giggity_769 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |  |  |  |
| systems.sieber.droid_scep_7 | 1 | 1 | 1 | 1 | 1 | 0 | 1 |  |  |  |
| com.ivanovsky.passnotes_11700 | 1 | 1 | 1 | 1 | 1 | 1 | 0 |  |  |  |
| com.nononsenseapps.feeder.play_4025 | 1 | 0 | 1 | 1 | 1 | 1 | 0 |  |  |  |
| rocks.poopjournal.metadataremover_20020 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |  |  |  |
| com.beemdevelopment.aegis_81 | 1 | 1 | 1 | 0 | 0 | 0 | 0 |  |  |  |

## 7. Decisão do pipeline × rótulos

### detector_violation × origem (só findings analisados)

| origem | n | confirmed | not_confirmed | unknown |
|---|---|---|---|---|
| app | 33 | 27 | 3 | 3 |
| library | 1 | 1 | 0 | 0 |

### detector_violation × outcome do pacote (só findings analisados)

| outcome do pacote | n | confirmed | not_confirmed | unknown |
|---|---|---|---|---|
| primary | 23 | 21 | 2 | 0 |
| severe-constant | 5 | 4 | 0 | 1 |
| severe-trust-all | 3 | 3 | 0 | 0 |
| undecided | 3 | 0 | 1 | 2 |

### detector_violation × label do monitor (só findings analisados)

| label do monitor | n | confirmed | not_confirmed | unknown |
|---|---|---|---|---|
| application-manager | 3 | 3 | 0 | 0 |
| not-observed | 8 | 4 | 1 | 3 |
| sequence | 3 | 3 | 0 | 0 |
| violation | 20 | 18 | 2 | 0 |

*Concordância com o veredito humano: forneça --annotations (colunas apk, class, method, spec, verdict com R/W/F/E).*

## 8. Estabilidade

### Estabilidade entre execuções

| par de execuções | analisados em ambas | mesma decisão | mesmo detector_violation | κ (detector_violation) |
|---|---|---|---|---|
| run2 × subset_gates | 4 | 4 (100.0%) | 4 (100.0%) | – |
| run2 × subset_policy | 4 | 4 (100.0%) | 4 (100.0%) | – |
| run2 × subset_policy2 | 3 | 3 (100.0%) | 3 (100.0%) | – |
| run2 × subset_bitbanana | 2 | 2 (100.0%) | 2 (100.0%) | – |
| run2 × subset_bitbanana_readers | 1 | 1 (100.0%) | 1 (100.0%) | – |
| run2 × subset_bitbanana_readers2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| run2 × subset_bitbanana_scope | 1 | 1 (100.0%) | 1 (100.0%) | – |
| run2 × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| run2 × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| run2 × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| run2 × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| run2 × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_policy | 2 | 2 (100.0%) | 2 (100.0%) | – |
| subset_gates × subset_policy2 | 2 | 2 (100.0%) | 2 (100.0%) | – |
| subset_gates × subset_bitbanana | 2 | 2 (100.0%) | 2 (100.0%) | – |
| subset_gates × subset_bitbanana_readers | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_bitbanana_readers2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_bitbanana_scope | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_gates × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_policy2 | 3 | 3 (100.0%) | 3 (100.0%) | – |
| subset_policy × subset_bitbanana | 2 | 2 (100.0%) | 2 (100.0%) | – |
| subset_policy × subset_bitbanana_readers | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_bitbanana_readers2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_bitbanana_scope | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana | 2 | 2 (100.0%) | 2 (100.0%) | – |
| subset_policy2 × subset_bitbanana_readers | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana_readers2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana_scope | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_policy2 × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_readers | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_readers2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_scope | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers × subset_bitbanana_readers2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers × subset_bitbanana_scope | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers2 × subset_bitbanana_scope | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers2 × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers2 × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers2 × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers2 × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_readers2 × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope × subset_bitbanana_scope2 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope2 × subset_bitbanana_scope3 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope2 × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope2 × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope2 × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope3 × subset_bitbanana_scope4 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope3 × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope3 × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope4 × subset_bitbanana_scope5 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope4 × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |
| subset_bitbanana_scope5 × subset_bitbanana_scope6 | 1 | 1 (100.0%) | 1 (100.0%) | – |

Atenção: se o código do pipeline mudou entre as execuções, a diferença mistura o efeito das mudanças com a variação do LLM. Para medir só a variação, compare execuções com o código congelado.

## 9. Builds

### run2

### Builds de validação

| causa | n |
|---|---|
| sucesso | 10 |
| ambiente: processador de anotações | 2 |
| não classificada | 1 |

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|
| app.michaelwuensch.bitbanana_79.apk | assembleDebug | success | – | sucesso |
| com.craxiom.networksurvey_114.apk | :networksurvey:assembleCdrDebug | success | – | sucesso |
| com.ivanovsky.passnotes_11700.apk | :app:assembleAutomationDebug | success | – | sucesso |
| com.leekleak.trafficlight_38.apk | :app:assembleFullDebug | success | – | sucesso |
| com.nononsenseapps.feeder.play_4025.apk | :app:compileFdroidDebugSources | success_app_module_only | – | sucesso |
| com.sapuseven.untis_29198884.apk | assembleDebug | success | – | sucesso |
| github.paroj.dsub2000_217.apk | :app:assembleFlossDebug | success | – | sucesso |
| me.diamondforge.tokn_19.apk | assembleDebug | success | – | sucesso |
| systems.sieber.droid_scep_7.apk | :app:assembleGoogleDebug | success | – | sucesso |
| app.michaelwuensch.bitbanana_79.apk | assembleDebug | failed | generated_patch | ambiente: processador de anotações |
| com.gelakinetic.mtgfam_99.apk | :mobile:assembleDebugAndroidTest | success | – | sucesso |
| org.cry.otp_31.apk | assembleDebug | failed | – | não classificada |
| rocks.poopjournal.metadataremover_20020.apk | assembleDebug | failed | verified_preexisting_build_failure | ambiente: processador de anotações |

### subset_gates

### Builds de validação

| causa | n |
|---|---|

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|

### subset_policy

### Builds de validação

| causa | n |
|---|---|
| não classificada | 1 |

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|
| org.cry.otp_31.apk | assembleDebug | failed | – | não classificada |

### subset_policy2

### Builds de validação

| causa | n |
|---|---|

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|

### subset_bitbanana

### Builds de validação

| causa | n |
|---|---|
| sucesso | 1 |

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|
| app.michaelwuensch.bitbanana_79.apk | assembleDebug | success | – | sucesso |

### subset_bitbanana_scope6

### Builds de validação

| causa | n |
|---|---|
| sucesso | 1 |

| app | tarefa | status | atribuição | causa |
|---|---|---|---|---|
| app.michaelwuensch.bitbanana_79.apk | assembleDebug | success | – | sucesso |

## 10. Custo

| execução | findings | chamadas LLM | falhas | h de modelo | h etapa llm | h validação | h total |
|---|---|---|---|---|---|---|---|
| run2 | 34 | 283 | 0 | 2.7 | 3.4 | 3.4 | 7.2 |
| subset_gates | 4 | 27 | 0 | 0.3 | 0.5 | 0.0 | 0.5 |
| subset_policy | 4 | 45 | 0 | 0.4 | 0.4 | 0.0 | 0.5 |
| subset_policy2 | 3 | 34 | 0 | 0.3 | 0.3 | 0.0 | 0.3 |
| subset_bitbanana | 2 | 19 | 0 | 0.2 | 0.2 | 0.0 | 0.3 |
| subset_bitbanana_scope6 | 1 | 15 | 0 | 0.2 | 0.0 | 0.0 | 0.1 |
