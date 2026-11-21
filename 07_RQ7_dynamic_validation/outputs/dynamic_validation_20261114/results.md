# Validação dinâmica: RVSec no APK base e no APK corrigido

Plano: `docs/PLANO_VALIDACAO_DINAMICA.md`, seção 1. Os dois APKs de cada caso saem do mesmo commit (o que o pipeline usou para o patch), com a mesma tarefa Gradle; o corrigido tem o diff do pipeline aplicado sem alteração. RVSec: `monkey:fast,ape:sata`, 600 s, jca_android, 3 repetições.

Corrigido: 5; não corrigido: 0; inconclusivo (cobertura): 1; base não reproduz: 0; pendente: 0 (de 6 casos).

| Caso | Spec | Base r1 / r2 / r3 | Corrigido r1 / r2 / r3 | Resultado |
|---|---|---|---|---|
| tokn_19 | KeyStoreSpec | V cov / V cov / V cov | - cov / - cov / - cov | corrigido |
| bitbanana_79 | PBEKeySpecSpec | - nocov / V cov / - nocov | - nocov / - nocov / - nocov | inconclusivo (cobertura) |
| untis_29198884 | MessageDigestSpec | V cov / V cov / V cov | - cov / - cov / - nocov | corrigido |
| networksurvey_114 | KeyStoreSpec | V cov / V cov / V cov | - cov / - cov / - cov | corrigido |
| passnotes_11700 | SSLContextSpec | - cov / - cov / V cov | - cov / - cov / - cov | corrigido |
| trafficlight_38 | KeyStoreSpec | V cov / V cov / V cov | - cov / - cov / - cov | corrigido |

Legenda: `V` violação da spec no método; `V*` violação da spec no método com outra mensagem (conta como violação na tabela do plano); `-` nenhuma; `cov`/`nocov` método executado ou não (RVSEC-COV); `missing`/`unfinished`/`unexplorable` execução ausente, interrompida ou que o RVSec não conseguiu instrumentar/instalar.

## tokn_19

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 3377 métodos (site: 1); ape:sata COMPLETED 3601 métodos (site: 1)
  - me.diamondforge.tokn.security.KeystoreManager.getOrCreateKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2572 métodos (site: 1); ape:sata COMPLETED 4527 métodos (site: 1)
- base_r2: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2435 métodos (site: 1); ape:sata COMPLETED 3760 métodos (site: 1)
  - me.diamondforge.tokn.security.KeystoreManager.getOrCreateKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r2: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2711 métodos (site: 1); ape:sata COMPLETED 3159 métodos (site: 1)
- base_r3: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 2785 métodos (site: 1); ape:sata COMPLETED 3178 métodos (site: 1)
  - me.diamondforge.tokn.security.KeystoreManager.getOrCreateKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r3: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 3221 métodos (site: 1); ape:sata COMPLETED 2977 métodos (site: 1)

## bitbanana_79

Nota: encodePbkdf2 runs only from the app-lock password/PIN setup and entry screens; the explorers must turn an app lock on. The patch renames the method, so the patched sites are encodeCurrentAppLockPbkdf2 / encodeLegacyAppLockPbkdf2.

- base_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 2986 métodos; ape:sata ERROR 3813 métodos; ape:sata COMPLETED 3813 métodos
- patched_r1: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3013 métodos; ape:sata COMPLETED 5218 métodos
- base_r2: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 2532 métodos; ape:sata COMPLETED 3945 métodos (site: 1)
  - app.michaelwuensch.bitbanana.util.UtilFunctions.encodePbkdf2 [PBEKEYSPEC-CONSTR-00] v=1 code=PBEKEYSPEC-CONSTR-00 ev=c1 obj=PBEKeySpec val='5000' exp='>= 10000' msg='the third argument should be >= 10000'
  - app.michaelwuensch.bitbanana.util.UtilFunctions.encodePbkdf2 [PBEKEYSPEC-NOBS-01] v=1 code=PBEKEYSPEC-NOBS-01 ev=c1 obj=PBEKeySpec val='' exp='a randomized byte[]' msg='the second argument was not observed to come from a randomized source' vfp='sha256:9ffdc54e15abd290'
- patched_r2: violação=não (-), coberto=não (-); monkey:fast COMPLETED 2719 métodos; ape:sata COMPLETED 4990 métodos
- base_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 2764 métodos; ape:sata COMPLETED 5026 métodos
- patched_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 3039 métodos; ape:sata COMPLETED 5196 métodos

## untis_29198884

Nota: BetterUntis normally needs a WebUntis school login; the cache may not be written without one.

- base_r1: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 4921 métodos; ape:sata COMPLETED 8141 métodos (site: 1)
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r1: violação=não (-), coberto=sim (ape:sata); monkey:fast COMPLETED 4563 métodos; ape:sata COMPLETED 8908 métodos (site: 1)
- base_r2: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 4779 métodos; ape:sata COMPLETED 8595 métodos (site: 1)
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r2: violação=não (-), coberto=sim (ape:sata); monkey:fast COMPLETED 4800 métodos; ape:sata COMPLETED 7755 métodos (site: 1)
- base_r3: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 4524 métodos; ape:sata COMPLETED 9185 métodos (site: 1)
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ALG-02] v=1 code=MESSAGEDIGEST-ALG-02 ev=g4 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ALG-01] v=1 code=MESSAGEDIGEST-ALG-01 ev=d2 obj=MessageDigest val='MD5' exp='SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512' msg='expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5'
  - com.sapuseven.untis.core.data.cache.DiskCache.getCacheFile [MESSAGEDIGEST-ORDER-02] v=1 code=MESSAGEDIGEST-ORDER-02 ev=d2 obj=MessageDigest val='' exp='' msg='the observed call sequence is not one MessageDigestSpec accepts, after a creation the specification refused'
- patched_r3: violação=não (-), coberto=não (-); monkey:fast COMPLETED 4543 métodos; ape:sata COMPLETED 4900 métodos

## networksurvey_114

Nota: APKs rebuilt without net.bytebuddy in both variants (same commit, same Gradle task): RVSec could not instrument the first builds (dexlib2 failed on bytebuddy; base_r*/patched_r* unexplorable x6). base_nobb_r1 was rerun after the first attempt died of SIGHUP (exit 129) after 21 s; that attempt is kept as base_nobb_r1.hup_20261115.

- base_r1: violação=sim (ape:sata), coberto=sim (ape:sata); monkey:fast COMPLETED 10758 métodos; ape:sata COMPLETED 12164 métodos (site: 1)
  - com.craxiom.networksurvey.util.CryptoManager.getOrCreateKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 11916 métodos (site: 1); ape:sata COMPLETED 9089 métodos (site: 1)
- base_r2: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 9198 métodos (site: 1); ape:sata COMPLETED 12155 métodos (site: 1)
  - com.craxiom.networksurvey.util.CryptoManager.getOrCreateKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r2: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 13187 métodos (site: 1); ape:sata COMPLETED 10227 métodos (site: 1)
- base_r3: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 12216 métodos (site: 1); ape:sata COMPLETED 12333 métodos (site: 1)
  - com.craxiom.networksurvey.util.CryptoManager.getOrCreateKey [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r3: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 14838 métodos (site: 1); ape:sata COMPLETED 10005 métodos (site: 1)

## passnotes_11700

Nota: The violating branch is `BuildConfig.DEBUG && type == UNSECURE`: it runs only after a WebDAV client of type UNSECURE is created, which needs a WebDAV server set up in the UI. The patch keeps SSLContext.getInstance("TLS") and the hostname verifier; another SSLContextSpec message at the same site counts as a violation in the plan's table and is shown under 'messages'.

- base_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 4201 métodos (site: 1); ape:sata COMPLETED 6124 métodos (site: 1)
- patched_r1: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 4185 métodos (site: 1); ape:sata COMPLETED 3117 métodos
- base_r2: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 4245 métodos (site: 1); ape:sata COMPLETED 4558 métodos
- patched_r2: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 3708 métodos (site: 1); ape:sata COMPLETED 5175 métodos (site: 1)
- base_r3: violação=sim (monkey:fast), coberto=sim (monkey:fast); monkey:fast COMPLETED 4327 métodos (site: 1); ape:sata COMPLETED 5240 métodos
  - com.ivanovsky.passnotes.data.repository.file.webdav.HttpClientFactory.createHttpClient [SSLCONTEXT-NOBS-03] v=1 code=SSLCONTEXT-NOBS-03 ev=init obj=SSLContext val='TLS' exp='a KeyManager[] an observed KeyManagerFactory returned' msg='a null KeyManager[] given to SSLContext.init requests the platform default
  - com.ivanovsky.passnotes.data.repository.file.webdav.HttpClientFactory.createHttpClient [SSLCONTEXT-NOBS-05] v=1 code=SSLCONTEXT-NOBS-05 ev=init obj=SSLContext val='TLS' exp='a TrustManager[] an observed TrustManagerFactory returned' msg='the TrustManager[] given to SSLContext.init holds a manager of a class
- patched_r3: violação=não (-), coberto=sim (monkey:fast); monkey:fast COMPLETED 4362 métodos (site: 1); ape:sata COMPLETED 3914 métodos

## trafficlight_38

- base_r1: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1062 métodos (site: 3); ape:sata COMPLETED 1064 métodos (site: 3)
  - com.leekleak.trafficlight.database.CryptoManager.getSecretKey$lambda$0 [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r1: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1060 métodos (site: 3); ape:sata COMPLETED 1062 métodos (site: 3)
- base_r2: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1062 métodos (site: 3); ape:sata COMPLETED 1070 métodos (site: 3)
  - com.leekleak.trafficlight.database.CryptoManager.getSecretKey$lambda$0 [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r2: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1059 métodos (site: 3); ape:sata COMPLETED 1062 métodos (site: 3)
- base_r3: violação=sim (ape:sata, monkey:fast), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1062 métodos (site: 3); ape:sata COMPLETED 1056 métodos (site: 3)
  - com.leekleak.trafficlight.database.CryptoManager.getSecretKey$lambda$0 [KEYSTORE-ORDER-00] v=1 code=KEYSTORE-ORDER-00 ev=ge1 obj=KeyStore val='' exp='' msg='the observed call sequence is not one KeyStoreSpec accepts'
- patched_r3: violação=não (-), coberto=sim (monkey:fast, ape:sata); monkey:fast COMPLETED 1060 métodos (site: 3); ape:sata COMPLETED 1064 métodos (site: 3)
