<!-- PREVIEW ONLY: this Pull request has not been published to GitHub. -->

# Use SHA-256 for disk-cache file names

**Repository:** `SapuSeven/BetterUntis`  
**Type:** Pull request

## Finding review

| Field | Value |
|---|---|
| Finding ID | `21b3aced6bcf` |
| Solution revision | `47b1165883` |
| Preview status | `REVIEW_ONLY_NOT_PUBLISHABLE` |
| Manual review required | `yes` |
| Auto-apply allowed | `no` |
| Publication allowed by analysis | `no` |
| Build validation | `success` |
| Commit | `05a2e50813f9424abe92f81b044abfe38e36fc04` |

This artifact is review-only. After reviewing the evidence and diff, authorize this exact finding with:

```bash
--approve-reviewed-finding 21b3aced6bcf
```

This flag is a publication authorization; build success alone does not approve the finding.

---

## Summary

I found that `getCacheFile` creates disk-cache file names by hashing each cache key with MD5. The MessageDigestSpec tool rule accepts SHA-256, SHA-384, SHA-512, SHA256, SHA384, and SHA512, so MD5 does not meet that rule.

I changed the digest algorithm to SHA-256 while preserving the method signature, `File` return type, and digest-to-hex filename derivation. The change is limited to the selected hashing algorithm.

I found this while running the app under RVSec, a runtime-verification tool that watches the app's calls to the Java cryptography and TLS APIs and checks each sequence of calls against usage rules written in CrySL. `MessageDigestSpec` is the rule this code did not satisfy.

## How was this tested?

- No automated test was added in this change
- [x] Project compiles successfully (`assembleDebug`)

---

*I'm an undergraduate Computer Engineering student at the University of Brasília (UnB); this contribution is part of a research project on the analysis of Android apps, and I'm happy to adjust it to match the project's conventions.*
---

## Code change preview

### Coordinated edits

#### `core/data/src/main/kotlin/com/sapuseven/untis/core/data/cache/DiskCache.kt` — `DiskCache.getCacheFile`

````kotlin
	@OptIn(ExperimentalStdlibApi::class)
	private fun getCacheFile(key: CacheKey<KeyType>): File {
		val md = MessageDigest.getInstance("SHA-256")
		return File(cacheDir, md.digest(key.toString().toByteArray()).toHexString())
	}
````

### Unified diff

````diff
diff --git a/core/data/src/main/kotlin/com/sapuseven/untis/core/data/cache/DiskCache.kt b/core/data/src/main/kotlin/com/sapuseven/untis/core/data/cache/DiskCache.kt
index b550ed62..db575527 100644
--- a/core/data/src/main/kotlin/com/sapuseven/untis/core/data/cache/DiskCache.kt
+++ b/core/data/src/main/kotlin/com/sapuseven/untis/core/data/cache/DiskCache.kt
@@ -59,7 +59,7 @@ class DiskCache<KeyType : Any, ValueType : Any>(
 
 	@OptIn(ExperimentalStdlibApi::class)
 	private fun getCacheFile(key: CacheKey<KeyType>): File {
-		val md = MessageDigest.getInstance("MD5")
+		val md = MessageDigest.getInstance("SHA-256")
 		return File(cacheDir, md.digest(key.toString().toByteArray()).toHexString())
 	}
 }
````
