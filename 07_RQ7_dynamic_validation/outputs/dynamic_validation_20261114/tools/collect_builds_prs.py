"""Write build_status_prs.json from what tools/build_prs.sh left in build/<case>/.

Same fields as build_status.json (tools/collect_builds.py), for the PR cases of
pr_cases_resolved.json, plus: pr_url, pr_status, build_order, case_status
(built / não compilou / patch não aplica / clone falhou / não compilado ainda),
per variant the attempts, the APK pick rule, net.bytebuddy in the dex, and the
log tail of a failed attempt.
"""
import glob
import hashlib
import json
import sys
from pathlib import Path

V = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114")
ROOT = V.parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cognalys.builds.apk_build import compatible_java_home  # noqa: E402  (read-only: which JDK the helper picks)

BUILDS = Path.home() / "dynval_builds"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else ""


def tail(path: Path, n: int = 30) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])


def pipeline_apk(case: dict):
    """The APK the pipeline's production build made from its patch (only meaningful when the PR diff
    equals the pipeline's)."""
    if not case.get("pr_diff_matches_pipeline_diff") or not case.get("run_dir") or not case.get("finding_id"):
        return None
    pattern = str(ROOT / case["run_dir"] / "application_results/build_validation" / case["finding_id"]
                  / "production/apks/*" / (case["build"].get("apk_glob") or "*.apk"))
    found = sorted(glob.glob(pattern))
    return Path(found[0]) if len(found) == 1 else None


def main() -> int:
    resolved = json.loads((V / "pr_cases_resolved.json").read_text(encoding="utf-8"))
    # Deviation 5 (2026-11-17): the seven old-message cases of pr_cases_oldmsg.json are collected too.
    oldmsg = V / "pr_cases_oldmsg.json"
    if oldmsg.is_file():
        resolved["cases"] = resolved["cases"] + json.loads(oldmsg.read_text(encoding="utf-8"))["cases"]
    # Deviation 6 (2026-11-18): the cases of pr_cases_desvio6.json built by tools/build_prs.sh (not the
    # `reuse_apks` entry, bitbanana_79, which runs on the 14/11 APKs).
    desvio6 = V / "pr_cases_desvio6.json"
    if desvio6.is_file():
        resolved["cases"] = resolved["cases"] + [c for c in json.loads(desvio6.read_text(encoding="utf-8"))["cases"]
                                                 if not c.get("reuse_apks")]
    out = {}
    for case in resolved["cases"]:
        name = case["case"]
        s = V / "build" / name
        patch = ROOT / case["patch_to_apply"]
        clone = BUILDS / name / "src" / name
        report = {}
        for rp in sorted(s.glob("base.a*.build_report.json")):
            try:
                report = json.loads(rp.read_text(encoding="utf-8"))[0]
            except (ValueError, IndexError, KeyError):
                pass
        gradle_root = Path(report.get("gradle_root") or clone)
        jdk = None
        if gradle_root.is_dir():
            try:
                jdk = compatible_java_home(gradle_root)
            except Exception:  # noqa: BLE001 - recording only
                jdk = None
        entry = {"pr_url": case["pr_url"], "pr_status": case["status"], "build_order": case["build_order"],
                 "repo_url": case["repo_url"], "commit": case["build_commit"],
                 "commit_basis": case.get("build_commit_basis"),
                 "jdk": str(jdk) if jdk else "chosen by cognalys.builds.apk_build (compatible_java_home); not yet known",
                 "task": "clean " + (case["build"].get("build_task") or ""),
                 "init_script": "minification off (cognalys.builds.apk_build init script, same as the pipeline's)",
                 "builder": ("python -m cognalys.builds.apk_build --clean-first (no --java-home, JAVA_HOME unset: "
                             "the helper picks the JDK; CARGO_HOME/RUSTUP_HOME=~/dynval_builds/rust in both variants)"),
                 "apk_chosen": case["build"].get("apk_glob"),
                 "patch_file": str(patch), "patch_sha256": sha256(patch) if patch.is_file() else None,
                 "patch_applied": (s / "applied.stat").is_file(), "patch_apply_error": read(s / "apply_check.err"),
                 "locate_note": str(V / "pr_cases_resolved.json"), "reused_pipeline_apk": False}
        for variant in ("base", "patched"):
            apk_path = read(s / f"{variant}.apk_path")
            apk = Path(apk_path) if apk_path else None
            ok = bool(apk and apk.is_file())
            attempts = sorted({p.name.split(".")[1] for p in s.glob(f"{variant}.a[12].*")})
            failed = [a for a in ("a1", "a2") if (s / f"{variant}.{a}.failed").is_file()]
            entry[f"{variant}_apk"] = str(apk) if ok else None
            entry[f"{variant}_sha256"] = sha256(apk) if ok else None
            entry[f"{variant}_badging"] = read(s / f"{variant}.badging") or None
            entry[f"{variant}_built_name"] = read(s / f"{variant}.built_name") or None
            entry[f"{variant}_apk_pick"] = read(s / f"{variant}.apk_pick") or None
            seconds = read(s / f"{variant}.seconds")
            entry[f"{variant}_build_seconds"] = int(seconds) if seconds else None
            entry[f"{variant}_attempts"] = attempts
            entry[f"{variant}_failed_attempts"] = {a: read(s / f"{variant}.{a}.failed") for a in failed}
            if ok:
                status = "built"
            elif (s / f"{variant}.failed").is_file():
                status = "não compilou"
            elif variant == "patched" and (s / "patched.apply_failed").is_file():
                status = "patch não aplica"
            elif failed:
                status = "failed attempt " + ", ".join(failed) + " (retry pending)"
            else:
                status = "not built"
            entry[f"{variant}_status"] = status
            ok_attempt = read(s / f"{variant}.attempt_ok")
            last = f"a{ok_attempt}" if ok_attempt else (failed[-1] if failed else "a1")
            entry[f"{variant}_log"] = str(V / "logs" / f"build_{name}_{variant}.{last}.gradle.log")
            if not ok and failed:
                log = V / "logs" / f"build_{name}_{variant}.{failed[-1]}.gradle.log"
                out_log = V / "logs" / f"build_{name}_{variant}.{failed[-1]}.out"
                entry[f"{variant}_log_tail"] = tail(log) or tail(out_log)
                entry[f"{variant}_apks_found"] = read(s / f"{variant}.{failed[-1]}.apks_found").splitlines()
            entry[f"{variant}_bytebuddy_dex"] = read(s / f"{variant}.bytebuddy") if ok else None
        entry["base_commit_checked_out"] = read(s / "base.commit") or None
        papk = pipeline_apk(case)
        entry["pipeline_patched_apk"] = str(papk) if papk else None
        entry["pipeline_patched_apk_sha256"] = sha256(papk) if papk and papk.is_file() else None
        if entry["pipeline_patched_apk_sha256"] and entry["patched_sha256"]:
            entry["patched_identical_to_pipeline_apk"] = entry["pipeline_patched_apk_sha256"] == entry["patched_sha256"]
        if entry["base_status"] == "built" and entry["patched_status"] == "built":
            case_status = "built"
        elif (s / "clone.failed").is_file():
            case_status = "clone falhou"
        elif "não compilou" in (entry["base_status"], entry["patched_status"]):
            case_status = "não compilou"
        elif entry["patched_status"] == "patch não aplica":
            case_status = "patch não aplica"
        else:
            case_status = "não compilado ainda"
        entry["case_status"] = case_status
        case_seconds = read(s / "case.seconds")
        entry["case_seconds"] = int(case_seconds) if case_seconds else None
        problems = list(case.get("problems_in_pr_cases") or [])
        for field, filled in (case.get("filled") or {}).items():
            problems.append(f"{field} fixed by rule: {filled.get('how')}")
        for variant in ("base", "patched"):
            if entry[f"{variant}_status"] not in ("built", "not built"):
                problems.append(f"{variant} APK {entry[f'{variant}_status']}; see {entry[f'{variant}_log']}")
            if entry.get(f"{variant}_apk_pick") and "not found" in entry[f"{variant}_apk_pick"]:
                problems.append(f"{variant}: {entry[f'{variant}_apk_pick']}")
            if entry.get(f"{variant}_bytebuddy_dex"):
                problems.append(f"{variant} APK carries net.bytebuddy ({entry[f'{variant}_bytebuddy_dex']}): RVSec's "
                                "dexlib2 instrumenter failed on it for networksurvey_114")
        if entry["patch_apply_error"] and not entry["patch_applied"]:
            problems.append("git apply failed: " + entry["patch_apply_error"][:300])
        entry["problems"] = problems
        out[name] = entry
    (V / "build_status_prs.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    for name, e in out.items():
        print(e["build_order"], name, e["case_status"], e["base_status"], e["patched_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
