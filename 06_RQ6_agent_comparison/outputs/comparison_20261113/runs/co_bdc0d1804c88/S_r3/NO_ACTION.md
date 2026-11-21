No report or change is being sent to the maintainers.

The runtime checker flagged the use of MD5 in `AccessPointsAdapterData.calculateChildType` (the same pattern exists in `GraphWrapper.calculateGraphType`). The flag is correct that MD5 is used, but it does not point to a security problem here.

The code hashes the app's own package name and folds the digest into an `Int` with `contentHashCode()`. That value is only compared against three hardcoded constants (`TYPE1`, `TYPE2`, `TYPE3` in `GraphConstants.kt`) to choose between two layout sizes. Nothing is signed, authenticated, encrypted, stored or sent anywhere. The input is a public string, and the result is a 32-bit number, so collision or preimage resistance is not what protects anything. The app does not use the network either.

Switching to SHA-256 would change every digest. The three constants would have to be recomputed from the package names they were derived from. Only the release package name is visible in `app/build.gradle`, and I could not confirm what the other constants were derived from. If a constant were wrong, the release app would silently fall back to the other layout size. That is a visible behaviour change, and the change would buy nothing in security terms.

If the maintainers want to quiet this kind of tool, a code comment saying the digest is a non-security fingerprint would be enough. I don't think that justifies a pull request or an issue on its own.
