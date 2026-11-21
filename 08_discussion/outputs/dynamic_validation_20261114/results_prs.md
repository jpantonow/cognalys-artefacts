# Validação dinâmica dos PRs publicados: RVSec no APK base e no APK corrigido

Pré-registro: `docs/preregistro_validacao_prs_20261115.md`. Os dois APKs de cada caso saem do mesmo commit com a mesma tarefa Gradle; o corrigido tem o diff do PR (ou do pipeline, quando iguais) aplicado sem alteração. RVSec: `monkey:fast,ape:sata`, 600 s, jca_android; uma repetição por caso (segunda só para inconclusivos). Regra de `classify.py` sem mudança. Resultado relatado como "a violação reportada deixou de aparecer no método corrigido". Desvio declarado em 2026-11-17: quando o APK corrigido nunca executa o site e o diff do PR remove do arquivo do site o uso da API da especificação sem acrescentar outro, o caso é "corrigido (chamada removida pelo patch)" (a regra daria "inconclusivo (cobertura)"; as duas leituras ficam em results_prs.json). Segundo desvio declarado em 2026-11-17: quando a mensagem reportada aparece no original e em nenhuma repetição do corrigido, o site corrigido foi coberto e toda mensagem que resta nele já aparecia no original, o caso é "corrigido (mensagem reportada sumiu; restam outras já presentes no original)" (a regra daria "não corrigido"; as duas leituras ficam em results_prs.json). Desvio 5 (2026-11-17): os sete PRs excluídos por mensagem em formato antigo (pr_cases_oldmsg.json) são validados no nível da especificação (qualquer violação da especificação no site; nenhuma mensagem é comparada, por isso suas células mostram `V*`) e marcados com `deviation`; ficam excluídos até terem resultado. Desvio 6 (2026-11-18): seis PRs excluídos (pr_cases_desvio6.json) entram com o valor que faltava fixado por fonte registrada (keepassvault #347 e HTTP-Shortcuts #576 no nível da especificação); os inconclusivos e de base que não reproduz ganham uma repetição a mais (BitBanana: rep 4 sobre as três de 14/11), rotulados com todas as repetições juntas. Correções de rótulo (2026-11-18, revisão de todo caso sem "corrigido"; o rótulo anterior fica em `rule_result`): "sem mudança de produção (o PR só altera testes)" quando todo arquivo do diff é de teste; "não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original)" quando o "não corrigido" vem só de mensagens que já estavam no site do original e a reportada não aparece em nenhum APK; "não corrigido (a violação reportada passou para outro método do mesmo arquivo)" quando o corrigido nunca executa o site e a mensagem reportada aparece em outro método do mesmo arquivo-fonte; "corrigido (o patch desliga o caminho que executava o site)" quando o corrigido nunca executa o site e toda chamada do site, no código-fonte do original, está sob um `if` cuja variável tem o valor padrão trocado pelo diff; "não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação)" quando o "base não reproduz" tem o site executado no original e a mensagem do relato original não aparece em nenhuma execução da validação. Execuções com a imagem da campanha original (runs_version/<tag>/) aparecem no detalhe do caso.

População: 49 PRs. Todos: corrigido: 21; corrigido (chamada removida pelo patch): 2; corrigido (mensagem reportada sumiu; restam outras já presentes no original): 5; corrigido (o patch desliga o caminho que executava o site): 1; excluído: 3; inconclusivo (cobertura): 1; já validado (14/11, 3 repetições): 4; não compilou: 1; não corrigido: 3; não corrigido (a violação reportada passou para outro método do mesmo arquivo): 1; não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): 4; não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação): 2; sem mudança de produção (o PR só altera testes): 1.

Casos novos (41): corrigido: 21; corrigido (chamada removida pelo patch): 2; corrigido (mensagem reportada sumiu; restam outras já presentes no original): 5; corrigido (o patch desliga o caminho que executava o site): 1; não compilou: 1; não corrigido: 3; não corrigido (a violação reportada passou para outro método do mesmo arquivo): 1; não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): 4; não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação): 2; sem mudança de produção (o PR só altera testes): 1.

- aceito: corrigido: 9; corrigido (chamada removida pelo patch): 1; corrigido (mensagem reportada sumiu; restam outras já presentes no original): 2; não corrigido: 2; não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): 2; não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação): 1; sem mudança de produção (o PR só altera testes): 1
- aberto: corrigido: 6; corrigido (chamada removida pelo patch): 1; corrigido (mensagem reportada sumiu; restam outras já presentes no original): 3; corrigido (o patch desliga o caminho que executava o site): 1; não compilou: 1; não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): 2; não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação): 1
- fechado pelo mantenedor: corrigido: 6; não corrigido: 1; não corrigido (a violação reportada passou para outro método do mesmo arquivo): 1

| PR | Status do PR | Caso | Spec | Base | Corrigido | Resultado |
|---|---|---|---|---|---|---|
| gigabytedevelopers/CometOTP/pull/114 | aceito | cometotp_1 | CipherSpec | V cov | - cov | corrigido |
| FliegendeWurst/TriliumDroid/pull/177 | aceito | triliumdroid_24 | KeyManagerFactorySpec | V cov | - cov | corrigido |
| mouse233/localsend-kotlin/pull/58 | aceito | localsendkotlin_23 | KeyStoreSpec | V cov | - cov | corrigido |
| Crustack/NotallyX/pull/1167 | aceito | notallyx_71134 | MessageDigestSpec | V cov | - cov | corrigido |
| jimvixx/ShortMessageSecure/pull/89 | aceito | smsecure_10400_pr89 | MessageDigestSpec | V cov | - cov | corrigido |
| freeotp/freeotp-android/pull/524 | aceito | freeotp_48_pr524 | CipherSpec | - nocov / - nocov | - nocov / unfinished | corrigido |
| freeotp/freeotp-android/pull/521 | aceito | freeotp_48_pr521 | CipherSpec | - nocov | - nocov | sem mudança de produção (o PR só altera testes) |
| jenspfahl/ANOTHERpass/pull/103 | aceito | yapm_202006 | KeyStoreSpec | V cov | V cov | não corrigido |
| ounben/AMARadio/pull/122 | aceito | amaradio_159 | MessageDigestSpec | V cov | - cov | corrigido |
| jimvixx/ShortMessageSecure/pull/83 | aceito | smsecure_10400_pr83 | KeyGeneratorSpec | V cov | V cov | não corrigido |
| thatSFguy/meshcore-mobile-app/pull/10 | aceito | native_1 | CipherSpec | V cov | - cov | corrigido |
| wolpi/prim-ftpd/pull/453 | aberto | primftpd_71 | SecretKeySpecSpec | V cov / V cov | - nocov / - nocov | corrigido (chamada removida pelo patch) |
| thuryn/your-local-weather/pull/209 | aceito | yourlocalweather_213 | SecretKeyFactorySpec | - nocov / - nocov / V cov | - cov / - cov / - cov | corrigido |
| PrivacyDragon/Bookwyrm_Android/pull/29 | aberto | bookwyrm_16 | GCMParameterSpecSpec | V cov | - cov | corrigido |
| Vauth/salty-app/pull/6 | aberto | salty_2 | CipherSpec | V cov | - cov | corrigido |
| 0xZhangKe/Fread/pull/168 | aberto | fread_109000 | SSLContextSpec | V cov | V* cov | corrigido (mensagem reportada sumiu; restam outras já presentes no original) |
| orgzly-revived/orgzly-android-revived/pull/1121 | aberto | orgzlyrevived_292 | MessageDigestSpec | V cov | - cov | corrigido |
| yogeshpaliyal/KeyPass/pull/1252 | aberto | keypass_1442 | CipherSpec | V cov | - cov | corrigido |
| patzly/grocy-android/pull/1013 | aberto | grocy_64_pr1013 | KeyStoreSpec | V cov | - cov | corrigido |
| patzly/grocy-android/pull/1012 | aberto | grocy_64_pr1012 | SSLContextSpec | V cov | V* cov | corrigido (mensagem reportada sumiu; restam outras já presentes no original) |
| schorschii/Droid_SCEP/pull/2 | aberto | droidscep_7 | KeyPairGeneratorSpec | V cov | - cov | corrigido |
| ReadYouApp/ReadYou/pull/1334 | aberto | reader_47 | SSLContextSpec | V cov / V cov / V cov | - nocov / - nocov / - nocov | corrigido (o patch desliga o caminho que executava o site) |
| zfdang/zSMTH-Android/pull/66 | aberto | zsmthandroid_1031 | SSLContextSpec | V cov | V* cov | corrigido (mensagem reportada sumiu; restam outras já presentes no original) |
| ItzSkyeYT/InterTune/pull/52 | fechado pelo mantenedor | intertune_96 | MessageDigestSpec | V cov / V cov / - nocov | - nocov / - nocov / - nocov | não corrigido (a violação reportada passou para outro método do mesmo arquivo) |
| vitorpamplona/amethyst/pull/4642 | fechado pelo mantenedor | amethyst_464 | GCMParameterSpecSpec | V cov | V cov | não corrigido |
| woheller69/whoBIRD/pull/144 | fechado pelo mantenedor | whobird_54 | MessageDigestSpec | V cov | - cov | corrigido |
| javalc6/rssreader/pull/4 | fechado pelo mantenedor | rssreader_120 | MessageDigestSpec | V cov | - cov | corrigido |
| QuantumBadger/RedReader/pull/1373 | fechado pelo mantenedor | redreader_117 | MessageDigestSpec | V cov | - cov | corrigido |
| nextcloud/news-android/pull/1716 | aceito | owncloudnewsreader_196 | KeyStoreSpec | - cov / - cov / - cov | - cov / - cov / - cov | não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação) |
| bitcraze/crazyflie-android-client/pull/103 | aceito | crazyfliecontrol2_22 | SSLContextSpec | V* cov / V* cov | - nocov / - nocov | corrigido (chamada removida pelo patch) |
| paroj/DSub2000/pull/160 | aceito | dsub2000_217 | SSLContextSpec | V* cov | V* cov | não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original) |
| spacecowboy/Feeder/pull/1210 | aberto | feeder_4025 | SSLContextSpec | V* cov | V* cov | não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original) |
| etesync/android/pull/301 | aberto | syncadapter_20700 | SSLContextSpec | V* cov | V* cov | não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original) |
| Wilm0r/giggity/pull/543 | fechado pelo mantenedor | giggity_769 | MessageDigestSpec | V* cov | - cov | corrigido |
| VREMSoftwareDevelopment/WiFiAnalyzer/pull/618 | fechado pelo mantenedor | wifianalyzer_71 | MessageDigestSpec | V* cov | - cov | corrigido |
| session-foundation/session-android/pull/2241 | aceito | messenger_4545 | SSLContextSpec | V cov | V* cov | corrigido (mensagem reportada sumiu; restam outras já presentes no original) |
| Waboodoo/HTTP-Shortcuts/pull/576 | aceito | httpshortcuts_1104060001 | SSLContextSpec | V* nocov | V* cov | não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original) |
| lneugebauer/nextcloud-cookbook/pull/204 | aceito | nextcloudcookbook_62 | SSLContextSpec | V cov | V* cov | corrigido (mensagem reportada sumiu; restam outras já presentes no original) |
| Crazy-Marvin/MetadataRemover/pull/264 | aberto | metadataremover_20020 | SecureRandomSpec | - | - | não compilou |
| aivanovski/keepassvault/pull/347 | aberto | passnotes_11700_pr347 | SSLContextSpec | - nocov / - cov | - cov / - nocov | não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação) |
| idunnololz/summit/pull/54 | fechado pelo mantenedor | summit_355 | MessageDigestSpec | V cov | - cov | corrigido |
| leekleak/traffic-light/pull/238 | aceito | trafficlight_38 | KeyStoreSpec | - | - | já validado (14/11, 3 repetições): corrigido |
| tokn-authenticator/tokn/pull/138 | aceito | tokn_19 | KeyStoreSpec | - | - | já validado (14/11, 3 repetições): corrigido |
| BombusMod/BombusMod/pull/316 | aceito | bombusmod_1422 | Closeable_MeaninglessClose | - | - | excluído (spec fora do conjunto jca_android; mensagem sem linha do detector correspondente no site) |
| michaelWuensch/BitBanana/pull/184 | aberto | bitbanana_79 | PBEKeySpecSpec | - nocov / V cov / - nocov / V cov | - nocov / - nocov / - nocov / - nocov | inconclusivo (cobertura) |
| nelenkov/wwwjdic/pull/115 | aberto | wwwjdic_2370 | Closeable_MeaninglessClose | - | - | excluído (spec fora do conjunto jca_android; mensagem sem linha do detector correspondente no site; sem build que tenha produzido APK, ou vários APKs sem registro de qual o RVSec analisou) |
| binwiederhier/ntfy-android/pull/184 | aberto | ntfy_32 | Collections_SynchronizedMap | - | - | excluído (sem identidade do achado ou sem metadados do pipeline; spec fora do conjunto jca_android; mensagem sem linha do detector correspondente no site; sem build que tenha produzido APK, ou vários APKs sem registro de qual o RVSec analisou) |
| christianrowlands/android-network-survey/pull/154 | fechado pelo mantenedor | networksurvey_114 | KeyStoreSpec | - | - | já validado (14/11, 3 repetições): corrigido |
| SapuSeven/BetterUntis/pull/515 | fechado pelo mantenedor | untis_29198884 | MessageDigestSpec | - | - | já validado (14/11, 3 repetições): corrigido |

Legenda das células: as de `classify.py` (`V` violação no site, `V*` com outra mensagem, `-` nenhuma, `cov`/`nocov` método executado ou não; `missing`/`unfinished`/`unexplorable`).

## cometotp_1

- base_r1: violação=sim (monkey:fast), coberto=sim (monkey:fast); monkey:fast COMPLETED 1588 métodos (site: 1); ape:sata COMPLETED 1087 métodos
  - com.gigabytedevelopersinc.app.cometOTP.Utilities.SecretKeyWrapper.unwrap [CIPHER-ORDER-02] v=1 code=CIPHER-ORDER-02 ev=i2 obj=Cipher val='' exp='' msg='the Cipher was initialised again after it completed an operation, which the rule does not accept'
- patched_r1: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 1172 métodos (site: 1); ape:sata COMPLETED 1128 métodos

## triliumdroid_24

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1565 métodos (site: 1); ape:sata COMPLETED 1564 métodos (site: 1)
  - eu.fliegendewurst.triliumdroid.sync.ConnectionUtil$reset$2.invokeSuspend [KEYMANAGERFACTORY-NOBS-02] v=1 code=KEYMANAGERFACTORY-NOBS-02 ev=init obj=KeyManagerFactory val='PKIX' exp='a KeyStore this instrumentation observed being loaded' msg='a null KeyStore given to KeyManagerFactory.init requests th
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1521 métodos (site: 1); ape:sata COMPLETED 1651 métodos (site: 1)

## localsendkotlin_23

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1621 métodos (site: 1); ape:sata COMPLETED 1687 métodos (site: 1)
  - io.github.mouse233.localsendkotlin.security.TlsIdentity.loadOrCreateKeyStore [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=store obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1589 métodos (site: 1); ape:sata COMPLETED 1689 métodos (site: 1)

## notallyx_71134

- patched_sites fixado por regra: [['com.philkes.notallyx.utils.IOExtensionsKt', 'sha256Hash']] (PR diff Crustack__NotallyX__1167.diff, app/src/main/java/com/philkes/notallyx/utils/IOExtensions.kt: the three top-level md5Hash overloads are renamed sha256Hash; the governed call `MessageDigest.getInstance("SHA-256")` (was "MD5") is in `fun InputStream.sha256Hash()`. Kotlin top-level functions of IOExtensions.kt compile to class IOExtensionsKt.)
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 3508 métodos (site: 1); ape:sata COMPLETED 4485 métodos (site: 1)
  - com.philkes.notallyx.utils.IOExtensionsKt.md5Hash [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.philkes.notallyx.utils.IOExtensionsKt.md5Hash [MESSAGEDIGEST-ALG-00] v=1 code=MESSAGEDIGEST-ALG-00 ev=update obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.philkes.notallyx.utils.IOExtensionsKt.md5Hash [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=update obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
  - com.philkes.notallyx.utils.IOExtensionsKt.md5Hash [MESSAGEDIGEST-ALG-03] v=1 code=MESSAGEDIGEST-ALG-03 ev=d1 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.philkes.notallyx.utils.IOExtensionsKt.md5Hash [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d1 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4184 métodos (site: 1); ape:sata COMPLETED 4347 métodos (site: 1)

## smsecure_10400_pr89

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 3618 métodos (site: 1); ape:sata COMPLETED 5288 métodos (site: 4)
  - org.jimvixx.smsecure.ConversationListAdapter.<init> [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='SHA1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA1'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1634 métodos (site: 1); ape:sata ERROR 3895 métodos (site: 1); ape:sata COMPLETED 3895 métodos (site: 1)

## freeotp_48_pr524

- app_packages fixado por regra: ['org.fedorahosted.freeotp.debug'] (manifest package of the built base APK, `aapt2 dump badging` (build/freeotp_48_pr524/base.badging: package: name='org.fedorahosted.freeotp.debug' versionCode='48' versionName='2.0.6' platformBuildVersionName='15' platformBuildVersionCode='35' compileSdkVersio))
- corrigido: com as ferramentas do relato original (droidbot dfs/bfs_greedy, 300 s, 3 repetições): corrigido; com monkey/ape: base não reproduz
- base_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 180 métodos; ape:sata COMPLETED 208 métodos
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 191 métodos; ape:sata COMPLETED 212 métodos
- base_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 196 métodos; ape:sata COMPLETED 209 métodos
- patched_r2: unfinished

## freeotp_48_pr521

- app_packages fixado por regra: ['org.fedorahosted.freeotp.debug'] (manifest package of the built base APK, `aapt2 dump badging` (build/freeotp_48_pr521/base.badging: package: name='org.fedorahosted.freeotp.debug' versionCode='48' versionName='2.0.6' platformBuildVersionName='15' platformBuildVersionCode='35' compileSdkVersio))
- sem mudança de produção (o PR só altera testes): pela regra: não corrigido; mobile/src/androidTest/java/org/fedorahosted/freeotp/EncryptedKeyTest.java
- base_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 185 métodos; ape:sata COMPLETED 212 métodos
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 194 métodos; ape:sata COMPLETED 212 métodos

## yapm_202006

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 939 métodos (site: 1); ape:sata COMPLETED 860 métodos (site: 1)
  - de.jepfa.yapm.service.secret.SecretService.getAndroidSecretKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=load obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
  - de.jepfa.yapm.service.secret.SecretService.getAndroidSecretKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 956 métodos (site: 1); ape:sata COMPLETED 890 métodos (site: 1)
  - de.jepfa.yapm.service.secret.SecretService.getAndroidSecretKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=load obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
  - de.jepfa.yapm.service.secret.SecretService.getAndroidSecretKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=gk1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'

## amaradio_159

- patched_sites fixado por regra: [['com.ounben.amaradio.Utils', 'getCacheFile'], ['com.ounben.amaradio.Utils', 'writeFileCache']] (PR diff ounben__AMARadio__122.diff, app/src/main/kotlin/com/ounben/amaradio/Utils.kt: the private extension `String.md5()` is removed and its governed call is inlined as `MessageDigest.getInstance("SHA-256")` into the two suspend functions that called it, getCacheFile and writeFileCache (object Utils; their bodies run in withContext lambdas, Utils$getCacheFile$N / Utils$writeFileCache$N, which classify.site_matches accepts). The governed call is moved, not removed, so the sites are the methods holding it after the patch.)
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4887 métodos (site: 3); ape:sata COMPLETED 5731 métodos (site: 3)
  - com.ounben.amaradio.Utils.md5 [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.ounben.amaradio.Utils.md5 [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.ounben.amaradio.Utils.md5 [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 5509 métodos (site: 16); ape:sata COMPLETED 5643 métodos (site: 16)

## smsecure_10400_pr83

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1682 métodos (site: 1); ape:sata COMPLETED 4710 métodos (site: 1)
  - org.jimvixx.smsecure.crypto.MasterSecretUtil.generateMacSecret [KEYGENERATOR-ALG-00] v=1 code=KEYGENERATOR-ALG-00 ev=gk1 obj=KeyGenerator val='HmacSHA1' exp='AES,HmacSHA256,HmacSHA384,HmacSHA512,HMAC-SHA256,HMAC/SHA256,HMAC-SHA384,HMAC/SHA384,HMAC/SHA512,HMAC-SHA512' msg='expecting on
  - org.jimvixx.smsecure.crypto.MasterSecretUtil.generateMacSecret [KEYGENERATOR-ORDER-02] v=1 code=KEYGENERATOR-ORDER-02 ev=gk1 obj=KeyGenerator val='' exp='' msg='the observed call sequence is not one KeyGeneratorSpec accepts, after a creation the specification refused'
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1543 métodos (site: 1); ape:sata COMPLETED 1614 métodos (site: 1)
  - org.jimvixx.smsecure.crypto.MasterSecretUtil.generateMacSecret [KEYGENERATOR-ORDER-02] v=1 code=KEYGENERATOR-ORDER-02 ev=init obj=KeyGenerator val='' exp='' msg='the observed call sequence is not one KeyGeneratorSpec accepts, after a creation the specification refused'
  - org.jimvixx.smsecure.crypto.MasterSecretUtil.generateMacSecret [KEYGENERATOR-ALG-00] v=1 code=KEYGENERATOR-ALG-00 ev=gk1 obj=KeyGenerator val='HmacSHA1' exp='AES,HmacSHA256,HmacSHA384,HmacSHA512,HMAC-SHA256,HMAC/SHA256,HMAC-SHA384,HMAC/SHA384,HMAC/SHA512,HMAC-SHA512' msg='expecting on
  - org.jimvixx.smsecure.crypto.MasterSecretUtil.generateMacSecret [KEYGENERATOR-ORDER-02] v=1 code=KEYGENERATOR-ORDER-02 ev=gk1 obj=KeyGenerator val='' exp='' msg='the observed call sequence is not one KeyGeneratorSpec accepts, after a creation the specification refused'

## native_1

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2361 métodos (site: 1); ape:sata COMPLETED 2121 métodos (site: 1)
  - io.github.thatsfguy.meshcore.android.storage.KeystoreSecretVault.seal [CIPHER-NOBS-00] v=1 code=CIPHER-NOBS-00 ev=i2 obj=Cipher val='' exp='a key produced by one of the generators the rule names' msg='no generator of the key given to Cipher.init was observed'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2340 métodos (site: 1); ape:sata COMPLETED 2465 métodos (site: 1)

## primftpd_71

- corrigido (chamada removida pelo patch): pela regra: inconclusivo (cobertura); o diff do PR remove do arquivo do site: import javax.crypto.spec.SecretKeySpec;; new SecretKeySpec(key, algorithm));
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4297 métodos (site: 1); ape:sata COMPLETED 4321 métodos (site: 1)
  - org.apache.sshd.common.cipher.BaseRC4Cipher.init [SECRETKEYSPEC-ALG-00] v=1 code=SECRETKEYSPEC-ALG-00 ev=c1 obj=SecretKeySpec val='ARCFOUR' exp='AES,HMACSHA256,HMACSHA384,HMACSHA512,HMAC-SHA256,HMAC/SHA256,HMAC-SHA384,HMAC/SHA384,HMAC/SHA512,HMAC-SHA512' msg='expecting on
  - org.apache.sshd.common.cipher.BaseRC4Cipher.init [SECRETKEYSPEC-NOBS-00] v=1 code=SECRETKEYSPEC-NOBS-00 ev=c1 obj=SecretKeySpec val='' exp='prepared key material' msg='the keyMaterial given to SecretKeySpec(byte[], String) was not observed to have been prepared by a Key.ge
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 4241 métodos; ape:sata COMPLETED 4327 métodos
- base_r2: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 3727 métodos (site: 1); ape:sata COMPLETED 4355 métodos (site: 1)
  - org.apache.sshd.common.cipher.BaseRC4Cipher.init [SECRETKEYSPEC-ALG-00] v=1 code=SECRETKEYSPEC-ALG-00 ev=c1 obj=SecretKeySpec val='ARCFOUR' exp='AES,HMACSHA256,HMACSHA384,HMACSHA512,HMAC-SHA256,HMAC/SHA256,HMAC-SHA384,HMAC/SHA384,HMAC/SHA512,HMAC-SHA512' msg='expecting on
  - org.apache.sshd.common.cipher.BaseRC4Cipher.init [SECRETKEYSPEC-NOBS-00] v=1 code=SECRETKEYSPEC-NOBS-00 ev=c1 obj=SecretKeySpec val='' exp='prepared key material' msg='the keyMaterial given to SecretKeySpec(byte[], String) was not observed to have been prepared by a Key.ge
- patched_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 4272 métodos; ape:sata COMPLETED 4170 métodos

## yourlocalweather_213

- base_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 4845 métodos; ape:sata COMPLETED 4924 métodos
- patched_r1: violação=não (-), coberto=sim (ape:sata); monkey:fast COMPLETED 4437 métodos; ape:sata COMPLETED 5037 métodos (site: 1)
- base_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3825 métodos; ape:sata COMPLETED 5017 métodos
- patched_r2: violação=não (-), coberto=sim (ape:sata); monkey:fast COMPLETED 4467 métodos; ape:sata COMPLETED 4999 métodos (site: 1)
- base_r3: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 4731 métodos; ape:sata COMPLETED 5028 métodos (site: 1)
  - org.thosp.yourlocalweather.settings.fragments.GeneralPreferenceFragment.encryptKey [SECRETKEYFACTORY-ALG-00] v=1 code=SECRETKEYFACTORY-ALG-00 ev=get obj=SecretKeyFactory val='PBKDF2WithHmacSHA1' exp='PBKDF2WithHmacSHA512,PBKDF2WithHmacSHA384,PBKDF2WithHmacSHA256,PBKDF2WithHmacSHA224,PBEWithHmacSHA512AndAES_1
- patched_r3: violação=não (-), coberto=sim (ape:sata); monkey:fast COMPLETED 4748 métodos; ape:sata COMPLETED 4908 métodos (site: 1)

## bookwyrm_16

- base_r1: violação=sim (monkey:fast), coberto=sim (monkey:fast); monkey:fast COMPLETED 322 métodos (site: 1); ape:sata COMPLETED 2 métodos
  - nl.privacydragon.bookwyrm.MainActivity.LogIn [GCMPARAMETERSPEC-NOBS-00] v=1 code=GCMPARAMETERSPEC-NOBS-00 ev=c1 obj=GCMParameterSpec val='' exp='a randomized byte[]' msg='the IV material was not observed to come from a randomized source' vfp='sha256:3fdbb8e3682f4c94'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 323 métodos (site: 1); ape:sata ERROR 533 métodos (site: 1)

## salty_2

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 424 métodos (site: 1); ape:sata COMPLETED 420 métodos (site: 1)
  - com.vauth.salty.utils.HashUtils.encode [CIPHER-NOBS-01] v=1 code=CIPHER-NOBS-01 ev=i2 obj=Cipher val='' exp='a key produced by one of the generators the rule names' msg='the key given to Cipher.init was already reported by the specification that produced i
- patched_r1: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 346 métodos (site: 1); ape:sata COMPLETED 315 métodos

## fread_109000

- corrigido (mensagem reportada sumiu; restam outras já presentes no original): pela regra: não corrigido; a mensagem reportada não aparece em nenhuma repetição do corrigido; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-06) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 9783 métodos (site: 1); ape:sata COMPLETED 18831 métodos (site: 1)
  - com.zhangke.framework.architect.http.GlobalOkHttpClient.buildSSLFactory [SSLCONTEXT-PROTO-00] v=1 code=SSLCONTEXT-PROTO-00 ev=init obj=SSLContext val='SSL' exp='TLSV1.2,TLSV1.3,TLS' msg='expecting one of TLSV1.2,TLSV1.3,TLS but found SSL'
  - com.zhangke.framework.architect.http.GlobalOkHttpClient.buildSSLFactory [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='SSL' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.zhangke.framework.architect.http.GlobalOkHttpClient.buildSSLFactory [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='SSL' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 9948 métodos (site: 1); ape:sata COMPLETED 19181 métodos (site: 1)
  - com.zhangke.framework.architect.http.GlobalOkHttpClient.buildSSLFactory [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.zhangke.framework.architect.http.GlobalOkHttpClient.buildSSLFactory [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de

## orgzlyrevived_292

- patched_sites fixado por regra: [['com.orgzly.android.util.MiscUtils', 'sha256']] (PR diff orgzly-revived__orgzly-android-revived__1121.diff, app/src/main/java/com/orgzly/android/util/MiscUtils.java: `public static long sha1(String s)` is renamed `sha256`; the governed call `MessageDigest.getInstance("SHA-256")` (was "SHA-1") stays in it.)
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4559 métodos (site: 1); ape:sata COMPLETED 6104 métodos (site: 1)
  - com.orgzly.android.util.MiscUtils.sha1 [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - com.orgzly.android.util.MiscUtils.sha1 [MESSAGEDIGEST-ALG-00] v=1 code=MESSAGEDIGEST-ALG-00 ev=update obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SH
  - com.orgzly.android.util.MiscUtils.sha1 [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=update obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
  - com.orgzly.android.util.MiscUtils.sha1 [MESSAGEDIGEST-ALG-03] v=1 code=MESSAGEDIGEST-ALG-03 ev=d1 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - com.orgzly.android.util.MiscUtils.sha1 [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d1 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 5160 métodos (site: 1); ape:sata COMPLETED 5795 métodos (site: 1)

## keypass_1442

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 981 métodos (site: 1); ape:sata COMPLETED 979 métodos (site: 1)
  - com.yogeshpaliyal.common.utils.CryptoManager.encrypt [CIPHER-NOBS-00] v=1 code=CIPHER-NOBS-00 ev=i2 obj=Cipher val='' exp='a key produced by one of the generators the rule names' msg='no generator of the key given to Cipher.init was observed'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 947 métodos (site: 1); ape:sata COMPLETED 979 métodos (site: 1)

## grocy_64_pr1013

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4123 métodos (site: 1); ape:sata COMPLETED 9495 métodos (site: 1)
  - xyz.zedler.patrick.grocy.ssl.ikm.X509KeyStoreFile.load [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=load obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 9212 métodos (site: 1); ape:sata COMPLETED 9572 métodos (site: 1)

## grocy_64_pr1012

- corrigido (mensagem reportada sumiu; restam outras já presentes no original): pela regra: não corrigido; a mensagem reportada não aparece em nenhuma repetição do corrigido; as que restam no site (SSLCONTEXT-NOBS-00, SSLCONTEXT-NOBS-05) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 8803 métodos (site: 1); ape:sata COMPLETED 9327 métodos (site: 1)
  - xyz.zedler.patrick.grocy.web.RequestQueueSingleton$TLSSocketFactory.<init> [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - xyz.zedler.patrick.grocy.web.RequestQueueSingleton$TLSSocketFactory.<init> [SSLCONTEXT-NOBS-04] v=1 code=SSLCONTEXT-NOBS-04 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='a null TrustManager[] given to SSLContext.init requests the platform d
  - xyz.zedler.patrick.grocy.web.RequestQueueSingleton$TLSSocketFactory.<init> [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
  - xyz.zedler.patrick.grocy.web.RequestQueueSingleton$TLSSocketFactory.<init> [SSLCONTEXT-NOBS-00] v=1 code=SSLCONTEXT-NOBS-00 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='the KeyManager[] given to SSLContext.init was not observed coming from a K
  - xyz.zedler.patrick.grocy.web.RequestQueueSingleton$TLSSocketFactory.<init> [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 6233 métodos (site: 1); ape:sata COMPLETED 9022 métodos (site: 1)
  - xyz.zedler.patrick.grocy.web.RequestQueueSingleton$TLSSocketFactory.<init> [SSLCONTEXT-NOBS-00] v=1 code=SSLCONTEXT-NOBS-00 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='the KeyManager[] given to SSLContext.init was not observed coming from a K
  - xyz.zedler.patrick.grocy.web.RequestQueueSingleton$TLSSocketFactory.<init> [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class

## droidscep_7

- app_packages fixado por regra: ['systems.sieber.droid_scep'] (manifest package of the built base APK, `aapt2 dump badging` (build/droidscep_7/base.badging: package: name='systems.sieber.droid_scep' versionCode='7' versionName='1.5' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVersion='36' c))
- base_r1: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 70 métodos; ape:sata COMPLETED 682 métodos (site: 1)
  - systems.sieber.droid_scep.ScepClient.CertReq [KEYPAIRGENERATOR-KEYSIZE-00] v=1 code=KEYPAIRGENERATOR-KEYSIZE-00 ev=initError obj=KeyPairGenerator val='8192' exp='the key size the expert KeyPairGenerator.crysl declares for RSA' msg='invalid key size for algorithm RSA'
  - systems.sieber.droid_scep.ScepClient.CertReq [KEYPAIRGENERATOR-KEYSIZE-00] v=1 code=KEYPAIRGENERATOR-KEYSIZE-00 ev=initError obj=KeyPairGenerator val='1024' exp='the key size the expert KeyPairGenerator.crysl declares for RSA' msg='invalid key size for algorithm RSA'
- patched_r1: violação=não (-), coberto=sim (ape:sata); monkey:fast COMPLETED 79 métodos; ape:sata COMPLETED 1052 métodos (site: 1)

## reader_47

- corrigido (o patch desliga o caminho que executava o site): pela regra: inconclusivo (cobertura); if (!clientCertificateAlias.isNullOrBlank() || trustAllCerts) { [trustAllCerts: true -> false]
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 5278 métodos (site: 5); ape:sata COMPLETED 11447 métodos (site: 5)
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-00] v=1 code=SSLCONTEXT-NOBS-00 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='the KeyManager[] given to SSLContext.init was not observed coming from a K
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 5178 métodos; ape:sata COMPLETED 11280 métodos
- base_r2: violação=sim (ape:sata), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 6841 métodos (site: 2); ape:sata COMPLETED 10066 métodos (site: 5)
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-00] v=1 code=SSLCONTEXT-NOBS-00 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='the KeyManager[] given to SSLContext.init was not observed coming from a K
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 6870 métodos; ape:sata COMPLETED 10529 métodos
- base_r3: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 2383 métodos; ape:sata COMPLETED 6965 métodos (site: 3)
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-00] v=1 code=SSLCONTEXT-NOBS-00 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='the KeyManager[] given to SSLContext.init was not observed coming from a K
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
  - me.ash.reader.infrastructure.di.OkHttpClientModuleKt.setupSsl [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 2658 métodos; ape:sata COMPLETED 7494 métodos

## zsmthandroid_1031

- corrigido (mensagem reportada sumiu; restam outras já presentes no original): pela regra: não corrigido; a mensagem reportada não aparece em nenhuma repetição do corrigido; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-05) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4684 métodos (site: 1); ape:sata COMPLETED 5740 métodos (site: 1)
  - com.zfdang.zsmth_android.newsmth.SMTHHelper.getUnsafeOkHttpClient [SSLCONTEXT-PROTO-00] v=1 code=SSLCONTEXT-PROTO-00 ev=init obj=SSLContext val='SSL' exp='TLSV1.2,TLSV1.3,TLS' msg='expecting one of TLSV1.2,TLSV1.3,TLS but found SSL'
  - com.zfdang.zsmth_android.newsmth.SMTHHelper.getUnsafeOkHttpClient [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='SSL' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.zfdang.zsmth_android.newsmth.SMTHHelper.getUnsafeOkHttpClient [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='SSL' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 3733 métodos (site: 1); ape:sata COMPLETED 4407 métodos (site: 1)
  - com.zfdang.zsmth_android.newsmth.SMTHHelper.getUnsafeOkHttpClient [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.zfdang.zsmth_android.newsmth.SMTHHelper.getUnsafeOkHttpClient [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class

## intertune_96

- não corrigido (a violação reportada passou para outro método do mesmo arquivo): pela regra: inconclusivo (cobertura); com.dd3boh.outertune.widget.WidgetStoreKt.legacySha1WidgetArtworkHash (patched_r1); com.dd3boh.outertune.widget.WidgetStoreKt.legacySha1WidgetArtworkHash (patched_r2)
- base_r1: violação=sim (monkey:fast), coberto=sim (monkey:fast); monkey:fast COMPLETED 17313 métodos (site: 2); ape:sata COMPLETED 9177 métodos
  - com.dd3boh.outertune.widget.WidgetStore.hash [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - com.dd3boh.outertune.widget.WidgetStore.hash [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - com.dd3boh.outertune.widget.WidgetStore.hash [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 17106 métodos; ape:sata COMPLETED 16626 métodos
- base_r2: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 16292 métodos (site: 2); ape:sata COMPLETED 20535 métodos (site: 2)
  - com.dd3boh.outertune.widget.WidgetStore.hash [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - com.dd3boh.outertune.widget.WidgetStore.hash [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - com.dd3boh.outertune.widget.WidgetStore.hash [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 16362 métodos; ape:sata COMPLETED 9274 métodos
- base_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 12123 métodos; ape:sata COMPLETED 6738 métodos
- patched_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 8297 métodos; ape:sata COMPLETED 12827 métodos

## amethyst_464

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 48108 métodos (site: 1); ape:sata COMPLETED 47732 métodos (site: 1)
  - com.vitorpamplona.amethyst.commons.model.preferences.SecretEncryption.decrypt [GCMPARAMETERSPEC-NOBS-00] v=1 code=GCMPARAMETERSPEC-NOBS-00 ev=c1 obj=GCMParameterSpec val='' exp='a randomized byte[]' msg='the IV material was not observed to come from a randomized source' vfp='sha256:e8688c50341180de'
  - com.vitorpamplona.amethyst.commons.model.preferences.SecretEncryption.decrypt [GCMPARAMETERSPEC-NOBS-00] v=1 code=GCMPARAMETERSPEC-NOBS-00 ev=c1 obj=GCMParameterSpec val='' exp='a randomized byte[]' msg='the IV material was not observed to come from a randomized source' vfp='sha256:a595279ee6797558'
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 44454 métodos (site: 1); ape:sata COMPLETED 43471 métodos (site: 1)
  - com.vitorpamplona.amethyst.commons.model.preferences.SecretEncryption.decrypt [GCMPARAMETERSPEC-NOBS-00] v=1 code=GCMPARAMETERSPEC-NOBS-00 ev=c1 obj=GCMParameterSpec val='' exp='a randomized byte[]' msg='the IV material was not observed to come from a randomized source' vfp='sha256:5fd63761e9da3a75'
  - com.vitorpamplona.amethyst.commons.model.preferences.SecretEncryption.decrypt [GCMPARAMETERSPEC-NOBS-00] v=1 code=GCMPARAMETERSPEC-NOBS-00 ev=c1 obj=GCMParameterSpec val='' exp='a randomized byte[]' msg='the IV material was not observed to come from a randomized source' vfp='sha256:eaa7453d46f6ac0b'

## whobird_54

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 450 métodos (site: 1); ape:sata COMPLETED 38 métodos (site: 1)
  - org.tensorflow.lite.examples.soundclassifier.Downloader.lambda$downloadModels$4 [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - org.tensorflow.lite.examples.soundclassifier.Downloader.lambda$downloadModels$4 [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - org.tensorflow.lite.examples.soundclassifier.Downloader.lambda$downloadModels$4 [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 441 métodos (site: 1); ape:sata ERROR 38 métodos (site: 1)

## rssreader_120

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1675 métodos (site: 1); ape:sata COMPLETED 1872 métodos (site: 1)
  - tools.FileManager.writeFile [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - tools.FileManager.writeFile [MESSAGEDIGEST-ALG-00] v=1 code=MESSAGEDIGEST-ALG-00 ev=update obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - tools.FileManager.writeFile [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=update obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
  - tools.FileManager.writeFile [MESSAGEDIGEST-ALG-03] v=1 code=MESSAGEDIGEST-ALG-03 ev=d1 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - tools.FileManager.writeFile [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d1 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1650 métodos (site: 1); ape:sata COMPLETED 1773 métodos (site: 1)

## redreader_117

- app_packages fixado por regra: ['org.quantumbadger.redreader'] (manifest package of the built base APK, `aapt2 dump badging` (build/redreader_117/base.badging: package: name='org.quantumbadger.redreader' versionCode='118' versionName='1.26' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVersion='))
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1722 métodos (site: 1); ape:sata COMPLETED 2101 métodos (site: 1)
  - org.quantumbadger.redreader.common.General.sha1 [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - org.quantumbadger.redreader.common.General.sha1 [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='SHA-1' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found SHA-1'
  - org.quantumbadger.redreader.common.General.sha1 [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1758 métodos (site: 1); ape:sata COMPLETED 3083 métodos (site: 1)

## owncloudnewsreader_196

- detector_code/message fixado por regra: sentinels matching no report (deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False)
- apk_glob fixado por regra: News-Android-App-oss-debug.apk (data/raw/exp_novo/dataset.csv row de.luhmer.owncloudnewsreader_196.apk: head_build_variant 'News-Android-App-oss-debug.apk' (head_build_task 'assembleDebug', head_pinned_commit '3c4c4d5e013827f914d029227e68689f79971aa3'); pr_cases.json gave None with pipeline APKs ['News-Android-App-dev-debug.apk', 'News-Android-App-oss-debug.apk'])
- app_packages fixado por regra: ['de.luhmer.owncloudnewsreader'] (manifest package of the built base APK (build/owncloudnewsreader_196/base.badging: package: name='de.luhmer.owncloudnewsreader' versionCode='197' versionName='0.9.9.96' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVers))
- não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação): pela regra: base não reproduz; o site foi executado no original (base_r1, base_r2, base_r3) sem violação; a mensagem do relato original ("unknown") não aparece em nenhuma execução da validação
- base_r1: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 1526 métodos (site: 1); ape:sata ERROR 0 métodos; ape:sata ERROR 0 métodos
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2415 métodos (site: 1); ape:sata COMPLETED 1513 métodos (site: 1)
- base_r2: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1440 métodos (site: 1); ape:sata COMPLETED 1527 métodos (site: 1)
- patched_r2: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1529 métodos (site: 1); ape:sata COMPLETED 1527 métodos (site: 1)
- base_r3: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1060 métodos (site: 1); ape:sata COMPLETED 1527 métodos (site: 1)
- patched_r3: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1532 métodos (site: 1); ape:sata COMPLETED 1527 métodos (site: 1)

## crazyfliecontrol2_22

- detector_code/message fixado por regra: sentinels matching no report (deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False)
- app_packages fixado por regra: ['se.bitcraze.crazyfliecontrol2'] (manifest package of the built base APK (build/crazyfliecontrol2_22/base.badging: package: name='se.bitcraze.crazyfliecontrol2' versionCode='22' versionName='0.7.6' platformBuildVersionName='13' platformBuildVersionCode='33' compileSdkVersion))
- corrigido (chamada removida pelo patch): pela regra: inconclusivo (cobertura); o diff do PR remove do arquivo do site: import javax.net.ssl.SSLContext;; SSLContext context = SSLContext.getInstance("TLS");
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 566 métodos (site: 1); ape:sata COMPLETED 541 métodos (site: 1)
  - se.bitcraze.crazyfliecontrol.bootloader.TLSSocketFactory.<init> [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - se.bitcraze.crazyfliecontrol.bootloader.TLSSocketFactory.<init> [SSLCONTEXT-NOBS-04] v=1 code=SSLCONTEXT-NOBS-04 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='a null TrustManager[] given to SSLContext.init requests the platform d
  - se.bitcraze.crazyfliecontrol.bootloader.TLSSocketFactory.<init> [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 508 métodos; ape:sata COMPLETED 570 métodos
- base_r2: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 532 métodos (site: 1); ape:sata COMPLETED 543 métodos (site: 1)
  - se.bitcraze.crazyfliecontrol.bootloader.TLSSocketFactory.<init> [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - se.bitcraze.crazyfliecontrol.bootloader.TLSSocketFactory.<init> [SSLCONTEXT-NOBS-04] v=1 code=SSLCONTEXT-NOBS-04 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='a null TrustManager[] given to SSLContext.init requests the platform d
  - se.bitcraze.crazyfliecontrol.bootloader.TLSSocketFactory.<init> [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 555 métodos; ape:sata COMPLETED 564 métodos

## dsub2000_217

- detector_code/message fixado por regra: sentinels matching no report (deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False)
- apk_glob fixado por regra: DSub2000_5.7.4-floss-debug.apk (data/raw/exp_novo/dataset.csv row github.paroj.dsub2000_217.apk: head_build_variant 'DSub2000_5.7.4-floss-debug.apk' (head_build_task 'assembleDebug', head_pinned_commit '474f6ca847649220f7965fe95f1285d1d8b4ca64'); pr_cases.json gave None with pipeline APKs ['debug-DSub2000_5.7.4-floss-debug.apk', 'debug-DSub2000_5.7.4-google-debug.apk'])
- app_packages fixado por regra: ['github.paroj.dsub2000'] (manifest package of the built base APK (build/dsub2000_217/base.badging: package: name='github.paroj.dsub2000' versionCode='217' versionName='5.7.4' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVersion='36' c))
- não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): pela regra: não corrigido; a violação reportada não aparece em nenhuma repetição dos dois APKs; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-05) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2371 métodos (site: 3); ape:sata COMPLETED 2302 métodos (site: 3)
  - github.paroj.dsub2000.service.RESTMusicService.<init> [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - github.paroj.dsub2000.service.RESTMusicService.<init> [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2239 métodos (site: 4); ape:sata COMPLETED 3438 métodos (site: 4)
  - github.paroj.dsub2000.service.RESTMusicService.<init> [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - github.paroj.dsub2000.service.RESTMusicService.<init> [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class

## feeder_4025

- detector_code/message fixado por regra: sentinels matching no report (deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False)
- app_packages fixado por regra: ['com.nononsenseapps.feeder.debug'] (manifest package of the built base APK (build/feeder_4025/base.badging: package: name='com.nononsenseapps.feeder.debug' versionCode='4092' versionName='2.23.0' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVe))
- não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): pela regra: não corrigido; a violação reportada não aparece em nenhuma repetição dos dois APKs; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-05, SSLCONTEXT-NOBS-06) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 8644 métodos (site: 5); ape:sata COMPLETED 10709 métodos (site: 5)
  - com.nononsenseapps.jsonfeed.OkHttpBuilderExtensionsKt.trustAllCerts [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.nononsenseapps.jsonfeed.OkHttpBuilderExtensionsKt.trustAllCerts [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
  - com.nononsenseapps.jsonfeed.OkHttpBuilderExtensionsKt.trustAllCerts [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 9527 métodos (site: 5); ape:sata COMPLETED 10101 métodos (site: 5)
  - com.nononsenseapps.jsonfeed.OkHttpBuilderExtensionsKt.trustAllCerts [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.nononsenseapps.jsonfeed.OkHttpBuilderExtensionsKt.trustAllCerts [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
  - com.nononsenseapps.jsonfeed.OkHttpBuilderExtensionsKt.trustAllCerts [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de

## syncadapter_20700

- detector_code/message fixado por regra: sentinels matching no report (deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False)
- apk_glob fixado por regra: app-debug.apk (data/raw/exp_novo/dataset.csv row com.etesync.syncadapter_20700.apk: head_build_variant 'app-debug.apk' (head_build_task 'assembleDebug', head_pinned_commit 'e1de5c281729e656ec755ee41f140ffe6411ade0'); pr_cases.json gave 'debug-app-debug.apk' with pipeline APKs ['debug-app-debug.apk'])
- app_packages fixado por regra: ['com.etesync.syncadapter'] (manifest package of the built base APK (build/syncadapter_20700/base.badging: package: name='com.etesync.syncadapter' versionCode='20700' versionName='2.7.0' platformBuildVersionName='16' platformBuildVersionCode='36' compileSdkVersion='3))
- não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): pela regra: não corrigido; a violação reportada não aparece em nenhuma repetição dos dois APKs; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-05, SSLCONTEXT-NOBS-06) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1972 métodos (site: 1); ape:sata COMPLETED 2049 métodos (site: 1)
  - com.etesync.syncadapter.HttpClient$Builder.build [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.etesync.syncadapter.HttpClient$Builder.build [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
  - com.etesync.syncadapter.HttpClient$Builder.build [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1949 métodos (site: 1); ape:sata COMPLETED 1999 métodos (site: 1)
  - com.etesync.syncadapter.HttpClient$Builder.build [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.etesync.syncadapter.HttpClient$Builder.build [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
  - com.etesync.syncadapter.HttpClient$Builder.build [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de

## giggity_769

- detector_code/message fixado por regra: sentinels matching no report (deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False)
- apk_glob fixado por regra: app-debug.apk (data/raw/exp_novo/dataset.csv row net.gaast.giggity_769.apk: head_build_variant 'app-debug.apk' (head_build_task 'assembleDebug', head_pinned_commit '122b04236191cff9fba621fee9a176410ab8062f'); pr_cases.json gave 'debug-app-debug.apk' with pipeline APKs ['debug-app-debug.apk'])
- app_packages fixado por regra: ['net.gaast.giggity'] (manifest package of the built base APK (build/giggity_769/base.badging: package: name='net.gaast.giggity' versionCode='769' versionName='3.0.1' platformBuildVersionName='15' platformBuildVersionCode='35' compileSdkVersion='35' compi))
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 962 métodos (site: 1); ape:sata COMPLETED 1121 métodos (site: 1)
  - net.gaast.giggity.Schedule$Item.hashCode [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - net.gaast.giggity.Schedule$Item.hashCode [MESSAGEDIGEST-ALG-00] v=1 code=MESSAGEDIGEST-ALG-00 ev=update obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - net.gaast.giggity.Schedule$Item.hashCode [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=update obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
  - net.gaast.giggity.Schedule$Item.hashCode [MESSAGEDIGEST-ALG-03] v=1 code=MESSAGEDIGEST-ALG-03 ev=d1 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - net.gaast.giggity.Schedule$Item.hashCode [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d1 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 925 métodos (site: 1); ape:sata COMPLETED 1004 métodos (site: 1)

## wifianalyzer_71

- detector_code/message fixado por regra: sentinels matching no report (deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False)
- apk_glob fixado por regra: app-debug.apk (data/raw/exp_novo/dataset.csv row com.vrem.wifianalyzer_71.apk: head_build_variant 'app-debug.apk' (head_build_task 'assembleDebug', head_pinned_commit '131c2fae4f86e54aa71d87e522a3b4874bb4e68a'); pr_cases.json gave None with pipeline APKs [])
- build_task fixado por regra: assembleDebug (dataset.csv head_build_task 'assembleDebug'; the pipeline's validation task was 'assembleDebug' (success))
- app_packages fixado por regra: ['com.vrem.wifianalyzer.BETA'] (manifest package of the built base APK (build/wifianalyzer_71/base.badging: package: name='com.vrem.wifianalyzer.BETA' versionCode='72' versionName='3.3.1.5-BETA' platformBuildVersionName='17' platformBuildVersionCode='37' compileSdkVer))
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2340 métodos (site: 1); ape:sata COMPLETED 2317 métodos (site: 1)
  - com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterData.calculateChildType [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterData.calculateChildType [MESSAGEDIGEST-ALG-00] v=1 code=MESSAGEDIGEST-ALG-00 ev=update obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterData.calculateChildType [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=update obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
  - com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterData.calculateChildType [MESSAGEDIGEST-ALG-03] v=1 code=MESSAGEDIGEST-ALG-03 ev=d1 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterData.calculateChildType [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d1 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2020 métodos (site: 1); ape:sata COMPLETED 2305 métodos (site: 1)

## messenger_4545

- status fixado por regra: accepted (GitHub API 2026-11-18: merged true, merged_at 2026-11-17T23:39:24Z)
- build_task fixado por regra: :app:assembleWebsiteDebug (outputs/rvsec_tls_20261015/run.log: 'network.loki.messenger.fdroid.debug_4545.apk  https://github.com/session-foundation/session-android @ dd6c41a0c6df  task :app:assembleWebsiteDebug' and '[1/1] network.loki.messenger.fdroid: success, apk_count=1' (the queue build that produced the analysed APK))
- apk_glob fixado por regra: None (no APK name recorded; pick rule 3 (clarification 3): the only APK of the build)
- corrigido (mensagem reportada sumiu; restam outras já presentes no original): pela regra: não corrigido; a mensagem reportada não aparece em nenhuma repetição do corrigido; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-05) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 12476 métodos (site: 5); ape:sata COMPLETED 17745 métodos (site: 5)
  - org.thoughtcrime.securesms.api.http.HttpClientsKt.createRegularNodeOkHttpClient [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='SSL' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - org.thoughtcrime.securesms.api.http.HttpClientsKt.createRegularNodeOkHttpClient [SSLCONTEXT-PROTO-00] v=1 code=SSLCONTEXT-PROTO-00 ev=init obj=SSLContext val='SSL' exp='TLSV1.2,TLSV1.3,TLS' msg='expecting one of TLSV1.2,TLSV1.3,TLS but found SSL'
  - org.thoughtcrime.securesms.api.http.HttpClientsKt.createRegularNodeOkHttpClient [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='SSL' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
- patched_r1: violação=sim (ape:sata), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 9937 métodos (site: 2); ape:sata COMPLETED 10630 métodos (site: 3)
  - org.thoughtcrime.securesms.api.http.HttpClientsKt.createRegularNodeOkHttpClient [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - org.thoughtcrime.securesms.api.http.HttpClientsKt.createRegularNodeOkHttpClient [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class

## httpshortcuts_1104060001

- finding identity fixado por regra: ['ch.rmy.android.http_shortcuts.http.HttpClientFactory', 'configureTLS', 'SSLContextSpec', 'expecting one of {TLSv1.2, TLSv1.3} but found TLS.'] (data/processed/errors_amostragem.csv row: ch.rmy.android.http_shortcuts_1104060001.apk,1,180,ape,12,SSLContextSpec,ch.rmy.android.http_shortcuts.http.HttpClientFactory,configureTLS,expecting one of {TLSv1.2, TLSv1.3} but found TLS.; PR head branch 'fix/configuretls-sslcontextspec-20260809143627-5f4e475a' (configureTLS, SSLContextSpec))
- build_task/apk_glob fixado por regra: ['assembleDebug', 'app-universal-debug.apk'] (data/raw/exp_novo/dataset.csv row ch.rmy.android.http_shortcuts_1104060001.apk: head_build_task / head_build_variant)
- sites fixado por regra: [['ch.rmy.android.http_shortcuts.http.HttpClientFactory', 'configureTLS']] (the finding's site in both APKs; the PR diff changes TLSEnabledSSLSocketFactory.withTLS (the socket factory configureTLS installs), not the site's file)
- detector_code/message fixado por regra: sentinels matching no report (old-format message with no current detector row: spec-level criterion as in deviation 5)
- app_packages fixado por regra: ['ch.rmy.android.http_shortcuts.debug'] (manifest package of the built base APK (build/httpshortcuts_1104060001/base.badging))
- não verificável (a violação reportada não aparece em nenhum dos dois APKs; restam outras já presentes no original): pela regra: não corrigido; a violação reportada não aparece em nenhuma repetição dos dois APKs; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-06) já apareciam no original
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=não (-); monkey:fast COMPLETED 2268 métodos; monkey:fast COMPLETED 2268 métodos; ape:sata COMPLETED 2673 métodos
  - ch.rmy.android.http_shortcuts.http.HttpClientFactory.configureTLS [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - ch.rmy.android.http_shortcuts.http.HttpClientFactory.configureTLS [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de
- patched_r1: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 2415 métodos; monkey:fast COMPLETED 2415 métodos; ape:sata COMPLETED 6337 métodos (site: 1)
  - ch.rmy.android.http_shortcuts.http.HttpClientFactory.configureTLS [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - ch.rmy.android.http_shortcuts.http.HttpClientFactory.configureTLS [SSLCONTEXT-NOBS-06] v=1 code=SSLCONTEXT-NOBS-06 ev=init obj=SSLContext val='TLS' exp='a SecureRandom this instrumentation observed being created' msg='a null SecureRandom given to SSLContext.init requests the platform de

## nextcloudcookbook_62

- build_task/apk_glob fixado por regra: ['assembleDebug', 'app-googlePlay-debug.apk'] (data/raw/exp_novo/dataset.csv row de.lukasneugebauer.nextcloudcookbook_62.apk: head_build_task 'assembleDebug', head_build_variant 'app-googlePlay-debug.apk' (head_pinned_commit 'cf063f9be44143013ff44ee3ce4fdd768aca113e'); the source deviation 5 used)
- finding identity fixado por regra: ['4c1c74bcbc8f', 'SSLCONTEXT-PROTO-00'] (pr_cases.json: the inventory's finding 4c1c74bcbc8f and the only detector row at the site and spec)
- app_packages fixado por regra: ['de.lukasneugebauer.nextcloudcookbook.debug'] (manifest package of the built base APK (build/nextcloudcookbook_62/base.badging))
- corrigido (mensagem reportada sumiu; restam outras já presentes no original): pela regra: não corrigido; a mensagem reportada não aparece em nenhuma repetição do corrigido; as que restam no site (SSLCONTEXT-NOBS-03, SSLCONTEXT-NOBS-05) já apareciam no original
- base_r1: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 3801 métodos; monkey:fast COMPLETED 3801 métodos; ape:sata COMPLETED 4370 métodos (site: 4)
  - de.lukasneugebauer.nextcloudcookbook.core.util.OkHttpClientProvider.configureTrustAllCertificates [SSLCONTEXT-PROTO-00] v=1 code=SSLCONTEXT-PROTO-00 ev=init obj=SSLContext val='SSL' exp='TLSV1.2,TLSV1.3,TLS' msg='expecting one of TLSV1.2,TLSV1.3,TLS but found SSL'
  - de.lukasneugebauer.nextcloudcookbook.core.util.OkHttpClientProvider.configureTrustAllCertificates [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='SSL' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - de.lukasneugebauer.nextcloudcookbook.core.util.OkHttpClientProvider.configureTrustAllCertificates [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='SSL' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
- patched_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4062 métodos (site: 4); ape:sata COMPLETED 4290 métodos (site: 4)
  - de.lukasneugebauer.nextcloudcookbook.core.util.OkHttpClientProvider.configureTrustAllCertificates [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - de.lukasneugebauer.nextcloudcookbook.core.util.OkHttpClientProvider.configureTrustAllCertificates [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class

## metadataremover_20020

- build_task/apk_glob fixado por regra: ['assembleDebug', 'app-armeabi-v7a-debug.apk'] (data/raw/exp_novo/dataset.csv row rocks.poopjournal.metadataremover_20020.apk: head_build_task 'assembleDebug', head_build_variant 'app-armeabi-v7a-debug.apk' (head_pinned_commit 'b09bc5a97c922c25c9c56d3c97a4d343712ce808'); the source deviation 5 used)
- app_packages fixado por regra: None (pending: from `aapt2 dump badging` of the base APK once tools/build_prs.sh builds it)
- não compilou: base: não compilou; patched: not built; Gradle: Execution failed for task ':app:hiltJavaCompileDebug' (registered by plugin 'com.google.dagger.hilt.android'). > dagger/spi/internal/shaded/androidx/room/compiler/processing/javac/JavacBasicAnnotationProcessor
- fim do log: `34 actionable tasks: 9 executed, 22 from cache, 3 up-to-date`

## passnotes_11700_pr347

- finding identity fixado por regra: ['com.ivanovsky.passnotes.data.repository.file.webdav.HttpClientFactory', 'createHttpClient', 'SSLContextSpec', 'expecting one of {TLSv1.2, TLSv1.3} but found TLS.'] (pr_cases.json pr_message (the PR's finding, old-format TLS-protocol message); the 14/11 validation used another finding (SSLCONTEXT-NOBS-05) and another patch (patches/passnotes_11700.diff))
- detector_code/message fixado por regra: sentinels matching no report (old-format message with no current detector row: spec-level criterion as in deviation 5)
- não verificável (o original executou o site sem violação; a mensagem relatada não é emitida pela versão do RVSec usada na validação): pela regra: base não reproduz; o site foi executado no original (base_r2) sem violação; a mensagem do relato original ("expecting one of {TLSv1.2, TLSv1.3} but found TLS.") não aparece em nenhuma execução da validação; com a imagem rvandroid:0.9.4 (6 execuções): base_r1: não executou o site; base_r2: não executou o site; base_r3: não executou o site; patched_r1: não executou o site; patched_r2: não executou o site; patched_r3: não executou o site
- base_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3553 métodos; ape:sata COMPLETED 4154 métodos
- patched_r1: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 3516 métodos (site: 1); ape:sata COMPLETED 3721 métodos
- base_r2: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 3271 métodos (site: 1); ape:sata ERROR 2705 métodos
- patched_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3477 métodos; ape:sata COMPLETED 2485 métodos

## summit_355

- apk_glob fixado por regra: None (outputs/rvsec_misuse5_20261017/build.log: '[1/1] com.idunnololz.summit.fdroid: success, apk_count=1' for :app:assembleFdroidDebug; pick rule 3: the only APK)
- patched_sites fixado por regra: [['com.idunnololz.summit.cache.SimpleDiskCache', 'sha256']] (PR diff idunnololz__summit__54.diff, SimpleDiskCache.kt: `private fun md5(s: String)` is renamed `sha256`; the governed call `MessageDigest.getInstance("SHA-256")` (was "MD5") and its update/digest stay in it (pre-registered rule for a renamed method))
- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 16755 métodos (site: 1); ape:sata COMPLETED 18082 métodos (site: 1)
  - com.idunnololz.summit.cache.SimpleDiskCache.md5 [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.idunnololz.summit.cache.SimpleDiskCache.md5 [MESSAGEDIGEST-ALG-00] v=1 code=MESSAGEDIGEST-ALG-00 ev=update obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.idunnololz.summit.cache.SimpleDiskCache.md5 [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=update obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
  - com.idunnololz.summit.cache.SimpleDiskCache.md5 [MESSAGEDIGEST-ALG-03] v=1 code=MESSAGEDIGEST-ALG-03 ev=d1 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.idunnololz.summit.cache.SimpleDiskCache.md5 [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d1 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 15758 métodos (site: 1); ape:sata COMPLETED 18734 métodos (site: 1)

## bitbanana_79

- inconclusivo (cobertura): desvio 6: 4 repetições juntas (inconclusivo (cobertura)); rótulo de 14/11 com 3 repetições: inconclusivo (cobertura); com a imagem rvandroid:0.9.4 (6 execuções): base_r1: não executou o site; base_r2: não executou o site; base_r3: não executou o site; patched_r1: não executou o site; patched_r2: não executou o site; patched_r3: não executou o site
- base_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 2986 métodos; ape:sata ERROR 3813 métodos; ape:sata COMPLETED 3813 métodos
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3013 métodos; ape:sata COMPLETED 5218 métodos
- base_r2: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 2532 métodos; ape:sata COMPLETED 3945 métodos (site: 1)
  - app.michaelwuensch.bitbanana.util.UtilFunctions.encodePbkdf2 [PBEKEYSPEC-CONSTR-00] v=1 code=PBEKEYSPEC-CONSTR-00 ev=c1 obj=PBEKeySpec val='5000' exp='>= 10000' msg='the third argument should be >= 10000'
  - app.michaelwuensch.bitbanana.util.UtilFunctions.encodePbkdf2 [PBEKEYSPEC-NOBS-01] v=1 code=PBEKEYSPEC-NOBS-01 ev=c1 obj=PBEKeySpec val='' exp='a randomized byte[]' msg='the second argument was not observed to come from a randomized source' vfp='sha256:9ffdc54e15abd290'
- patched_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 2719 métodos; ape:sata COMPLETED 4990 métodos
- base_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 2764 métodos; ape:sata COMPLETED 5026 métodos
- patched_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3039 métodos; ape:sata COMPLETED 5196 métodos
- base_r4: violação=sim (monkey:fast), coberto=sim (monkey:fast); monkey:fast COMPLETED 3116 métodos (site: 1); ape:sata COMPLETED 3451 métodos
  - app.michaelwuensch.bitbanana.util.UtilFunctions.encodePbkdf2 [PBEKEYSPEC-CONSTR-00] v=1 code=PBEKEYSPEC-CONSTR-00 ev=c1 obj=PBEKeySpec val='5000' exp='>= 10000' msg='the third argument should be >= 10000'
  - app.michaelwuensch.bitbanana.util.UtilFunctions.encodePbkdf2 [PBEKEYSPEC-NOBS-01] v=1 code=PBEKEYSPEC-NOBS-01 ev=c1 obj=PBEKeySpec val='' exp='a randomized byte[]' msg='the second argument was not observed to come from a randomized source' vfp='sha256:7bbb90e3051353e8'
- patched_r4: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3153 métodos; ape:sata COMPLETED 5340 métodos
