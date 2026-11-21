"""Findings from RVSec, the runtime verification of crypto API misuses.

RVSec (https://github.com/PAMunb/rvsec) instruments an APK with JavaMOP
monitors, exercises it on an emulator with test-generation tools, and writes
every monitor report to errors.csv in the experiment's results directory
(rv-android, `rv-experiment run`):

    apk,rep,timeout,tool,time,spec,class,method,source,code,event,message,unique_msg

with the message in the monitor envelope
`v=1 code=... ev=... obj=... val=... exp=... msg='...'`. The codes are
described in the specification set's codes.csv
(rvsec/rvsec-mop/src/main/resources/<set>/codes.csv): their error type, the
kind of site and the label (violation, sequence, not-observed, ...).

This turns those reports into findings the pipeline reads: one per misuse
(apk, class, method, spec), with every report of it across repetitions and
tools summarised as the detector's own context, in the same columns
corpus.import_rv_misuses writes for the curated estudo02 package. What
RVSec does not know is where an APK's source code is; a source map says so
(apk -> repository and the commit the APK was built from), or, for a single
app, the local checkout. A misuse whose APK has no source is reported, not
dropped and not guessed.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

from cognalys.corpus.import_rv_misuses import (
    COMMIT, LABEL_MEANINGS, NO_MESSAGE, SITE_KINDS, parse_envelope,
    repo_slug, runtime_observation,
)
from cognalys.sources import read_rows
from cognalys.utils.detector_labels import detector_tables

ERRORS_FILE = "errors.csv"
REPORT_COLUMNS = ("apk", "spec", "class", "method", "message")
DEFAULT_SPECIFICATION_SET = "jca_android"
# How a specification set's codes table sits in an RVSec checkout.
CODES_IN_CHECKOUT = Path("rvsec") / "rvsec-mop" / "src" / "main" / "resources"


@dataclass(frozen=True)
class SourceLocation:
    """Where an APK's source is: a repository and the commit it was built from.

    ``packages`` are the app's own Java/Kotlin packages (its applicationId or
    namespace), which tell the app's code from bundled libraries.
    """

    repo_url: str
    commit: str
    packages: tuple[str, ...] = ()


@dataclass
class ImportResult:
    findings: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    reports: int = 0
    # Echo findings folded into the finding they echo (fold_echoes).
    folded: int = 0

    @property
    def complete(self) -> bool:
        return not self.problems


def errors_files(paths: Iterable[Path]) -> list[Path]:
    """errors.csv files: given directly, or inside rv-experiment result dirs.

    A results directory without any errors.csv is read through its tasks'
    logcat captures instead (see logcat_rows). One with an errors.csv also
    reads the logcat of each task errors.csv holds nothing of
    (unreported_task_logcats).
    """
    found = []
    for path in map(Path, paths):
        if path.is_dir():
            reports = sorted(path.rglob(ERRORS_FILE))
            found.extend(reports + unreported_task_logcats(path, reports) if reports
                         else sorted(path.rglob(f"*{LOGCAT_SUFFIX}")))
        else:
            found.append(path)
    return found


def _tasks(results_dir: Path) -> Iterable[tuple[dict, dict]]:
    """(config, result) of each task in the tasks.json files under ``results_dir``."""
    import json

    for path in sorted(Path(results_dir).rglob("tasks.json")):
        try:
            tasks = json.loads(path.read_text(encoding="utf-8")).get("tasks", [])
        except (OSError, ValueError, AttributeError):
            continue
        for task in tasks:
            yield task.get("config", {}), task.get("result") or {}


def _task_tool(config: Mapping) -> str:
    """name:variant, as errors.csv's tool column and the logcat's file name spell it."""
    tool = config.get("tool_config", {})
    return ":".join(part for part in (tool.get("name"), tool.get("variant")) if part)


def _task_logcat(results_dir: Path, result: Mapping) -> Optional[Path]:
    """A task's logcat capture, found by name: tasks.json holds the container's path."""
    name = Path(result.get("logcat_file") or "").name
    found = sorted(Path(results_dir).rglob(name)) if name else []
    return found[0] if found else None


def unreported_task_logcats(results_dir: Path, reports: Iterable[Path]) -> list[Path]:
    """The logcat of each task that did not complete or has no row in ``reports``.

    rv-experiment writes errors.csv rows only for tasks that complete. On
    2026-10-23 DNSToggle's monkey task completed without a report and its ape
    task ended in ERROR after its monitors had reported twice in the app's own
    EncryptionManager: errors.csv held its header alone and the import gave
    0 findings (Beta, 2026-10-29: ape's 18 reports lost behind monkey's 15).
    A completed task errors.csv already holds is not read again, so no report
    counts twice.
    """
    reported = {(row.get("apk", ""), row.get("rep", ""), row.get("timeout", ""),
                 row.get("tool", "")) for path in reports for row in read_rows(path)}
    logcats: list[Path] = []
    for config, result in _tasks(results_dir):
        key = (config.get("apk_name", ""), str(config.get("repetition", "")),
               str(config.get("timeout", "")), _task_tool(config))
        if result.get("state") == COMPLETED and key in reported:
            continue
        logcat = _task_logcat(results_dir, result)
        if logcat and logcat not in logcats:
            logcats.append(logcat)
    return logcats


# tasks.json's state of a task that ran to its end and wrote its errors.csv rows.
COMPLETED = "COMPLETED"
LOGCAT_SUFFIX = ".logcat"
# Where a finding's reports were read (its report_source): one read only from
# a logcat came from a task errors.csv does not hold, and is counted apart.
LOGCAT_SOURCE = "logcat"
# A monitor report as the instrumented app logs it under the RVSEC tag:
#   spec,class,simple class,method,source,error type,v=1 code=... msg='...'
_LOGCAT_REPORT = re.compile(r"\sRVSEC\s*:\s(?P<body>.*)$")
_LOGCAT_TASK = re.compile(r"^(?P<apk>.+?\.apk)__(?P<rep>\d+)__(?P<timeout>\d+)__(?P<tool>.+)$")


def logcat_rows(path: Path) -> list[dict]:
    """The monitor reports of one task's logcat, in errors.csv's columns.

    rv-experiment writes errors.csv only for tasks that complete. On
    2026-10-12 Monkey aborted (exit 91) on all four apps after about a
    minute, every task was marked failed and no errors.csv was written,
    while the monitors had already logged 30 reports. The logcat capture
    (<apk>__<rep>__<timeout>__<tool>.logcat) keeps them.
    """
    task = _LOGCAT_TASK.match(Path(path).name[: -len(LOGCAT_SUFFIX)])
    if not task:
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOGCAT_REPORT.search(line)
        if not match or "v=1 code=" not in match.group("body"):
            continue
        fields = match.group("body").split(",", 6)
        if len(fields) < 7:
            continue
        spec, class_name, _simple, method, source, error_type, message = fields
        envelope = parse_envelope(message)
        rows.append({
            "apk": task["apk"], "rep": task["rep"], "timeout": task["timeout"],
            "tool": task["tool"], "time": "", "spec": spec, "class": class_name,
            "method": method, "source": source, "code": envelope.get("code", ""),
            "event": envelope.get("ev", ""), "message": message,
            "unique_msg": ":::".join((class_name, method, spec, error_type,
                                      envelope.get("code", ""), message)),
            "report_source": LOGCAT_SOURCE,
        })
    return rows


def report_rows(path: Path) -> list[dict]:
    """The reports of an errors.csv or of a task's logcat capture."""
    return logcat_rows(path) if Path(path).name.endswith(LOGCAT_SUFFIX) else read_rows(path)


def codes_table(codes: Path | None = None, rvsec_home: Path | None = None,
                specification_set: str = DEFAULT_SPECIFICATION_SET) -> dict[str, dict]:
    """code -> codes.csv row, from a file or from an RVSec checkout; {} if neither."""
    path = Path(codes) if codes else None
    if path is None and rvsec_home:
        path = Path(rvsec_home) / CODES_IN_CHECKOUT / specification_set / "codes.csv"
    if path is None or not path.is_file():
        return {}
    return {row["code"]: row for row in read_rows(path) if row.get("code")}


def source_map(path: Path) -> dict[str, SourceLocation]:
    """apk -> source, from a CSV with apk, repo_url and a commit column.

    RVSec's corpus sheet (dataset.csv) works as is: its head_pinned_commit is
    the commit the analysed APK was built from, and is preferred to
    pinned_commit, which belongs to an earlier build.
    """
    sources = {}
    for row in read_rows(path):
        apk = (row.get("apk") or "").strip()
        repo_url = (row.get("repo_url") or row.get("repository") or "").strip()
        commit = next((str(row.get(column) or "").strip()
                       for column in ("head_pinned_commit", "commit", "pinned_commit")
                       if str(row.get(column) or "").strip()), "")
        # Several packages, separated by ";": Futon's own code is under
        # io.github.landwarderer.futon and eu.kanade.tachiyomi, and with only
        # the first every report in the second was taken for a library.
        packages = tuple(package for package in re.split(
            r"[;\s]+", (row.get("manifest_package") or row.get("package") or "").strip()) if package)
        if apk and repo_url:
            sources[apk] = SourceLocation(repo_url, commit, packages)
    return sources


_APPLICATION_ID = re.compile(r"""\b(?:applicationId|namespace)\s*=?\s*["']([\w.]+)["']""")


def checkout_source(path: Path) -> SourceLocation:
    """A local checkout as the source of every APK: its HEAD and its app ids."""
    from cognalys.detectors import snapshot

    state = snapshot(path)
    packages = []
    for build in sorted(state.path.glob("*/build.gradle*")) + sorted(state.path.glob("build.gradle*")):
        for package in _APPLICATION_ID.findall(build.read_text(encoding="utf-8", errors="replace")):
            if package not in packages:
                packages.append(package)
    return SourceLocation(str(state.path), state.commit, tuple(packages))


def manifest_package(apk: str) -> str:
    """The package an F-Droid-style APK name carries: org.app_123.apk -> org.app.

    "" when the name carries none (cryptoapp.apk).
    """
    stem = apk.removesuffix(".apk")
    head, separator, tail = stem.rpartition("_")
    package = head if separator and tail.isdigit() else stem
    return package if "." in package else ""


# A class name R8 renamed: every segment one or two letters, maybe a digit
# or two (A.c, R1.b, Y1.d, a.b.c$d). On 2026-10-12 the F-Droid release of
# deutsia-radio reported ten misuses in R1.b, A.c, N1.b and Y1.d, which the
# app's package check called "library" and no checkout can resolve.
_OBFUSCATED_SEGMENT = re.compile(r"[A-Za-z]{1,2}\d{0,2}")


def is_obfuscated(class_name: str) -> bool:
    segments = re.split(r"[.$]", class_name)
    return len(segments) > 1 and all(_OBFUSCATED_SEGMENT.fullmatch(part) for part in segments)


# Which code describes a misuse whose reports carry several: the label that
# says most about the code, before the most frequent. okio's ByteString.digest
# reported MESSAGEDIGEST-ORDER-02 (creation-refused, an echo) 30 times and the
# MD5 violation behind it (MESSAGEDIGEST-ALG-02) 15 times.
LABEL_PRIORITY = (
    "violation", "random-key-material", "reuse-after-final", "sequence",
    "application-manager", "not-observed", "platform-default",
    "creation-unobserved", "upstream-refused", "creation-refused",
)


def representative_code(by_code: Mapping[str, int], codes: Mapping[str, dict]) -> str:
    def rank(code: str) -> tuple[int, int]:
        label = codes.get(code, {}).get("label", "")
        priority = LABEL_PRIORITY.index(label) if label in LABEL_PRIORITY else len(LABEL_PRIORITY)
        return priority, -by_code[code]
    return min(by_code, key=rank)


# Labels whose report repeats one already made: the producer of the object
# was reported (upstream-refused) or its creation was refused
# (creation-refused). Read from each label's report kind in
# data/detector_labels.json ("echo"), not from its name.
_LABELS_BY_REPORT = detector_tables("rvsec")["labels_by_report"]
ECHO_LABELS = frozenset(_LABELS_BY_REPORT.get("echo", ()))  # type: ignore[union-attr]

# Labels whose report is only that the monitor saw no admitted producer
# (report kind "unobserved_origin"). An upstream-refused IV is an echo, a
# report about a refused producer and not a gap in what the monitor sees; a
# value check reports what it saw; an unadmitted_origin report states an
# origin the monitor did see and the check does not admit. Cryptomator Lite run_v6
# (2026-10-30): only a not-observed IV report is settled by an IV the
# provider generated (iv_provenance).
UNOBSERVED_ORIGIN_LABELS = frozenset(
    _LABELS_BY_REPORT.get("unobserved_origin", ()))  # type: ignore[union-attr]


def fold_echoes(findings: list[dict]) -> tuple[list[dict], int]:
    """Fold each echo into the findings of the same method that it echoes.

    PaperCript's SecretCodec.aesCtr gave four findings for two causes: the
    IV and the key were reported where they were built (IvParameterSpecSpec,
    SecretKeySpecSpec), and Cipher.init then reported both again as
    upstream-refused (IvChainJunctionSpec, CipherSpec). Each was analysed on
    its own. An echo now travels as detector_echoes on the findings of its
    method that are not echoes; an echo with no such finding in its method
    (its origin is elsewhere) stays a finding of its own.
    """
    sites: dict[tuple, list[dict]] = collections.OrderedDict()
    for finding in findings:
        sites.setdefault((finding["apk"], finding["class"], finding["method"]), []).append(finding)
    dropped = set()
    for rows in sites.values():
        origins = [row for row in rows if row.get("detector_label") not in ECHO_LABELS]
        echoes = [row for row in rows if row.get("detector_label") in ECHO_LABELS]
        if not origins or not echoes:
            continue
        summary = "; ".join(
            f"{echo['spec']} {echo['detector_code']} ({echo['detector_label']}, "
            f"{echo['runs']} run(s)): {echo['message']}" for echo in echoes)
        for origin in origins:
            origin["detector_echoes"] = summary
        dropped.update(id(echo) for echo in echoes)
    return [finding for finding in findings if id(finding) not in dropped], len(dropped)


def code_origin(class_name: str, packages: Iterable[str]) -> str:
    """app, library, or "" when the app's packages are not known."""
    packages = [package for package in packages if package]
    if not packages:
        return ""
    return "app" if any(class_name == package or class_name.startswith(package + ".")
                        for package in packages) else "library"


def import_results(paths: Iterable[Path], sources: Mapping[str, SourceLocation],
                   codes: Mapping[str, dict] | None = None,
                   apks_dir: Path | None = None) -> ImportResult:
    """Every misuse in RVSec's errors.csv files as a findings row."""
    codes = codes or {}
    result = ImportResult()
    reports: dict[tuple, list[dict]] = collections.OrderedDict()
    for path in errors_files(paths):
        if not path.is_file():
            result.problems.append({"path": str(path), "problem": "not found"})
            continue
        rows = report_rows(path)
        for number, row in enumerate(rows, 2):
            missing = [column for column in REPORT_COLUMNS if not (row.get(column) or "").strip()]
            if missing:
                result.problems.append({"path": str(path), "line": number,
                                        "problem": f"missing {', '.join(missing)}"})
                continue
            result.reports += 1
            key = (row["apk"], row["class"], row["method"], row["spec"])
            reports.setdefault(key, []).append(row)

    for key, rows in reports.items():
        apk, class_name, method, spec = key
        source = sources.get(apk)
        if source is None:
            result.problems.append({"apk": apk, "class": class_name, "method": method,
                                    "spec": spec, "problem": "no source for this APK"})
            continue
        if source.commit and not COMMIT.match(source.commit):
            result.problems.append({"apk": apk, "problem": f"not a commit: {source.commit!r}"})
            continue
        if is_obfuscated(class_name):
            result.problems.append({"apk": apk, "class": class_name, "method": method,
                                    "spec": spec, "problem": "obfuscated class name (R8); "
                                    "build the APK without minification to locate it"})
            continue
        # The report the finding is described by (representative_code).
        by_code = collections.Counter(row.get("code", "") for row in rows)
        code = representative_code(by_code, codes)
        report = next(row for row in rows if row.get("code", "") == code)
        envelope = parse_envelope(report.get("message", ""))
        code = code or envelope.get("code", "")
        message = envelope.get("msg") or report.get("message") or NO_MESSAGE
        id_parts = (report.get("unique_msg") or "").split(":::")
        code_row = codes.get(code, {})
        error_type = (id_parts[3] if len(id_parts) > 4 else "") or code_row.get("error_type", "")
        site_kind = code_row.get("site_kind", "")
        label = code_row.get("label", "")
        runs = {(row.get("rep", ""), row.get("timeout", ""), row.get("tool", "")) for row in rows}
        tools = sorted({row.get("tool", "") for row in rows if row.get("tool")})
        packages = source.packages or (manifest_package(apk),)
        apk_file = Path(apks_dir) / apk if apks_dir else None
        result.findings.append({
            "apk": apk,
            "rep": report.get("rep", ""), "timeout": report.get("timeout", ""),
            # RVSec is the analyzer that reported it; ape or monkey only drove
            # the app's screens (they stay in detector_tools). Named as the
            # tool, the explorer reached maintainers as the analysis itself.
            "tool": "rvsec", "explorer": report.get("tool", ""), "time": report.get("time", ""),
            "spec": spec, "class": class_name, "method": method,
            "message": message,
            "unique_msg": ":::".join((class_name, method, spec, error_type, message)),
            "repo_url": source.repo_url,
            # owner/name of a hosted repository; a local checkout has none.
            "repo": repo_slug(source.repo_url) if "://" in source.repo_url else "",
            "commit": source.commit,
            "apk_url": str(apk_file.resolve()) if apk_file and apk_file.is_file() else "",
            # A heuristic, as the curated package's own origin column is: code
            # under the app's packages is the app's, anything else is bundled.
            "origin": code_origin(class_name, packages),
            "runs": str(len(runs)),
            "detector_code": code,
            "detector_site_kind": (f"{site_kind}: {SITE_KINDS[site_kind]}"
                                   if site_kind in SITE_KINDS else site_kind),
            "detector_label": label,
            "detector_label_meaning": LABEL_MEANINGS.get(label, ""),
            "detector_value": envelope.get("val", ""),
            "detector_expected": envelope.get("exp", ""),
            "detector_source_location": report.get("source", ""),
            "detector_codes": ", ".join(
                f"{name} [{codes[name]['label']}] ({count})" if codes.get(name, {}).get("label")
                else f"{name} ({count})" for name, count in by_code.most_common()),
            "detector_tools": ", ".join(tools),
            "detector_runtime_observation": runtime_observation(
                {"runs": str(len(runs))}, rows, []),
            # Not a detector_ column: the analysis judges the report, not the
            # file it was read from.
            "report_source": ", ".join(sorted({row.get("report_source") or ERRORS_FILE
                                               for row in rows})),
        })
    result.findings, result.folded = fold_echoes(result.findings)
    return result


# ---------------------------------------------------------------------------
# Running RVSec itself (rv-android's Docker image).
# ---------------------------------------------------------------------------

DEFAULT_IMAGE = "phtcosta/rvandroid:0.9.5"


@dataclass(frozen=True)
class ExperimentSettings:
    """What rv-experiment is asked to do, as its image's environment reads it.

    dexlib2 is the default instrumentation: the ajc variant (dex2jar,
    AspectJ, d8) failed on all four apps of 2026-10-12 with d8 internal
    errors on methods whose frames the weaving broke.

    Monkey runs its fast variant, which goes on after a crash or an ANR. The
    default variant stops at the first: on 2026-10-12 it exited 91 and 123
    within one to three minutes on all four apps, and every task failed.
    """

    tools: str = "monkey:fast"
    timeouts: str = "180"
    repetitions: int = 1
    specification_set: str = DEFAULT_SPECIFICATION_SET
    instrumentation: str = "dexlib2"
    image: str = DEFAULT_IMAGE
    cpus: str = "4"
    memory: str = "6g"
    # GATOR's static analysis of each APK. Nothing here reads its
    # <apk>.static.json, and RVSec's pre-processor says violations do not
    # depend on it; over the 158 runs of 2026-10-22..31 it took 16.8 h, 15.0 h
    # of them in runs where it failed and wrote nothing.
    static_analysis: bool = False


def docker_command(apks_dir: Path, work_dir: Path, name: str,
                   settings: ExperimentSettings = ExperimentSettings()) -> list[str]:
    """The `docker run` that executes rv-experiment over ``apks_dir``.

    RVSec writes into ``work_dir`` (out/, results/); keep it on a Linux
    filesystem, an emulator and an instrumentation on /mnt/c are slow.
    """
    container = "/opt/rvsec/rv-android"
    environment = {
        "RV_TOOLS": settings.tools, "RV_TIMEOUTS": settings.timeouts,
        "RV_REPETITIONS": str(settings.repetitions), "RV_NO_WINDOW": "true",
        "RV_SPEC_SET": settings.specification_set,
        "RV_INSTRUMENTATION_VARIANT": settings.instrumentation,
        "RV_EXPERIMENT_NAME": name, "RV_APKS_DIR": f"{container}/apks",
        "RV_EMULATOR_BOOT_TIMEOUT": "600", "RV_APK_INSTALL_TIMEOUT": "600",
        # Named "skip" but read as the value of rv-experiment's
        # --static-analysis/--skip-static flag (rv_experiment/__main__.py):
        # "false" is what skips GATOR.
        "RV_SKIP_STATIC_ANALYSIS": "true" if settings.static_analysis else "false",
    }
    command = ["docker", "run", "--rm", "--name", name, "--device", "/dev/kvm",
               "--cpus", settings.cpus, "--memory", settings.memory]
    for key, value in environment.items():
        command += ["-e", f"{key}={value}"]
    for host, inside in ((Path(apks_dir), "apks"), (Path(work_dir) / "out", "out"),
                         (Path(work_dir) / "results", "results")):
        command += ["-v", f"{host.resolve()}:{container}/{inside}"]
    return [*command, settings.image]


def run_experiment(apks_dir: Path, work_dir: Path, name: str, log_file: Path,
                   settings: ExperimentSettings = ExperimentSettings()) -> tuple[int, list[Path]]:
    """Run RVSec; (exit code, report files: errors_files). Output goes to ``log_file``."""
    import subprocess

    for folder in ("out", "results"):
        (Path(work_dir) / folder).mkdir(parents=True, exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as log:
        completed = subprocess.run(docker_command(apks_dir, work_dir, name, settings),
                                   stdout=log, stderr=subprocess.STDOUT)
    give_back(work_dir, settings.image)
    return completed.returncode, errors_files([Path(work_dir) / "results"])


def give_back(work_dir: Path, image: str = DEFAULT_IMAGE) -> None:
    """Make what the container wrote (as root) the caller's own.

    On 2026-10-12 copying a run's results stopped at a root-owned, mode-600
    <apk>.static.json; the caller could not read or delete its own run.
    """
    import os
    import subprocess

    if not hasattr(os, "getuid"):
        return
    subprocess.run(["docker", "run", "--rm", "--entrypoint", "chown",
                    "-v", f"{Path(work_dir).resolve()}:/work", image,
                    "-R", f"{os.getuid()}:{os.getgid()}", "/work"],
                   capture_output=True)


def subset_apks(apks_dir: Path, names: Iterable[str], target: Path) -> Path:
    """A folder holding only ``names`` of ``apks_dir`` (``apks_dir`` itself when that is all).

    rv-experiment runs every APK of the folder it is given; a second attempt
    for some of them gets its own folder. Hard links where the filesystem
    allows them, copies otherwise (the queue's APKs are on /mnt/c, the runs
    on WSL's disk).
    """
    import os
    import shutil

    apks_dir, names = Path(apks_dir), sorted(set(names))
    if names == sorted(path.name for path in apks_dir.glob("*.apk")):
        return apks_dir
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        source, copy = apks_dir / name, target / name
        if copy.exists():
            continue
        try:
            os.link(source, copy)
        except OSError:
            shutil.copy2(source, copy)
    return target


# ---------------------------------------------------------------------------
# A second attempt for an explorer task that reached nothing.
# ---------------------------------------------------------------------------

# Refusals of the package manager that depend only on the APK and the image:
# a rerun meets them again.
_DETERMINISTIC_TASK_ERROR = re.compile(
    r"INSTALL_(?:FAILED_(?:OLDER_SDK|NO_MATCHING_ABIS|MISSING_SHARED_LIBRARY|MISSING_FEATURE|"
    r"DUPLICATE_PERMISSION|CONFLICTING_PROVIDER|TEST_ONLY)|PARSE_FAILED_\w+)")
# The emulator or adb, not the app: capyreader, flux, ttrss and nightblast
# ("adb: device offline" installing the second task's APK, 2026-10-22..31),
# NotallyX ("emulator-5554 did not boot within 600s", 2026-10-28).
_TRANSIENT_TASK_ERROR = re.compile(
    r"device offline|device '?[\w.:-]*'? not found|no devices/emulators found|"
    r"did not boot|Failed to start emulator|Broken pipe|Connection reset",
    re.IGNORECASE)


def task_retry_cause(task: Mapping) -> str:
    """Why a task is worth one more run ("transient", "unknown"), or "" when it is not.

    A task that ended unfinished without reaching a single method lost that
    explorer's whole run (8 tasks, 2026-10-22..31); one that reached methods
    keeps its logcat, which the import reads. A deterministic install refusal
    is not retried.
    """
    if task.get("state") == COMPLETED or task.get("reached"):
        return ""
    error = str(task.get("error") or "")
    if _DETERMINISTIC_TASK_ERROR.search(error):
        return ""
    return "transient" if _TRANSIENT_TASK_ERROR.search(error) else "unknown"


# ---------------------------------------------------------------------------
# What the emulator image can install.
# ---------------------------------------------------------------------------

# aapt2 prints minSdkVersion:'31', aapt sdkVersion:'31'.
_BADGING_MIN_SDK = re.compile(r"^(?:minSdkVersion|sdkVersion):'(\d+)'", re.MULTILINE)


def find_aapt(environ: Mapping | None = None) -> str:
    """aapt2 (else aapt) from PATH or the newest installed Android build tools; "" if none."""
    import os
    import shutil

    from cognalys.extraction.artifact_cache import version_key

    environ = os.environ if environ is None else environ
    suffix = ".exe" if os.name == "nt" else ""
    for tool in ("aapt2", "aapt"):
        found = shutil.which(tool)
        if found:
            return found
    for root in (environ.get("ANDROID_HOME"), environ.get("ANDROID_SDK_ROOT"),
                 str(Path.home() / "Android" / "Sdk")):
        build_tools = Path(root) / "build-tools" if root else None
        if not build_tools or not build_tools.is_dir():
            continue
        for version in sorted((path for path in build_tools.iterdir() if path.is_dir()),
                              key=lambda path: version_key(path.name), reverse=True):
            for tool in ("aapt2", "aapt"):
                if (version / f"{tool}{suffix}").is_file():
                    return str(version / f"{tool}{suffix}")
    return ""


def apk_min_sdk(apk: Path, aapt: str | None = None, run=None) -> Optional[int]:
    """The APK's minSdkVersion, from aapt's badging; None when it cannot be read."""
    import subprocess

    aapt = find_aapt() if aapt is None else aapt
    if not aapt:
        return None
    run = run or (lambda command: subprocess.run(command, capture_output=True, text=True,
                                                 errors="replace", timeout=120))
    try:
        result = run([aapt, "dump", "badging", str(apk)])
    except (OSError, subprocess.SubprocessError):
        return None
    match = _BADGING_MIN_SDK.search(getattr(result, "stdout", "") or "")
    return int(match.group(1)) if match else None


_IMAGE_API = re.compile(r"^API_LEVEL=(\d+)$")
_IMAGE_SYSTEM_IMAGE = re.compile(r"system-images;android-(\d+);")


def image_api_level(image: str = DEFAULT_IMAGE, run=None) -> Optional[int]:
    """The Android API level of the image's emulator, from the image's own environment.

    rvandroid's Dockerfile builds its AVD from API_LEVEL (and names the
    system image in PACKAGE_PATH); 0.9.5 runs API 30. None when docker or
    the image cannot say.
    """
    import json
    import subprocess

    run = run or (lambda command: subprocess.run(command, capture_output=True, text=True,
                                                 timeout=60))
    try:
        result = run(["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image])
        environment = json.loads(getattr(result, "stdout", "") or "null") or []
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    for pattern in (_IMAGE_API, _IMAGE_SYSTEM_IMAGE):
        for variable in environment:
            match = pattern.search(str(variable))
            if match:
                return int(match.group(1))
    return None


def uninstallable_apks(apks_dir: Path, image: str = DEFAULT_IMAGE, aapt: str | None = None,
                       run=None, api_level=None) -> dict[str, tuple[int, int]]:
    """apk name -> (its minSdkVersion, the image's API level) for each APK the image cannot install.

    truvark, shellwave and filepipe (2026-10-25..31) need API 31 and the
    image runs API 30: both explorers failed at install with
    INSTALL_FAILED_OLDER_SDK after the instrumentation and the emulator's
    boot. An APK whose minSdk or the image's level cannot be read is kept.
    """
    needs = {apk.name: apk_min_sdk(apk, aapt, run) for apk in sorted(Path(apks_dir).glob("*.apk"))}
    needs = {name: level for name, level in needs.items() if level}
    if not needs:
        return {}
    api = api_level if api_level is not None else image_api_level(image, run)
    if not api:
        return {}
    return {name: (level, api) for name, level in needs.items() if level > api}


# ---------------------------------------------------------------------------
# What a finished run keeps.
# ---------------------------------------------------------------------------

# Regenerable from the APK and the specification set, and read by nothing
# once the reports are converted (src/ reads tasks.json, errors.csv and the
# logcats): 7.1 of the 7.7 GB in ~/rvsec_runs on 2026-10-31 (instrumented
# APKs 4.6 GB, traces 1.1 GB, woven dex 0.8 GB, lib_tmp 0.4 GB, monitor
# build 0.2 GB), 50-98 MB per new run.
_PRUNED_FILES = ("woven_classes*.dex", "*.trace")
_PRUNED_FOLDERS = ("lib_tmp", "monitor-build")


def prune_artifacts(work_dir: Path, skipped: Optional[list] = None) -> int:
    """Delete one run's regenerable artefacts; the bytes freed.

    Only the run's own results/<experiment>/ tree (a second attempt kept in a
    subfolder of ``work_dir`` is pruned on its own). The instrumented APKs go,
    their instrument_*.json reports stay.

    A file it may not delete is left and appended to ``skipped``: RVSec's
    Docker steps write some artefacts as root, and on 2026-11-03 one
    root-owned instrumented_apks/*.idsig aborted disk_cleanup.py with PermissionError before it
    reached the remaining runs. Only bytes actually removed are counted.
    """
    import os

    freed = 0

    def size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def remove(path: Path) -> None:
        nonlocal freed
        bytes_ = size(path)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            if skipped is not None:
                skipped.append(path)
            return
        freed += bytes_

    def files_under(folder: Path, pattern: str = "*") -> list:
        found = []
        try:
            for path in folder.rglob(pattern):
                try:
                    if path.is_file():
                        found.append(path)
                except OSError:
                    continue
        except OSError:
            if skipped is not None:
                skipped.append(folder)
        return found

    try:
        experiments = sorted((Path(work_dir) / "results").glob("*/"))
    except OSError:
        experiments = []
    for experiment in experiments:
        apks = experiment / INSTRUMENTED_APKS_DIR
        doomed = [path for path in (files_under(apks) if apks.is_dir() else [])
                  if path.parent == apks and path.suffix in (".apk", ".idsig")]
        doomed += [path for pattern in _PRUNED_FILES for path in files_under(experiment, pattern)]
        for path in doomed:
            remove(path)
        for name in _PRUNED_FOLDERS:
            folder = experiment / name
            if not folder.is_dir():
                continue
            for path in files_under(folder):
                remove(path)
            for root, dirs, _ in os.walk(folder, topdown=False):
                try:
                    os.rmdir(root)
                except OSError:
                    pass
    return freed


# A method the instrumented app ran, as it logs under RVSEC-COV:
#   <com.app.MainActivity: void onCreate(android.os.Bundle)>
_COVERAGE = re.compile(r"\sRVSEC-COV\s*:\s<(?P<cls>[^:>]+):\s\S+\s(?P<method>[^\s(]+)\(")


def reached_methods(logcat: Path) -> set[tuple[str, str]]:
    """(class, method) of every method a task's run executed, from its logcat.

    RVSec's own coverage columns stayed 0 on 2026-10-14 ("unresolved static
    data") while the logcat recorded each method: simpletextcrypt's 162 were
    all on its lock screen, which is why its crypto never ran.
    """
    try:
        text = Path(logcat).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {(match["cls"], match["method"]) for match in _COVERAGE.finditer(text)}


def attach_coverage(findings_file: Path, coverage: dict) -> Optional[Path]:
    """Write what each task executed beside the findings and point every finding at it.

    The file maps apk -> explorer -> ["class::method", ...]. Each finding gets
    `detector_runtime_coverage_file`, so it travels with the finding's
    detector_context (utils.corpus_rows.detector_context) to the evidence
    stage, which answers "was this executed" requests from it. Only JSON
    Lines findings are rewritten; other formats keep the file alone.
    """
    import json

    findings_file = Path(findings_file)
    if not coverage or not findings_file.is_file():
        return None
    target = findings_file.with_suffix(".coverage.json")
    target.write_text(json.dumps(coverage, indent=1), encoding="utf-8")
    if findings_file.suffix != ".jsonl":
        return target
    lines = []
    for line in findings_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record["detector_runtime_coverage_file"] = str(target.resolve())
        lines.append(json.dumps(record, ensure_ascii=False))
    findings_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def runtime_coverage(path: str, apk: str = "") -> dict[str, set[tuple[str, str]]]:
    """explorer -> {(class, method)} from an attach_coverage file; {} when unreadable."""
    import json

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    per_apk = data.get(apk) if apk and apk in data else (next(iter(data.values())) if len(data) == 1 else {})
    return {tool: {tuple(item.split("::", 1)) for item in methods if "::" in item}
            for tool, methods in (per_apk or {}).items()}


def task_outcomes(results_dir: Path) -> list[dict]:
    """Each task (apk x repetition x timeout x tool) and how it ended, from tasks.json.

    A failed task writes nothing to errors.csv; without this, an APK whose
    every task failed looks like an APK without misuses.
    """
    outcomes = []
    for config, result in _tasks(results_dir):
        metrics = result.get("coverage_metrics") or {}
        logcat = _task_logcat(results_dir, result)
        outcomes.append({
            "apk": config.get("apk_name", ""),
            "rep": str(config.get("repetition", "")),
            "tool": _task_tool(config),
            "state": result.get("state", ""),
            "seconds": result.get("execution_time_seconds"),
            "reports": metrics.get("total_errors"),
            "error": result.get("error_message") or "",
            "reached": reached_methods(logcat) if logcat else set(),
        })
    return outcomes


INSTRUMENTED_APKS_DIR = "instrumented_apks"
INSTRUMENT_ERRORS_FILE = "instrument_errors.json"
# The instrumenters' own failure lines: dexlib2 ("Failed to instrument
# /opt/.../x.apk after 62.4s: instr-cli exited with code 1") and ajc ("Failed
# to instrument APK: x.apk"). The static phase's "Skipping static analysis for
# x.apk: not instrumented" is read too, for logs of runs that had one.
_INSTRUMENT_FAILED = re.compile(r"Failed to instrument (?:APK: )?(?P<apk>\S+?\.apk)\b")
_NOT_INSTRUMENTED = re.compile(r"Skipping static analysis for (?P<apk>\S+?\.apk): not instrumented")


def _instrumenter_cause(message: str) -> str:
    """The instr-cli error of a failed APK in a line: its first and its deepest cause.

    passportreader (2026-10-29): "Exception occurred while writing code_item
    for method Lorg/bouncycastle/...", caused at the bottom by "Unsigned short
    value out of range: 65536".
    """
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    errors = [line for line in lines if not line.startswith(("at ", "...", "stderr:"))]
    first = next((line.split(": ", 1)[-1] for line in errors
                  if ("Exception" in line or "Error" in line) and not line.startswith("Caused by:")),
                 errors[0] if errors else "")
    causes = [line.removeprefix("Caused by: ").split(": ", 1)[-1]
              for line in errors if line.startswith("Caused by:")]
    cause = causes[-1] if causes and causes[-1] != first else ""
    return (first + (f" (caused by: {cause})" if cause else ""))[:400]


def instrumentation_problems(work_dir: Path | None = None, apks_dir: Path | None = None,
                             log_file: Path | None = None) -> dict[str, str]:
    """apk name -> why rv-experiment could not instrument it; {} when every APK was.

    Read from what the instrumentation phase itself leaves, not from the
    static phase's "not instrumented" line, which RVSec writes only while
    GATOR runs (pre_processor._get_target_apks_for_analysis): with GATOR off
    that line is gone, and the 11 runs of 2026-10-22..31 that lost their app
    to instr-cli (K-9, WeechatAndroid, Amaze, ...) would have looked like apps
    without findings. Three sources, any of which suffices: the instrumenter's
    error report (instrumented_apks/instrument_errors.json), an APK of
    ``apks_dir`` with no instrumented copy once instrumentation ran (the
    instrumented_apks folder exists), and the failure lines of the log.
    """
    import json

    problems: dict[str, str] = {}
    results = Path(work_dir) / "results" if work_dir else None
    instrumented_dirs = sorted(results.glob(f"*/{INSTRUMENTED_APKS_DIR}")) \
        if results and results.is_dir() else []
    for folder in instrumented_dirs:
        try:
            errors = json.loads((folder / INSTRUMENT_ERRORS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            errors = {}
        for path, error in (errors.items() if isinstance(errors, dict) else ()):
            message = error.get("message", "") if isinstance(error, dict) else str(error)
            problems.setdefault(Path(str(path)).name, _instrumenter_cause(message)
                                or "instr-cli failed")
    if instrumented_dirs and apks_dir and Path(apks_dir).is_dir():
        instrumented = {path.name for folder in instrumented_dirs for path in folder.glob("*.apk")}
        for apk in sorted(Path(apks_dir).glob("*.apk")):
            if apk.name not in instrumented:
                problems.setdefault(apk.name, "no instrumented copy was written")
    try:
        text = Path(log_file).read_text(encoding="utf-8", errors="replace") if log_file else ""
    except OSError:
        text = ""
    for pattern in (_INSTRUMENT_FAILED, _NOT_INSTRUMENTED):
        for match in pattern.finditer(text):
            problems.setdefault(Path(match["apk"]).name, "the instrumenter failed (see the log)")
    return dict(sorted(problems.items()))


def instrumentation_failures(log_file: Path) -> list[str]:
    """Which APKs rv-experiment could not instrument, read from its log alone."""
    return sorted(instrumentation_problems(log_file=log_file))
