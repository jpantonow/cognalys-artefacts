# Classificação dos 21 patches validados como "corrigido"

Os 21 casos são os 17 PRs de `results_prs.json` e os 4 validados em 14/11. Os diffs estão em `c:\Users\Antonow\Documents\tcc_v3\outputs\dynamic_validation_20261114\pr_api_cache\`. O analista e o cético concordaram em todos os casos e não houve correção de classe.

## (1) Tabela por caso

| caso | PR | spec | classe | mesma spec em site novo | comportamento mudou | evidência (uma linha) |
|---|---|---|---|---|---|---|
| cometotp_1 | CometOTP#114 | CipherSpec | B | não | sim (só o ciclo de vida do objeto) | `- mCipher = Cipher.getInstance(...)` no construtor; `+ val cipher = Cipher.getInstance(...)` + `init` em wrap e em unwrap (diff:19, 32-34, 46-50). unwrap foi coberto no patched (logcat monkey:fast:1082) sem CipherSpec |
| native_1 | meshcore-mobile-app#10 | CipherSpec (causa: KeyGenerator ORDER) | B | não | não | `val kg = KeyGenerator.getInstance(...)` sai de antes do laço e entra no laço de tiers (diff:9 → :17). O CIPHER-NOBS-00 em seal some porque o monitor passa a ver o gerador |
| triliumdroid_24 | TriliumDroid#177 | KeyManagerFactorySpec | C | não | não | `-val keyManagerFactory = KeyManagerFactory.getInstance(...)` / `-keyManagerFactory.init(null, null)` (diff:17-19). Era código morto; a mesma invokeSuspend roda no patched (SSLContextSpec em kt:228) |
| primftpd_71 | prim-ftpd#453 | SecretKeySpecSpec | A (com parte C) | não | sim | `- avail.add(new ARCFOUR128.Factory());` / `ARCFOUR256`; `BaseRC4Cipher.init` → `throw new UnsupportedOperationException`. Há 0 relatos ARCFOUR no patched r1/r2 |
| localsendkotlin_23 | localsend-kotlin#58 | KeyStoreSpec | B | não | não | `- store.setKeyEntry(...)` → `+ store.setEntry(ALIAS, KeyStore.PrivateKeyEntry(...), PasswordProtection(...))` (diff:217-222) |
| bookwyrm_16 | Bookwyrm_Android#29 | GCMParameterSpecSpec | A | não | sim | `- new GCMParameterSpec(128, magicCode.getBytes())` (java.util.Random) → `+ new SecureRandom().nextBytes(iv)` (diff:40-44) |
| notallyx_71134 | NotallyX#1167 | MessageDigestSpec | A | não | sim | `getInstance("MD5")` → `getInstance("SHA-256")` (diff:22-24). sha256Hash coberto no patched |
| smsecure_10400_pr89 | ShortMessageSecure#89 | MessageDigestSpec | A | não | sim | `getInstance("SHA1")` → `getInstance("SHA-256")` (diff:9-10). `<init>` coberto (monkey:fast.logcat:664) |
| salty_2 | salty-app#6 | CipherSpec (causa: PBEKeySpec) | A | não | sim | Sal fixo `appSalt + userSalt` → `SecureRandom().nextBytes(kdfSalt)` por mensagem (diff:21-29, 134) |
| orgzlyrevived_292 | orgzly-android-revived#1121 | MessageDigestSpec | A | não | sim | `getInstance("SHA-1")` → `getInstance("SHA-256")` (diff:27-28). sha256 coberto nos dois explorers |
| keypass_1442 | KeyPass#1252 | CipherSpec | B | não | não | `keyStore.getEntry(...).secretKey` → `keyStore.getKey(KEY_ALIAS, null)` (diff:9-15). O monitor passa a ver quem produziu a chave |
| grocy_64_pr1013 | grocy-android#1013 | KeyStoreSpec | B | não | sim | `- keyStore.load(null, null);` incondicional e `- save();` saem; agora é um único load por objeto (diff:18-31, 52-54) |
| droidscep_7 | Droid_SCEP#2 | KeyPairGeneratorSpec | A | não | sim | `keyGen.initialize(isKeyLen)` → `initialize(verifyRsaKeySize(isKeyLen))`, que só aceita 3072 e 4096. Na base havia 1024/8192 (ape:sata.logcat:693, :714) |
| whobird_54 | whoBIRD#144 | MessageDigestSpec | A | não | sim | 4× `getInstance("MD5")` → `getInstance("SHA-256")` e hashes de referência novos (diff:13-15, 30-91) |
| amaradio_159 | AMARadio#122 | MessageDigestSpec | A | não | sim | `String.md5()` removida; `MessageDigest.getInstance("SHA-256")` direto em getCacheFile e writeFileCache (diff:9-31) |
| rssreader_120 | rssreader#4 | MessageDigestSpec | A | não | sim | writeFile: `getInstance("MD5")` → `getInstance("SHA-256")` (diff:33-34). MD5 fica só em readFile para checksums antigos |
| redreader_117 | RedReader#1373 | MessageDigestSpec | A | não | sim | `getInstance("SHA-1")` → `getInstance("SHA-256")`. Site coberto (ape:sata.logcat:1511) |
| trafficlight_38 | traffic-light#238 | KeyStoreSpec | B | não | não | `getEntry(key, null) as? SecretKeyEntry)?.secretKey` → `getKey(key, null) as? SecretKey` (diff:9-10). 3 reps por lado |
| tokn_19 | tokn#138 | KeyStoreSpec | B | não | não | `(keystore.getEntry(...) as SecretKeyEntry).secretKey` → `keystore.getKey(KEY_ALIAS, null) as SecretKey`. 3 reps por lado |
| networksurvey_114 | android-network-survey#154 | KeyStoreSpec | B | não | sim, segundo o analista; o cético diz que é só sequência | `keyStore.getEntry(...)` → `keyStore.getKey(KEY_ALIAS, null) as? SecretKey` (diff:9-12). Runs *_nobb_* r1-r3 |
| untis_29198884 | BetterUntis#515 | MessageDigestSpec | A | não | sim | `getInstance("MD5")` → `getInstance("SHA-256")` (diff:9-12). Coberto em patched r1/r2; r3 não chegou ao site |

## (2) Contagens

**Por classe:**
- A (muda valor ou material): 12.
- B (muda sequência ou API como a especificação pede, com o mesmo material): 8.
- C (remove o caminho): 1.
- D (realoca ou esconde): 0.

**Achados do desafio:**
- hiding=true: 0.
- Divergência de classe entre analista e cético: 0.
- Mesma spec em site novo: 0 de 21.

**Comportamento, como cada analista registrou:** 15 sim e 6 não. Os 6 "não" são native_1, triliumdroid_24, localsendkotlin_23, keypass_1442, trafficlight_38 e tokn_19.

**Inconsistência nesse registro:** networksurvey_114 tem o mesmo patch que trafficlight_38 e tokn_19 (getEntry → getKey), mas o analista dele marcou "sim". Com o mesmo critério nos três, ficam 14 sim e 7 não. O "sim" de cometotp_1 também vale só para o ciclo de vida do objeto: wrap e unwrap devolvem o mesmo resultado.

**Por spec:**
- MessageDigestSpec: 8, todos A.
- KeyStoreSpec: 5, todos B.
- CipherSpec: 4 (3 B e 1 A).
- KeyManagerFactorySpec: 1, C.
- SecretKeySpecSpec: 1, A.
- GCMParameterSpecSpec: 1, A.
- KeyPairGeneratorSpec: 1, A.

## (3) Casos marcados D, hiding ou site novo

Nenhum. Nenhum caso foi classificado D, nenhum teve hiding=true e nenhum relatou a mesma spec num site novo. Os pontos abaixo não mudam a classe, mas o artigo precisa deles para não exagerar.

**B sem ganho de segurança (correção só de conformidade):**
- localsendkotlin_23, trafficlight_38, tokn_19, networksurvey_114 e keypass_1442: a própria especificação descreve setKeyEntry e getEntry/SecretKeyEntry como uso documentado ou sem impacto (KeyStoreSpec.json:130 e :223). O achado de keypass era uma lacuna de rastreamento.
- native_1: mesma situação, sem ganho de segurança.

**Silêncio porque o monitor passou a ver mais, não menos:** em native_1, keypass_1442, trafficlight_38 e tokn_19, o CIPHER-NOBS-00 some porque getKey ou o KeyGenerator novo emitem o predicado generatedKey. Esse é o argumento direto contra ocultação.

**C com efeito nulo:** triliumdroid_24 removeu uma KeyManagerFactory que nada usava. O SSLContextSpec ("KeyManager[] ... not observed coming from a KeyManagerFactory") continua no patched, e o material TLS não muda.

**Valor fraco mantido em caminho de compatibilidade:**
- rssreader_120: MD5 em readFile, para checksums de 22 caracteres.
- salty_2: decodeLegacyEnvelope com o sal fixo.

Os dois servem só para ler dados antigos e nenhum dos dois caminhos foi executado nos runs. O RVSec não diz nada sobre eles.

**Sites da mesma spec fora do PR:** smsecure_10400_pr89 tem ConversationAdapter e WirePrefix ainda com SHA1. Eles não foram cobertos no patched, então a ausência deles é falta de cobertura, não correção.

**Relatos de outra spec que aparecem só no patched:** em tokn_19 patched_r3, CipherSpec e SecretKeySpecSpec aparecem em vault.AesGcm (monkey:fast.logcat:1276, :1278). AesGcm não aparece na cobertura de nenhum outro run, então é diferença de exploração, não efeito do patch.

**Site remanescente pré-existente:** bookwyrm_16 StartActivity:166, no lado da decifragem. É a mesma lacuna de rastreamento da base.

**Ressalvas de execução:**
- A maioria dos casos tem uma repetição por lado (r1). As exceções são tokn, trafficlight, networksurvey e untis com 3, e primftpd com 2.
- whobird_54: o ape:sata do patched terminou em ERROR, mas cobriu o site.
- untis_29198884: patched_r3 não alcançou o site.

## (4) Frase para RQ7

"For the 21 published pull requests whose reported violation no longer appeared at the executed site, we classified each diff and had an adversarial second reading of each. We found 12 patches that change the governed value or material (algorithm, key size, IV or salt source, or removal of RC4), 8 that change the call sequence or API to the form the specification requires while keeping the same material, 1 that removes the governed call, and none that only relocates or hides the call from the monitor; no patched run reported the same specification at a new site, and the analyst found no change in cryptographic behaviour in 6 of the 21."
## Adendo (2026-11-17, desvio 4): três casos reclassificados como corrigidos

Esta classificação é do autor. Não houve a segunda leitura adversarial feita para os 21 casos acima.

| caso | PR | spec | classe | comportamento mudou | evidência |
|---|---|---|---|---|---|
| fread_109000 | Fread#168 | SSLContextSpec | A | sim | `getInstance("SSL")` → `getInstance("TLS")`; sai `protocols = arrayOf("SSLv3")` |
| zsmthandroid_1031 | zSMTH-Android#66 | SSLContextSpec | A | sim | o TrustManager que aceitava qualquer certificado foi trocado pelo da plataforma, com fallback para uma raiz embutida (ISRG X1) |
| grocy_64_pr1012 | grocy-android#1012 | SSLContextSpec | B | não | sai o `sslContext.init(null, null, null)` redundante; fica o `init` com os gerenciadores do app |

Total: 24 patches, sendo 14 A, 9 B, 1 C e 0 D.
