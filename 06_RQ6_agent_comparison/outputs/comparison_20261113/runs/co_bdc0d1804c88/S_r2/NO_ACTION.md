# No action: MD5 use in AccessPointsAdapterData is not a security use

A runtime crypto-usage checker flagged `MessageDigest.getInstance("MD5")` in `AccessPointsAdapterData.calculateChildType`, saying it expected a SHA-2 algorithm. I looked at the code and I don't think the maintainers need to hear about it.

**What the code does.** The method hashes the app's package name (`com.vrem.wifianalyzer`, plus the `.BETA` suffix for debug builds). It folds the digest into an `Int` with `contentHashCode()` and compares that to three fixed constants, `TYPE1`, `TYPE2` and `TYPE3`, in `GraphConstants.kt`. The result only picks between `SIZE_MAX` and `SIZE_MIN` for a UI size setting. `GraphWrapper.calculateGraphType` does the same thing.

**Why MD5 is not a vulnerability here.**
- The input is a public, constant string, not user data, a password or a secret.
- Nothing is signed, authenticated or verified, and no integrity guarantee depends on collision resistance.
- An attacker gains nothing from MD5 weaknesses. Anyone can already read the package name and change the constants or the comparison by repackaging the APK.

**Why changing it would be a bad idea.** `TYPE1` to `TYPE3` are precomputed values of this exact MD5-derived hash. Switching to SHA-256 would change the computed value, so it would never match the constants. Every build would then fall into the `SIZE_MIN` branch, which silently changes app behaviour. To keep behaviour the same, the constants would have to be recomputed and changed in the same patch, and that adds churn for no security benefit. It would also only quiet a warning.

**Conclusion.** The finding is a false positive for this usage. A PR would change behaviour or add churn for no gain, and an issue would only ask the maintainers to triage a non-problem. I made no code changes and am sending nothing.
