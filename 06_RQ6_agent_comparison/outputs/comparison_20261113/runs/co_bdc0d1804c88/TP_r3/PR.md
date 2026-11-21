# Select access point configuration by package name

## Summary

I found that the access point list hashes the app's package name with Java `MessageDigest` to choose the scan configuration. RVSec reported its use of MD5. I replaced that calculation with direct package-name checks because the digest is used to select a configuration, not to protect data.

## What does this implement/fix?

The release and debug package names retain their existing selection, as does the fallback when package lookup fails. I added unit tests for those paths and an unrelated package.

## Does this close any issues?

None identified.

## How was this tested?

The debug APK build passed with `/home/antonow/comparison_20261113/bin/build_check`. The focused `AccessPointsAdapterDataTest` unit tests passed. I did not run device tests, lint, or the full test suite.

## Checklist (required before marking ready)

- [x] I added or updated unit tests (see `app/src/test/`)
- [ ] I followed the project's coding style (ktlint) and formatting
- [ ] I ran lint and addressed or documented any warnings
- [ ] CI checks pass (unit tests, coverage, lint)
- [x] No sensitive data, keys, or secrets are included

## AI Assistance

- [x] AI tools were used (Copilot, ChatGPT, Claude, etc.) — AI-assisted.
- [ ] No AI tools were used

AI assistance was used for this contribution.

## Additional context

The existing comparison also includes a third numeric value whose package name I could not identify. Please check whether any other distributed package needs that selection; those packages now take the default path. The graph code has a separate calculation that this change does not touch.

## Reviewer notes

Please check the package mapping and whether RVSec accepts the change; neither was verified by the build.
