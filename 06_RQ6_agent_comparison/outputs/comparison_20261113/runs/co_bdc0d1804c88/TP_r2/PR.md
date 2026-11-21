# Select access point scan configuration by package name

## Summary

I found that the access point list hashes the app package name with Java `MessageDigest` and uses the result to select the scan configuration. RVSec reported its MD5 call. I replaced the digest with direct package name checks because this selection does not need a cryptographic hash.

## What does this implement/fix?

The release and debug package names keep their existing configuration, as does the fallback when the package name cannot be obtained. I added tests for those paths and another package name.

## Does this close any issues?

None identified.

## How was this tested?

- Debug app build: `/home/antonow/comparison_20261113/bin/build_check` passed with `ANDROID_USER_HOME` set to a writable directory.
- Focused `AccessPointsAdapterDataTest` unit tests passed with `./gradlew :app:testDebugUnitTest --tests com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterDataTest`.
- No device test was run.

## Checklist (required before marking ready)

- [x] I added or updated unit tests (see `app/src/test/`)
- [ ] I followed the project's coding style (ktlint) and formatting
- [ ] I ran lint and addressed or documented any warnings
- [ ] CI checks pass (unit tests, coverage, lint)
- [x] No sensitive data, keys, or secrets are included

## AI Assistance

- [x] AI tools were used (Copilot, ChatGPT, Claude, etc.) — please add the **“AI assistance used”** label.
- [ ] No AI tools were used

AI assistance was used for this contribution.

## Additional context

The old comparison also recognizes a third hash value whose package name I could not identify. Please check whether any other distribution uses it; that package would now receive the smaller configuration. I could not verify the change with RVSec on a device.

## Reviewer notes

Please check the package names used by other distributions.
