<!-- LOCAL LLM ANALYSIS PREVIEW: not intended for publication. -->

# LLM analysis: `encrypt`

**Project:** `org.fedorahosted.freeotp_48.apk`  
**Class:** `org.fedorahosted.freeotp.encryptor.EncryptedKey`  
**Specification:** `CipherSpec`  
**Decision:** `MULTI_FILE_FIX_PROPOSED_FOR_REVIEW`  
**Review status:** `PROPOSED_FIX_REQUIRES_REVIEW`

## Interpretation

A complete candidate patch was generated from confirmed evidence, but residual assumptions require human review. It is explicitly blocked from automatic application and publication.

## Evidence status

- **Cause:** `confirmed`
- **Detector violation:** `confirmed`
- **Runtime security impact:** `not_established`
- **Scope:** `complete`
- **Generation:** `supported_for_review`
- **Publication:** `blocked`
- **Diagnostic evidence:** `DET-001`
- **Diagnostic authority:** `informational_only`

## Reported finding

the key given to Cipher.init carries a key-origin predicate the rule does not admit

## Confirmed cause

On the confirmed MasterKey caller path, a PBKDF2 SecretKeyFactory result is passed as EncryptedKey.encrypt's key argument and then directly to AES/GCM Cipher.init. CipherSpec requires that initialization key to carry generatedKey for the transformation algorithm, generatedPubkey, or generatedPrivkey; this PBKDF2-derived value carries none of those admitted origins.

## Confirmed facts

- EncryptedKey.encrypt creates a resolved javax.crypto.Cipher for the literal transformation AES/GCM/NoPadding and initializes it in ENCRYPT_MODE with its key argument.
- The EncryptedKey encryption flow supplies the protected key algorithm name as AAD, finalizes the protected key encoding, and retains the Cipher's encoded parameters.
- The EncryptedKey decryption flow reconstructs retained AlgorithmParameters, initializes a Cipher in DECRYPT_MODE with those parameters, supplies the retained algorithm name as AAD, and finalizes the retained ciphertext.
- The MasterKey constructor passes a PBKDF2 SecretKeyFactory result as the encryption key to EncryptedKey.encrypt, which forwards it to AES/GCM Cipher.init.
- CipherSpec requires a key initialization to carry generatedKey for the transformation algorithm, generatedPubkey, or generatedPrivkey; the PBKDF2-derived MasterKey path does not meet that requirement for the AES transformation.
- The source selects an authenticated AES/GCM mode and supplies AAD before final processing on both the encryption and decryption flows; this establishes use of an authenticated-mode construction, not the success of any particular runtime operation.
- The runtime detector measurement records a value-constraint observation at EncryptedKey.java:42 in 12 application runs, but it does not identify the source expression that produced the observed key value.

## Unsupported hypotheses

None reported.

## Missing evidence

- Whether every provider-generated AES/GCM nonce or IV is unique for repeated encryption with the same key.
- Which concrete provider and parameter values are used at runtime when encryption initializes AES/GCM without explicit GCM parameters.
- Whether the AndroidKeyStore key retrieval path satisfies a named CipherSpec key-origin predicate.
- Whether Cipher.getParameters() is non-null and produces transferable encoded parameters on every supported runtime path.
- Whether each encryption and decryption operation completes successfully at runtime; source event order does not establish successful operation or exception behavior.

## Predicate assessment

### `The Cipher transformation used by EncryptedKey.encrypt is an AES/GCM transformation with NoPadding permitted by CipherSpec.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001. The method obtains a javax.crypto.Cipher with the literal transformation "AES/GCM/NoPadding". CipherSpec permits AES with GCM and requires NoPadding for AES.

### `A Cipher instance is obtained before it is initialized for encryption in EncryptedKey.encrypt.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001. EncryptedKey.encrypt calls Cipher.getInstance("AES/GCM/NoPadding") and then calls init(Cipher.ENCRYPT_MODE, key) on the resulting local cipher variable.

### `Additional authenticated data is supplied before final cipher processing on the encryption and decryption flows.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001. The constructor calls cipher.updateAAD(mToken bytes) before cipher.doFinal(token bytes). Decrypt similarly calls updateAAD before doFinal(ciphertext). Neither flow contains an update call between AAD submission and finalization.

### `Every key supplied to the AES/GCM Cipher initialization has a CipherSpec-accepted key origin for the transformation.` — violated (the requirement does not hold)

Evidence IDs: SRC-001, SRC-003, CALL-004, SPEC-001. CipherSpec requires generatedKey[key, alg(transformation)], generatedPubkey[key], or generatedPrivkey[key]. On the MasterKey call path, the value passed as EncryptedKey.encrypt's first argument is pwd, produced by a PBKDF2 SecretKeyFactory, and it reaches the AES/GCM Cipher.init call directly. This is not a generated AES key for alg(transformation), nor is it a public or private key origin.

### `The encryption parameters retained by EncryptedKey are supplied to the decryption Cipher initialization.` — confirmed (the requirement holds)

Evidence IDs: SRC-001. Encryption stores cipher.getParameters().getEncoded() in mParameters. Decryption reconstructs AlgorithmParameters from mParameters and passes that object to cipher.init(Cipher.DECRYPT_MODE, key, ap).

### `Each AES/GCM encryption using a given key uses a fresh nonce or IV.` — unknown (the evidence does not decide)

Evidence IDs: SRC-001, WEB-002. The encryption initialization supplies no explicit GCM parameter object, and the source does not reveal the provider-generated parameter values or establish uniqueness across separate encryptions using the same key.

## Other requirements this source appears to break

These are requirements of the same specification that the reported finding is not about. The decision above answers for the reported condition only.

- **Every key supplied to the AES/GCM Cipher initialization has a CipherSpec-accepted key origin for the transformation.**
  CipherSpec requires generatedKey[key, alg(transformation)], generatedPubkey[key], or generatedPrivkey[key]. On the MasterKey call path, the value passed as EncryptedKey.encrypt's first argument is pwd, produced by a PBKDF2 SecretKeyFactory, and it reaches the AES/GCM Cipher.init call directly. This is not a generated AES key for alg(transformation), nor is it a public or private key origin.

## Migration assessment

**Usage kind:** `unknown`  
**Legacy compatibility required:** `True`  
**Policy authorized:** `False`

**Safe strategy:** Use a CipherSpec-recognized key-production and lifecycle design for each key source that reaches EncryptedKey.encrypt, rather than using PBKDF2 output directly as the AES/GCM Cipher key. Prepare compliant fresh GCM parameters for encryption while retaining the resulting parameters and algorithm AAD for decryption. Preserve existing decryptability unless a reviewer approves a versioned legacy-data migration.

- EncryptedKey.encrypt creates a resolved javax.crypto.Cipher for the literal transformation AES/GCM/NoPadding and initializes it in ENCRYPT_MODE with its key argument.
- The EncryptedKey encryption flow supplies the protected key algorithm name as AAD, finalizes the protected key encoding, and retains the Cipher's encoded parameters.
- The EncryptedKey decryption flow reconstructs retained AlgorithmParameters, initializes a Cipher in DECRYPT_MODE with those parameters, supplies the retained algorithm name as AAD, and finalizes the retained ciphertext.
- The MasterKey constructor passes a PBKDF2 SecretKeyFactory result as the encryption key to EncryptedKey.encrypt, which forwards it to AES/GCM Cipher.init.
- CipherSpec requires a key initialization to carry generatedKey for the transformation algorithm, generatedPubkey, or generatedPrivkey; the PBKDF2-derived MasterKey path does not meet that requirement for the AES transformation.
- The source selects an authenticated AES/GCM mode and supplies AAD before final processing on both the encryption and decryption flows; this establishes use of an authenticated-mode construction, not the success of any particular runtime operation.

## Forward compatibility

**Status:** `regression_risk`

Claim IDs: CLM-001, CLM-002, CLM-003, CLM-004, CLM-005, CLM-006

**Strategy:** Use a CipherSpec-recognized key-production and lifecycle design for each key source that reaches EncryptedKey.encrypt, rather than using PBKDF2 output directly as the AES/GCM Cipher key. Prepare compliant fresh GCM parameters for encryption while retaining the resulting parameters and algorithm AAD for decryption. Preserve existing decryptability unless a reviewer approves a versioned legacy-data migration.

## External sources

- [official](https://docs.oracle.com/en/java/javase/26/docs/api/java.base/javax/crypto/Cipher.html) — `Cipher` is the JCE cryptographic engine API and documents initialization and data/key-processing contracts.
- [official](https://docs.oracle.com/en/java/javase/26/security/java-cryptography-architecture-jca-reference-guide.html) — Use fully specified transformations; retain/use applicable parameters and AAD; change a GCM IV when reusing a key for encryption.
- [official](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/javax/crypto/spec/GCMParameterSpec.html) — `GCMParameterSpec` has existed since Java 7.
- [official](https://docs.oracle.com/javase/8/docs/technotes/guides/security/enhancements-8.html) — Oracle documented SunJCE support for AES/GCM/NoPadding and GCM algorithm parameters as a JDK 8 enhancement.
- [primary](https://raw.githubusercontent.com/CROSSINGTUD/Crypto-API-Rules/refs/heads/master/JavaCryptographicArchitecture/src/Cipher.crysl) — The rule is declared for `javax.crypto.Cipher`, models listed event parameter shapes, and requires one of three key-origin predicates for its `key` variable.
- [official](https://developer.android.com/reference/javax/crypto/Cipher) — `Cipher.getInstance` accepts a `String` transformation argument, so `mCipher` must be compatible with `String` when passed directly to this overload.
