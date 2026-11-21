<!-- PREVIEW ONLY: this Pull request has not been published to GitHub. -->

# Use a factory-provided trust manager for WebDAV clients

**Repository:** `aivanovski/kpassnotes`  
**Type:** Pull request

## Finding review

| Field | Value |
|---|---|
| Finding ID | `6d29118e4263` |
| Solution revision | `4be51154ed` |
| Preview status | `REVIEW_ONLY_NOT_PUBLISHABLE` |
| Manual review required | `yes` |
| Auto-apply allowed | `no` |
| Publication allowed by analysis | `no` |
| Build validation | `success` |
| Commit | `189d56a620074e38f98ada68004e42940d441c8a` |

This artifact is review-only. After reviewing the evidence and diff, authorize this exact finding with:

```bash
--approve-reviewed-finding 6d29118e4263
```

This flag is a publication authorization; build success alone does not approve the finding.

---

## Summary

Affects `HttpClientFactory.createHttpClient` in `app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt`.

The debug `UNSECURE` WebDAV client now initializes its TLS context with an `X509TrustManager` from the default `TrustManagerFactory`, while continuing to disable hostname verification in that branch.

I replaced the application-defined no-op trust manager that was passed directly to `SSLContext.init`, which did not follow the `SSLContextSpec` usage rule. I also removed the unused trust-all implementation and imports. This is safe because the existing method signature, debug branch condition, and `TLS` context selector remain unchanged.

I found this while running the app under RVSec, a runtime-verification tool that watches the app's calls to the Java cryptography and TLS APIs and checks each sequence of calls against usage rules written in CrySL. `SSLContextSpec` is the rule this code did not satisfy.

## How was this tested?

- No automated test was added in this change
- [x] Project compiles successfully (`:app:assembleAutomationDebug`)

---

*I'm an undergraduate Computer Engineering student at the University of Brasília (UnB); this contribution is part of a research project on the analysis of Android apps, and I'm happy to adjust it to match the project's conventions.*
---

## Code change preview

### Coordinated edits

#### `app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt` — `HttpClientFactory imports`

````kotlin
import com.ivanovsky.passnotes.BuildConfig
import java.security.KeyStore
import java.security.SecureRandom
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import okhttp3.OkHttp
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import timber.log.Timber
````

#### `app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt` — `HttpClientFactory.createHttpClient`

````kotlin
    fun createHttpClient(type: HttpClientType): OkHttpClient {
        val builder = OkHttpClient.Builder()

        val interceptor = HttpLoggingInterceptor {
            Timber.tag(OkHttp::class.java.simpleName).d(it)
        }.apply {
            setLevel(HttpLoggingInterceptor.Level.BASIC)
        }

        builder.addInterceptor(interceptor)

        if (BuildConfig.DEBUG && type == HttpClientType.UNSECURE) {
            Timber.w("--------------------------------------------")
            Timber.w("--                                        --")
            Timber.w("--                                        --")
            Timber.w("-- SSL hostname verification is disabled   --")
            Timber.w("--                                        --")
            Timber.w("--                                        --")
            Timber.w("--------------------------------------------")

            val trustManagerFactory = TrustManagerFactory.getInstance(
                TrustManagerFactory.getDefaultAlgorithm()
            )
            trustManagerFactory.init(null as KeyStore?)
            val trustManager = trustManagerFactory.trustManagers
                .filterIsInstance<X509TrustManager>()
                .firstOrNull()
                ?: throw IllegalStateException("No X509TrustManager available")
            val sslContext = SSLContext.getInstance("TLS")
            sslContext.init(null, arrayOf(trustManager), SecureRandom())

            builder.sslSocketFactory(sslContext.socketFactory, trustManager)
            builder.hostnameVerifier { _, _ -> true }
        }

        return builder.build()
    }
````

#### `app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt` — `HttpClientFactory.createUnsecuredTrustManager`

````kotlin
// Deleted: HttpClientFactory.createUnsecuredTrustManager
````

### Unified diff

````diff
diff --git a/app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt b/app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt
index fdc3802..4200902 100644
--- a/app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt
+++ b/app/src/main/kotlin/com/ivanovsky/passnotes/data/repository/file/webdav/HttpClientFactory.kt
@@ -1,10 +1,10 @@
 package com.ivanovsky.passnotes.data.repository.file.webdav
 
-import android.annotation.SuppressLint
 import com.ivanovsky.passnotes.BuildConfig
+import java.security.KeyStore
 import java.security.SecureRandom
-import java.security.cert.X509Certificate
 import javax.net.ssl.SSLContext
+import javax.net.ssl.TrustManagerFactory
 import javax.net.ssl.X509TrustManager
 import okhttp3.OkHttp
 import okhttp3.OkHttpClient
@@ -28,42 +28,27 @@ object HttpClientFactory {
             Timber.w("--------------------------------------------")
             Timber.w("--                                        --")
             Timber.w("--                                        --")
-            Timber.w("-- SSL Certificate validation is disabled --")
+            Timber.w("-- SSL hostname verification is disabled   --")
             Timber.w("--                                        --")
             Timber.w("--                                        --")
             Timber.w("--------------------------------------------")
 
-            val unsecuredTrustManager = createUnsecuredTrustManager()
+            val trustManagerFactory = TrustManagerFactory.getInstance(
+                TrustManagerFactory.getDefaultAlgorithm()
+            )
+            trustManagerFactory.init(null as KeyStore?)
+            val trustManager = trustManagerFactory.trustManagers
+                .filterIsInstance<X509TrustManager>()
+                .firstOrNull()
+                ?: throw IllegalStateException("No X509TrustManager available")
             val sslContext = SSLContext.getInstance("TLS")
-            sslContext.init(null, arrayOf(unsecuredTrustManager), SecureRandom())
+            sslContext.init(null, arrayOf(trustManager), SecureRandom())
 
-            builder.sslSocketFactory(sslContext.socketFactory, unsecuredTrustManager)
+            builder.sslSocketFactory(sslContext.socketFactory, trustManager)
             builder.hostnameVerifier { _, _ -> true }
         }
 
         return builder.build()
     }
 
-    @SuppressLint("CustomX509TrustManager")
-    private fun createUnsecuredTrustManager(): X509TrustManager {
-        return object : X509TrustManager {
-            @SuppressLint("TrustAllX509TrustManager")
-            override fun checkClientTrusted(
-                chain: Array<out X509Certificate>?,
-                authType: String?
-            ) {
-            }
-
-            @SuppressLint("TrustAllX509TrustManager")
-            override fun checkServerTrusted(
-                chain: Array<out X509Certificate>?,
-                authType: String?
-            ) {
-            }
-
-            override fun getAcceptedIssuers(): Array<X509Certificate> {
-                return arrayOf()
-            }
-        }
-    }
 }
\ No newline at end of file
````
