<!-- PREVIEW ONLY: this Pull request has not been published to GitHub. -->

# Use KeyStore.getKey to read secret keys

**Repository:** `leekleak/traffic-light`  
**Type:** Pull request

## Finding review

| Field | Value |
|---|---|
| Finding ID | `32249b3e5e5d` |
| Solution revision | `ac66f81076` |
| Preview status | `REVIEW_ONLY_NOT_PUBLISHABLE` |
| Manual review required | `yes` |
| Auto-apply allowed | `no` |
| Publication allowed by analysis | `no` |
| Build validation | `success` |
| Commit | `b35609dbd240cbc769a03230effbc0ac03a51dcd` |

This artifact is review-only. After reviewing the evidence and diff, authorize this exact finding with:

```bash
--approve-reviewed-finding 32249b3e5e5d
```

This flag is a publication authorization; build success alone does not approve the finding.

---

## Summary

`getSecretKey` currently reads an entry with `KeyStore.getEntry` and extracts its `SecretKeyEntry.secretKey`. The KeyStoreSpec rule expects this read path to use `KeyStore.getKey` after loading the keystore.

I changed the lookup to call `getKey(alias, null)` and cache the result only when it is a `SecretKey`. The existing cache-miss fallback still creates a key for absent or unusable values, so the cache and key-creation behavior remain intact.

I found this while running the app under RVSec, a runtime-verification tool that watches the app's calls to the Java cryptography and TLS APIs and checks each sequence of calls against usage rules written in CrySL. `KeyStoreSpec` is the rule this code did not satisfy.

## How was this tested?

- No automated test was added in this change
- [x] Project compiles successfully (`:app:assembleFullDebug`)

---

*I'm an undergraduate Computer Engineering student at the University of Brasília (UnB); this contribution is part of a research project on the analysis of Android apps, and I'm happy to adjust it to match the project's conventions.*
---

## Code change preview

### Coordinated edits

#### `app/src/main/java/com/leekleak/trafficlight/database/CryptoManager.kt` — `CryptoManager.getSecretKey`

````kotlin
    private fun getSecretKey(alias: String): SecretKey {
        return keyCache.computeIfAbsent(alias) { key ->
            (keyStore.getKey(key, null) as? SecretKey) ?: createKey(key)
        }
    }
````

### Unified diff

````diff
diff --git a/app/src/main/java/com/leekleak/trafficlight/database/CryptoManager.kt b/app/src/main/java/com/leekleak/trafficlight/database/CryptoManager.kt
index d36939c..f8aa630 100644
--- a/app/src/main/java/com/leekleak/trafficlight/database/CryptoManager.kt
+++ b/app/src/main/java/com/leekleak/trafficlight/database/CryptoManager.kt
@@ -22,7 +22,7 @@ object CryptoManager {
 
     private fun getSecretKey(alias: String): SecretKey {
         return keyCache.computeIfAbsent(alias) { key ->
-            (keyStore.getEntry(key, null) as? KeyStore.SecretKeyEntry)?.secretKey ?: createKey(key)
+            (keyStore.getKey(key, null) as? SecretKey) ?: createKey(key)
         }
     }
 
````
