<!-- PREVIEW ONLY: this Pull request has not been published to GitHub. -->

# Use an unseeded SecureRandom for filename selection

**Repository:** `Crazy-Marvin/MetadataRemover`  
**Type:** Pull request

## Finding review

| Field | Value |
|---|---|
| Finding ID | `40d39da41dec` |
| Solution revision | `be04b4d231` |
| Preview status | `BUILD_VALIDATION_BLOCKED` |
| Manual review required | `yes` |
| Auto-apply allowed | `no` |
| Publication allowed by analysis | `no` |
| Build validation | `failed` |
| Commit | `missing` |

This artifact is review-only and cannot be approved for publication yet: build status is failed; commit hash is missing.

---

## Summary

Affects `MainViewModel.<clinit>` in `app/src/main/java/rocks/poopjournal/metadataremover/viewmodel/MainViewModel.kt`.

I found that the companion object initializes `SecureRandom` with UTF-8 bytes from a compiled string literal. This violates `SecureRandomSpec` because that constructor expects seed bytes from a randomized source, not fixed literal data.

I replaced the literal-seeded constructor call with `SecureRandom()`. This retains the shared random instance used for bounded filename selection and timestamps without supplying compiled seed material.

I found this while running the app under RVSec, a runtime-verification tool that watches the app's calls to the Java cryptography and TLS APIs and checks each sequence of calls against usage rules written in CrySL. `SecureRandomSpec` is the rule this code did not satisfy.

## How was this tested?

- No automated test was added in this change
- [ ] Build validation has not completed successfully (local preview only)

---

*I'm an undergraduate Computer Engineering student at the University of Brasília (UnB); this contribution is part of a research project on the analysis of Android apps, and I'm happy to adjust it to match the project's conventions.*
---

## Code change preview

### Coordinated edits

#### `app/src/main/java/rocks/poopjournal/metadataremover/viewmodel/MainViewModel.kt` — `MainViewModel.Companion.random`

````kotlin
        val random = SecureRandom()
````

### Unified diff

````diff
diff --git a/app/src/main/java/rocks/poopjournal/metadataremover/viewmodel/MainViewModel.kt b/app/src/main/java/rocks/poopjournal/metadataremover/viewmodel/MainViewModel.kt
index 8773368..5f6f5ea 100644
--- a/app/src/main/java/rocks/poopjournal/metadataremover/viewmodel/MainViewModel.kt
+++ b/app/src/main/java/rocks/poopjournal/metadataremover/viewmodel/MainViewModel.kt
@@ -43,7 +43,7 @@ class MainViewModel @Inject constructor(
 ) : ViewModel() {
 
     companion object {
-        val random = SecureRandom("75rgu86gr59ht86".toByteArray(Charsets.UTF_8))
+        val random = SecureRandom()
         const val FILE_NAME_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
         const val FILE_NAME_LENGTH = 10
     }
````
