# Compare package name directly in AccessPointsAdapterData

Thanks for sending a pull request!

## Summary
- `AccessPointsAdapterData` hashed the app's package name with `MessageDigest.getInstance("MD5")`, took `contentHashCode()` of the result, and compared it with `TYPE1`/`TYPE2`/`TYPE3` to choose `SIZE_MAX` or `SIZE_MIN`. RVSec reported the MD5 call. The hash only picks a variant, so I replaced it with a direct package name check instead of a different algorithm.

## What does this implement/fix?
- `AccessPointsAdapterData.kt`: `calculateChildType`/`type` become `calculateSize`, which checks the package name against `com.vrem.wifianalyzer` and `com.vrem.wifianalyzer.BETA`. I worked out by hand that `TYPE1` and `TYPE3` are the MD5 hash codes of those two names. If the package name can't be read, it still falls back to `SIZE_MAX`.
- `AccessPointsAdapterDataTest.kt`: added tests for the release, BETA, other and unreadable cases.

## Does this close any issues?
- No.

## How was this tested?
- `assembleDebug` built successfully. I did not run the unit tests, ktlint or lint, and did not test on a device.

## Checklist
- [x] I added or updated unit tests (see `app/src/test/`)
- [ ] I followed the project's coding style (ktlint) and formatting
- [ ] I ran lint and addressed or documented any warnings
- [ ] CI checks pass (unit tests, coverage, lint)
- [x] No sensitive data, keys, or secrets are included

## AI Assistance
- [x] AI tools were used — please add the **“AI assistance used”** label.

AI assistance was used for this contribution.

## Reviewer notes
- I could not find which package name `TYPE2` stands for, so it is not in the new check. Any build with that package name would now get `SIZE_MIN`. Please add it if one exists.
- `GraphWrapper.calculateGraphType` has the same MD5 check. RVSec did not report it, so I left it and the `TYPE` constants alone.
- Decision for you: I put the package names directly in this class; the alternative is one shared list used here and in `GraphWrapper`.
