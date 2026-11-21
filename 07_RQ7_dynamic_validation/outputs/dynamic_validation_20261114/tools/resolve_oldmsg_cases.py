"""Write pr_cases_oldmsg.json: the seven PRs of deviation 5 (docs/preregistro_validacao_prs_20261115.md,
"Desvios declarados (2026-11-17, segunda parte)").

These PRs were excluded because their finding's message came from an older RVSec format with no detector row
at the site. Under deviation 5 they are validated with the current RVSec (jca_android) and the spec-level
criterion: any violation of the PR's specification at the PR's site. pr_cases.json and pr_cases_resolved.json
are read, never edited. Each entry is the pr_cases.json entry plus:

  - detector_code / message: sentinels that match no report, so `same_message` stays False and the
    classification is the pre-registered spec-level rule (classify.classify unchanged);
  - build.apk_glob, when pr_cases.json gives none, several, or the pipeline's copy name ("debug-" prefix the
    build helper does not produce): the variant the RVSec campaign built and analysed for that APK
    (data/raw/exp_novo/dataset.csv, head_build_variant). WiFiAnalyzer's build task, not recorded by the
    pipeline's production build, is the campaign's head_build_task (the pipeline's validation task is the same);
  - app_packages: the manifest package of the built base APK (`aapt2 dump badging`), the pre-registered rule
    for missing packages; pending until tools/build_prs.sh builds the base APK (run this again after it).

Every filled value is recorded under `filled` with how it was obtained. Build order continues after the 28
cases of pr_cases_resolved.json, in the same group order (accepted, open, closed_by_maintainer).
Also writes build/prs_build_cases_oldmsg.txt for tools/build_prs.sh (CASEFILE=...).
Run in WSL: source ~/venv/bin/activate && python tools/resolve_oldmsg_cases.py
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
DEVIATION = "2026-11-17 desvio 5"
NO_CODE = "__deviation5_spec_level__"
NO_MESSAGE = "\u0000deviation 5: spec-level criterion, no message is compared\u0000"

OLDMSG = ("owncloudnewsreader_196", "crazyfliecontrol2_22", "dsub2000_217", "feeder_4025", "syncadapter_20700",
          "giggity_769", "wifianalyzer_71")
# pr_cases.json's apk_glob kept as is (it names the APK the build helper produces).
KEEP_GLOB = {"crazyfliecontrol2_22", "feeder_4025"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else ""


def campaign_rows() -> dict:
    csv.field_size_limit(1 << 30)
    with DATASET.open(encoding="utf-8", errors="replace") as handle:
        return {row["apk"]: row for row in csv.DictReader(handle)}


def main() -> int:
    source = json.loads((HERE / "pr_cases.json").read_text(encoding="utf-8"))
    resolved = json.loads((HERE / "pr_cases_resolved.json").read_text(encoding="utf-8"))
    first_order = max(c["build_order"] for c in resolved["cases"]) + 1
    campaign = campaign_rows()
    chosen = [c for c in source["cases"] if c.get("case") in OLDMSG]
    assert len(chosen) == len(OLDMSG), [c.get("case") for c in chosen]
    chosen.sort(key=lambda c: GROUPS.index(c["status"]))

    out_cases, lines = [], []
    for order, case in enumerate(chosen, first_order):
        name = case["case"]
        entry = json.loads(json.dumps(case))
        entry["build_order"] = order
        entry["inclusion"] = f"{DEVIATION}: excluded for an old-format message; validated at spec level"
        entry["deviation"] = DEVIATION
        entry["problems_in_pr_cases"] = case.get("problems") or []
        entry["detector_code"] = NO_CODE
        entry["message"] = NO_MESSAGE
        filled = {"detector_code/message": {
            "value": "sentinels matching no report",
            "how": "deviation 5: spec-level criterion (any violation of the spec at the site); same_message stays False"}}
        build = entry.setdefault("build", {})
        row = campaign.get(case["apk"]) or {}
        if name not in KEEP_GLOB:
            variant = row.get("head_build_variant") or ""
            build["apk_glob_in_pr_cases"] = build.get("apk_glob")
            build["apk_glob"] = variant or None
            filled["apk_glob"] = {
                "value": build["apk_glob"],
                "how": (f"data/raw/exp_novo/dataset.csv row {case['apk']}: head_build_variant {variant!r} "
                        f"(head_build_task {row.get('head_build_task')!r}, head_pinned_commit "
                        f"{row.get('head_pinned_commit')!r}); pr_cases.json gave {build['apk_glob_in_pr_cases']!r} "
                        f"with pipeline APKs {build.get('pipeline_apks')}") if variant else "not in the campaign dataset"}
        if not build.get("build_task"):
            task = row.get("head_build_task") or ""
            build["build_task"] = task or None
            filled["build_task"] = {
                "value": build["build_task"],
                "how": (f"dataset.csv head_build_task {task!r}; the pipeline's validation task was "
                        f"{build.get('pipeline_validation_task')!r} ({build.get('pipeline_validation_status')})")}
        badging = read(HERE / "build" / name / "base.badging")
        match = re.search(r"package: name='([^']*)'", badging)
        entry["app_packages"] = [match.group(1)] if match else None
        filled["app_packages"] = {
            "value": entry["app_packages"],
            "how": (f"manifest package of the built base APK (build/{name}/base.badging: {badging[:160]})" if match
                    else "pending: filled from `aapt2 dump badging` of the base APK once tools/build_prs.sh builds it"),
            "rule": "pre-registration: app packages missing -> manifest package read from the built base APK"}
        entry["filled"] = filled
        entry["unresolved"] = [k for k in ("repo_url", "build_commit", "patch_to_apply", "base_sites", "patched_sites",
                                           "app_packages", "spec") if not entry.get(k)]
        if not build.get("build_task"):
            entry["unresolved"].append("build_task")
        out_cases.append(entry)
        pr_number = case["pr_url"].rstrip("/").rsplit("/", 1)[-1]
        lines.append("|".join([name, case["status"], case["repo_url"], case["build_commit"],
                               str(ROOT / case["patch_to_apply"]), build.get("build_task") or "",
                               build.get("apk_glob") or "", pr_number]))

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preregistration": "docs/preregistro_validacao_prs_20261115.md (desvio 5, 2026-11-17)",
        "source": "outputs/dynamic_validation_20261114/pr_cases.json (unchanged)",
        "build_order_rule": "after the 28 cases of pr_cases_resolved.json; accepted, open, closed_by_maintainer",
        "counts": {"cases": len(out_cases), "by_status": {g: sum(c["status"] == g for c in out_cases) for g in GROUPS}},
        "cases": out_cases,
    }
    (HERE / "pr_cases_oldmsg.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    (HERE / "build" / "prs_build_cases_oldmsg.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for c in out_cases:
        print(c["build_order"], c["status"], c["case"], c["build"].get("build_task"), c["build"].get("apk_glob"),
              "unresolved:", c["unresolved"] or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
