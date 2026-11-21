# Select access point cache behavior by package name

## Summary

I found that `AccessPointsAdapterData` hashes the app package name with Java `MessageDigest` and uses the result to select scan-cache behavior. RVSec reported the MD5 call. I replaced it with explicit checks for the release and debug package names because the hash is only used to select between those behaviors.

## What does this implement/fix?

The two package names declared by this project retain their current selection. Other package names use the smaller-cache path; I added tests for both declared names and another name.

## Does this close any issues?

None known.

## How was this tested?

The debug app build passed with `build_check`, and `:app:testDebugUnitTest --tests com.vrem.wifianalyzer.wifi.accesspoint.AccessPointsAdapterDataTest` passed. I used a writable `ANDROID_USER_HOME` for the build. I did not test on a device.

## Checklist (required before marking ready)

- [x] I added or updated unit tests
- [ ] I followed the project's coding style (ktlint) and formatting
- [ ] I ran lint and addressed or documented any warnings
- [ ] CI checks pass (unit tests, coverage, lint)
- [x] No sensitive data, keys, or secrets are included

## AI Assistance

- [x] AI tools were used — AI-assisted; please add the **“AI assistance used”** label.
- [ ] No AI tools were used

AI assistance was used for this contribution.

## Additional context

The old selector includes a third integer whose package name I could not identify in this repository. Please check whether another distributed package relies on it. RVSec has not been rerun on this change.

## Reviewer notes

Please check the package-name mapping for any other supported builds.
