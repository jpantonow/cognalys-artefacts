# No action

The runtime checker flagged the use of MD5 in `AccessPointsAdapterData.calculateChildType` (the same pattern also appears in `GraphWrapper.calculateGraphType`). I looked at the code and I don't think the maintainers need to hear about it.

The digest is not used for anything security-related. The code hashes the app's package name, reduces the result to an `Int` with `contentHashCode()`, and compares it against three fixed constants (`TYPE1`, `TYPE2`, `TYPE3`). If it matches, the list size is `SIZE_MAX`. Otherwise it is `SIZE_MIN`. No secret, signature, password or integrity guarantee depends on MD5, so collision or preimage weaknesses don't apply. It is only a cheap fingerprint of a public string.

Switching to SHA-256 would not be a drop-in fix. The three constants were precomputed from the current MD5-based value, so they would all have to be regenerated for every package name the project ships. That would change a mechanism the maintainers set up on purpose, and I can't tell from the repository what it is meant to cover. A mistake would silently cap the list size for legitimate builds. A cosmetic change to satisfy a scanner doesn't justify that risk, and I didn't build or test one.

Nothing here is a vulnerability, so I'm not sending a PR or an issue.
