#!/usr/bin/env python3
"""Check every specification against the source it claims to come from.

A specification carries `source_verified`, and almost everything the pipeline
will do with it hangs off that flag: only a verified specification gets its
research questions asked, has its detector semantics handed to the
adjudication, or may have one of its documented false-positive shapes quoted
as a rationale. The flag was written by whatever generated the specification
and never checked afterwards.

Checking it found two different problems in one directory. Six
specifications were marked unverified although their source resolves and
contains the rule -- their research was switched off for nothing. Ten cite a
URL that answers 404: they are the concerns CrySL does not model at all (Zip
Slip, SQL injection, Intent redirection, path traversal, broadcast and
message authorization, PIN lockout, TLS certificate validation,
URLEncoder), and the generator pointed each at a plausible-looking path in
the CrySL rule repository that does not exist. Between them they carry 66
`known_false_positive_shapes`: sixty-six statements about when a finding may
be dismissed, whose cited authority is not there.

Those shapes are inert today only because `source_verified` gates them. That
coupling is implicit and easy to undo by accident, so this also records
`shapes_grounded` explicitly, with the reason, next to the shapes it governs.

Reports by default and writes only with --write, because demoting a
specification changes what the pipeline does with every finding that uses it.
"""
from __future__ import annotations

from cognalys.workspace import library_data
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


USER_AGENT = "tcc-v3-specification-audit"
TIMEOUT_SECONDS = 25


def fetch(url: str) -> tuple[str, str]:
    """Return (content, "") or ("", reason)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            charset = reply.headers.get_content_charset() or "utf-8"
            return reply.read().decode(charset, errors="replace"), ""
    except urllib.error.HTTPError as exc:
        return "", f"http_{exc.code}"
    except urllib.error.URLError as exc:
        return "", f"unreachable: {exc.reason}"
    except (OSError, ValueError) as exc:
        return "", f"fetch_failed: {exc}"


# Types any Android application can use without declaring them: the platform
# and the language runtimes. A governed type outside these belongs to some
# library, and a specification may only claim to govern it when the findings
# it was generated from show that library in use.
PLATFORM_PREFIXES = (
    "android.", "androidx.", "java.", "javax.", "kotlin.", "kotlinx.",
    "dalvik.", "org.json.", "org.xml.", "org.w3c.",
)


def unevidenced_library_types(specification: dict) -> list[str]:
    """Governed library types the specification's own findings never mention.

    WebViewJavascriptBridgeSpec (run_20260921_124815) was generated for a
    finding about android.webkit.WebView.addJavascriptInterface, required by
    the validator to govern a type named WebViewJavascriptBridge, and anchored
    to com.github.lzyzsd.jsbridge.WebViewJavascriptBridge -- an unrelated
    third-party library that happens to own a class of that name. Its source
    fetched fine and mentioned the type, so the audit called it verified. The
    specification then described an API the application never calls.

    A platform type needs no such evidence: findings rarely spell out
    `android.webkit.WebSettings`. A library type does, because the only reason
    to govern it is that the reported code uses it. Checked against the
    detector examples the generator recorded; a specification without them
    is not judged here.
    """
    evidence = (specification.get("generation_evidence") or {}).get(
        "detector_examples"
    ) or []
    if not isinstance(evidence, list) or not evidence:
        return []
    text = " ".join(
        " ".join(str(item.get(key, "")) for key in ("class", "method", "message"))
        for item in evidence if isinstance(item, dict)
    )
    unevidenced = []
    for value in specification.get("applies_to_types") or []:
        qualified = str(value).strip()
        if not qualified or qualified.startswith(PLATFORM_PREFIXES):
            continue
        simple = qualified.replace("$", ".").rsplit(".", 1)[-1]
        package_root = ".".join(qualified.split(".")[:2])
        if simple in text or (package_root and package_root in text):
            continue
        unevidenced.append(qualified)
    return unevidenced


def candidate_source_urls(specification: dict) -> list[str]:
    """The source_url, then every other source the specification cites.

    Only source_url was ever fetched. A generator that put a guessed CrySL path
    first and the official API reference second -- CursorLeakSpec cited
    Cursor#close() on developer.android.com right after a 404 -- left the
    specification unverified although it named a source that holds.
    """
    urls = [str(specification.get("source_url", "")).strip()]
    urls += [
        str(item).strip() for item in specification.get("authoritative_sources") or []
    ]
    return [
        url for url in dict.fromkeys(urls)
        if url.startswith(("http://", "https://"))
    ]


def governed_names(specification: dict) -> list[str]:
    """Every name the specification says its rule governs."""
    names = []
    for value in specification.get("applies_to_types") or []:
        text = str(value).strip()
        if text:
            names.append(text)
            names.append(text.rsplit(".", 1)[-1])
    spec_id = str(specification.get("spec_id", "")).strip()
    if spec_id:
        names.append(spec_id)
    return list(dict.fromkeys(name for name in names if name))


def rule_is_present(url: str, content: str, specification: dict) -> tuple[bool, str]:
    """Whether the fetched source really carries the rule claimed here.

    The check is chosen by source kind and reported by name, so a weak check
    is never mistaken for a strong one. A CrySL rule names its governed type
    on a SPEC line and a JavaMOP property names itself; an API document can
    only be asked whether it mentions the governed type at all, which is
    weaker and is recorded as such.
    """
    names = governed_names(specification)
    if not names:
        return False, "specification names no governed type"
    lowered = url.lower()
    if lowered.endswith(".crysl"):
        for match in re.finditer(r"^\s*SPEC\s+([\w$.]+)", content, re.MULTILINE):
            declared = match.group(1)
            if declared in names or declared.rsplit(".", 1)[-1] in names:
                return True, f"crysl_spec_line:{declared}"
        return False, "crysl source declares a different SPEC type"
    if lowered.endswith(".mop"):
        for name in names:
            if re.search(rf"\b{re.escape(name)}\s*\(", content):
                return True, f"mop_property_declaration:{name}"
        return False, "mop source declares a different property"
    # A document is asked about the governed types only. The specification's
    # own id is a label this project chose; a page that happens to contain
    # the same word says nothing about the rule.
    document_names = [
        name for name in governed_names({
            "applies_to_types": specification.get("applies_to_types") or [],
        })
        if len(name) >= 4
    ]
    for name in document_names:
        # A whole identifier, not a substring: `WebView` must not be found
        # inside `WebViewJavascriptBridgeSpec`.
        if re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", content):
            return True, f"document_mentions_type:{name}"
    return False, "document does not mention the governed type"


def audit(path: Path) -> dict:
    specification = json.loads(path.read_text(encoding="utf-8"))
    url = str(specification.get("source_url", "")).strip()
    claimed = specification.get("source_verified") is True
    record = {
        "path": path,
        "spec_id": str(specification.get("spec_id") or path.stem),
        "claimed_verified": claimed,
        "shape_count": len(specification.get("known_false_positive_shapes") or []),
        "url": url,
    }
    if not url:
        record.update(state="no_source_url", detail="no source_url recorded")
        return record
    unrelated = unevidenced_library_types(specification)
    if unrelated:
        # Checked before any fetch: a source that documents a library the
        # application never uses verifies nothing about the finding, however
        # well it matches the name.
        record.update(
            state="governs_unevidenced_library_type",
            detail="none of the specification's findings mention "
                   + ", ".join(unrelated),
        )
        return record
    failures = []
    rejections = []
    for candidate in candidate_source_urls(specification) or [url]:
        content, failure = fetch(candidate)
        if failure:
            failures.append(f"{candidate}: {failure}")
            continue
        present, detail = rule_is_present(candidate, content, specification)
        if present:
            record.update(state="verified", detail=detail, url=candidate)
            return record
        rejections.append(f"{candidate}: {detail}")
    if rejections:
        record.update(
            state="source_does_not_contain_rule",
            detail="; ".join(rejections)[:600],
        )
    else:
        # Every cited source failed to fetch; the first failure is the
        # source_url's, which is the one earlier records reported.
        record.update(
            state="unreachable_source",
            detail=failures[0].rsplit(": ", 1)[-1] if failures else "not fetched",
        )
    return record


def apply(record: dict) -> bool:
    """Write the audit result into the specification. True when it changed."""
    path = record["path"]
    specification = json.loads(path.read_text(encoding="utf-8"))
    verified = record["state"] == "verified"
    before = (
        specification.get("source_verified"),
        specification.get("shapes_grounded"),
    )
    specification["source_verified"] = verified
    # Recorded next to the shapes it governs rather than left implicit in
    # source_verified: a documented false-positive shape whose source cannot
    # be produced must never be quotable as the reason a finding was
    # dismissed, and that must not depend on a reader remembering which flag
    # happens to gate it.
    specification["shapes_grounded"] = verified
    specification["source_audit"] = {
        "state": record["state"],
        "detail": record["detail"],
        "checked_url": record["url"],
    }
    if before == (specification["source_verified"],
                  specification["shapes_grounded"]):
        path.write_text(
            json.dumps(specification, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return False
    path.write_text(
        json.dumps(specification, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--specifications-dir", type=Path,
        default=library_data("specifications"),
    )
    parser.add_argument(
        "--write", action="store_true",
        help=(
            "Write source_verified, shapes_grounded and source_audit back "
            "into each specification. Without it this only reports."
        ),
    )
    parser.add_argument("--spec", default="", help="Audit one spec_id only.")
    args = parser.parse_args()

    paths = sorted(args.specifications_dir.glob("*.json"))
    if args.spec:
        paths = [path for path in paths if path.stem == args.spec]
    if not paths:
        print("No specifications found.", file=sys.stderr)
        return 1

    records = []
    for path in paths:
        try:
            records.append(audit(path))
        except (OSError, ValueError) as exc:
            print(f"{path.name}: could not audit: {exc}", file=sys.stderr)

    promotions = [r for r in records
                  if r["state"] == "verified" and not r["claimed_verified"]]
    demotions = [r for r in records
                 if r["state"] != "verified" and r["claimed_verified"]]
    ungrounded = [r for r in records if r["state"] != "verified"]

    print(f"specifications audited: {len(records)}")
    print(f"  verified by their own source : "
          f"{sum(1 for r in records if r['state'] == 'verified')}")
    print(f"  source unreachable           : "
          f"{sum(1 for r in records if r['state'] == 'unreachable_source')}")
    print(f"  source lacks the rule        : "
          f"{sum(1 for r in records if r['state'] == 'source_does_not_contain_rule')}")
    print(f"  no source_url                : "
          f"{sum(1 for r in records if r['state'] == 'no_source_url')}")

    for title, group in (
        ("PROMOTED (source checks out; was marked unverified)", promotions),
        ("DEMOTED (was marked verified; source does not support it)", demotions),
    ):
        if group:
            print()
            print(f"{title}: {len(group)}")
            for record in group:
                print(f"   {record['spec_id']:34} {record['detail']}")

    shapes = sum(r["shape_count"] for r in ungrounded)
    if shapes:
        print()
        print(f"UNGROUNDED false-positive shapes quarantined: {shapes} "
              f"across {len([r for r in ungrounded if r['shape_count']])} "
              f"specification(s)")
        for record in ungrounded:
            if record["shape_count"]:
                print(f"   {record['spec_id']:34} "
                      f"{record['shape_count']:2d} shape(s)  "
                      f"{record['state']}")

    if not args.write:
        print()
        print("Report only. Re-run with --write to record these results.")
        return 0

    changed = sum(1 for record in records if apply(record))
    print()
    print(f"written: {len(records)} specification(s), "
          f"{changed} with a changed verification state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
