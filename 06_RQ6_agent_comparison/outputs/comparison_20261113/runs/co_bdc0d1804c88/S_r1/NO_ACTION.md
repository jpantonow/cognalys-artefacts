No report or change is needed.

The runtime checker flagged the use of MD5 in the access point list's size calculation (`AccessPointsAdapterData.calculateChildType`). The same pattern appears in `GraphWrapper.calculateGraphType`.

In both places MD5 is not used for anything security-sensitive. It hashes the app's own package name, which is a public string. The resulting bytes are folded into a 32-bit `contentHashCode()`, and that value is only compared with three constants (`TYPE1`, `TYPE2`, `TYPE3`) to pick a display size. No secret, signature, integrity check or authentication depends on it. MD5's weakness (deliberate collisions) gives an attacker nothing here, because the input is a fixed public string and the output is a 32-bit value.

Switching to SHA-256 would only quiet the warning. The three constants would also have to be recomputed and kept in sync in two places, and the current behaviour could regress if any of them were wrong. That is a behaviour change with some risk, for no security benefit.

The warning is a false positive for this use, so I am not sending a PR or an issue to the maintainers. I did not change any code, so there was nothing to build.
