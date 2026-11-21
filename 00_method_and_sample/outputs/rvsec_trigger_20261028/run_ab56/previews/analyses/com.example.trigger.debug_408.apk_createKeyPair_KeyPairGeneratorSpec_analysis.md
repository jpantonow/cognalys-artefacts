<!-- LOCAL LLM ANALYSIS PREVIEW: not intended for publication. -->

# LLM analysis: `createKeyPair`

**Project:** `com.example.trigger.debug_408.apk`  
**Class:** `app.trigger.ssh.GenerateIdentityTask`  
**Specification:** `KeyPairGeneratorSpec`  
**Decision:** `MULTI_FILE_FIX_PROPOSED_FOR_REVIEW`  
**Review status:** `PROPOSED_FIX_REQUIRES_REVIEW`

## Interpretation

A complete candidate patch was generated from confirmed evidence, but residual assumptions require human review. It is explicitly blocked from automatic application and publication.

## Evidence status

- **Cause:** `confirmed`
- **Detector violation:** `confirmed`
- **Runtime security impact:** `non_security`
- **Scope:** `complete`
- **Generation:** `supported_for_review`
- **Publication:** `blocked`
- **Diagnostic evidence:** `DET-001`
- **Diagnostic authority:** `informational_only`

## Reported finding

expecting one of RSA,EC,DSA,DiffieHellman,DH but found ED25519

## Confirmed cause

The reported getInstance algorithm argument is ED25519, supplied by the resolved ED25519 caller and observed at the governed call; ED25519 is outside this CrySL KeyPairGeneratorSpec allowlist {RSA, EC, DSA, DiffieHellman, DH} (SRC-001, SUP-001, CALL-002, RUN-001, SPEC-001; CLM-002, CLM-003, CLM-004).

## Confirmed facts

- The reviewed method uses the governed JCA type java.security.KeyPairGenerator through getInstance(type), initialize(bits, random), and generateKeyPair().
- The source exposes six resolved createKeyPair parameter combinations: ED25519/256, EC/384, EC/521, RSA/2048, RSA/4096, and DSA/1024.
- ED25519 reached the governed call in one measured application run.
- The observed ED25519 mismatch is a failure of this KeyPairGeneratorSpec allowlist, not evidence that Ed25519 is absent from Java's documented KeyPairGenerator algorithm names.
- The reviewed method serializes the generated private and public keys into a KeyPairBean as cloned encoded byte arrays; with the empty secret passed by this method, getEncodedPrivate returns the private key's encoded bytes rather than invoking its encryption branch.

## Unsupported hypotheses

None reported.

## Missing evidence

- Which provider and provider version the Android runtime selects for KeyPairGenerator.getInstance(type).
- Whether every requested algorithm/size pair is accepted and completes key generation on deployed runtimes.
- Whether generateKeyPair completed successfully during the measured ED25519 run.
- Which persisted keys already exist and whether they must remain usable after any algorithm-policy change.
- Whether the target SSH endpoints and any external consumers support each generated key algorithm and encoding.

## Predicate assessment

### `The governed java.security.KeyPairGenerator usage follows the required Get → Init → Gen event order.` — confirmed (the requirement holds)

Evidence IDs: SRC-001, SPEC-001, SEQ-001. The reviewed method obtains a generator, initializes the same local generator, and then invokes generateKeyPair; the resolved binding identifies all three calls as java.security.KeyPairGenerator calls governed by the specification.

### `Each governed KeyPairGenerator.getInstance algorithm argument is in the specification allowlist {RSA, EC, DSA, DiffieHellman, DH}.` — violated (the requirement does not hold)

Evidence IDs: SRC-001, SUP-001, CALL-002, RUN-001, SPEC-001. A resolved caller invokes createKeyPair with KeyPairBean.KEY_TYPE_ED25519, whose supplied definition is "ED25519"; this reaches getInstance(type). The runtime measurement also records ED25519 reaching the governed call. ED25519 is not in the specification allowlist.

### `For each RSA initialization, the integer key size is either 3072 or 4096 as required by the specification.` — violated (the requirement does not hold)

Evidence IDs: SRC-001, SUP-001, CALL-006, CALL-004, SPEC-001. The resolved RSA-2048 caller passes KEY_TYPE_RSA and 2048 to createKeyPair, and the method passes bits to initialize(bits, random). Although a separate RSA-4096 caller passes 4096, the 2048 invocation does not meet the applicable RSA size condition.

### `For each DSA initialization, the integer key size is 3072 as required by the specification.` — violated (the requirement does not hold)

Evidence IDs: SRC-001, SUP-001, CALL-005, SPEC-001. The resolved DSA caller passes KEY_TYPE_DSA and 1024 to createKeyPair, and bits is forwarded to initialize(bits, random). The specification's DSA integer-size condition admits 3072 only.

### `For each EC initialization, the integer key size is 256 as required by the specification.` — violated (the requirement does not hold)

Evidence IDs: SRC-001, SUP-001, CALL-001, CALL-003, SPEC-001. The two resolved EC callers pass 384 and 521, respectively, and the method forwards bits to initialize(bits, random). Neither value meets the specification's stated EC integer-size condition of 256.

### `The initialization call supplies a SecureRandom argument.` — confirmed (the requirement holds)

Evidence IDs: SRC-001. The reviewed method constructs SecureRandom(), stores it in random, and passes random as the second argument to keyPairGen.initialize(bits, random).

### `The selected runtime provider supports each requested algorithm and integer initialization value.` — unknown (the evidence does not decide)

Evidence IDs: SRC-001, RUN-001, WEB-001. The one-argument getInstance(type) call does not name a provider. The supplied source and runtime measurement do not identify the installed provider, provider version, or successful acceptance of every algorithm/size combination.

### `Each generated key pair receives the specification's generatedKeypair[keyPair, algorithm] assurance.` — violated (the requirement does not hold)

Evidence IDs: SRC-001, SPEC-001, SUP-001, CALL-001, CALL-002, CALL-003, CALL-005, CALL-006. The specification states that this assurance follows compliant usage. The reviewed usage has algorithm and applicable integer-size constraint failures, so the required compliant usage condition does not hold for all generated pairs.

## Other requirements this source appears to break

These are requirements of the same specification that the reported finding is not about. The decision above answers for the reported condition only.

- **Each generated key pair receives the specification's generatedKeypair[keyPair, algorithm] assurance.**
  The specification states that this assurance follows compliant usage. The reviewed usage has algorithm and applicable integer-size constraint failures, so the required compliant usage condition does not hold for all generated pairs.

## Migration assessment

**Usage kind:** `unknown`  
**Legacy compatibility required:** `True`  
**Policy authorized:** `False`

**Safe strategy:** Retain the documented ED25519 KeyPairGenerator use. The durable resolution is a version-aware update to the governing rule/allowlist rather than an application downgrade from ED25519 to a different key algorithm. Any separately authorized algorithm migration would need an explicit SSH-key storage and endpoint-compatibility migration policy.

- The reviewed method uses the governed JCA type java.security.KeyPairGenerator through getInstance(type), initialize(bits, random), and generateKeyPair().
- The source exposes six resolved createKeyPair parameter combinations: ED25519/256, EC/384, EC/521, RSA/2048, RSA/4096, and DSA/1024.
- ED25519 reached the governed call in one measured application run.
- The observed ED25519 mismatch is a failure of this KeyPairGeneratorSpec allowlist, not evidence that Ed25519 is absent from Java's documented KeyPairGenerator algorithm names.
- The reviewed method serializes the generated private and public keys into a KeyPairBean as cloned encoded byte arrays; with the empty secret passed by this method, getEncodedPrivate returns the private key's encoded bytes rather than invoking its encryption branch.

## Forward compatibility

**Status:** `preserved`

Claim IDs: CLM-001, CLM-002, CLM-003, CLM-004, CLM-005

**Strategy:** Retain the documented ED25519 KeyPairGenerator use. The durable resolution is a version-aware update to the governing rule/allowlist rather than an application downgrade from ED25519 to a different key algorithm. Any separately authorized algorithm migration would need an explicit SSH-key storage and endpoint-compatibility migration policy.

## External sources

- [official](https://docs.oracle.com/en/java/javase/15/docs/api/java.base/java/security/KeyPairGenerator.html) — `KeyPairGenerator` provides provider-based generation of public/private key pairs; explicit initialization is recommended because defaults may differ or change; algorithm and initialization failures are documented through the applicable exceptions.
- [official](https://docs.oracle.com/en/java/javase/25/docs/specs/security/standard-names.html) — Ed25519 is a documented `KeyPairGenerator` algorithm; the specification also identifies generic names including PKCS#8, X.509, RAW, and PKCS#1.
- [official](https://docs.oracle.com/en/java/javase/15/docs/api/java.base/java/security/spec/NamedParameterSpec.html) — `NamedParameterSpec.ED25519` documents Ed25519 parameters and is available since JDK 15.
- [official](https://docs.oracle.com/en/java/javase/15/docs/api/java.base/java/security/interfaces/EdECKey.html) — Edwards-curve keys are distinct from keys represented by `ECKey` and are intended for RFC 8032-based algorithms such as EdDSA.
- [official](https://docs.oracle.com/en/java/javase/15/security/oracle-providers.html) — Oracle JDK 15 documents SunEC support for Ed25519 and specifies 255 as its default and supported integer key size for the Ed25519 key-pair generator.
- [official](https://docs.oracle.com/en/java/javase/23/migrate/security-updates.html) — EdDSA was implemented in JDK 15 and does not replace ECDSA.
- [primary](https://raw.githubusercontent.com/CROSSINGTUD/Crypto-API-Rules/master/JavaCryptographicArchitecture/src/KeyPairGenerator.crysl) — The rule declares `SPEC java.security.KeyPairGenerator`, models `getInstance`, `initialize`, `generateKeyPair`, and `genKeyPair` events, specifies `Get, Init, Gen`, and evaluates the quoted algorithm allowlist condition.
- [primary](https://raw.githubusercontent.com/connectbot/trilead-ssh2/main/src/main/java/com/trilead/ssh2/crypto/keys/Ed25519Provider.java) — `Ed25519Provider` declares `public static final String KEY_ALGORITHM = "Ed25519"` and registers an Ed25519 key-pair generator.
- [official](https://docs.oracle.com/en/java/javase/17/docs/specs/security/standard-names.html) — `Ed25519` is a documented `KeyPairGenerator` algorithm name; Java SE standard algorithm names are not case-sensitive.
- [primary](https://www.rfc-editor.org/rfc/rfc8709.html) — RFC 8709 specifies the SSH identifier `ssh-ed25519` and its Ed25519 public-key encoding.
