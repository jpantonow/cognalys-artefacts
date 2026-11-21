"""gabarito_v3.json: the answer key of reading v3 (2026-11-16), built from gabarito_v2.json.

Why (review of gabarito_v2.json, 2026-11-16): v2 made every deliverable acceptable for every unit but kept the
protocol key's reason text, which in 17 units tells the judge to send nothing (or that a PR is not acceptable)
and in 3 says NO_ACTION is not appropriate; the same kind of change (a rule-only change on non-security code)
was "hygiene" in two units and "pointless churn" in five; two must_not_break items forbade a lookup mechanism
(getEntry) instead of a behaviour; one key kept a maintainer response v2 was meant to drop; one said a change
"could still be flagged", which the RVSec run on the patched APK refuted (dynamic_validation_20261114).

v3 changes only the text the judges read, the same way for every arm:
- every delivery-verdict sentence ("send nothing", "a PR is not acceptable", "NO_ACTION is not appropriate")
  is removed or rewritten as the constraint behind it; the technical analysis is kept word for word;
- one delivery policy (POLICY) is appended to every reason;
- the listed must_change / must_not_break edits; exact duplicate list items are dropped.
Every edit names the exact text it replaces; the build aborts when that text is missing.
Run: python3 build_gabarito_v3.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

POLICY = (
    "\n\nDelivery policy (v3, the same for every unit): a pull request, an issue and no action are all "
    "acceptable deliveries; judge each on its quality, not on whether it should have been sent. A pull request "
    "that changes the code only so that it satisfies the specification, without changing behaviour, is "
    "acceptable when it is correct, keeps every must_not_break constraint and does not present itself as a "
    "vulnerability fix; do not mark it down as unnecessary churn or because the finding is a false positive. "
    "Do not guess whether the runtime checker would still report the patched code; judge the code.")

# unit -> list of (old, new) replacements in "reason"
REASON = {
    "co_0a32f516feb2": [
        ("Changing it would be cosmetic and would only quiet the checker, with no security gain. So there is no "
         "weakness to fix and nothing worth reporting; an issue would only make the maintainers close a false "
         "positive. ", "Changing it is cosmetic: the HMAC output stays the same. "),
        ("NO_ACTION is therefore reasonable for these generic allowlist findings. An ISSUE explaining the protocol "
         "requirement and optional key-label cleanup is acceptable. ", ""),
    ],
    "co_0b7656970a91": [
        ("A PR is therefore not acceptable. An issue that only reports 'SHA-1 found' would also be noise for the "
         "maintainers, because the algorithm is fixed by the third-party API and the maintainer cannot change it.",
         "A PR that replaces the digest therefore breaks the feature; the algorithm is fixed by the third-party API."),
        ("This algorithm-only warning does not establish a vulnerability requiring a PR or issue.",
         "This algorithm-only warning does not establish a vulnerability."),
    ],
    "co_2aaa72c51c14": [
        ("So the RVSec MessageDigestSpec finding is a false positive in context. A PR brings risk and no benefit, "
         "and an issue would only take up the maintainers' time. ",
         "So the RVSec MessageDigestSpec finding is a false positive in context. "),
        ("An issue explaining the coordinated migration is acceptable. NO_ACTION is also defensible: matching",
         "Matching"),
    ],
    "co_3c8e59dbbfb4": [
        ("A PR would bring risk to the protocol and no security gain. An issue saying SHA-1 must be replaced would "
         "mislead the maintainers, because this is a false positive for the rule's intent (non-cryptographic "
         "identifier hashing). The right response is to send nothing.",
         "This is a false positive for the rule's intent (non-cryptographic identifier hashing)."),
        (", making NO_ACTION defensible. An ISSUE can reasonably document the finding and ask about protocol "
         "constraints. A compatibility-aware hardening PR is also acceptable, but a blind", ". A blind"),
    ],
    "co_737920a862cd": [
        ("A PR that swapped in KeyManagerFactory would break client-certificate auth. An issue would only ask the "
         "maintainers to triage a checker heuristic, with nothing to fix. A careful contributor would send nothing.",
         "A PR that swapped in KeyManagerFactory would break client-certificate auth."),
        (" The reported provenance condition therefore warrants neither a corrective PR nor a defect issue.", ""),
    ],
    "co_911173ce3742": [
        ("The rule's suggested fix of swapping in SHA-256 is wrong here, so a PR cannot be accepted. An issue asking "
         "for SHA-256 would only point maintainers at a change that breaks authentication. The finding is a false "
         "positive for this code, so nothing should be sent.",
         "The rule's suggested fix of swapping in SHA-256 for the token would break authentication."),
        ("No corrective algorithm-replacement PR is justified by this report. NO_ACTION is reasonable; an ISSUE is "
         "also acceptable if it explains the protocol constraint and authentication limitation without claiming "
         "that a client-only SHA substitution fixes it.",
         "A delivery must not claim that a client-only SHA substitution of the token fixes it."),
    ],
    "co_9da181fd6978": [
        ("So the RVSec finding is a false positive for this app. A PR that changes the digest would break the "
         "feature. An issue would only tell the maintainers that the mOTP spec uses MD5, which they already know "
         "and cannot change, so nothing useful would be sent.",
         "So the RVSec finding is a false positive for this app. A PR that changes the digest would break the "
         "feature."),
        (" An issue explaining the protocol constraint and discussing coordinated migration is acceptable; taking no "
         "action on this generic rule violation is also reasonable. An algorithm-replacement PR is not justified by "
         "this finding.", ""),
    ],
    "co_bdc0d1804c88": [
        ("The check is also deliberately opaque, so a public issue or PR spelling it out would mostly document the "
         "anti-clone mechanism for the people it is meant to stop. That helps neither users nor maintainers. A "
         "careful contributor should send nothing. An ISSUE would only be noise saying \"MD5 used as a non-security "
         "fingerprint\", and a PR is pointless churn with real regression risk.",
         "A PR that changes the digest must recompute all three constants correctly; that is its regression risk."),
    ],
    "co_ed824f6818ef": [
        ("As a statement about key or IV misuse, the finding is a false positive, so sending nothing is acceptable. "
         "Real but separate weaknesses exist",
         "As a statement about key or IV misuse, the finding is a false positive. Real but separate weaknesses exist"),
        (" Pointing these out in an ISSUE, framed as hardening and not as a confirmed RVSec bug, is also acceptable. "
         "A PR is not warranted by this report.", ""),
        (" That is a design change for the maintainers to decide on, not a fix for this finding.", ""),
        (" A scoped hardening PR or an issue explaining these actual weaknesses is reasonable.", ""),
    ],
    "co_f637c4b8467e": [
        ("There is no weakness to fix, and an issue would only pass along a tool limitation. Changing the code would "
         "add risk and no security benefit: re-encoding the salt would invalidate stored hashes, and dropping the "
         "dummy salt would make login timing reveal which usernames exist.",
         "Re-encoding the salt would invalidate stored hashes, and dropping the dummy derivation would make login "
         "timing reveal which usernames exist."),
        (" NO_ACTION is therefore justified. An ISSUE may explain these paths and the report's limitations. A "
         "narrowly scoped PR replacing the dummy constant with a SecureRandom-generated dummy salt is also "
         "acceptable as optional cleanup, without claiming that existing account salts are insecure.",
         " A PR replacing the dummy constant with a SecureRandom-generated dummy salt must not claim that existing "
         "account salts are insecure."),
    ],
    "pr_013d2748f3f8": [
        ("Nothing in the code matches what the rule is meant to catch, so there is no real defect for a PR to fix "
         "and nothing worth an issue. A PR that 'fixes' this, for example by generating a fresh key on every "
         "encrypt or wrapping key creation so the checker sees it, would add risk without any security gain. Other "
         "concerns, such as CBC having no authentication, have nothing to do with this rule and do not justify "
         "sending something for this report.",
         "A PR that generates a fresh key on every encrypt would make the stored settings unreadable. Other "
         "concerns, such as CBC having no authentication, have nothing to do with this rule. The second judge "
         "found a defect in createKey (below); a PR may fix it, as must_change describes."),
        (" A focused provider fix or an issue explaining this defect is justified.", ""),
    ],
    "pr_06ff0c1fc96c": [
        ("So the careful choice is NO_ACTION. An ISSUE reporting this as a weak-crypto vulnerability would mislead "
         "the maintainers. A small hygiene PR is still acceptable and low-risk, as long as it doesn't claim to fix a "
         "security hole.",
         "An ISSUE reporting this as a weak-crypto vulnerability would mislead the maintainers. A small hygiene PR "
         "is low-risk, as long as it doesn't claim to fix a security hole."),
        (" NO_ACTION is reasonable; an ISSUE explaining this limited use and optional replacement is acceptable; a "
         "small PR replacing MD5 with SHA-256 is also acceptable as maintenance hardening.", ""),
    ],
    "pr_1381cd4ddb3f": [
        ("**Why not a PR or an issue.** Changing the code only to satisfy the order model would mean, for example, "
         "loading once lazily or creating a new `KeyStore` per call. That brings no security benefit and puts at "
         "risk the existing retry and fallback logic around KeyStore problems (the workarounds for issues #52 and "
         "#61, and the StrongBox fallback). An issue would only pass on a tool limitation, so nothing should be "
         "sent.",
         "**What a change must keep.** A change to the keystore lifecycle (for example loading once lazily, "
         "creating a new `KeyStore` per call, or reading the key through another lookup) must keep the existing "
         "retry and fallback logic around KeyStore problems (the workarounds for issues #52 and #61, and the "
         "StrongBox fallback)."),
        ("The report therefore provides no actionable app defect; its exact checker-model mismatch cannot be "
         "established without the rule and trace. A code fix or an app issue alleging insecure keystore use is "
         "unwarranted.",
         "The report provides no security defect; its exact checker-model mismatch cannot be established without "
         "the rule and trace. A delivery must not allege insecure keystore use."),
        ("\n\nPR added: a maintainer incorporated https://github.com/jenspfahl/ANOTHERpass/pull/103.", ""),
    ],
    "pr_2b8cb7f6653b": [
        ("NO_ACTION is not appropriate: the salt really is fixed and public in the default build, and the report "
         "correctly points to it.",
         "The salt really is fixed and public in the default build, and the report correctly points to it."),
        (" An issue explaining this and the compatibility implications is acceptable, as is a complete PR.", ""),
    ],
    "pr_39bba5f1270f": [
        ("No code change is needed, and an issue would only tell maintainers about a tool artifact, so the careful "
         "choice is to send nothing. ", ""),
    ],
    "pr_6d0a3cf73013": [
        ("RVSec's message is therefore correct, not a false positive, so NO_ACTION is not acceptable. A PR is "
         "appropriate: drop",
         "RVSec's message is therefore correct, not a false positive. A PR should drop"),
        (" An ISSUE is also acceptable. The secure fix", " The secure fix"),
        (" A corrective PR or an issue explaining the exposure and compatibility implications is appropriate.", ""),
    ],
    "pr_7d98a07e751d": [
        ("There is nothing for a maintainer to act on. An issue would only pass along a false positive. A PR that "
         "makes the checker quiet would have to regenerate or re-key the vault key (losing every sealed secret and "
         "the DB key) or change behaviour for no security gain. A cosmetic change (a new KeyGenerator per tier) "
         "would fix nothing real and could still be flagged.",
         "A PR must not regenerate or re-key the vault key (that would lose every sealed secret and the DB key). A "
         "change that keeps the key and the behaviour, such as a new KeyGenerator per fallback tier, is a rule-only "
         "change."),
        (" No code repair is justified by this report. NO_ACTION is acceptable; an ISSUE is also acceptable if it "
         "explains these paths and requests checker triage without asserting a confirmed vulnerability.",
         " A delivery must not assert a confirmed vulnerability."),
    ],
    "pr_9215f97597a6": [
        ("A small PR is appropriate: drop 1024 from the list and enforce a 2048-bit minimum in CertReq or where the "
         "restriction is read. An ISSUE is also acceptable. Whether",
         "A PR can drop 1024 from the list and enforce a 2048-bit minimum in CertReq or where the restriction is "
         "read. Whether"),
        ("NO_ACTION is not appropriate, because the app actively offers a weak option and passes MDM values through "
         "with no check.",
         "The app actively offers a weak option and passes MDM values through with no check."),
        (" A scoped hardening PR is reasonable; an issue explaining the weak option and asking maintainers to settle "
         "legacy compatibility policy is also reasonable. The repository does not establish that any peer requires "
         "1024-bit keys, so dismissing the finding outright is unwarranted.",
         " The repository does not establish that any peer requires 1024-bit keys."),
        ("If the rule also flags 8192, that part is a false positive and is not a reason to remove 8192.",
         "If the rule also flags 8192, that part is a false positive and is not a reason to remove 8192; a delivery "
         "that keeps to the size the report observed is not marked down for leaving the 1024 option, which "
         "must_change describes for a PR that tightens the policy."),
    ],
    "pr_ac1a8dd4d680": [
        ("A pull request would only reshape correct code to satisfy the tool, for example by switching to getKey, "
         "adding a dummy password, or calling store(). That would risk breaking keystore access to the existing key "
         "alias, which the app uses to encrypt stored MQTT and OpenCelliD credentials. An issue would just send the "
         "maintainers a false positive. So the right response is to send nothing.",
         "A pull request must not add a dummy password or call store(), which the AndroidKeyStore provider does not "
         "support, and must keep access to the existing key alias, which the app uses to encrypt stored MQTT and "
         "OpenCelliD credentials. Reading the same key through getKey(alias, null) instead of getEntry keeps that "
         "access."),
    ],
    "pr_fff072cb8953": [
        ("Since MD5 is fine for this purpose, sending nothing is a correct response. ", ""),
        (" NO_ACTION is reasonable because the repository does not establish a security vulnerability here; an "
         "ISSUE explaining the limited purpose and optional hardening is also reasonable. A focused PR switching "
         "both sides to SHA-256 is acceptable without a data migration.",
         " The repository does not establish a security vulnerability here. A PR switching both sides to SHA-256 "
         "needs no data migration."),
    ],
}

# unit -> {"must_not_break"|"must_change": [(old item, new item)]}
LISTS = {
    "pr_1381cd4ddb3f": {"must_not_break": [(
        "Existing AndroidKeyStore aliases and the AES/GCM secret keys stored under them must still resolve through "
        "`getEntry`. These keys protect the master key and other encrypted preferences, and losing them or "
        "regenerating them silently would make stored vault data impossible to decrypt.",
        "Existing AndroidKeyStore aliases and the AES/GCM secret keys stored under them must still resolve to the "
        "same keys (through `getEntry` or an equivalent lookup such as `getKey`). These keys protect the master key "
        "and other encrypted preferences, and losing them or regenerating them silently would make stored vault "
        "data impossible to decrypt.")]},
    "pr_ac1a8dd4d680": {"must_not_break": [(
        "The keystore must keep loading with load(null) and getEntry(alias, null). The AndroidKeyStore provider does "
        "not support a file stream, a password, or store().",
        "The keystore must keep loading with load(null) and reading the existing key with a null protection "
        "parameter (getEntry(alias, null) or the equivalent getKey(alias, null)). The AndroidKeyStore provider does "
        "not support a file stream, a password, or store().")]},
}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main():
    v2 = load(HERE / "gabarito_v2.json")
    out, problems = {}, []
    for unit, gab in v2.items():
        g = json.loads(json.dumps(gab))
        for old, new in REASON.get(unit, []):
            n = g["reason"].count(old)
            if n != 1:
                problems.append(f"{unit}: reason text found {n} times: {old[:70]!r}")
                continue
            g["reason"] = g["reason"].replace(old, new)
        for field, edits in LISTS.get(unit, {}).items():
            for old, new in edits:
                if g[field].count(old) != 1:
                    problems.append(f"{unit}: {field} item not found: {old[:70]!r}")
                    continue
                g[field][g[field].index(old)] = new
        for field in ("must_change", "must_not_break"):
            g[field] = list(dict.fromkeys(g[field]))
        g["reason"] = g["reason"].rstrip() + POLICY
        g["source"] = "gabarito_v2.json"
        g["fixed_by"] = "reading v3, the author's review of 2026-11-16 (build_gabarito_v3.py)"
        out[unit] = g
    if problems:
        raise SystemExit("\n".join(problems))
    (HERE / "gabarito_v3.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    left = [u for u, g in out.items() for s in ("send nothing", "nothing should be sent", "not acceptable",
                                                 "not appropriate", "careful choice", "would send nothing")
            if s in g["reason"].split("Delivery policy (v3")[0]]
    print(f"{len(out)} units written; verdict phrases left: {left or 'none'}")


if __name__ == "__main__":
    main()
