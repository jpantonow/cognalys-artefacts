<!-- PREVIEW ONLY: this Pull request has not been published to GitHub. -->

# Use KeyStore.getKey when reading the encryption key

**Repository:** `christianrowlands/android-network-survey`  
**Type:** Pull request

## Finding review

| Field | Value |
|---|---|
| Finding ID | `ac1a8dd4d680` |
| Solution revision | `eccca7a79a` |
| Preview status | `REVIEW_ONLY_NOT_PUBLISHABLE` |
| Manual review required | `yes` |
| Auto-apply allowed | `no` |
| Publication allowed by analysis | `no` |
| Build validation | `success` |
| Commit | `ccf0358df471ef8e1bd7e09158220716d2235b73` |

This artifact is review-only. After reviewing the evidence and diff, authorize this exact finding with:

```bash
--approve-reviewed-finding ac1a8dd4d680
```

This flag is a publication authorization; build success alone does not approve the finding.

---

## Summary

`getOrCreateKey` currently reads the existing Android keystore entry with `getEntry` and extracts its `SecretKey`; this call sequence does not match `KeyStoreSpec`’s accepted usage.

I changed the read branch to call `getKey(KEY_ALIAS, null)` and use the result only when it is a `SecretKey`. The existing key-generation fallback remains unchanged, so the method still creates a key when no usable existing key is returned.

I found this while running the app under RVSec, a runtime-verification tool that watches the app's calls to the Java cryptography and TLS APIs and checks each sequence of calls against usage rules written in CrySL. `KeyStoreSpec` is the rule this code did not satisfy.

## How was this tested?

- No automated test was added in this change
- [x] Project compiles successfully (`:networksurvey:assembleCdrDebug`)

---

*I'm an undergraduate Computer Engineering student at the University of Brasília (UnB); this contribution is part of a research project on the analysis of Android apps, and I'm happy to adjust it to match the project's conventions.*
---

## Code change preview

### Coordinated edits

#### `networksurvey/src/main/java/com/craxiom/networksurvey/util/CryptoManager.kt` — `CryptoManager.getOrCreateKey`

````kotlin
    /**
     * Get or create the encryption key
     */
    private fun getOrCreateKey(): SecretKey {
        val existingKey = keyStore.getKey(KEY_ALIAS, null) as? SecretKey
        return existingKey ?: createKey()
    }
````

### Unified diff

````diff
diff --git a/networksurvey/src/main/java/com/craxiom/networksurvey/util/CryptoManager.kt b/networksurvey/src/main/java/com/craxiom/networksurvey/util/CryptoManager.kt
index f151c38..57227ec 100644
--- a/networksurvey/src/main/java/com/craxiom/networksurvey/util/CryptoManager.kt
+++ b/networksurvey/src/main/java/com/craxiom/networksurvey/util/CryptoManager.kt
@@ -36,8 +36,8 @@ class CryptoManager {
      * Get or create the encryption key
      */
     private fun getOrCreateKey(): SecretKey {
-        val existingKey = keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry
-        return existingKey?.secretKey ?: createKey()
+        val existingKey = keyStore.getKey(KEY_ALIAS, null) as? SecretKey
+        return existingKey ?: createKey()
     }
 
     /**
````
