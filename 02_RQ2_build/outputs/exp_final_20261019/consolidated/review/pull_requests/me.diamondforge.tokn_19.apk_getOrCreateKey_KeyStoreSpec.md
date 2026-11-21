<!-- PREVIEW ONLY: this Pull request has not been published to GitHub. -->

# Read Android keystore keys with getKey

**Repository:** `fthomys/tokn`  
**Type:** Pull request

## Finding review

| Field | Value |
|---|---|
| Finding ID | `0aeb52bd220d` |
| Solution revision | `93a7a5a1b3` |
| Preview status | `REVIEW_ONLY_NOT_PUBLISHABLE` |
| Manual review required | `yes` |
| Auto-apply allowed | `no` |
| Publication allowed by analysis | `no` |
| Build validation | `success` |
| Commit | `e94899d4b623aae18dc813bf5e789838cea2f4c9` |

This artifact is review-only. After reviewing the evidence and diff, authorize this exact finding with:

```bash
--approve-reviewed-finding 0aeb52bd220d
```

This flag is a publication authorization; build success alone does not approve the finding.

---

## Summary

Affects `KeystoreManager.getOrCreateKey` in `core/security/src/main/java/me/diamondforge/tokn/security/KeystoreManager.kt`.

I found that the keystore manager creates an AES key when needed, then reads an existing key through `getEntry`. The KeyStoreSpec usage rule requires a `getEntry` read to be immediately followed by `getKey`, but this path did not call `getKey`.

I replaced the `SecretKeyEntry` lookup with `keystore.getKey(KEY_ALIAS, null)` and cast the result to `SecretKey`. This keeps the existing AndroidKeyStore setup and key-creation flow unchanged while returning the same key type to its callers.

I found this while running the app under RVSec, a runtime-verification tool that watches the app's calls to the Java cryptography and TLS APIs and checks each sequence of calls against usage rules written in CrySL. `KeyStoreSpec` is the rule this code did not satisfy.

## How was this tested?

- No automated test was added in this change
- [x] Project compiles successfully (`assembleDebug`)

---

*I'm an undergraduate Computer Engineering student at the University of Brasília (UnB); this contribution is part of a research project on the analysis of Android apps, and I'm happy to adjust it to match the project's conventions.*
---

## Code change preview

### Coordinated edits

#### `core/security/src/main/java/me/diamondforge/tokn/security/KeystoreManager.kt` — `KeystoreManager.getOrCreateKey`

````kotlin
    private fun getOrCreateKey(): SecretKey {
        if (!keystore.containsAlias(KEY_ALIAS)) {
            val keyGen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
            keyGen.init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .setUserAuthenticationRequired(false)
                    .build(),
            )
            keyGen.generateKey()
        }
        return keystore.getKey(KEY_ALIAS, null) as SecretKey
    }
````

### Unified diff

````diff
diff --git a/core/security/src/main/java/me/diamondforge/tokn/security/KeystoreManager.kt b/core/security/src/main/java/me/diamondforge/tokn/security/KeystoreManager.kt
index 6569298..0f25e90 100644
--- a/core/security/src/main/java/me/diamondforge/tokn/security/KeystoreManager.kt
+++ b/core/security/src/main/java/me/diamondforge/tokn/security/KeystoreManager.kt
@@ -85,7 +85,7 @@ open class KeystoreManager @Inject constructor(
             )
             keyGen.generateKey()
         }
-        return (keystore.getEntry(KEY_ALIAS, null) as KeyStore.SecretKeyEntry).secretKey
+        return keystore.getKey(KEY_ALIAS, null) as SecretKey
     }
 
     open fun hasBiometricKey(): Boolean = keystore.containsAlias(BIOMETRIC_KEY_ALIAS)
````
