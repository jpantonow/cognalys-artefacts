"""Write pr_cases_desvio6.json: the cases of deviation 6 (docs/preregistro_validacao_prs_20261115.md,
"Desvio declarado (2026-11-18)"), written before any build or RVSec run of them.

A. Six excluded PRs, each with the missing value fixed from a recorded source (never chosen on a result):
   - messenger_4545 (session-android #2241): build task of the RVSec queue build that produced the analysed APK
     (outputs/rvsec_tls_20261015/run.log: ":app:assembleWebsiteDebug", apk_count=1). No APK name recorded:
     the pre-registered pick rule falls to "the only APK of the build". Merged 2026-11-17 (GitHub API).
   - metadataremover_20020 (MetadataRemover #264), nextcloudcookbook_62 (nextcloud-cookbook #204): build task and
     APK = head_build_task / head_build_variant of the RVSec campaign (data/raw/exp_novo/dataset.csv), the source
     deviation 5 used.
   - summit_355 (summit #54): the queue build (outputs/rvsec_misuse5_20261017/build.log, :app:assembleFdroidDebug)
     produced one APK; patched site = SimpleDiskCache.sha256 (md5 renamed; holds the governed call after the patch).
   - httpshortcuts_1104060001 (HTTP-Shortcuts #576): finding identity from the RVSec row in
     data/processed/errors_amostragem.csv and the PR head branch (fix/configuretls-sslcontextspec-...); task/APK
     from the campaign dataset. Old-format message: spec-level criterion (deviation 5's sentinels).
   - passnotes_11700_pr347 (keepassvault #347): the finding the PR addresses (old TLS-protocol message at
     HttpClientFactory.createHttpClient), spec-level; build commit/task/APK of pr_cases.json; both APKs rebuilt.
B. bitbanana_79: one more repetition (rep 4) on the APKs of 14/11 (apks/bitbanana_79), `reuse_apks`; it is not
   built and not in build/prs_build_cases_desvio6.txt. (The four other B cases are already in
   pr_cases_resolved.json / pr_cases_oldmsg.json; the chain runs their rep 3 with ONLY_CASES.)

pr_cases.json, pr_cases_resolved.json and pr_cases_oldmsg.json are read, never edited. app_packages missing ->
manifest package of the built base APK (build/<case>/base.badging), pending until tools/build_prs.sh builds it
(run this again after each build; build_prs.sh's collect() does). Every filled value is under `filled`.
Also writes build/prs_build_cases_desvio6.txt for tools/build_prs.sh (CASEFILE=...).
Run in WSL: source ~/venv/bin/activate && python tools/resolve_desvio6_cases.py
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
GROUPS = ("accepted", "open", "closed_by_maintainer")
DATASET = ROOT / "data/raw/exp_novo/dataset.csv"
DEVIATION = "2026-11-18 desvio 6"
NO_CODE = "__deviation6_spec_level__"
NO_MESSAGE = "\u0000deviation 6: spec-level criterion, no message is compared\u0000"
PR_DIFF = "outputs/dynamic_validation_20261114/pr_api_cache/{}.diff"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else ""


def campaign(apk: str) -> dict:
    csv.field_size_limit(1 << 30)
    with DATASET.open(encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            if row["apk"] == apk:
                return row
    return {}


def by_url(source: dict, fragment: str) -> dict:
    found = [c for c in source["cases"] if c["pr_url"].endswith(fragment)]
    assert len(found) == 1, fragment
    return json.loads(json.dumps(found[0]))


def errors_row(apk: str, cls: str, method: str, spec: str) -> dict:
    """The RVSec row of the finding (data/processed/errors_amostragem.csv)."""
    with (ROOT / "data/processed/errors_amostragem.csv").open(encoding="utf-8", errors="replace") as handle:
        rows = list(csv.reader(handle))
    header = rows[0]
    for row in rows[1:]:
        if row and row[0] == apk and cls in row and method in row and spec in row:
            return {"header": header, "row": row}
    return {}


def main() -> int:
    source = json.loads((HERE / "pr_cases.json").read_text(encoding="utf-8"))
    resolved = json.loads((HERE / "pr_cases_resolved.json").read_text(encoding="utf-8"))["cases"]
    oldmsg = json.loads((HERE / "pr_cases_oldmsg.json").read_text(encoding="utf-8"))["cases"]
    first_order = max(c["build_order"] for c in resolved + oldmsg) + 1
    cases = []

    # --- A1 session-android #2241
    c = by_url(source, "session-foundation/session-android/pull/2241")
    c["status_in_pr_cases"] = c["status"]
    c["status"] = "accepted"
    c["build"]["build_task"] = ":app:assembleWebsiteDebug"
    c["build"]["apk_glob"] = None
    c["filled"] = {
        "status": {"value": "accepted", "how": "GitHub API 2026-11-18: merged true, merged_at 2026-11-17T23:39:24Z"},
        "build_task": {"value": ":app:assembleWebsiteDebug",
                       "how": ("outputs/rvsec_tls_20261015/run.log: 'network.loki.messenger.fdroid.debug_4545.apk  "
                               "https://github.com/session-foundation/session-android @ dd6c41a0c6df  task "
                               ":app:assembleWebsiteDebug' and '[1/1] network.loki.messenger.fdroid: success, "
                               "apk_count=1' (the queue build that produced the analysed APK)")},
        "apk_glob": {"value": None, "how": "no APK name recorded; pick rule 3 (clarification 3): the only APK of the build"},
    }
    cases.append(c)

    # --- A2 MetadataRemover #264, A3 nextcloud-cookbook #204: task/APK from the campaign dataset
    for fragment in ("Crazy-Marvin/MetadataRemover/pull/264", "lneugebauer/nextcloud-cookbook/pull/204"):
        c = by_url(source, fragment)
        row = campaign(c["apk"])
        assert row.get("head_build_task") and row.get("head_build_variant"), c["apk"]
        c["build"] = dict(c.get("build") or {})
        c["build"]["build_task"] = row["head_build_task"]
        c["build"]["apk_glob"] = row["head_build_variant"]
        c["filled"] = {
            "build_task/apk_glob": {
                "value": [row["head_build_task"], row["head_build_variant"]],
                "how": (f"data/raw/exp_novo/dataset.csv row {c['apk']}: head_build_task {row['head_build_task']!r}, "
                        f"head_build_variant {row['head_build_variant']!r} (head_pinned_commit "
                        f"{row.get('head_pinned_commit')!r}); the source deviation 5 used")},
        }
        if c["case"] == "nextcloudcookbook_62":
            c["filled"]["finding identity"] = {
                "value": [c["finding_id"], c["detector_code"]],
                "how": "pr_cases.json: the inventory's finding 4c1c74bcbc8f and the only detector row at the site and spec"}
        cases.append(c)

    # --- A4 summit #54
    c = by_url(source, "idunnololz/summit/pull/54")
    c["build"]["apk_glob"] = None
    c["patched_sites"] = [["com.idunnololz.summit.cache.SimpleDiskCache", "sha256"]]
    c["filled"] = {
        "apk_glob": {"value": None, "how": ("outputs/rvsec_misuse5_20261017/build.log: '[1/1] com.idunnololz.summit.fdroid: "
                                            "success, apk_count=1' for :app:assembleFdroidDebug; pick rule 3: the only APK")},
        "patched_sites": {"value": c["patched_sites"],
                          "how": ("PR diff idunnololz__summit__54.diff, SimpleDiskCache.kt: `private fun md5(s: String)` is "
                                  "renamed `sha256`; the governed call `MessageDigest.getInstance(\"SHA-256\")` (was \"MD5\") "
                                  "and its update/digest stay in it (pre-registered rule for a renamed method)")},
    }
    cases.append(c)

    # --- A5 HTTP-Shortcuts #576 (no case in pr_cases.json)
    c = by_url(source, "Waboodoo/HTTP-Shortcuts/pull/576")
    apk = "ch.rmy.android.http_shortcuts_1104060001.apk"
    cls, method, spec = "ch.rmy.android.http_shortcuts.http.HttpClientFactory", "configureTLS", "SSLContextSpec"
    er = errors_row(apk, cls, method, spec)
    assert er, "HTTP-Shortcuts RVSec row not found"
    row = campaign(apk)
    c.update({"case": "httpshortcuts_1104060001", "apk": apk, "spec": spec, "class": cls, "method": method,
              "spec_active": True, "base_sites": [[cls, method]], "patched_sites": [[cls, method]],
              "pr_message": "expecting one of {TLSv1.2, TLSv1.3} but found TLS."})
    c["build"] = {"build_task": row["head_build_task"], "apk_glob": row["head_build_variant"]}
    c["filled"] = {
        "finding identity": {
            "value": [cls, method, spec, c["pr_message"]],
            "how": ("data/processed/errors_amostragem.csv row: " + ",".join(er["row"][:9])
                    + f"; PR head branch {c['pr_api']['head_ref']!r} (configureTLS, SSLContextSpec)")},
        "build_task/apk_glob": {
            "value": [row["head_build_task"], row["head_build_variant"]],
            "how": f"data/raw/exp_novo/dataset.csv row {apk}: head_build_task / head_build_variant"},
        "sites": {"value": [[cls, method]],
                  "how": ("the finding's site in both APKs; the PR diff changes TLSEnabledSSLSocketFactory.withTLS "
                          "(the socket factory configureTLS installs), not the site's file")},
    }
    c["spec_level"] = True
    cases.append(c)

    # --- A6 keepassvault #347
    c = by_url(source, "aivanovski/keepassvault/pull/347")
    c["case"] = "passnotes_11700_pr347"
    c["case_in_pr_cases"] = "passnotes_11700"
    c["already_validated"] = False
    c["filled"] = {"finding identity": {
        "value": ["com.ivanovsky.passnotes.data.repository.file.webdav.HttpClientFactory", "createHttpClient",
                  "SSLContextSpec", c["pr_message"]],
        "how": ("pr_cases.json pr_message (the PR's finding, old-format TLS-protocol message); the 14/11 validation "
                "used another finding (SSLCONTEXT-NOBS-05) and another patch (patches/passnotes_11700.diff)")}}
    c["spec_level"] = True
    cases.append(c)

    out_cases, lines = [], []
    cases.sort(key=lambda x: (GROUPS.index(x["status"]),
                              [s["pr_url"] for s in source["cases"]].index(x["pr_url"])))
    for order, c in enumerate(cases, first_order):
        name = c["case"]
        c["build_order"] = order
        c["deviation"] = DEVIATION
        c["inclusion"] = f"{DEVIATION}: excluded before; the missing value fixed from a recorded source"
        c["problems_in_pr_cases"] = c.get("problems") or []
        c["patch_to_apply"] = PR_DIFF.format(Path(c["pr_diff_file"]).stem)
        if c.get("spec_level"):
            c["detector_code"], c["message"] = NO_CODE, NO_MESSAGE
            c["filled"]["detector_code/message"] = {
                "value": "sentinels matching no report",
                "how": "old-format message with no current detector row: spec-level criterion as in deviation 5"}
        if not c.get("app_packages"):
            badging = read(HERE / "build" / name / "base.badging")
            match = re.search(r"package: name='([^']*)'", badging)
            c["app_packages"] = [match.group(1)] if match else None
            c["filled"]["app_packages"] = {
                "value": c["app_packages"],
                "how": (f"manifest package of the built base APK (build/{name}/base.badging)" if match
                        else "pending: from `aapt2 dump badging` of the base APK once tools/build_prs.sh builds it")}
        c["unresolved"] = [k for k in ("repo_url", "build_commit", "patch_to_apply", "base_sites", "patched_sites",
                                       "app_packages", "spec") if not c.get(k)]
        if not c["build"].get("build_task"):
            c["unresolved"].append("build_task")
        out_cases.append(c)
        pr_number = c["pr_url"].rstrip("/").rsplit("/", 1)[-1]
        lines.append("|".join([name, c["status"], c["repo_url"], c["build_commit"], str(ROOT / c["patch_to_apply"]),
                               c["build"]["build_task"] or "", c["build"].get("apk_glob") or "", pr_number]))

    # --- B: bitbanana_79 rep 4 on the 14/11 APKs (not built again)
    out_cases.append({
        "case": "bitbanana_79", "pr_url": "https://github.com/michaelWuensch/BitBanana/pull/184", "status": "open",
        "reuse_apks": True, "deviation": DEVIATION, "build_order": first_order + len(cases),
        "repo_url": "https://github.com/michaelWuensch/BitBanana",
        "build_commit": "e813eb374cb6bc23d2436b38ecada9c47d7cda38",
        "app_packages": ["app.michaelwuensch.bitbanana"],
        "inclusion": (f"{DEVIATION} B: one more repetition (rep 4) on apks/bitbanana_79 (14/11); config "
                      "classify.CASES['bitbanana_79'] unchanged; queue.sh's repository/commit/packages")})

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preregistration": "docs/preregistro_validacao_prs_20261115.md (desvio 6, 2026-11-18)",
        "source": "outputs/dynamic_validation_20261114/pr_cases.json (unchanged)",
        "build_order_rule": "after the cases of pr_cases_resolved.json and pr_cases_oldmsg.json; accepted, open, closed_by_maintainer",
        "extra_repetitions_B": {"reader_47": 3, "intertune_96": 3, "yourlocalweather_213": 3,
                                "owncloudnewsreader_196": 3, "bitbanana_79": 4},
        "cases": out_cases,
    }
    (HERE / "pr_cases_desvio6.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    (HERE / "build" / "prs_build_cases_desvio6.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for c in out_cases:
        print(c["build_order"], c["status"], c["case"], (c.get("build") or {}).get("build_task"),
              (c.get("build") or {}).get("apk_glob"), c.get("app_packages"), "unresolved:", c.get("unresolved") or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
