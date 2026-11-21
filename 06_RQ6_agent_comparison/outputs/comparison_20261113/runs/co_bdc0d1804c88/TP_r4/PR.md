# Select access point cache size by package name

## Summary

I found that the access point adapter uses Java `MessageDigest` with MD5 on the app's package name to choose a cache size. RVSec reported the MD5 call. I replaced the digest comparison with direct checks for the release and debug package names; those builds keep their existing selection.

I made this change because RVSec reports the call; I found no security use for this digest. The fallback on an error reading the package name remains the same.

## What does this implement/fix?

The change is limited to `AccessPointsAdapterData` and its unit tests.

## Does this close any issues?

None identified.

## How was this tested?

- Debug APK: `/home/antonow/comparison_20261113/bin/build_check` passed with a writable temporary Java home.
- Focused tests: `./gradlew :app:testDebugUnitTest --tests com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterDataTest` passed.

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

I could not identify the package name associated with the third existing numeric value. Please check any separately built variants that may rely on it and rerun RVSec on the APK.

## Reviewer notes

The graph screens have a separate package-name calculation that this change does not alter.
