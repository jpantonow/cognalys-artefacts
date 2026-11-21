"""Write build_status.json from what tools/build_all.sh left in build/<case>/."""
import hashlib
import json
from pathlib import Path

V = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114")
E = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/exp_final_20261019")
JDK = "/usr/lib/jvm/java-21-openjdk-amd64"
BV = E / "run2/application_results/build_validation"
CASES = {
    "tokn_19": {
        "repo": "https://github.com/tokn-authenticator/tokn", "commit": "19d4bdf53e1a641e36d47ecd5239a23d5202ef19",
        "task": "clean assembleDebug", "pick": "app-debug.apk",
        "pipeline_apk": BV / "0aeb52bd220d/production/apks/93a7a5a1b3-2/app-debug.apk",
        "problems": [
            "Built on main tip 19d4bdf (versionCode 22), the commit the pipeline built the patch on, not on the analysed F-Droid revision f7cd9237 (versionCode 19); KeystoreManager.kt is the same blob at both.",
            "Debug package me.diamondforge.tokn.debug, not the F-Droid me.diamondforge.tokn.",
            "The original finding came from ape alone (99 runs), at app start (7 s)."]},
    "bitbanana_79": {
        "repo": "https://github.com/michaelWuensch/BitBanana", "commit": "e813eb374cb6bc23d2436b38ecada9c47d7cda38",
        "task": "clean assembleDebug", "pick": "*debug.apk",
        "pipeline_apk": E / "subset_bitbanana_scope6/application_results/build_validation/53c35a191a23/production/apks/fc7ca09577-3/bitbanana-1.1.0-debug.apk",
        "problems": [
            "Built on master tip e813eb3 (versionCode 81), where the base-port step moved the patch; RVSec analysed 14a4bb5 (versionCode 79).",
            "The patch is bitbanana_migration.patch (byte-identical to the PR json diff), the diff committed after an LLM compiler repair; 7 files.",
            "The patch renames encodePbkdf2 (encodeLegacyAppLockPbkdf2 for stored verifiers, encodeCurrentAppLockPbkdf2 for new ones): classify.py looks for the governed call in those methods.",
            "encodePbkdf2 runs only from the app-lock setup/entry screens; the explorers must turn an app lock on (the original finding: ape, 2 runs). A manual script may be needed.",
            "The pipeline's original build had no `clean` (incremental after a failed build); both builds here use clean.",
            "locate/bitbanana_79.json is not valid JSON (unescaped Windows paths)."]},
    "untis_29198884": {
        "repo": "https://github.com/SapuSeven/BetterUntis", "commit": "dbb978db73f9549869f9aa831ad619b6d4afbcd8",
        "task": "clean assembleDebug", "pick": "app-foss-debug.apk",
        "pipeline_apk": BV / "21b3aced6bcf/production/apks/47b1165883/app-foss-debug.apk",
        "problems": [
            "Built on develop tip dbb978db, where DiskCache moved to core/data (com.sapuseven.untis.core.data.cache.DiskCache); RVSec analysed 250d629b (com.sapuseven.untis.data.cache.DiskCache). classify.py matches both names.",
            "assembleDebug builds foss and gms; foss is used.",
            "BetterUntis normally needs a WebUntis school login; whether the disk cache is written without one is unchecked (the original finding: ape, 55 runs)."]},
    "networksurvey_114": {
        "repo": "https://github.com/christianrowlands/android-network-survey", "commit": "fa9259c6c9586b306f634650ea7e588005e7ad4d",
        "task": "clean :networksurvey:assembleCdrDebug", "pick": "*x86_64-debug.apk",
        "pipeline_apk": BV / "ac1a8dd4d680/production/apks/eccca7a79a/networksurvey-1.59-cdr-x86_64-debug.apk",
        "problems": [
            "Built on develop tip fa9259c6 (67 commits after the analysed 6352b70); CryptoManager.kt is the same blob at both.",
            "ABI splits: the x86_64 split is used (the emulator image is x86_64).",
            "Debug cdr build, package com.craxiom.networksurvey.dev.",
            "Adjudicated non_security (rule-only KeyStore ORDER fix)."]},
    "passnotes_11700": {
        "repo": "https://github.com/aivanovski/keepassvault", "commit": "77b70f8a30586252b278cf0c0d0ff142e8096008",
        "task": "clean :app:assembleAutomationDebug", "pick": "app-automation-debug.apk",
        "pipeline_apk": BV / "6d29118e4263/production/apks/4be51154ed/app-automation-debug.apk",
        "problems": [
            "Built on master tip 77b70f8a (2.1.0, versionCode 20100); RVSec analysed 5fbadf40 (1.17.0); HttpClientFactory.kt is identical at both.",
            "automation flavour + debug (package com.ivanovsky.passnotes.automation), as the pipeline built it.",
            "The violating branch needs BuildConfig.DEBUG and a WebDAV client of type UNSECURE, i.e. a WebDAV server set up in the UI; exploration may never reach it.",
            "The patch also keeps SSLContext.getInstance(\"TLS\") and hostnameVerifier { _, _ -> true }, and changes a log banner.",
            "The keepass-rs-android module builds Rust JNI libraries with cargo + cargo-ndk. ~/.cargo no longer exists (the pipeline's 2026-10-19 build had it), so the first attempt of both variants failed (\"Required command is not installed: cargo\"); rustup stable (rustc 1.100.0), the three Android targets and cargo-ndk 4.1.2 were installed under ~/dynval_builds/rust (CARGO_HOME/RUSTUP_HOME, no profile edits) and both variants rebuilt with them. The Rust toolchain version of the pipeline's build is unknown; base and patched here share the same one."]},
    "trafficlight_38": {
        "repo": "https://github.com/leekleak/traffic-light", "commit": "e40dd7a77e24290345b0f99f4990467758d9acbd",
        "task": "clean :app:assembleFullDebug", "pick": "*full-debug.apk",
        "pipeline_apk": BV / "32249b3e5e5d/production/apks/ac66f81076/com.leekleak.trafficlight-3.4-full-debug.apk",
        "problems": [
            "Built on main tip e40dd7a (versionCode 54), where the base-port step moved the patch; RVSec analysed 810679a (versionCode 38). At e40dd7a getSecretKey reads the key inside keyCache.computeIfAbsent, so the call site may be a lambda of getSecretKey.",
            "Needs the recursive submodule iperfIntegration/src/main/cpp/iperf and the NDK.",
            "Adjudicated non_security (KeyStore ORDER rule)."]},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else ""


out = {}
for case, c in CASES.items():
    s = V / "build" / case
    entry = {"repo_url": c["repo"], "commit": c["commit"], "jdk": JDK, "task": c["task"],
             "init_script": "minification off (cognalys.builds.apk_build init script, same as the pipeline's)",
             "builder": "python -m cognalys.builds.apk_build --clean-first --java-home " + JDK,
             "apk_chosen": c["pick"], "patch_file": str(V / "patches" / f"{case}.diff"),
             "patch_sha256": sha256(V / "patches" / f"{case}.diff"),
             "patch_applied": (s / "applied.stat").is_file(), "patch_apply_error": read(s / "apply_check.err"),
             "locate_note": str(V / "locate" / f"{case}.json"), "reused_pipeline_apk": False}
    for variant in ("base", "patched"):
        apk = Path(read(s / f"{variant}.apk_path")) if read(s / f"{variant}.apk_path") else None
        ok = bool(apk and apk.is_file())
        entry[f"{variant}_apk"] = str(apk) if ok else None
        entry[f"{variant}_sha256"] = sha256(apk) if ok else None
        entry[f"{variant}_badging"] = read(s / f"{variant}.badging") or None
        entry[f"{variant}_built_name"] = read(s / f"{variant}.built_name") or None
        entry[f"{variant}_build_seconds"] = int(read(s / f"{variant}.seconds")) if read(s / f"{variant}.seconds") else None
        entry[f"{variant}_status"] = "built" if ok else ("failed rc " + read(s / f"{variant}.failed")
                                                          if (s / f"{variant}.failed").is_file() else "not built")
        entry[f"{variant}_log"] = str(V / "logs" / f"build_{case}_{variant}.gradle.log")
    entry["base_commit_checked_out"] = read(s / "base.commit") or None
    pipeline = c["pipeline_apk"]
    entry["pipeline_patched_apk"] = str(pipeline)
    entry["pipeline_patched_apk_sha256"] = sha256(pipeline) if pipeline.is_file() else None
    problems = list(c["problems"])
    if read(s / "tracked_changes_before_apply.txt"):
        problems.append("Tracked files were left modified by the base build and reset before applying: "
                        + read(s / "tracked_changes_before_apply.txt"))
    for variant in ("base", "patched"):
        if entry[f"{variant}_status"] != "built":
            problems.append(f"{variant} APK {entry[f'{variant}_status']}; see {entry[f'{variant}_log']}")
    if entry["patch_apply_error"]:
        problems.append("git apply failed: " + entry["patch_apply_error"][:300])
    if entry["pipeline_patched_apk_sha256"] and entry["patched_sha256"]:
        entry["patched_identical_to_pipeline_apk"] = entry["pipeline_patched_apk_sha256"] == entry["patched_sha256"]
    entry["problems"] = problems
    out[case] = entry
(V / "build_status.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
for case, e in out.items():
    print(case, e["base_status"], e["patched_status"], (e["base_sha256"] or "")[:12], (e["patched_sha256"] or "")[:12])
