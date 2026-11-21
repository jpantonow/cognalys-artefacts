# Consolidação dos 34 findings

**Revisão técnica posterior:** [coerência dos 17 findings e dos oito previews](COERENCIA_17_FINDINGS.md). A contagem por commit/build não é um parecer de correção: há divergências no replay TLS, no contrato do cache e em alguns textos.

A run2 foi combinada com as reexecuções posteriores. Cada finding aparece uma vez, com o resultado da última análise disponível. Os originais foram preservados. Esta pasta reúne relatórios e previews para revisão; não é uma nova execução da pipeline.

| Etapa | run2 original | Consolidado atual |
|---|---:|---:|
| Findings únicos | 34 | 34 |
| Decisão MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | 17 | 17 |
| Soluções geradas | 15 | 11 |
| Previews de PR registrados | 13 | 8 |
| Previews de PR com commit e build aprovado | 10 | 7 |

Há oito previews locais: sete com commit e build aprovado, mais o Metadata Remover, bloqueado por falha de build e sem commit. Não são sete PRs publicados: nenhuma destas runs registrou publicação de PR no GitHub. Os oito previews estão em review/pull_requests.

Os 15 da run2 são soluções geradas, não 15 PRs concluídos. A decisão de propor correção também não garante geração, aplicação ou compilação. Os previews com build aprovado continuam sujeitos à revisão e aos bloqueios de publicação; nenhum envio ao GitHub foi feito nesta consolidação.

Uma reexecução que terminou em issue ou não aplicabilidade substitui o PR antigo do mesmo finding. O consolidado representa o estado mais recente de cada achado.

## Desfechos atuais

| Desfecho | Findings |
|---|---:|
| PR pronto | 7 |
| issue para o mantenedor do app | 16 |
| falso positivo reconhecido | 3 |
| correção não aplicável: código mudou upstream | 3 |
| evidência insuficiente | 3 |
| violação confirmada sem correção segura | 1 |
| correção gerada, sem PR | 1 |

## Previews com build aprovado

- [app.michaelwuensch.bitbanana_79.apk — encodePbkdf2 — PBEKeySpecSpec](review/pull_requests/app.michaelwuensch.bitbanana_79.apk_encodePbkdf2_PBEKeySpecSpec.md) — origem `subset_bitbanana_scope6`.
- [com.craxiom.networksurvey_114.apk — getOrCreateKey — KeyStoreSpec](review/pull_requests/com.craxiom.networksurvey_114.apk_getOrCreateKey_KeyStoreSpec.md) — origem `run2`.
- [com.ivanovsky.passnotes_11700.apk — createHttpClient — SSLContextSpec](review/pull_requests/com.ivanovsky.passnotes_11700.apk_createHttpClient_SSLContextSpec.md) — origem `run2`.
- [com.leekleak.trafficlight_38.apk — getSecretKey — KeyStoreSpec](review/pull_requests/com.leekleak.trafficlight_38.apk_getSecretKey_KeyStoreSpec.md) — origem `run2`.
- [com.nononsenseapps.feeder.play_4025.apk — trustAllCerts — SSLContextSpec](review/pull_requests/com.nononsenseapps.feeder.play_4025.apk_trustAllCerts_SSLContextSpec.md) — origem `run2`.
- [com.sapuseven.untis_29198884.apk — getCacheFile — MessageDigestSpec](review/pull_requests/com.sapuseven.untis_29198884.apk_getCacheFile_MessageDigestSpec.md) — origem `run2`.
- [me.diamondforge.tokn_19.apk — getOrCreateKey — KeyStoreSpec](review/pull_requests/me.diamondforge.tokn_19.apk_getOrCreateKey_KeyStoreSpec.md) — origem `run2`.

## Issues propostas

- [app.michaelwuensch.bitbanana_79.apk — encodePbkdf2 — SecretKeyFactorySpec](review/issues/app.michaelwuensch.bitbanana_79.apk_encodePbkdf2_SecretKeyFactorySpec_no_safe_fix.md) — origem `subset_bitbanana`.
- [com.gelakinetic.mtgfam_99.apk — fetch — IvParameterSpecSpec](review/issues/com.gelakinetic.mtgfam_99.apk_fetch_IvParameterSpecSpec_no_safe_fix.md) — origem `run2`.
- [com.gelakinetic.mtgfam_99.apk — fetch — SecretKeySpecSpec](review/issues/com.gelakinetic.mtgfam_99.apk_fetch_SecretKeySpecSpec_no_safe_fix.md) — origem `subset_policy`.
- [com.password.monitor_102.apk — appModule$lambda$0$3 — MessageDigestSpec](review/issues/com.password.monitor_102.apk_appModule_lambda_0_3_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [com.password.monitor_102.apk — invokeSuspend — MessageDigestSpec](review/issues/com.password.monitor_102.apk_invokeSuspend_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [com.schwegelbin.openbible_46.apk — getChecksum — MessageDigestSpec](review/issues/com.schwegelbin.openbible_46.apk_getChecksum_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [com.vrem.wifianalyzer_71.apk — calculateChildType — MessageDigestSpec](review/issues/com.vrem.wifianalyzer_71.apk_calculateChildType_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [com.vrem.wifianalyzer_71.apk — calculateGraphType — MessageDigestSpec](review/issues/com.vrem.wifianalyzer_71.apk_calculateGraphType_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [de.stephanlindauer.criticalmaps_104.apk — SHA1 — MessageDigestSpec](review/issues/de.stephanlindauer.criticalmaps_104.apk_SHA1_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [eu.faircode.email_2322.apk — getFingerprint — MessageDigestSpec](review/issues/eu.faircode.email_2322.apk_getFingerprint_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [github.paroj.dsub2000_217.apk — md5Hex — MessageDigestSpec](review/issues/github.paroj.dsub2000_217.apk_md5Hex_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [org.cry.otp_31.apk — <init> — MessageDigestSpec](review/issues/org.cry.otp_31.apk_init_MessageDigestSpec_no_safe_fix.md) — origem `subset_policy2`.
- [org.cry.otp_31.apk — hmac_sha — MacSpec](review/issues/org.cry.otp_31.apk_hmac_sha_MacSpec_no_safe_fix.md) — origem `run2`.
- [org.quantumbadger.redreader_117.apk — parseConfig — IvParameterSpecSpec](review/issues/org.quantumbadger.redreader_117.apk_parseConfig_IvParameterSpecSpec_no_safe_fix.md) — origem `run2`.
- [org.quantumbadger.redreader_117.apk — sha1 — MessageDigestSpec](review/issues/org.quantumbadger.redreader_117.apk_sha1_MessageDigestSpec_no_safe_fix.md) — origem `run2`.
- [systems.sieber.droid_scep_7.apk — CertReq — KeyPairGeneratorSpec](review/issues/systems.sieber.droid_scep_7.apk_CertReq_KeyPairGeneratorSpec_no_safe_fix.md) — origem `subset_gates`.

## Preview bloqueado por build

- [rocks.poopjournal.metadataremover_20020.apk — <clinit> — SecureRandomSpec](review/pull_requests/rocks.poopjournal.metadataremover_20020.apk_clinit_SecureRandomSpec.md) — build falhou; commit ausente. Origem `run2`.

## PRs anteriores substituídos

| Finding | run2 | Resultado atual | Origem |
|---|---|---|---|
| com.gelakinetic.mtgfam_99.apk / fetch / SecretKeySpecSpec | PR com build aprovado | issue para o mantenedor do app | subset_policy |
| github.paroj.dsub2000_217.apk / <init> / SSLContextSpec | PR com build aprovado | correção não aplicável: código mudou upstream | subset_gates |
| systems.sieber.droid_scep_7.apk / CertReq / KeyPairGeneratorSpec | PR com build aprovado | issue para o mantenedor do app | subset_gates |

## Arquivos

- [Estatísticas completas](statistics/statistics.md).
- [Tabela dos 34 findings e sua origem](statistics/findings.csv).
- [Registros consolidados em JSON](findings.json).
- [Manifesto dos artefatos selecionados](manifest.json).

`subset_openai` não foi usado como sobrescrita: terminou antes da run2. As execuções usam versões diferentes da biblioteca, portanto este é um acompanhamento consolidado, não uma medição homogênea de uma única versão.
