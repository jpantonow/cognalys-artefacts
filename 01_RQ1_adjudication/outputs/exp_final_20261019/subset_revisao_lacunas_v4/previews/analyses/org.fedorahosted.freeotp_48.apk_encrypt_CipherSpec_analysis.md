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

The reported required-predicate violation is confirmed: the governed AES/GCM Cipher.init receives a caller-supplied key that failed CipherSpec's accepted key-origin predicate in observed executions (CLM-001, CLM-006). The bounded production callers supply either an AndroidKeyStore key or a PBKDF2-derived SecretKey (CLM-004, CLM-005), but the measured runs do not map a failing value to one specific source. Separately, the same AES-GCM initialization supplies only mode and key, while the verified rule requires preparedGCM parameters for GCM; this is an additional required-predicate specification violation. The provider-generated nonce uniqueness question remains unresolved and is not used to dismiss either predicate violation.

## Confirmed facts

- EncryptedKey.encrypt creates a javax.crypto.Cipher with transformation AES/GCM/NoPadding and initializes it in encryption mode using its key parameter.
- The encrypted representation stores the transformation string, plaintext-key algorithm identifier, ciphertext, and encoded Cipher parameters.
- The code supplies the plaintext SecretKey algorithm as AAD before encryption finalization and supplies the same stored algorithm string as AAD before decryption finalization.
- The resolved repository callers of EncryptedKey.encrypt include two tests, Encryptor.encryptToken, and the MasterKey constructor.
- One production caller passes a KeyStore key retrieved from AndroidKeyStore as the encryption-key argument, while the MasterKey constructor passes a PBKDF2-derived SecretKey as that argument.
- A runtime measurement recorded CipherSpec key-origin-check failures at the encryption Cipher.init location in 12 reported application runs.
- AES/GCM/NoPadding configures an AEAD transformation, but the supplied evidence does not establish successful encryption or decryption for all production executions.

## Unsupported hypotheses

None reported.

## Missing evidence

- Which concrete key-producing source expression reached EncryptedKey.java:42 in each measured failing run?
- Which operations, if any, establish the CipherSpec generatedKey, generatedPubkey, or generatedPrivkey predicates for the relevant key values?
- What nonce/IV does the active provider generate for the two-argument AES-GCM encryption initialization, and is it unique per encryption for each key?
- Whether all persisted EncryptedKey records can be decrypted successfully under the deployed provider and key lifecycle is not established.

## Predicate assessment

### `The encryption Cipher transformation is fully specified as AES/GCM/NoPadding.` — confirmed (the requirement holds)

Evidence IDs: SRC-001. The reviewed encrypt method obtains a javax.crypto.Cipher using the literal transformation "AES/GCM/NoPadding".

### `The encryption Cipher is initialized before it processes AAD or plaintext.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, CTOR-001. encrypt obtains the Cipher and calls init in encryption mode before constructing EncryptedKey; that constructor calls updateAAD and doFinal on the passed Cipher.

### `Encryption AAD is supplied before ciphertext processing.` — confirmed (the requirement holds)

Evidence IDs: SRC-001. The EncryptedKey constructor calls cipher.updateAAD(mToken bytes) before cipher.doFinal(token.getEncoded()).

### `The key supplied to the governed encryption Cipher.init call has a key origin accepted by the CipherSpec rule.` — violated (the requirement does not hold)

Evidence IDs: RUN-001. The runtime measurement at EncryptedKey.java:42 records a CIPHER-CONSTR-00 violation for the expectation that the key be produced by one of the rule-named generators. This establishes a failing governed-call execution, although it does not identify the particular source expression that produced the measured key.

### `The encryption-generated encoded AlgorithmParameters are retained and supplied to decryption initialization.` — confirmed (the requirement holds)

Evidence IDs: SRC-001. Encryption saves cipher.getParameters().getEncoded() in mParameters. Decryption reconstructs AlgorithmParameters from mParameters and supplies that object to the decrypt-mode Cipher.init call.

### `Each AES-GCM encryption uses a fresh nonce/IV for a reused encryption key.` — unknown (the evidence does not decide)

Evidence IDs: SRC-001. encrypt supplies only the key to Cipher.init and does not explicitly supply GCM parameters or an IV/nonce. The source stores provider-returned parameters, but it does not establish the provider-generated nonce values or their uniqueness across encryption operations and key reuse.

### `Decryption supplies AAD before finalizing the stored ciphertext under the reconstructed Cipher parameters.` — confirmed (the requirement holds)

Evidence IDs: SRC-001. decrypt reconstructs AlgorithmParameters from mParameters, initializes the Cipher in decrypt mode with them, then supplies mToken-derived AAD before doFinal(mCipherText).

## Other requirements this source appears to break

These are requirements of the same specification that the reported finding is not about. The decision above answers for the reported condition only.

- **The key supplied to the governed encryption Cipher.init call has a key origin accepted by the CipherSpec rule.**
  The runtime measurement at EncryptedKey.java:42 records a CIPHER-CONSTR-00 violation for the expectation that the key be produced by one of the rule-named generators. This establishes a failing governed-call execution, although it does not identify the particular source expression that produced the measured key.

## Migration assessment

**Usage kind:** `unknown`  
**Legacy compatibility required:** `True`  
**Policy authorized:** `False`

**Safe strategy:** Any review patch must preserve decryption of existing EncryptedKey records and the existing key-lifecycle semantics for both AndroidKeyStore-backed and PBKDF2-derived wrapping keys. It must retain persisted Cipher parameters for new AES-GCM records and, if it makes GCM parameters explicit, generate a fresh nonce per encryption without discarding legacy parameter decoding.

- EncryptedKey.encrypt creates a javax.crypto.Cipher with transformation AES/GCM/NoPadding and initializes it in encryption mode using its key parameter.
- The encrypted representation stores the transformation string, plaintext-key algorithm identifier, ciphertext, and encoded Cipher parameters.
- The resolved repository callers of EncryptedKey.encrypt include two tests, Encryptor.encryptToken, and the MasterKey constructor.
- One production caller passes a KeyStore key retrieved from AndroidKeyStore as the encryption-key argument, while the MasterKey constructor passes a PBKDF2-derived SecretKey as that argument.
- A runtime measurement recorded CipherSpec key-origin-check failures at the encryption Cipher.init location in 12 reported application runs.

## Forward compatibility

**Status:** `regression_risk`

Claim IDs: CLM-001, CLM-002, CLM-004, CLM-005, CLM-006

**Strategy:** Any review patch must preserve decryption of existing EncryptedKey records and the existing key-lifecycle semantics for both AndroidKeyStore-backed and PBKDF2-derived wrapping keys. It must retain persisted Cipher parameters for new AES-GCM records and, if it makes GCM parameters explicit, generate a fresh nonce per encryption without discarding legacy parameter decoding.

## External sources

- [official](https://docs.oracle.com/en/java/javase/26/docs/api/java.base/javax/crypto/Cipher.html) — `Cipher` is the JCE cryptographic engine API and documents initialization and data/key-processing contracts.
- [official](https://docs.oracle.com/en/java/javase/26/security/java-cryptography-architecture-jca-reference-guide.html) — Use fully specified transformations; retain/use applicable parameters and AAD; change a GCM IV when reusing a key for encryption.
- [official](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/javax/crypto/spec/GCMParameterSpec.html) — `GCMParameterSpec` has existed since Java 7.
- [official](https://docs.oracle.com/javase/8/docs/technotes/guides/security/enhancements-8.html) — Oracle documented SunJCE support for AES/GCM/NoPadding and GCM algorithm parameters as a JDK 8 enhancement.
- [primary](https://raw.githubusercontent.com/CROSSINGTUD/Crypto-API-Rules/refs/heads/master/JavaCryptographicArchitecture/src/Cipher.crysl) — The rule is declared for `javax.crypto.Cipher`, models listed event parameter shapes, and requires one of three key-origin predicates for its `key` variable.
- [official](https://developer.android.com/reference/javax/crypto/Cipher) — `Cipher.getInstance` accepts a `String` transformation argument, so `mCipher` must be compatible with `String` when passed directly to this overload.
