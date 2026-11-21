# Select access-point configuration by package name

## Summary

`AccessPointsAdapterData.calculateChildType` hashes the package name with `java.security.MessageDigest` to select scan-cache behavior. RVSec reported MD5 on that call. I replaced hashing with explicit package comparisons because this selector needs no cryptography; I found no security impact here.

## What does this implement/fix?

I preserved the error fallback and added four tests through `update`. Release and BETA users retain their scan-cache behavior. Builds with other package names select `SIZE_MIN`.

## Does this close any issues?

None.

## How was this tested?

Debug APK: `build_check` passed with a writable `ANDROID_USER_HOME`. JDK 21, Gradle 9.4.1, SDK 36.

`./gradlew :app:testDebugUnitTest --tests com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterDataTest ktlintCheck` passed: eight tests.

## Checklist (required before marking ready)

- [x] I added or updated unit tests (see `app/src/test/`)
- [x] I followed the project's coding style (ktlint) and formatting
- [ ] I ran lint and addressed or documented any warnings
- [ ] CI checks pass (unit tests, coverage, lint)
- [x] No sensitive data, keys, or secrets are included

## AI Assistance

AI-assisted. AI assistance was used for this contribution.

- [x] AI tools were used (Copilot, ChatGPT, Claude, etc.) — please add the **“AI assistance used”** label.
- [ ] No AI tools were used

## Additional context

No stored format changes.

## Reviewer notes

I couldn't identify a package corresponding to `TYPE2` in the build or callers; please check whether another supported package needs a case. Device behavior and RVSec acceptance remain unverified.
