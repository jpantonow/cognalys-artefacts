"""Write pr_cases_resolved.json: the PR cases the pre-registration includes, in build order.

Pre-registration: docs/preregistro_validacao_prs_20261115.md. pr_cases.json (from
tools/pr_cases.py) is read, never edited. Included:

  - entries with no `problems` that were not validated before (21);
  - seven entries with one value fixed by rule before any result:
      * app packages missing (freeotp_48_pr521, freeotp_48_pr524, droidscep_7,
        redreader_117): the manifest package of the built base APK
        (`aapt2 dump badging`, written by tools/build_prs.sh to
        build/<case>/base.badging). Until the base APK exists the value is null
        and marked pending; this script is run again after each base build;
      * method renamed or removed by the patch (notallyx_71134,
        orgzlyrevived_292, amaradio_159): patched_sites = the method(s) holding
        the governed call after the patch, read from the PR diff (PATCHED_SITES
        below, with the diff lines they come from). If the patch removes the
        governed call, the method that held it.

Every filled value is recorded under `filled` with how it was obtained.
Build order: accepted, open, closed_by_maintainer; within a group, pr_cases.json order.

Also writes build/prs_build_cases.txt (one line per case, `|`-separated) for
tools/build_prs.sh. Run in WSL: source ~/venv/bin/activate && python tools/resolve_pr_cases.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
GROUPS = ("accepted", "open", "closed_by_maintainer")

PACKAGES_FROM_BADGING = ("freeotp_48_pr521", "freeotp_48_pr524", "droidscep_7", "redreader_117")

# Read from the PR diffs (pr_api_cache/<owner>__<repo>__<n>.diff) on 2026-11-15, before any build or run.
PATCHED_SITES = {
    "notallyx_71134": {
        "value": [["com.philkes.notallyx.utils.IOExtensionsKt", "sha256Hash"]],
        "how": ("PR diff Crustack__NotallyX__1167.diff, app/src/main/java/com/philkes/notallyx/utils/"
                "IOExtensions.kt: the three top-level md5Hash overloads are renamed sha256Hash; the governed "
                "call `MessageDigest.getInstance(\"SHA-256\")` (was \"MD5\") is in `fun InputStream.sha256Hash()`. "
                "Kotlin top-level functions of IOExtensions.kt compile to class IOExtensionsKt."),
    },
    "orgzlyrevived_292": {
        "value": [["com.orgzly.android.util.MiscUtils", "sha256"]],
        "how": ("PR diff orgzly-revived__orgzly-android-revived__1121.diff, app/src/main/java/com/orgzly/android/"
                "util/MiscUtils.java: `public static long sha1(String s)` is renamed `sha256`; the governed call "
                "`MessageDigest.getInstance(\"SHA-256\")` (was \"SHA-1\") stays in it."),
    },
    "amaradio_159": {
        "value": [["com.ounben.amaradio.Utils", "getCacheFile"], ["com.ounben.amaradio.Utils", "writeFileCache"]],
        "how": ("PR diff ounben__AMARadio__122.diff, app/src/main/kotlin/com/ounben/amaradio/Utils.kt: the private "
                "extension `String.md5()` is removed and its governed call is inlined as "
                "`MessageDigest.getInstance(\"SHA-256\")` into the two suspend functions that called it, "
                "getCacheFile and writeFileCache (object Utils; their bodies run in withContext lambdas, "
                "Utils$getCacheFile$N / Utils$writeFileCache$N, which classify.site_matches accepts). "
                "The governed call is moved, not removed, so the sites are the methods holding it after the patch."),
    },
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else ""


def badging_package(case: str):
    badging = read(HERE / "build" / case / "base.badging")
    match = re.search(r"package: name='([^']*)'", badging)
    return (match.group(1) if match else None), badging


def main() -> int:
    source = json.loads((HERE / "pr_cases.json").read_text(encoding="utf-8"))
    chosen = []
    for case in source["cases"]:
        name = case.get("case")
        if case.get("already_validated"):
            continue
        if not case.get("problems"):
            chosen.append((case, "no problems in pr_cases.json"))
        elif name in PACKAGES_FROM_BADGING or name in PATCHED_SITES:
            chosen.append((case, "one value fixed by rule (pre-registration)"))
    chosen.sort(key=lambda item: GROUPS.index(item[0]["status"]))  # stable: pr_cases.json order within a group

    out_cases = []
    lines = []
    for order, (case, inclusion) in enumerate(chosen, 1):
        entry = dict(case)
        entry["build_order"] = order
        entry["inclusion"] = inclusion
        entry["problems_in_pr_cases"] = case.get("problems") or []
        filled = {}
        name = case["case"]
        if name in PACKAGES_FROM_BADGING:
            package, badging = badging_package(name)
            entry["app_packages"] = [package] if package else None
            filled["app_packages"] = {
                "value": entry["app_packages"],
                "how": ("manifest package of the built base APK, `aapt2 dump badging` "
                        f"(build/{name}/base.badging: {badging[:160]})" if package else
                        "pending: filled from `aapt2 dump badging` of the base APK once tools/build_prs.sh builds it"),
                "rule": "pre-registration: app packages missing -> manifest package read from the built base APK",
            }
            if package and case.get("problems"):
                hint = re.search(r"only a hint: (\S+)", " ".join(case["problems"]))
                if hint and hint.group(1) != package:
                    filled["app_packages"]["note"] = (f"differs from the APK-name hint {hint.group(1)} in "
                                                      "pr_cases.json; the rule's value (badging) is used")
        if name in PATCHED_SITES:
            entry["patched_sites"] = PATCHED_SITES[name]["value"]
            filled["patched_sites"] = dict(PATCHED_SITES[name],
                                           rule=("pre-registration: method renamed or removed by the patch -> "
                                                 "the method(s) holding the governed call after the patch, read "
                                                 "from the diff; if the call is removed, the method that held it"))
        entry["filled"] = filled
        missing = [k for k in ("repo_url", "build_commit", "patch_to_apply", "base_sites", "patched_sites",
                               "app_packages", "spec") if not entry.get(k)]
        entry["unresolved"] = missing
        out_cases.append(entry)
        build = case.get("build") or {}
        pr_number = case["pr_url"].rstrip("/").rsplit("/", 1)[-1]
        lines.append("|".join([name, case["status"], case["repo_url"], case["build_commit"],
                               str(ROOT / case["patch_to_apply"]), build.get("build_task") or "",
                               build.get("apk_glob") or "", pr_number]))

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preregistration": "docs/preregistro_validacao_prs_20261115.md",
        "source": "outputs/dynamic_validation_20261114/pr_cases.json (unchanged)",
        "build_order_rule": "accepted, open, closed_by_maintainer; within a group the pr_cases.json order",
        "counts": {"cases": len(out_cases),
                   "no_problems": sum(c["inclusion"].startswith("no problems") for c in out_cases),
                   "fixed_by_rule": sum(c["inclusion"].startswith("one value") for c in out_cases),
                   "by_status": {g: sum(c["status"] == g for c in out_cases) for g in GROUPS}},
        "cases": out_cases,
    }
    (HERE / "pr_cases_resolved.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    (HERE / "build").mkdir(exist_ok=True)
    (HERE / "build" / "prs_build_cases.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(out["counts"]))
    for c in out_cases:
        print(c["build_order"], c["status"], c["case"], "unresolved:", c["unresolved"] or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
