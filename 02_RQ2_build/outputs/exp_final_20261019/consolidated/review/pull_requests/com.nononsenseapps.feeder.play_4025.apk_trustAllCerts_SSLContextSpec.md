<!-- PREVIEW ONLY: this Pull request has not been published to GitHub. -->

# Use platform trust managers for TLS setup

**Repository:** `spacecowboy/Feeder`  
**Type:** Pull request

## Finding review

| Field | Value |
|---|---|
| Finding ID | `3b969f2358c1` |
| Solution revision | `d7762e8b1b` |
| Preview status | `REVIEW_ONLY_NOT_PUBLISHABLE` |
| Manual review required | `yes` |
| Auto-apply allowed | `no` |
| Publication allowed by analysis | `no` |
| Build validation | `success_app_module_only` |
| Commit | `e433c91341e60c178f70e72f96dfef51f4a0e5dd` |

This artifact is review-only. After reviewing the evidence and diff, authorize this exact finding with:

```bash
--approve-reviewed-finding 3b969f2358c1
```

This flag is a publication authorization; build success alone does not approve the finding.

---

## Summary

I found that `trustAllCerts` created an app-defined trust manager with empty certificate checks, passed it to `SSLContext.init`, reused it for OkHttp, and installed a hostname verifier that always returned true. This violates the SSLContext analysis rule because the manager does not come from a `TrustManagerFactory`.

I now initialize the platform-default `TrustManagerFactory`, select its `X509TrustManager`, and use that same manager for both SSL context and OkHttp setup. I removed the permissive hostname verifier, retained the `TLS` context selector, and handle `KeyStoreException` alongside the existing setup exceptions.

I found this while running the app under RVSec, a runtime-verification tool that watches the app's calls to the Java cryptography and TLS APIs and checks each sequence of calls against usage rules written in CrySL. `SSLContextSpec` is the rule this code did not satisfy.

## How was this tested?

- No automated test was added in this change
- [x] The patched module compiles successfully (`:app:compileFdroidDebugSources`); the full multi-module build was not completed in this environment

---

*I'm an undergraduate Computer Engineering student at the University of Brasília (UnB); this contribution is part of a research project on the analysis of Android apps, and I'm happy to adjust it to match the project's conventions.*
---

## Code change preview

### Coordinated edits

#### `app/src/main/java/com/nononsenseapps/jsonfeed/OkHttpBuilderExtensions.kt` — `imports`

````kotlin
import okhttp3.OkHttpClient
import java.security.KeyManagementException
import java.security.KeyStore
import java.security.KeyStoreException
import java.security.NoSuchAlgorithmException
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
````

#### `app/src/main/java/com/nononsenseapps/jsonfeed/OkHttpBuilderExtensions.kt` — `trustAllCerts`

````kotlin
fun OkHttpClient.Builder.trustAllCerts() {
    try {
        val trustManagerFactory =
            TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        trustManagerFactory.init(null as KeyStore?)
        val trustManager =
            trustManagerFactory.trustManagers.filterIsInstance<X509TrustManager>().firstOrNull()
                ?: throw NoSuchAlgorithmException("No X509TrustManager available")

        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, arrayOf<TrustManager>(trustManager), null)
        val sslSocketFactory = sslContext.socketFactory

        sslSocketFactory(sslSocketFactory, trustManager)
    } catch (e: NoSuchAlgorithmException) {
        // ignore
    } catch (e: KeyManagementException) {
        // ignore
    } catch (e: KeyStoreException) {
        // ignore
    }
}
````

### Unified diff

````diff
diff --git a/app/src/main/java/com/nononsenseapps/jsonfeed/OkHttpBuilderExtensions.kt b/app/src/main/java/com/nononsenseapps/jsonfeed/OkHttpBuilderExtensions.kt
index 3c66965..0a1bfc6 100644
--- a/app/src/main/java/com/nononsenseapps/jsonfeed/OkHttpBuilderExtensions.kt
+++ b/app/src/main/java/com/nononsenseapps/jsonfeed/OkHttpBuilderExtensions.kt
@@ -2,41 +2,33 @@ package com.nononsenseapps.jsonfeed
 
 import okhttp3.OkHttpClient
 import java.security.KeyManagementException
+import java.security.KeyStore
+import java.security.KeyStoreException
 import java.security.NoSuchAlgorithmException
-import java.security.cert.X509Certificate
-import javax.net.ssl.HostnameVerifier
 import javax.net.ssl.SSLContext
 import javax.net.ssl.TrustManager
+import javax.net.ssl.TrustManagerFactory
 import javax.net.ssl.X509TrustManager
 
 fun OkHttpClient.Builder.trustAllCerts() {
     try {
+        val trustManagerFactory =
+            TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
+        trustManagerFactory.init(null as KeyStore?)
         val trustManager =
-            object : X509TrustManager {
-                override fun checkClientTrusted(
-                    chain: Array<out X509Certificate>?,
-                    authType: String?,
-                ) {
-                }
-
-                override fun checkServerTrusted(
-                    chain: Array<out X509Certificate>?,
-                    authType: String?,
-                ) {
-                }
-
-                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
-            }
+            trustManagerFactory.trustManagers.filterIsInstance<X509TrustManager>().firstOrNull()
+                ?: throw NoSuchAlgorithmException("No X509TrustManager available")
 
         val sslContext = SSLContext.getInstance("TLS")
         sslContext.init(null, arrayOf<TrustManager>(trustManager), null)
         val sslSocketFactory = sslContext.socketFactory
 
         sslSocketFactory(sslSocketFactory, trustManager)
-            .hostnameVerifier(HostnameVerifier { _, _ -> true })
     } catch (e: NoSuchAlgorithmException) {
         // ignore
     } catch (e: KeyManagementException) {
         // ignore
+    } catch (e: KeyStoreException) {
+        // ignore
     }
 }
````
