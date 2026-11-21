"""Rebuild the article's bounded case inventory from run and PR artifacts.

Does not read the historical errors spreadsheet or execute any pipeline stage.
"""
import collections
import csv
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def relative(path):
    return str(path.relative_to(ROOT))


def key(row):
    return (row["apk"], row.get("class", row.get("csv_class", "")),
            row.get("method", row.get("method_name", "")), row["spec"])


def main(prs_summary=None):
    base = load(ROOT / "outputs/exp_final_20261019/consolidated/findings.json")
    report = (ROOT / "docs/relatorio_prs.md").read_text(encoding="utf-8")
    urls = list(dict.fromkeys(re.findall(r"https://github.com/[^\s)]+/pull/\d+", report)))
    # The report explicitly identifies this closure as automated, not a maintainer decision.
    excluded_prs = [u for u in urls if u.endswith("FossifyOrg/Gallery/pull/1145")]
    urls = [u for u in urls if u not in excluded_prs]
    survey = None
    if prs_summary:
        # A dated survey (scripts/levantar_prs.py report-rvsec): its PRs and
        # their states replace the curated report's list.
        survey = load(Path(prs_summary))
        urls = [row["url"] for row in survey["prs"]]
        excluded_prs = []
    feedback = {}
    for path in sorted((ROOT / "data/pr_outcomes").glob("*.json")):
        for row in load(path):
            feedback[row["pull_request"].lower()] = (row, relative(path))
    section = ""
    report_status = {}
    for line in report.splitlines():
        if line.startswith("## "):
            section = line
        for url in re.findall(r"https://github.com/[^\s)]+/pull/\d+", line):
            report_status[url.lower()] = ("accepted" if "Aceitos" in section else
                                          "closed_by_maintainer" if "Fechados" in section else "open")
    if survey:
        report_status = {row["url"].lower(): {"merged": "accepted", "manual": "accepted",
                                              "maintainer": "closed_by_maintainer"}.get(row["category"], row["category"])
                         for row in survey["prs"]}
    with (ROOT / "data/processed/exp_final/findings_exp_final.csv").open(encoding="utf-8-sig", newline="") as stream:
        source34 = list(csv.DictReader(stream))
    # Manually checked against the method and change described in the curated report.
    report_identity = {
        "https://github.com/sapuseven/betteruntis/pull/515": ("getCacheFile", "MessageDigestSpec"),
        "https://github.com/quantumbadger/redreader/pull/1373": ("sha1", "MessageDigestSpec"),
        "https://github.com/lneugebauer/nextcloud-cookbook/pull/204": ("configureTrustAllCertificates", "SSLContextSpec"),
    }

    # Inspect only metadata directories, never clone/worktree trees.
    dirs = [ROOT / "outputs/pull_requests"]
    for parent in (ROOT / "outputs").iterdir():
        if not parent.is_dir():
            continue
        dirs.append(parent / "pull_requests")
        if parent.name.startswith(("rvsec", "demo")) or parent.name == "exp_final_20261019":
            dirs += [p / "pull_requests" for p in parent.iterdir()
                     if p.is_dir() and p.name.startswith(("run", "subset", "rvsec"))]
    # Publication records also sit deeper (rvsec_tls_20261015/<app>/run,
    # rerun_correcoes_20261022/run): every pull_requests directory up to four
    # levels down, outside clone and worktree trees.
    found = subprocess.run(["find", str(ROOT / "outputs"), "-maxdepth", "4",
                            "(", "-name", "clones", "-o", "-name", "worktrees", "-o", "-name", ".claude", ")",
                            "-prune", "-o", "-type", "d", "-name", "pull_requests", "-print"],
                           capture_output=True, text=True).stdout.split("\n")
    dirs = list(dict.fromkeys(dirs + [Path(p) for p in found if p]))
    metadata = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                row = load(path)
            except (ValueError, OSError):
                continue
            if isinstance(row, dict):
                metadata.append((path, row))

    created_urls = {}
    for path, row in metadata:
        if path.name.endswith("_pr_created.json") and row.get("html_url"):
            sibling = path.with_name(path.name.replace("_pr_created.json", "_pr.json"))
            created_urls[row["html_url"].lower()] = sibling
            if not survey and row.get("created_at", "") >= "2026-10-20" and row["html_url"] not in urls:
                urls.append(row["html_url"])
                report_status[row["html_url"].lower()] = "merged" if row.get("merged_at") else row.get("state", "unknown")
    cases = {key(row): {"apk": row["apk"], "class": row["class"],
                       "method": row["method"], "spec": row["spec"],
                       "finding_id": row.get("finding_id"), "run34": row, "prs": []}
             for row in base}
    prs = []
    for url in urls:
        outcome, source = feedback.get(url.lower(), ({}, None))
        sibling = created_urls.get(url.lower())
        exact = [(p, r) for p, r in metadata
                 if p == sibling or any(str(r.get(field, "")).lower().rstrip("/") == url.lower()
                        for field in ("pull_request_url", "pr_url", "pull_request"))]
        if not exact and outcome.get("finding_id"):
            exact = [(p, r) for p, r in metadata if r.get("finding_id") == outcome["finding_id"]]
        if not exact and outcome.get("csv_class"):
            exact = [(p, r) for p, r in metadata
                     if all(r.get(f) == outcome.get(f) for f in ("csv_class", "method_name", "spec"))]
        match_basis = "published_url_or_feedback_identity"
        if not exact:
            repository = url.rsplit("/pull/", 1)[0].lower()
            authorized_identity = report_identity.get(url.lower())
            exact = [(p, r) for p, r in metadata
                     if str(r.get("remote_url", "")).lower().removesuffix(".git").rstrip("/") == repository
                     and authorized_identity == (r.get("method_name"), r.get("spec")) and r.get("csv_class")]
            match_basis = "manually_checked_report_method_and_specification"
        # The small 34-case input also preserves identities of cases superseded upstream.
        if not exact:
            repository = url.rsplit("/pull/", 1)[0].lower()
            source_matches = [r for r in source34 if r.get("repo_url", "").lower().removesuffix(".git") == repository
                              and report_identity.get(url.lower()) == (r.get("method"), r.get("spec"))]
            if len(source_matches) == 1:
                r = source_matches[0]
                exact = [(ROOT / "data/processed/exp_final/findings_exp_final.csv",
                          {"apk": r["apk"], "csv_class": r["class"], "method_name": r["method"], "spec": r["spec"]})]
                match_basis = "small_input_and_manually_checked_report_identity"
        identities = {}
        for path, row in exact:
            apk = row.get("apk", "")
            if not apk:
                match = re.match(r"(.+?\.apk)_", path.name)
                apk = match.group(1) if match else ""
            identity = (apk, row.get("csv_class", ""), row.get("method_name", ""), row.get("spec", ""))
            if all(identity):
                identities.setdefault(identity, []).append(relative(path))
        entry = {"url": url, "outcome_record": outcome, "outcome_source": source,
                 "match_basis": match_basis,
                 "recorded_status": report_status.get(url.lower()),
                 "identity_candidates": [dict(zip(("apk", "class", "method", "spec"), k),
                                              metadata=v) for k, v in identities.items()]}
        if len(identities) == 1:
            identity = next(iter(identities))
            entry["identity_status"] = "resolved"
            entry["overlaps_run34"] = identity in cases
            case = cases.setdefault(identity, dict(zip(("apk", "class", "method", "spec"), identity), prs=[]))
            case["prs"].append(url)
        else:
            entry["identity_status"] = "ambiguous" if identities else "missing"
        prs.append(entry)

    # Find supplied APKs only in acquisition locations, not generated build trees.
    apk_dirs = [ROOT / "outputs/apks", ROOT / "outputs/built_apks_no_proguard", ROOT / "built_apks_no_proguard"]
    apk_dirs += [p / "apks" for p in (ROOT / "outputs").iterdir()
                 if p.is_dir() and (p.name.startswith("rvsec") or p.name.startswith("demo"))]
    apk_files = collections.defaultdict(list)
    for directory in apk_dirs:
        if directory.is_dir():
            for path in directory.rglob("*.apk"):
                apk_files[path.name].append(path)
    aapt = Path("/home/antonow/Android/Sdk/build-tools/35.0.0/aapt2")
    apks = []
    for name in sorted({k[0] for k in cases}):
        paths = apk_files.get(name, [])
        checked = []
        for path in paths:
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                    valid = "AndroidManifest.xml" in names and any(re.fullmatch(r"classes\d*\.dex", n) for n in names)
                artifact = {"path": relative(path), "bytes": path.stat().st_size, "apk_container": valid}
                if valid and aapt.exists():
                    proc = subprocess.run([str(aapt), "dump", "badging", str(path)], capture_output=True, text=True, timeout=30)
                    match = re.search(r"package: name='([^']+)' versionCode='([^']+)'", proc.stdout)
                    if match:
                        artifact.update(package=match[1], version_code=match[2])
                checked.append(artifact)
            except (OSError, zipfile.BadZipFile):
                checked.append({"path": relative(path), "apk_container": False})
        apks.append({"apk": name, "artifacts": checked,
                     "status": "local_container_verified" if any(p["apk_container"] for p in checked) else "identifier_only"})
    result = {"snapshot_date": "2026-10-22", "pr_status_source_dates": ["2026-10-20", "2026-10-21"],
              "selection": "34 consolidated findings plus eligible PR cases in docs/relatorio_prs.md and later local publication records; no historical error spreadsheet",
              } if not survey else {
              "snapshot_date": "2026-10-22", "pr_status_source_dates": [survey["report_date"]],
              "selection": f"34 consolidated findings plus the RVSec pull requests of {prs_summary} (open, incorporated or closed by a maintainer); no historical error spreadsheet",
              }
    result.update({
              "identity": ["apk", "class", "method", "spec"],
              "counts": {"run_findings": len(base), "run_apks": len({r["apk"] for r in base}),
                         "pr_records": len(prs), "pr_identity_status": dict(collections.Counter(r["identity_status"] for r in prs)),
                         "pr_recorded_status": dict(collections.Counter(r["recorded_status"] for r in prs)),
                         "overlapping_pr_findings": sum(r.get("overlaps_run34", False) for r in prs),
                         "union_findings_resolved": len(cases), "union_apks_resolved": len(apks),
                         "apk_status": dict(collections.Counter(r["status"] for r in apks))},
              "excluded_prs": [{"url": u, "reason": "closed by bot, not a maintainer decision"} for u in excluded_prs],
              "findings": list(cases.values()), "prs": prs, "apks": apks})
    (OUT / "inventario.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert len(base) == len({key(r) for r in base}) == 34
    assert len(cases) == len({key(r) for r in result["findings"]})
    with (OUT / "findings.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["apk", "class", "method", "spec", "in_run34", "pr_urls"])
        writer.writerows([*k, bool(v.get("run34")), " | ".join(v["prs"])] for k, v in cases.items())
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
    for row in prs:
        print(row["url"], row["identity_status"], [(r["apk"], r["method"], r["spec"]) for r in row["identity_candidates"]])


if __name__ == "__main__":
    import sys
    # Optional: a survey's summary_rvsec.json (outputs/prs_reports/<date>/).
    main(sys.argv[1] if len(sys.argv) > 1 else None)
