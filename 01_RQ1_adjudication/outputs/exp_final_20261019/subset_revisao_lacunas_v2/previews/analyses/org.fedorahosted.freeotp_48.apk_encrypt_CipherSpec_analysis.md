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
- **Runtime security impact:** `unknown`
- **Scope:** `complete`
- **Generation:** `supported_for_review`
- **Publication:** `blocked`
- **Diagnostic evidence:** `DET-001`
- **Diagnostic authority:** `informational_only`

## Reported finding

the key given to Cipher.init carries a key-origin predicate the rule does not admit

## Confirmed cause

The dynamic analysis observed this violation while the app ran: the key given to Cipher.init carries a key-origin predicate the rule does not admit

## Confirmed facts

- EncryptedKey.encrypt creates a javax.crypto.Cipher with the literal transformation AES/GCM/NoPadding and initializes it for encryption with its key parameter.
- The protected plaintext in EncryptedKey.encrypt is the encoded byte representation of the supplied SecretKey, and the plaintext key algorithm name is supplied as AAD.
- EncryptedKey stores ciphertext, encoded AlgorithmParameters, the cipher algorithm string, and the plaintext key algorithm string; decrypt reconstructs AlgorithmParameters and initializes a Cipher for decryption with them.
- CipherSpec requires a generated key-origin predicate for a Cipher initialization key and requires a preparedGCM predicate for GCM parameters.
- The two production callers shown supply encryption keys from distinct paths: PBKDF2 SecretKeyFactory output in MasterKey and AndroidKeyStore lookup output in Encryptor.
- The reviewed source establishes use of an authenticated GCM transformation and structural AAD-before-finalization ordering, but it does not establish successful authentication outcomes for every runtime decryption path.

## Unsupported hypotheses

None reported.

## Missing evidence

- Whether the non-parameterized GCM encryption initialization produces a nonce that is unique for every reused encryption key in the deployed provider.
- Whether each production key origin is recognized by the installed CrySL rules as generatedKey, generatedPubkey, or generatedPrivkey.
- Which transformation and parameters are present in EncryptedKey objects reconstructed from persisted JSON rather than directly returned by EncryptedKey.encrypt.
- Whether all persisted ciphertext records were produced by this exact AES/GCM/NoPadding encryption path and can be decrypted successfully in the target runtime.

## Predicate assessment

### `The encryption flow uses a CipherSpec-permitted AES/GCM/NoPadding transformation.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001. EncryptedKey.encrypt obtains javax.crypto.Cipher with the literal transformation "AES/GCM/NoPadding". CipherSpec permits AES in GCM mode with NoPadding.

### `Cipher factory creation occurs before the corresponding encryption initialization.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001. EncryptedKey.encrypt calls Cipher.getInstance("AES/GCM/NoPadding") and subsequently invokes init(Cipher.ENCRYPT_MODE, key) on the local cipher variable.

### `The key passed to the encryption Cipher.init call is non-null.` — confirmed (the requirement holds)

Evidence IDs: SRC-001. EncryptedKey.encrypt rejects a null key by throwing InvalidKeyException before creating and initializing the Cipher.

### `GCM initialization receives a GCM parameter specification with CipherSpec's preparedGCM predicate.` — violated (the requirement does not hold)

Evidence IDs: SRC-001, SPEC-001. The CipherSpec constraints require preparedGCM[paramSpec] for GCM. The encryption flow uses the two-argument init(Cipher.ENCRYPT_MODE, key) overload and therefore supplies no GCM parameter specification to which that predicate could apply.

### `The key passed to Cipher.init carries one of CipherSpec's required generatedKey, generatedPubkey, or generatedPrivkey predicates.` — unknown (the evidence does not decide)

Evidence IDs: SRC-001, SRC-002, SRC-003, TEST-001, SPEC-001. CipherSpec requires one of these key-origin predicates. Repository callers supply the key from PBKDF2 SecretKeyFactory output, AndroidKeyStore.getKey output, or test KeyGenerator output, but the supplied evidence does not establish whether any producer satisfies the named CrySL predicates.

### `AAD is supplied before the encryption Cipher processes the protected key bytes with doFinal.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001. The EncryptedKey constructor calls updateAAD with the plaintext key algorithm name before calling doFinal on token.getEncoded().

### `GCM decryption supplies the same stored AAD value before processing the stored ciphertext with doFinal.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001. Encryption stores the plaintext key algorithm in mToken and uses it as AAD. Decryption uses mToken as AAD before calling doFinal on mCipherText.

### `The GCM nonce is unique for every encryption performed with the same key.` — unknown (the evidence does not decide)

Evidence IDs: SRC-001, WEB-002. The encryption initialization provides no explicit GCM parameters or nonce. The source persists provider-returned AlgorithmParameters, but it does not establish provider-generated nonce values or their uniqueness across encryptions that use the same key.

### `The plaintext input to Cipher.doFinal satisfies CipherSpec's un-MACed plaintext predicate.` — unknown (the evidence does not decide)

Evidence IDs: SRC-001, SPEC-001. CipherSpec requires !macced[this, plainText] for modeled byte-array operations. The source shows token.getEncoded() as the doFinal input but does not establish the CrySL macced predicate for those bytes.

## Other requirements this source appears to break

These are requirements of the same specification that the reported finding is not about. The decision above answers for the reported condition only.

- **GCM initialization receives a GCM parameter specification with CipherSpec's preparedGCM predicate.**
  The CipherSpec constraints require preparedGCM[paramSpec] for GCM. The encryption flow uses the two-argument init(Cipher.ENCRYPT_MODE, key) overload and therefore supplies no GCM parameter specification to which that predicate could apply.

## Migration assessment

**Usage kind:** `unknown`  
**Legacy compatibility required:** `True`  
**Policy authorized:** `False`

**Safe strategy:** Do not change key-management paths until their CipherSpec predicate provenance is established. Any later GCM hardening should retain AES/GCM/NoPadding, AAD ordering, and persistence of the AlgorithmParameters needed to decrypt existing self-contained records; it should use a fresh nonce and explicit GCM parameters for each new encryption where the deployed provider supports that form.

- EncryptedKey.encrypt creates a javax.crypto.Cipher with the literal transformation AES/GCM/NoPadding and initializes it for encryption with its key parameter.
- EncryptedKey stores ciphertext, encoded AlgorithmParameters, the cipher algorithm string, and the plaintext key algorithm string; decrypt reconstructs AlgorithmParameters and initializes a Cipher for decryption with them.
- CipherSpec requires a generated key-origin predicate for a Cipher initialization key and requires a preparedGCM predicate for GCM parameters.
- The two production callers shown supply encryption keys from distinct paths: PBKDF2 SecretKeyFactory output in MasterKey and AndroidKeyStore lookup output in Encryptor.

## Forward compatibility

**Status:** `unknown`

Claim IDs: CLM-001, CLM-003, CLM-004, CLM-005

**Strategy:** Do not change key-management paths until their CipherSpec predicate provenance is established. Any later GCM hardening should retain AES/GCM/NoPadding, AAD ordering, and persistence of the AlgorithmParameters needed to decrypt existing self-contained records; it should use a fresh nonce and explicit GCM parameters for each new encryption where the deployed provider supports that form.

## External sources

- [official](https://docs.oracle.com/en/java/javase/26/docs/api/java.base/javax/crypto/Cipher.html) — `Cipher` is the JCE cryptographic engine API and documents initialization and data/key-processing contracts.
- [official](https://docs.oracle.com/en/java/javase/26/security/java-cryptography-architecture-jca-reference-guide.html) — Use fully specified transformations; retain/use applicable parameters and AAD; change a GCM IV when reusing a key for encryption.
- [official](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/javax/crypto/spec/GCMParameterSpec.html) — `GCMParameterSpec` has existed since Java 7.
- [official](https://docs.oracle.com/javase/8/docs/technotes/guides/security/enhancements-8.html) — Oracle documented SunJCE support for AES/GCM/NoPadding and GCM algorithm parameters as a JDK 8 enhancement.
- [primary](https://raw.githubusercontent.com/CROSSINGTUD/Crypto-API-Rules/refs/heads/master/JavaCryptographicArchitecture/src/Cipher.crysl) — The rule is declared for `javax.crypto.Cipher`, models listed event parameter shapes, and requires one of three key-origin predicates for its `key` variable.
- [official](https://developer.android.com/reference/javax/crypto/Cipher) — `Cipher.getInstance` accepts a `String` transformation argument, so `mCipher` must be compatible with `String` when passed directly to this overload.
