"""Expected digests a class compares downloads against, verified by download.

whoBIRD (2026-10-18) checks the models it downloads from fixed URLs against
fixed MD5 constants. Replacing the algorithm alone would make every check
fail and delete the models; the analysis stopped there and asked the
maintainer where the expected values come from. They come from the files at
the URLs in the same class. Downloading each file, the pipeline can show
which constant its weak digest equals -- proving the pairing rather than
assuming it -- and compute the SHA-256 that replaces that constant.

A constant no reachable file matches (whoBIRD's metaModelV1MD5, a previous
model version no URL serves any more) cannot be given a new value; the
evidence names it so the pull request says what changes for devices that
hold that file. Downloads are bounded in size and time and cached per URL.
"""
from __future__ import annotations

import hashlib
import re
import urllib.request
from functools import lru_cache
from pathlib import Path

TIMEOUT = 120
MAX_BYTES = 300 * 1024 * 1024
_HEX_LENGTH = {"MD5": 32, "SHA-1": 40}
_URL = re.compile(r"\"(https://[^\"\s]+)\"")
_BIGINTEGER_HEX = re.compile(r"BigInteger\s*\(\s*1\s*,[^;]*?\)\s*\.\s*toString\s*\(\s*16\s*\)")


def weak_algorithm(error_message: str) -> str:
    found = re.search(r"\bfound\s+(MD-?5|SHA-?1)\b", str(error_message or ""), re.IGNORECASE)
    if not found:
        return ""
    return "MD5" if found.group(1).upper().startswith("MD") else "SHA-1"


def expected_constants(source: str, algorithm: str) -> dict[str, str]:
    """name -> lowercase hex value, for string constants of the algorithm's length."""
    length = _HEX_LENGTH.get(algorithm, 0)
    if not length:
        return {}
    pattern = re.compile(rf"\b(\w+)\s*=\s*\"([0-9a-fA-F]{{{length}}})\"")
    return {name: value.lower() for name, value in pattern.findall(source or "")}


def artifact_urls(source: str) -> list[str]:
    return sorted(set(_URL.findall(source or "")))


@lru_cache(maxsize=None)
def fetch_digests(url: str) -> dict:
    """MD5, SHA-1 and SHA-256 of the file at `url`, or an error."""
    digests = {"MD5": hashlib.md5(), "SHA-1": hashlib.sha1(), "SHA-256": hashlib.sha256()}
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    return {"url": url, "error": "file larger than the download limit"}
                for digest in digests.values():
                    digest.update(chunk)
    except (OSError, ValueError) as exc:
        return {"url": url, "error": f"download failed: {exc.__class__.__name__}"}
    return {"url": url, "size": size,
            **{name: digest.hexdigest() for name, digest in digests.items()}}


def _strip_leading_zeros(value: str) -> str:
    # whoBIRD prints its digest with BigInteger.toString(16), which drops
    # leading zeros; a constant written that way still names the same digest.
    return value.lstrip("0") or "0"


def digest_reference_evidence(error: dict, fetch=fetch_digests) -> dict:
    algorithm = weak_algorithm(str(error.get("error_message", "")))
    source = str(error.get("method_body", ""))
    try:
        source = (Path(str(error.get("repository_path", ""))) / str(
            error.get("source_file_path", ""))).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    constants = expected_constants(source, algorithm)
    urls = artifact_urls(source)
    if not constants or not urls:
        return {}
    verified, by_value = [], {_strip_leading_zeros(value): name for name, value in constants.items()}
    failures = []
    for url in urls:
        result = fetch(url)
        if "error" in result:
            failures.append({"url": url, "error": result["error"]})
            continue
        name = by_value.get(_strip_leading_zeros(result[algorithm]))
        if name:
            verified.append({"constant": name, "url": url, "old_value": constants[name],
                             "algorithm": algorithm, "sha256": result["SHA-256"],
                             "size": result["size"]})
    matched = {item["constant"] for item in verified}
    unmatched = sorted(set(constants) - matched)
    evidence = {"expected_digest_constants": sorted(constants),
                "verified_digest_references": verified,
                "unmatched_digest_constants": unmatched,
                "expected_digests_verified": bool(verified)}
    if failures:
        evidence["download_failures"] = failures
    if verified:
        # The new value is written as the code formats a digest: through
        # BigInteger(1, hash).toString(16) a SHA-256 starting with 0 loses
        # its leading zeros, and the exact hex would never compare equal.
        drops_zeros = bool(_BIGINTEGER_HEX.search(source))
        evidence["replacement_values"] = [
            {"constant": item["constant"], "old_value": item["old_value"],
             "new_value": (_strip_leading_zeros(item["sha256"]) if drops_zeros
                           else item["sha256"]),
             "url": item["url"]} for item in verified]
        evidence["maintainer_facts"] = {"digest_references": digest_facts(evidence)}
    return evidence


def digest_facts(evidence: dict) -> str:
    verified = evidence.get("verified_digest_references") or []
    algorithm = verified[0]["algorithm"] if verified else "MD5"
    count = len(verified)
    files = "file" if count == 1 else "files"
    sentences = [f"I downloaded the {count} {files} from the URLs in the code: "
                 f"{'its' if count == 1 else 'each one’s'} {algorithm} matches "
                 f"{'its' if count == 1 else 'a'} constant, and the new values are "
                 f"{'its' if count == 1 else 'their'} SHA-256."]
    unmatched = evidence.get("unmatched_digest_constants") or []
    if unmatched:
        names = ", ".join(f"`{name}`" for name in unmatched)
        one = len(unmatched) == 1
        # The one behavior the patch changes, said plainly (whoBIRD's
        # metaModelV1MD5 check is dropped, not migrated).
        sentences.append(f"{names} {'matches' if one else 'match'} none of them, so I removed "
                         f"{'its check' if one else 'their checks'}: a device that still has "
                         "such a file will delete it and download the current one once.")
    return " ".join(sentences)
