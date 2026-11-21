# Conferência das reexecuções posteriores

Inventário dos diretórios realmente executados neste lote, ordenado pelos horários de `step_timings.json`, em America/Sao_Paulo. Configurações `.toml` e scripts `.sh` sem artefatos de execução não entram como resultado.

| Finding | Run | Término da run | Solução | Build | PR com build | Resultado |
|---|---|---|---|---|---|---|
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / SecretKeyFactorySpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| com.craxiom.networksurvey_114.apk / getOrCreateKey / KeyStoreSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | issue para o mantenedor do app |
| com.gelakinetic.mtgfam_99.apk / fetch / SecretKeySpecSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| com.ivanovsky.passnotes_11700.apk / createHttpClient / SSLContextSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| com.leekleak.trafficlight_38.apk / getSecretKey / KeyStoreSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | falso positivo reconhecido |
| com.nononsenseapps.feeder.play_4025.apk / trustAllCerts / SSLContextSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| com.sapuseven.untis_29198884.apk / getCacheFile / MessageDigestSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| de.lukasneugebauer.nextcloudcookbook_62.apk / configureTrustAllCertificates / SSLContextSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| github.paroj.dsub2000_217.apk / <init> / SSLContextSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| me.diamondforge.tokn_19.apk / getOrCreateKey / KeyStoreSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | falso positivo reconhecido |
| net.gaast.giggity_769.apk / hashCode / MessageDigestSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| org.cry.otp_31.apk / <init> / MessageDigestSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| org.cry.otp_31.apk / hmac_sha / MacSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | falso positivo reconhecido |
| org.quantumbadger.redreader_117.apk / sha1 / MessageDigestSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | issue para o mantenedor do app |
| rocks.poopjournal.metadataremover_20020.apk / <clinit> / SecureRandomSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | não analisado |
| systems.sieber.droid_scep_7.apk / CertReq / KeyPairGeneratorSpec | run | 2026-10-19T03:35:32-03:00 | False | — | False | evidência insuficiente |
| com.craxiom.networksurvey_114.apk / getOrCreateKey / KeyStoreSpec | subset_openai | 2026-10-19T13:35:31-03:00 | False | — | False | não analisado |
| com.gelakinetic.mtgfam_99.apk / fetch / SecretKeySpecSpec | subset_openai | 2026-10-19T13:35:31-03:00 | False | — | False | evidência insuficiente |
| com.leekleak.trafficlight_38.apk / getSecretKey / KeyStoreSpec | subset_openai | 2026-10-19T13:35:31-03:00 | True | success | True | PR pronto |
| me.diamondforge.tokn_19.apk / getOrCreateKey / KeyStoreSpec | subset_openai | 2026-10-19T13:35:31-03:00 | True | success | True | PR pronto |
| org.cry.otp_31.apk / hmac_sha / MacSpec | subset_openai | 2026-10-19T13:35:31-03:00 | True | failed | False | correção gerada, sem PR |
| org.quantumbadger.redreader_117.apk / sha1 / MessageDigestSpec | subset_openai | 2026-10-19T13:35:31-03:00 | False | — | False | issue para o mantenedor do app |
| systems.sieber.droid_scep_7.apk / CertReq / KeyPairGeneratorSpec | subset_openai | 2026-10-19T13:35:31-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / SecretKeyFactorySpec | run2 | 2026-10-19T23:38:39-03:00 | True | failed | False | correção gerada, sem PR |
| com.craxiom.networksurvey_114.apk / getOrCreateKey / KeyStoreSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| com.gelakinetic.mtgfam_99.apk / fetch / SecretKeySpecSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| com.ivanovsky.passnotes_11700.apk / createHttpClient / SSLContextSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| com.leekleak.trafficlight_38.apk / getSecretKey / KeyStoreSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| com.nononsenseapps.feeder.play_4025.apk / trustAllCerts / SSLContextSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success_app_module_only | True | PR pronto |
| com.sapuseven.untis_29198884.apk / getCacheFile / MessageDigestSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| de.lukasneugebauer.nextcloudcookbook_62.apk / configureTrustAllCertificates / SSLContextSpec | run2 | 2026-10-19T23:38:39-03:00 | True | — | False | correção não aplicável: código mudou upstream |
| github.paroj.dsub2000_217.apk / <init> / SSLContextSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| me.diamondforge.tokn_19.apk / getOrCreateKey / KeyStoreSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| net.gaast.giggity_769.apk / hashCode / MessageDigestSpec | run2 | 2026-10-19T23:38:39-03:00 | True | — | False | correção não aplicável: código mudou upstream |
| org.cry.otp_31.apk / <init> / MessageDigestSpec | run2 | 2026-10-19T23:38:39-03:00 | True | failed | False | correção gerada, sem PR |
| org.cry.otp_31.apk / hmac_sha / MacSpec | run2 | 2026-10-19T23:38:39-03:00 | False | — | False | issue para o mantenedor do app |
| org.quantumbadger.redreader_117.apk / sha1 / MessageDigestSpec | run2 | 2026-10-19T23:38:39-03:00 | False | — | False | issue para o mantenedor do app |
| rocks.poopjournal.metadataremover_20020.apk / <clinit> / SecureRandomSpec | run2 | 2026-10-19T23:38:39-03:00 | True | failed | False | correção gerada, sem PR |
| systems.sieber.droid_scep_7.apk / CertReq / KeyPairGeneratorSpec | run2 | 2026-10-19T23:38:39-03:00 | True | success | True | PR pronto |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_gates | 2026-10-20T00:35:56-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / SecretKeyFactorySpec | subset_gates | 2026-10-20T00:35:56-03:00 | False | — | False | issue para o mantenedor do app |
| github.paroj.dsub2000_217.apk / <init> / SSLContextSpec | subset_gates | 2026-10-20T00:35:56-03:00 | True | — | False | correção não aplicável: código mudou upstream |
| systems.sieber.droid_scep_7.apk / CertReq / KeyPairGeneratorSpec | subset_gates | 2026-10-20T00:35:56-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_policy | 2026-10-20T02:36:58-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / SecretKeyFactorySpec | subset_policy | 2026-10-20T02:36:58-03:00 | False | — | False | issue para o mantenedor do app |
| com.gelakinetic.mtgfam_99.apk / fetch / SecretKeySpecSpec | subset_policy | 2026-10-20T02:36:58-03:00 | False | — | False | issue para o mantenedor do app |
| org.cry.otp_31.apk / <init> / MessageDigestSpec | subset_policy | 2026-10-20T02:36:58-03:00 | True | failed | False | correção gerada, sem PR |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_policy2 | 2026-10-20T10:47:42-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / SecretKeyFactorySpec | subset_policy2 | 2026-10-20T10:47:42-03:00 | False | — | False | issue para o mantenedor do app |
| org.cry.otp_31.apk / <init> / MessageDigestSpec | subset_policy2 | 2026-10-20T10:47:42-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana | 2026-10-20T11:06:06-03:00 | True | success | True | PR pronto |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / SecretKeyFactorySpec | subset_bitbanana | 2026-10-20T11:06:06-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_readers | 2026-10-20T12:05:25-03:00 | True | success | True | PR pronto |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_readers2 | 2026-10-20T12:13:08-03:00 | False | — | False | issue para o mantenedor do app |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_scope | 2026-10-20T13:06:37-03:00 | True | — | False | correção gerada, sem PR |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_scope2 | 2026-10-20T13:25:49-03:00 | True | success | True | PR pronto |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_scope3 | 2026-10-20T13:32:49-03:00 | True | — | False | correção gerada, sem PR |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_scope4 | 2026-10-20T13:40:30-03:00 | True | — | False | correção gerada, sem PR |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_scope5 | 2026-10-20T13:48:15-03:00 | True | success | True | PR pronto |
| app.michaelwuensch.bitbanana_79.apk / encodePbkdf2 / PBEKeySpecSpec | subset_bitbanana_scope6 | 2026-10-20T14:01:12-03:00 | True | success | True | PR pronto |
