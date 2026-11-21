"""An algorithm a patch introduces that Android's platform providers lack at the app's minSdk.

SMSecure run_v4 (2026-10-31) replaced the master-secret KDF with
SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256"), which Android's
platform providers offer only from API 26, in an app whose minSdk is 24 and
which bundles no provider: on Android 7.0 and 7.1 every existing user would
be locked out after the migration. Nothing compared the algorithm with
minSdk; the behaviour review set the concern aside because "the supplied
evidence names no supported platform where those documented APIs are
unavailable".

The predicate is here; the first API level of each standard name is data
(data/android_algorithm_availability.json, from Android's reference pages).
A name the table does not know, or marks "unknown", never triggers it, nor
does a module whose minSdk is not written as a number, a call under an
SDK_INT check or @RequiresApi, a call naming its provider, or an app that
bundles one (BouncyCastle, SpongyCastle, Conscrypt, Tink, or addProvider /
insertProviderAt in its code). The check regenerates the draft with the
constraint explained; it never refuses one.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from cognalys.workspace import library_data

CODE = "ALGORITHM_ABOVE_MIN_SDK"
_SERVICES = ("Cipher", "KeyAgreement", "KeyFactory", "KeyGenerator", "KeyManagerFactory",
             "KeyPairGenerator", "KeyStore", "Mac", "MessageDigest", "SecretKeyFactory",
             "Signature", "SSLContext", "TrustManagerFactory")
# Service.getInstance(<first argument>[, <provider>]).
_GET_INSTANCE = re.compile(
    r"\b(?P<service>" + "|".join(_SERVICES) + r")\s*\.\s*getInstance\s*\(\s*"
    r"(?P<argument>\"[^\"\n]*\"|[A-Za-z_$][\w$.]*)\s*(?P<more>,)?")
_CONSTANT = r"\b{name}\b\s*(?::\s*String\s*)?=\s*\"(?P<value>[^\"\n]*)\""
_GUARD = re.compile(r"\bSDK_INT\b|@RequiresApi\b|@TargetApi\b|\bisAtLeast[A-Z]\w*\("
                    r"|\bis\w*(?:OrLater|OrAbove|OrNewer|OrHigher)\s*\(|@ChecksSdkIntAtLeast\b")
_BUNDLED_PROVIDER = re.compile(
    r"org\.bouncycastle|\bbcprov\b|\bbcpkix\b|spongycastle|org\.conscrypt|conscrypt-android"
    r"|com\.google\.crypto\.tink|\btink-android\b", re.IGNORECASE)
_ADDS_PROVIDER = re.compile(r"\b(?:addProvider|insertProviderAt)\s*\(")
_SOURCE_SUFFIXES = (".java", ".kt")
_MAX_SOURCE_FILES = 6000


@lru_cache(maxsize=1)
def availability_table() -> Dict[str, Dict[str, object]]:
    """{service: {lower-cased name: first API level or "unknown"}}."""
    try:
        data = json.loads(library_data("android_algorithm_availability.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {service: {str(name).lower(): level for name, level in names.items()}
            for service, names in data.items()
            if not service.startswith("_") and isinstance(names, dict)}


def first_api_level(service: str, name: str) -> Optional[int]:
    """The first API level from which ``name`` stays available for ``service``; None if unknown."""
    names = availability_table().get(service) or {}
    key = str(name or "").strip().lower()
    level = names.get(key)
    if level is None and service == "Cipher" and "/" in key:
        level = names.get(key.split("/", 1)[0])
    return level if isinstance(level, int) else None


def _resolved(argument: str, code: str) -> str:
    """The algorithm name a getInstance argument passes: its literal, or a constant's."""
    if argument.startswith('"'):
        return argument.strip('"')
    name = re.escape(argument.rsplit(".", 1)[-1])
    declared = re.search(_CONSTANT.format(name=name), code)
    return declared.group("value") if declared else ""


def _guarded_at(text: str, position: int) -> bool:
    """Whether the call at ``position`` sits under an API-level guard.

    One guard anywhere in an edit covered every call in it (review of batch
    14, 2026-10-31: an SDK_INT branch hid an unguarded Mac.getInstance). The
    guard counts on the call's own line, or before it with a brace it opened
    still open at the call (an `if (SDK_INT >= 26) {` block, an annotated
    method). A guarded expression on the call's line counts even for its
    else branch: a miss, never a false flag.
    """
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if _GUARD.search(text[line_start:line_end if line_end != -1 else len(text)]):
        return True
    for guard in _GUARD.finditer(text, 0, position):
        between = text[guard.end():position]
        if between.count("{") > between.count("}"):
            return True
    return False


def _file_prefix(repository: Optional[Path], edit: Dict) -> str:
    """The edited file's text before the edit, for a guard the edit sits under; "" if unknown."""
    original = str(edit.get("original_code", ""))
    path = str(edit.get("source_file_path", ""))
    if repository is None or not original.strip() or not path:
        return ""
    try:
        text = (repository / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    index = text.find(original)
    return text[:index] if index > 0 else ""


def introduced_algorithms(edits: Iterable[Dict], repository: Optional[Path] = None) -> List[Dict]:
    """(service, name, path, guarded, provider_named) of each getInstance a patch's
    production edits make with a name their original code did not pass to that service."""
    from cognalys.utils.patch_scope import is_test_path

    production = [edit for edit in edits or [] if isinstance(edit, dict)
                  and edit.get("edit_kind") != "test"
                  and not is_test_path(str(edit.get("source_file_path", "")))]
    original_all = "\n".join(str(edit.get("original_code", "")) for edit in production)
    corrected_all = "\n".join(str(edit.get("corrected_code", "")) for edit in production)
    before = {(match.group("service"), _resolved(match.group("argument"), original_all).lower())
              for match in _GET_INSTANCE.finditer(original_all)}
    found, seen = [], set()
    for edit in production:
        corrected = str(edit.get("corrected_code", ""))
        prefix = None
        for match in _GET_INSTANCE.finditer(corrected):
            service = match.group("service")
            name = _resolved(match.group("argument"), corrected) or _resolved(
                match.group("argument"), corrected_all)
            key = (service, name.lower())
            if not name or key in before or key in seen:
                continue
            seen.add(key)
            found.append({"service": service, "name": name,
                          "path": str(edit.get("source_file_path", "")),
                          "guarded": _guarded_at(corrected, match.start()) or (
                              prefix := (_file_prefix(repository, edit) if prefix is None
                                         else prefix)) != "" and _guarded_at(
                              prefix + corrected, len(prefix) + match.start()),
                          "provider_named": bool(match.group("more"))})
    return found


def bundles_provider(repository: Path) -> bool:
    """Whether the app ships a JCA provider of its own, or registers one."""
    from cognalys.extraction.tls_client_evidence import _gradle_files, _read

    if any(_BUNDLED_PROVIDER.search(_read(path)) for path in _gradle_files(repository)):
        return True
    toml = repository / "gradle" / "libs.versions.toml"
    if toml.is_file() and _BUNDLED_PROVIDER.search(_read(toml)):
        return True
    count = 0
    for path in repository.rglob("*"):
        if path.suffix not in _SOURCE_SUFFIXES or not path.is_file():
            continue
        if any(part in {"build", ".gradle", ".git"} for part in path.relative_to(repository).parts):
            continue
        count += 1
        if count > _MAX_SOURCE_FILES:
            break
        if _ADDS_PROVIDER.search(_read(path)):
            return True
    return False


def algorithms_above_min_sdk(error: Dict, edits: Iterable[Dict]) -> List[Dict]:
    """One issue per algorithm the patch introduces above its module's minSdk, or []."""
    edits = list(edits or [])
    root = Path(str(error.get("repository_path") or ""))
    candidates = [item for item in introduced_algorithms(
                      edits, root if str(error.get("repository_path") or "") and root.is_dir()
                      else None)
                  if not item["guarded"] and not item["provider_named"]]
    leveled = [(item, first_api_level(item["service"], item["name"])) for item in candidates]
    leveled = [(item, level) for item, level in leveled if level]
    repository = Path(str(error.get("repository_path") or ""))
    if not leveled or not str(error.get("repository_path") or "") or not repository.is_dir():
        return []
    from cognalys.extraction.tls_client_evidence import module_min_sdk

    issues = []
    provider: Optional[bool] = None
    for item, level in leveled:
        min_sdk = module_min_sdk(repository, item["path"])
        if not min_sdk or level <= min_sdk:
            continue
        if provider is None:
            provider = bundles_provider(repository)
        if provider:
            return []
        issues.append({
            "code": CODE,
            "path": item["path"],
            "affected_paths": [item["path"]],
            "service": item["service"], "algorithm": item["name"],
            "first_api_level": level, "min_sdk": min_sdk,
            "message": (
                f"{item['service']}.getInstance(\"{item['name']}\") is available in Android's "
                f"platform providers only from API {level}, but this module's minSdk is "
                f"{min_sdk} and the app bundles no provider: on API {min_sdk}-{level - 1} the "
                "call throws NoSuchAlgorithmException. Keep the new algorithm behind "
                f"`Build.VERSION.SDK_INT >= {level}` and the existing behaviour below that "
                "level (data written by the old path must keep reading there), or keep the "
                f"change as it is and say that it needs minSdk {level}, a decision for the "
                "maintainer."),
            "details": [f"{item['service']} {item['name']}: API {level}+; minSdk {min_sdk}"],
        })
    return issues


def with_api_level_check(validation: Dict, error: Dict, edits: List[Dict],
                         final_attempt: bool = False) -> Dict:
    """``validation`` with algorithms_above_min_sdk: an error that regenerates the
    draft, and on the attempt no retry follows a warning the maintainer decides
    (raise minSdk), never a refusal."""
    try:
        issues = algorithms_above_min_sdk(error, edits)
    except Exception:  # noqa: BLE001 -- an availability check never costs a draft
        return validation
    if not issues:
        return validation
    if final_attempt:
        return {**validation, "warnings": [
            *(validation.get("warnings") or []),
            *({**issue, "maintainer_decides": True, "kept_on_final_attempt": True}
              for issue in issues)]}
    return {**validation, "status": "invalid",
            "errors": [*(validation.get("errors") or []), *issues]}


# Android versions by API level, for a sentence a maintainer reads.
_ANDROID_VERSIONS = {
    16: "4.1", 17: "4.2", 18: "4.3", 19: "4.4", 21: "5.0", 22: "5.1", 23: "6.0", 24: "7.0",
    25: "7.1", 26: "8.0", 27: "8.1", 28: "9", 29: "10", 30: "11", 31: "12", 32: "12L",
    33: "13", 34: "14", 35: "15", 36: "16",
}


def maintainer_notes(items: Iterable[object], diff_text: str = "") -> List[str]:
    """Plain sentences for an algorithm kept above minSdk, for the pull request's body.

    Your Local Weather (2026-11-02): the kept ALGORITHM_ABOVE_MIN_SDK warning
    ("SecretKeyFactory PBKDF2WithHmacSHA256: API 26+; minSdk 23") stayed in
    publication_notes, and the body the maintainer reads never said that the
    call throws on Android 6.0 to 7.1. Only warnings kept for the maintainer
    to decide, and only when the diff still adds the algorithm's name.
    """
    added = "\n".join(line[1:] for line in str(diff_text or "").splitlines()
                      if line.startswith("+") and not line.startswith("+++"))
    notes: List[str] = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("code") != CODE or not item.get("maintainer_decides"):
            continue
        service, name = str(item.get("service", "")), str(item.get("algorithm", ""))
        try:
            level, min_sdk = int(item.get("first_api_level")), int(item.get("min_sdk"))
        except (TypeError, ValueError):
            continue
        if not service or not name or level <= min_sdk or (diff_text and name not in added):
            continue
        # The added lines already branch on the API level: the old algorithm
        # is kept below it, so there is nothing for the maintainer to decide.
        if _GUARD.search(added):
            continue
        first = _ANDROID_VERSIONS.get(level, "")
        low = _ANDROID_VERSIONS.get(min_sdk, "")
        before = _ANDROID_VERSIONS.get(level - 1, "")
        if low and before:
            span = f" on Android {low} to {before}" if low != before else f" on Android {low}"
        else:
            # An API level with no version name of its own (20, 15, ...):
            # name the range by API level rather than the wrong release.
            span = (f" on API {min_sdk} to {level - 1}" if min_sdk != level - 1
                    else f" on API {min_sdk}")
        sentence = (f"Android's built-in `{service}` offers `{name}` only from "
                    f"{'Android ' + first + ' ' if first else ''}(API {level}), and the app's "
                    f"minSdk is {min_sdk}: without a newer minSdk or the old algorithm kept "
                    f"below API {level}, the call throws `NoSuchAlgorithmException`{span}. "
                    f"Which of those fits the app is your call.")
        if sentence not in notes:
            notes.append(sentence)
    return notes
