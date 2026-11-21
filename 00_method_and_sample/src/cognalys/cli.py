"""The `cognalys` command.

    cognalys plan        [pipeline options]       steps and commands, nothing run
    cognalys run         [pipeline options]       the pipeline (run_pipeline.py)
    cognalys scan        --repo PATH [--spec S]   detectors only
    cognalys propose-prs --repo PATH [--spec S] [pipeline options]
    cognalys results     RUN_DIR [RUN_DIR ...]    each finding's outcome
    cognalys status      RUN_DIR [--resume-check] [--json]
                                                  whether a run finished or may resume in place
    cognalys unpatched   RUN_DIR... -o FINDINGS.csv
                                                  the findings runs left without a fix, to run again
    cognalys rvsec-import RESULTS... (--repo PATH | --sources MAP.csv) [-o findings.jsonl]
                                                  RVSec reports -> findings
    cognalys fdroid-sources PACKAGE... [-o DIR]   F-Droid APKs + the commit each was built from
    cognalys fdroid-build PACKAGE... [-o DIR]     the same commits built without minification
    cognalys github-build REPO_URL... [-o DIR]    a GitHub app with no F-Droid recipe, likewise
    cognalys fdroid-packages DIR [--checkout PATH]
                                                  the app's packages again, for an older sources.csv
    cognalys rvsec-run   APKS_DIR --sources MAP.csv [-o findings.jsonl]
                                                  RVSec in Docker, then rvsec-import
    cognalys check       FINDINGS... [--repo PATH] [-o findings.jsonl]
                                                  what a findings file lacks
    cognalys template    [-o findings.csv]        an example findings file
    cognalys init        [-o minha_run.toml]      a run file to fill in
    cognalys publish     RUN_DIR --finding ID [--dry-run]
                                                  open approved pull requests
    cognalys diff        RUN_DIR [--finding ID] [--safe-only] [-o DIR]
                                                  a run's corrections as patches
    cognalys apply       RUN_DIR (--finding ID | --all-safe) [--repo PATH]
                                                  apply them to your checkout
    cognalys review      RUN_DIR [--repo PATH]    go through them one by one
    cognalys pr-outcomes [--json]                 what happened to every published PR
    cognalys pr-followup --pull-requests-dir DIR [--apply] [--approve-followup ID]
                                                  review-bot comments on published PRs:
                                                  verified, fixed and committed locally;
                                                  pushed only with --approve-followup
    cognalys cost        RUN_DIR... [--json]      tokens and dollars of runs' model calls
    cognalys specs       --review-shapes-only [--spec S]
                                                  review stored false-positive shapes (paid)
    cognalys cache       info | clear [--kind K] [--older-than DAYS] [--dry-run]
    cognalys doctor                               what this machine can run

Pipeline options are run_pipeline.py's own (--findings, --validate-build,
--llm-provider, ...); `cognalys run --help` lists them. A cognalys.toml in
the workspace may set them under [pipeline], with the option names as keys:

    [pipeline]
    llm-provider = "ollama"
    validate-build = true

Precedence: the pipeline's defaults, then cognalys.toml, then the command
line. Publishing still needs --publish-prs on the command or in the file;
nothing here publishes by default.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Sequence

CONFIG_FILE = "cognalys.toml"
FAIL_ON_LEVELS = ("none", "unfixed", "confirmed", "any")


# Read the way the launchers do (scripts/lib/pipeline_launcher.sh sources
# .env.openai before every run), so `cognalys run` and executar_pipeline.sh
# start the pipeline with the same credentials.
# .env.anthropic holds ANTHROPIC_API_KEY, for llm-provider = "anthropic".
# .env.github holds the GitHub token publication needs (`cognalys publish`).
CREDENTIAL_FILES = (".env.openai", ".env.anthropic", ".env.github")
NO_CREDENTIAL_FILES = "COGNALYS_NO_CREDENTIAL_FILES"


def load_env_file(path: Path) -> list[str]:
    """Set the KEY=VALUE (or `export KEY=VALUE`) lines of ``path``.

    A variable already in the environment is kept: what the caller set wins
    over the file. Returns the names it set, never the values.

    `$NAME` and `${NAME}` are expanded as the shell does, except inside
    single quotes: .env.github sets GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME",
    and read literally that put "Signed-off-by: $GIT_AUTHOR_NAME
    <$GIT_AUTHOR_EMAIL>" into a generated commit (AI Hub, run_aihub_6).
    """
    loaded = []
    if not path.is_file():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name.isidentifier():
            continue
        value = value.strip()
        literal = len(value) >= 2 and value[0] == value[-1] == "'"
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not literal:
            value = os.path.expandvars(value)
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def toml_arguments(path: Path) -> list[str]:
    """A TOML file's options as command-line arguments.

    The options are the [pipeline] table when there is one, otherwise the
    file's top-level keys: a run file (`cognalys run minha_run.toml`) may
    skip the table header.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        raise SystemExit(f"{path}: reading TOML needs Python 3.11 or later")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: {exc}")
    table = data.get("pipeline", data)
    arguments: list[str] = []
    for key, value in table.items():
        option = f"--{key.replace('_', '-')}"
        if value is True:
            arguments.append(option)
        elif value is False or value is None:
            continue
        elif isinstance(value, list):
            for item in value:
                arguments.extend([option, str(item)])
        else:
            arguments.extend([option, str(value)])
    return arguments


def config_arguments(workspace: Path) -> list[str]:
    """[pipeline] of the workspace's cognalys.toml as arguments ([] if absent)."""
    path = workspace / CONFIG_FILE
    return toml_arguments(path) if path.is_file() else []


def _config(pipeline_arguments: Sequence[str]):
    """Defaults < workspace cognalys.toml < run file < command line."""
    from cognalys.models import RunConfig
    from cognalys.workspace import workspace_root

    arguments = list(pipeline_arguments)
    run_file: list[str] = []
    if arguments and arguments[0].endswith(".toml"):
        path = Path(arguments.pop(0))
        if not path.is_file():
            raise ValueError(f"run file not found: {path}")
        run_file = toml_arguments(path)
    return RunConfig.from_legacy_args(
        [*config_arguments(workspace_root()), *run_file, *arguments])


RUN_FILE_TEMPLATE = """\
# A Cognalys run. Start it with:  cognalys run {name}
# Any option of `cognalys run --help` can go here, without the leading --.
# An option given on the command line wins over this file.

# The findings (CSV, JSONL, JSON, SARIF or Semgrep). Paths are relative to
# the workspace. A list analyses several files in one run.
findings = ["data/processed/rv_import_findings.csv"]
# findings-repo = "../my-app"          # resolves findings given by file and line

# Where this run's results go (analyses, patches, PRs, previews, statistics).
run-dir = "outputs/{stem}"

# The model.
llm-provider = "openai"
llm-model = "gpt-6.1-terra"
llm-endpoint = "https://api.openai.com/v1"
llm-reasoning-effort = "high"
# llm-service-tier = "auto"            # OpenAI tier; default flex (half price,
                                       # standard tier when Flex is busy)
# Claude instead (key in .env.anthropic):
# llm-provider = "anthropic"
# llm-model = "claude-opus-5-5"
# llm-endpoint = "https://api.anthropic.com"
# spec-model = "claude-opus-5-5"

# What to do with the corrections.
validate-build = true                  # compile each patch (needed for a PR)
# Most corrections are marked for review; this applies them in the run's own
# worktrees so they are compiled too. It never authorises publication.
approve-review-only-for-validation = true
generate-previews = true               # Markdown of each PR/issue/analysis
online-dependency-resolution = true    # find the code of bundled libraries
max-cost = 10                          # stop calling the model past US$ 10
                                       # (prices in data/llm_prices.json;
                                       # also the default; 0 turns it off)

# Only part of the findings or of the steps:
# project = "com.example_42.apk"
# skip = ["build"]
# start-from = "validation"

# Nothing is published unless you also run `cognalys publish`.
"""


def init(arguments) -> int:
    path = arguments.output
    if path.exists() and not arguments.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    path.write_text(RUN_FILE_TEMPLATE.format(name=path.name, stem=path.stem), encoding="utf-8")
    print(f"written {path}; edit it, then: cognalys run {path}")
    return 0


def run_publication(run_dir: Path, findings: Sequence[str], project: str = "",
                    without_build_validation: bool = False,
                    allow_duplicate_pr: bool = False, dry_run: bool = False,
                    allow_text_problems: bool = False,
                    waivers: Sequence[str] = ()) -> int:
    """pr_creator over a run's pull requests, approving exactly ``findings``.

    ``waivers`` are further pr_creator --allow-* flags, passed as given.
    """
    import subprocess

    from cognalys.orchestration.run_pipeline import RUN_DIR_LAYOUT

    pull_requests = Path(run_dir) / RUN_DIR_LAYOUT["pull_requests_dir"]
    if not pull_requests.is_dir():
        pull_requests = Path(run_dir)
    command = [sys.executable, "-m", "cognalys.prs.pr_creator",
               "--pull-requests-dir", str(pull_requests.resolve())]
    for finding in findings:
        command += ["--approve-reviewed-finding", finding]
        if without_build_validation:
            command += ["--publish-without-build-validation", finding]
    if project:
        command += ["--project", project]
    if allow_duplicate_pr:
        command.append("--allow-duplicate-pr")
    if allow_text_problems:
        command.append("--allow-text-problems")
    command.extend(waivers)
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command).returncode


def publish(arguments) -> int:
    """Open the pull requests of a run's approved findings (pr_creator)."""
    if arguments.refresh_text:
        from cognalys.prs.refresh_text import refresh_published_text

        directory = Path(arguments.run_dir)
        if (directory / "pull_requests").is_dir():
            directory = directory / "pull_requests"
        # The named findings are the approval: only their text is sent, and
        # with none named every pull request is a dry run (batch5 review,
        # 2026-10-30: the refresh sent every open pull request's new body).
        results = refresh_published_text(directory, arguments.finding, arguments.dry_run,
                                         approved=arguments.finding)
        for item in results:
            print(f"{item['result']:22} {item['url']}")
            if item["result"] == "would_update":
                if item.get("approval_needed"):
                    print(f"  not sent: pass --approve-reviewed-finding {item['finding_id']} "
                          "to send this description")
                print(item["body"], "\n")
            elif item["result"] == "held_follow_ups":
                print("  follow-up commits were pushed after its record; revise its text "
                      "with python -m cognalys.prs.pr_creator --update-description")
        if not results:
            print("no published pull requests in this run")
        return 0
    return run_publication(arguments.run_dir, arguments.finding, arguments.project or "",
                           arguments.without_build_validation, arguments.allow_duplicate_pr,
                           arguments.dry_run, arguments.allow_text_problems,
                           [flag for flag, given in (
                               ("--allow-ai-policy", arguments.allow_ai_policy),
                               ("--allow-changed-upstream", arguments.allow_changed_upstream),
                               ("--allow-closed-repository", arguments.allow_closed_repository),
                               ("--allow-stale-review", arguments.allow_stale_review))
                            if given])


def show_diff(arguments) -> int:
    from cognalys.corrections import corrections, patch

    found = [item for item in corrections(arguments.run_dir)
             if (not arguments.finding or item.finding_id in arguments.finding)
             and (item.safe or not arguments.safe_only)]
    if not found:
        print("no corrections in this run" + (" (safe ones)" if arguments.safe_only else ""),
              file=sys.stderr)
        return 1
    if arguments.output:
        arguments.output.mkdir(parents=True, exist_ok=True)
    for item in found:
        try:
            text = patch(item)
        except FileNotFoundError as exc:
            print(f"{item.finding_id}: {exc}", file=sys.stderr)
            continue
        header = (f"# {item.apk} {item.method} [{item.spec}] {item.level} "
                  f"build={item.build_status} id={item.finding_id}")
        if arguments.output:
            target = arguments.output / f"{item.name}.patch"
            target.write_text(text, encoding="utf-8")
            print(f"{header}\n#   -> {target}")
        else:
            print(header)
            print(text)
    return 0


def apply_corrections(arguments) -> int:
    from cognalys.corrections import apply, corrections

    found = corrections(arguments.run_dir)
    if arguments.finding:
        chosen = [item for item in found if item.finding_id in arguments.finding]
        missing = set(arguments.finding) - {item.finding_id for item in chosen}
        if missing:
            print(f"not in this run: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    elif arguments.all_safe:
        chosen = [item for item in found if item.safe]
    else:
        print("name the corrections with --finding ID, or --all-safe", file=sys.stderr)
        return 2
    if not chosen:
        print("nothing to apply: no safe corrections in this run "
              "(corrections marked review need --finding ID)", file=sys.stderr)
        return 1
    failed = 0
    for item in chosen:
        if not item.safe:
            print(f"note: {item.finding_id} is marked review ({item.decision}, build "
                  f"{item.build_status}); applying because you named it")
        ok, message = apply(item, arguments.repo, check_only=arguments.check)
        print(f"{item.finding_id} {item.apk} {item.method} [{item.spec}]: {message}")
        failed += not ok
    return 1 if failed else 0


def review_run(arguments) -> int:
    from cognalys.review import review

    def publish_one(finding_id: str, dry_run: bool) -> int:
        return run_publication(arguments.run_dir, [finding_id],
                               without_build_validation=arguments.without_build_validation,
                               dry_run=dry_run)

    session = review(arguments.run_dir, arguments.repo, publish_one,
                     only_pending=arguments.pending)
    counts: dict = {}
    for decision in session.values():
        counts[decision] = counts.get(decision, 0) + 1
    if counts:
        print("\n" + ", ".join(f"{name}: {count}" for name, count in sorted(counts.items())))
    return 0


def _fail_on(result, level: str, exit_code: int) -> int:
    """The run's own exit code, or 1 when findings reach ``level``."""
    failing = result.failing(level)
    if failing:
        print(f"--fail-on {level}: {len(failing)} finding(s)", file=sys.stderr)
        return exit_code or 1
    return exit_code


def _finding_usage(result) -> dict:
    """finding key -> its model calls in the run it came from (llm.cost)."""
    from cognalys.llm.cost import load_prices, run_log_dir, usage_by_finding

    import collections

    prices = load_prices()
    by_run = {Path(run).name: usage_by_finding(run_log_dir(run), prices) for run in result.runs}
    # The traces name class, method and spec but not the APK: three apps
    # bundling the same library class share one record, which is theirs
    # together, not each one's.
    sharing = collections.Counter((finding.run, finding.csv_class, finding.method, finding.spec)
                                  for finding in result.findings)
    usage = {}
    for finding in result.findings:
        entry = by_run.get(finding.run, {}).get((finding.csv_class, finding.method, finding.spec))
        if entry:
            shared = sharing[(finding.run, finding.csv_class, finding.method, finding.spec)]
            usage[finding.key] = {**entry, "shared_by": shared} if shared > 1 else entry
    return usage


def _run_version(run: Path) -> str:
    """The library version that produced a run (run_status.run_version; "(mixed)" if several)."""
    from cognalys.orchestration.run_status import run_version

    return run_version(Path(run))


def _results_by_version(runs, as_json: bool) -> None:
    """Each run's own outcomes, summed per library version (oldest run first).

    Runs are read one by one, not combined: combining keeps a finding only
    from the latest run that analysed it, so two runs of the same findings
    under two versions would leave the older version nothing to show.
    """
    import collections

    from cognalys import api

    groups: dict = {}
    for run in runs:
        group = groups.setdefault(_run_version(run), {"runs": [], "findings": 0,
                                                      "outcomes": collections.Counter()})
        result = api.load_results(run)
        group["runs"].append(str(run))
        group["findings"] += len(result.findings)
        group["outcomes"].update(result.outcomes())
    if as_json:
        print(json.dumps({"by_version": {
            version: {**group, "outcomes": dict(group["outcomes"].most_common())}
            for version, group in groups.items()}}, indent=2, ensure_ascii=False))
        return
    for version, group in groups.items():
        counts = ", ".join(f"{name}: {count}" for name, count in group["outcomes"].most_common())
        print(f"{version}: {len(group['runs'])} run(s), {group['findings']} findings. {counts}")
        for run in group["runs"]:
            print(f"  {run}")


def _print_results(result, as_json: bool, show_versions: bool = False) -> None:
    """``show_versions`` (cognalys results only) names each run's library version."""
    from cognalys.llm.cost import short_usage

    usage = _finding_usage(result)
    if as_json:
        payload = {"runs": [str(run) for run in result.runs]}
        if show_versions:
            payload["library_versions"] = {str(run): _run_version(run) for run in result.runs}
        payload.update({
            "exit_code": result.exit_code,
            "findings": [{**finding.__dict__, "model_usage": usage.get(finding.key)}
                         for finding in result.findings],
        })
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for finding in result.findings:
        line = f"{finding.outcome:42} {finding.apk} {finding.method} [{finding.spec}]"
        if finding.pr_ready:
            line += f" -> {finding.pr_title}"
            if finding.detail:
                # "revisão anterior: reexecutar": the draft predates the
                # current behaviour review (finding_statistics.STALE_REVIEW_DETAIL);
                # or its fix path has none (UNREVIEWED_PATH_DETAIL).
                line += f" [{finding.detail}]"
        if finding.key in usage:
            line += f"  ({short_usage(usage[finding.key])})"
        print(line)
    counts = ", ".join(f"{name}: {count}" for name, count in result.outcomes().most_common())
    print(f"\n{len(result.findings)} findings. {counts}")
    if show_versions:
        for run in result.runs:
            print(f"Library of {run}: {_run_version(run)}")
    # Where the model's work went without producing a pull request.
    seen, unproductive = set(), []
    for finding in result.findings:
        entry = usage.get(finding.key)
        shared_key = (finding.run, finding.csv_class, finding.method, finding.spec)
        if finding.pr_ready or not entry or shared_key in seen:
            continue
        seen.add(shared_key)
        unproductive.append((entry, finding))
    costly = sorted(unproductive,
                    key=lambda pair: -(pair[0]["input_tokens"] + pair[0]["output_tokens"]))[:3]
    if costly:
        print("Most model use without a pull request:")
        for entry, finding in costly:
            print(f"  {short_usage(entry):40} {finding.apk} {finding.method} [{finding.spec}] "
                  f"-> {finding.outcome}")


def doctor() -> int:
    """Which capabilities this machine has, and what a missing one stops."""
    checks = []

    def check(capability: str, ok: bool, detail: str, blocks: str) -> None:
        checks.append((capability, ok, detail, blocks))

    check("python", sys.version_info >= (3, 10), sys.version.split()[0], "everything")
    for module, blocks in (("tree_sitter", "extraction"), ("requests", "LLM and GitHub calls"),
                           ("pyflakes", "the pre-run checks")):
        try:
            __import__(module)
            check(module, True, "importable", blocks)
        except ImportError:
            check(module, False, "not installed", blocks)
    check("git", bool(shutil.which("git")), shutil.which("git") or "not on PATH", "everything")
    from cognalys.validation.semgrep_rules import find_semgrep
    semgrep = find_semgrep()
    check("semgrep", bool(semgrep), semgrep or "not found", "scan, propose-prs, detector replay")
    java = os.environ.get("JAVA_HOME") or shutil.which("java") or ""
    check("java", bool(java), java or "neither JAVA_HOME nor java on PATH", "build validation")
    from cognalys.builds.apk_build import detect_android_sdk
    sdk = detect_android_sdk(None)
    check("android sdk", bool(sdk), str(sdk) if sdk else
          "none found (ANDROID_HOME, ANDROID_SDK_ROOT, ~/Android/Sdk)", "build validation")
    from cognalys.workspace import data_path, workspace_root
    root = workspace_root()
    openai = ("set" if os.environ.get("OPENAI_API_KEY") else "")
    check("openai key", bool(openai), openai or "OPENAI_API_KEY is not set and "
          f"{root / '.env.openai'} has none", "analysis with the OpenAI provider "
          "(Ollama needs no key)")
    anthropic_key = ("set" if os.environ.get("ANTHROPIC_API_KEY") else "")
    check("anthropic key", bool(anthropic_key), anthropic_key or "ANTHROPIC_API_KEY is not set "
          f"and {root / '.env.anthropic'} has none", "analysis with the Anthropic provider")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    github_file = root / ".env.github"
    check("github token", bool(token), "set" if token else (
        f"not set; {github_file} exists but is not loaded for runs, source it to publish"
        if github_file.is_file() else "GITHUB_TOKEN is not set"),
          "publishing pull requests and issues")
    check("workspace", data_path("specifications").is_dir(), str(workspace_root()),
          "specifications, policies and rules (set COGNALYS_WORKSPACE)")
    width = max(len(name) for name, *_ in checks)
    for name, ok, detail, blocks in checks:
        status = "ok  " if ok else "MISS"
        suffix = "" if ok else f"  (needed for: {blocks})"
        print(f"{status} {name:{width}}  {detail}{suffix}")
    return 0 if all(ok for name, ok, *_ in checks if name in {"python", "git", "workspace"}) else 1


def rvsec_import(arguments) -> int:
    """RVSec reports to a findings file: 0 complete, 1 with problems, 2 nothing usable."""
    from cognalys.integrations import rvsec
    from cognalys.sources import write_rows

    if arguments.repo:
        source = rvsec.checkout_source(arguments.repo)
        reports = rvsec.errors_files(arguments.results)
        apks = {row["apk"] for path in reports if path.is_file()
                for row in rvsec.report_rows(path) if row.get("apk")}
        sources = {apk: source for apk in apks}
    else:
        sources = rvsec.source_map(arguments.sources)
    codes = rvsec.codes_table(arguments.codes, arguments.rvsec_home, arguments.specification_set)
    imported = rvsec.import_results(arguments.results, sources, codes, arguments.apks_dir)
    if imported.findings:
        write_rows(imported.findings, arguments.output)
    print(f"{imported.reports} RVSec report(s) -> {len(imported.findings)} finding(s)"
          + (f" in {arguments.output}" if imported.findings else "")
          + (f"; {imported.folded} echo finding(s) folded into the finding they echo"
             if imported.folded else ""))
    if not codes:
        print("note: no codes table (give --rvsec-home or --codes); labels are empty")
    for problem in imported.problems:
        print(f"PROBLEM {problem}")
    if not imported.findings:
        return 2
    return 0 if imported.complete else 1


def fdroid_sources(arguments) -> int:
    """APKs and their exact sources: 0 all, 1 some, 2 none."""
    from cognalys.integrations import fdroid

    taken, problems = fdroid.prepare(arguments.packages, arguments.output)
    for build in taken:
        print(f"{build.apk:50} {build.repo_url} @ {build.commit[:12]}")
    for problem in problems:
        print(f"PROBLEM {problem['package']}: {problem['problem']}")
    print(f"{len(taken)} APK(s) in {arguments.output / 'apks'}; "
          f"source map {arguments.output / 'sources.csv'}")
    if not taken:
        return 2
    return 0 if not problems else 1


def fdroid_build(arguments) -> int:
    """Debug builds without minification of F-Droid's commits: 0 all, 1 some, 2 none."""
    from cognalys.integrations import fdroid

    work = arguments.work_dir or Path.home() / "rvsec_builds"
    built, problems = fdroid.build_from_source(arguments.packages, arguments.output, work,
                                               timeout=arguments.build_timeout)
    for build in built:
        print(f"{build.package}.debug_{build.version_code}.apk  {build.repo_url} @ "
              f"{build.commit[:12]}  task {fdroid.gradle_task(build)}")
    for problem in problems:
        print(f"PROBLEM {problem['package']}: {problem['problem']}")
    print(f"{len(built)} APK(s) in {arguments.output / 'apks'}; "
          f"source map {arguments.output / 'sources.csv'}")
    if not built:
        return 2
    return 0 if not problems else 1


def github_build(arguments) -> int:
    """Debug builds without minification of GitHub default-branch heads: 0 all, 1 some, 2 none."""
    from cognalys.integrations import fdroid, github_build as github

    work = arguments.work_dir or Path.home() / "rvsec_builds"
    built, problems = fdroid.build_from_source(
        arguments.repositories, arguments.output, work, timeout=arguments.build_timeout,
        resolve=lambda repo_url: github.probe(repo_url, work),
        prepare=github.write_google_services_placeholder)
    for build in built:
        print(f"{build.package}.debug_{build.version_code}.apk  {build.repo_url} @ "
              f"{build.commit[:12]}  task {fdroid.gradle_task(build)}")
    for problem in problems:
        print(f"PROBLEM {problem['package']}: {problem['problem']}")
    print(f"{len(built)} APK(s) in {arguments.output / 'apks'}; "
          f"source map {arguments.output / 'sources.csv'}")
    if not built:
        return 2
    return 0 if not problems else 1


def fdroid_packages(arguments) -> int:
    """App packages derived again where sources.csv predates app_packages: 0 done, 1 problems."""
    import re

    from cognalys.integrations import fdroid
    from cognalys.utils.network_git import run_git_command

    work = arguments.work_dir or Path.home() / "rvsec_builds" / "packages"

    def git(*command) -> bool:
        return run_git_command(["git", *command], capture_output=True, text=True).returncode == 0

    def checkout_for(row: dict):
        """The row's commit checked out: --checkout, else a depth-1 fetch of that commit."""
        if arguments.checkout:
            return arguments.checkout
        url, commit = (row.get("repo_url") or "").strip(), (row.get("commit") or "").strip()
        if not url or not fdroid.is_commit(commit):
            return None
        target = work / re.sub(r"[^\w.-]", "_", Path(row.get("apk") or commit).stem)
        head = run_git_command(["git", "-C", str(target), "rev-parse", "HEAD"],
                               capture_output=True, text=True) if (target / ".git").is_dir() else None
        if head is not None and head.returncode == 0 and head.stdout.strip() == commit:
            return target
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        if (git("-C", str(target), "init", "-q")
                and git("-C", str(target), "fetch", "-q", "--depth", "1", url, commit)
                and git("-C", str(target), "checkout", "-q", "FETCH_HEAD")):
            return target
        return None

    findings = arguments.findings or [arguments.output / "findings_all.jsonl"]
    changes = fdroid.refresh_app_packages(arguments.output, checkout_for, findings)
    for change in changes:
        if "problem" in change:
            print(f"PROBLEM {change['apk']}: {change['problem']}")
            continue
        print(f"{change['apk']}: app packages {';'.join(change['before']) or '-'} -> "
              f"{';'.join(change['after'])}; {change['relabelled']} finding(s) now in the app's code")
    if not changes:
        print(f"{arguments.output / 'sources.csv'}: app packages up to date")
    return 1 if any("problem" in change for change in changes) else 0


def rvsec_run(arguments) -> int:
    """RVSec over APKS_DIR in Docker, then its reports as findings."""
    import dataclasses
    import re
    import time

    from cognalys.integrations import rvsec

    settings = rvsec.ExperimentSettings(
        tools=arguments.tools, timeouts=arguments.timeout, repetitions=arguments.repetitions,
        specification_set=arguments.specification_set, instrumentation=arguments.instrumentation,
        image=arguments.image, cpus=arguments.cpus, memory=arguments.memory,
        static_analysis=arguments.static_analysis,
    )
    name = f"rvsec_{time.strftime('%Y%m%d_%H%M%S')}"
    work = (arguments.work_dir or Path.home() / "rvsec_runs") / name
    work.mkdir(parents=True, exist_ok=True)
    log_file = work / "rvsec.log"
    print(f"RVSec: {settings.instrumentation} instrumentation, tools {settings.tools}, "
          f"{settings.timeouts}s x {settings.repetitions}; log {log_file}", flush=True)
    attempts: list[dict] = []

    def attempt(folder: Path, run_name: str, run_settings, apks: Path, label: str = "") -> dict:
        """One rv-experiment over ``apks``: its reports, its tasks, the APKs it could not instrument."""
        run_log = folder / "rvsec.log"
        folder.mkdir(parents=True, exist_ok=True)
        # errors.csv, plus the logcat of each task it holds nothing of (one
        # that failed after its monitors reported); errors_files.
        code, reports = rvsec.run_experiment(apks, folder, run_name, run_log, run_settings)
        attempts.append({"work": folder, "settings": run_settings, "apks": apks, "label": label,
                         "log": run_log, "code": code, "reports": reports,
                         "problems": rvsec.instrumentation_problems(folder, apks, run_log),
                         "tasks": rvsec.task_outcomes(folder / "results")})
        return attempts[-1]

    # An APK whose minSdk is above the image's API level fails both
    # explorers at install, after its instrumentation and the emulator's boot
    # (truvark, shellwave, filepipe: API 31 on the API 30 image, 2026-10-25..31).
    apks_dir = Path(arguments.apks_dir)
    uninstallable = rvsec.uninstallable_apks(apks_dir, settings.image)
    for apk, (needed, level) in uninstallable.items():
        # The queue reads this line as the app's last state (queue.sh).
        print(f"PROBLEM {apk}: needs Android API {needed} and the emulator image runs API"
              f" {level}; RVSec cannot install it, not explored")
    installable = sorted(path.name for path in apks_dir.glob("*.apk")
                         if path.name not in uninstallable)
    if uninstallable and not installable:
        print(f"RVSec not run: no APK of {apks_dir} installs on the emulator image")
        return 2
    first = attempt(work, name, settings, rvsec.subset_apks(apks_dir, installable, work / "apks")
                    if uninstallable else apks_dir)
    failed = first["problems"]
    fallback = arguments.instrumentation_fallback
    if failed and fallback and fallback != settings.instrumentation:
        # Opt-in: dexlib2's smali writer failed on 9 apps of 2026-10-22..31
        # ("Unsigned short value out of range" in androidx, guava or
        # bouncycastle methods; babymonitor's Format4rcc), and ajc failed on
        # all four apps of 2026-10-12, so whether ajc rescues them is unproven.
        for apk, cause in failed.items():
            print(f"note {apk}: the {settings.instrumentation} instrumentation failed ({cause});"
                  f" trying {fallback} once", flush=True)
        folder = work / f"fallback_{fallback}"
        retried = attempt(folder, f"{name}_{fallback}",
                          dataclasses.replace(settings, instrumentation=fallback),
                          rvsec.subset_apks(first["apks"], failed, folder / "apks"), fallback)
        failed = retried["problems"]
    tried = settings.instrumentation + (f" or {fallback}" if fallback and fallback !=
                                        settings.instrumentation and first["problems"] else "")
    for apk, cause in failed.items():
        # The queue reads this line as the app's last state (queue.sh).
        print(f"PROBLEM {apk}: RVSec could not instrument it with {tried}"
              f" ({cause}; see {attempts[-1]['log']})")
    # An explorer task that ended unfinished without reaching one method lost
    # that explorer's whole run: 8 tasks of 2026-10-22..31, 4 of them "adb:
    # device offline" installing the second task's APK and NotallyX's emulator
    # not booting in 600 s. Each is run again in its own rv-experiment, over
    # its APK and its tool alone, at most --explorer-retries times (default 1).
    latest = list(attempts)
    for round_number in range(1, max(arguments.explorer_retries, 0) + 1):
        groups: dict = {}
        for run in latest:
            for task in run["tasks"]:
                cause = rvsec.task_retry_cause(task)
                if cause:
                    groups.setdefault((id(run), task["tool"]), (run, []))[1].append(task["apk"])
                    print(f"note {task['apk']} {task['tool']}: {task['state']} with no method"
                          f" reached ({cause} cause: {task['error'] or 'no error message'});"
                          f" run again", flush=True)
        latest = []
        for (_, tool), (run, apks) in groups.items():
            tag = re.sub(r"[^A-Za-z0-9_.-]", "_", f"retry{round_number}_{tool}")
            folder = run["work"] / tag
            latest.append(attempt(folder, f"{name}_{tag}",
                                  dataclasses.replace(run["settings"], tools=tool),
                                  rvsec.subset_apks(run["apks"], apks, folder / "apks"),
                                  " ".join(part for part in (run["label"], f"retry {round_number}")
                                           if part)))
        if not latest:
            break
    reports = [report for run in attempts for report in run["reports"]]
    code = first["code"]
    sources = rvsec.source_map(arguments.sources)
    coverage: dict = {}
    for run in attempts:
        for task in run["tasks"]:
            reached = coverage.setdefault(task["apk"], {}).setdefault(task["tool"], set())
            reached.update(f"{cls}::{method}" for cls, method in task["reached"])
            source = sources.get(task["apk"])
            packages = (source.packages if source else ()) or (rvsec.manifest_package(task["apk"]),)
            own = sum(rvsec.code_origin(cls, packages) == "app" for cls, _ in task["reached"])
            # How far the exploration went: a misuse in a method it never ran
            # cannot be reported, which an empty errors.csv does not say.
            print(f"task {task['apk']} rep {task['rep']} {task['tool']}: {task['state']}"
                  f" in {task['seconds']}s, {task['reports']} report(s),"
                  f" {len(task['reached'])} method(s) reached ({own} in the app's packages)"
                  + (f" -- {task['error']}" if task["error"] else "")
                  + (f" [{run['label']}]" if run["label"] else ""))
    coverage = {apk: {tool: sorted(methods) for tool, methods in tools.items()}
                for apk, tools in coverage.items()}

    def prune() -> None:
        """Drop what the reports were converted from and can be regenerated.

        ~50-98 MB a run that nothing reads afterwards (7.1 of the 7.7 GB in
        ~/rvsec_runs on 2026-10-31), also for the runs without any report
        (38 of 124 queued apps). A run with an APK it could not instrument
        keeps everything, for diagnosis.
        """
        if arguments.keep_artifacts:
            return
        freed = 0
        for run in attempts:
            if run["problems"]:
                continue
            freed += rvsec.prune_artifacts(run["work"])
            if run["apks"] != apks_dir and work in run["apks"].parents:
                # A copy of some of the APKs, made for this attempt (subset_apks).
                freed += sum(path.stat().st_size for path in run["apks"].glob("*.apk"))
                shutil.rmtree(run["apks"], ignore_errors=True)
        if freed:
            print(f"RVSec: {freed / 1e6:.0f} MB of instrumented APKs, woven dex and traces"
                  " removed (regenerable; reports, tasks, logcats and coverage kept)")

    if not reports:
        print(f"RVSec wrote no reports (exit {code}); see {log_file}")
        prune()
        return 2
    arguments.results = reports
    arguments.repo = None
    arguments.apks_dir = Path(arguments.apks_dir)
    arguments.codes = None
    status = rvsec_import(arguments)
    # What the exploration executed, kept beside the findings: the analysis
    # answers "did this run" questions from it (evidence_requests).
    rvsec.attach_coverage(Path(arguments.output), coverage)
    prune()
    return status


COLUMN_HELP = """Required: apk, class, method, spec (or source_file + line with --repo).
Recommended: message, repo_url, commit. Anything else is kept and shown to
the analysis; detector_* columns are the detector's own context.
Accepted as files: CSV, JSON Lines, JSON array, SARIF, Semgrep --json.
Format: docs/FORMATO_DE_ENTRADA.md"""


def unpatched(arguments) -> int:
    """The findings runs left without a fix: 0 written, 2 none or none found in the inputs."""
    from collections import Counter

    from cognalys.reporting.unpatched import write_unpatched

    written = write_unpatched(arguments.runs, arguments.output, arguments.include)
    for finding in written["kept"]:
        print(f"{finding.apk}  {finding.method}  {finding.spec}  -- {finding.outcome}"
              + (f" ({finding.detail})" if finding.detail else ""))
    for finding in written["missing"]:
        print(f"not in the runs' findings files: {finding.apk} {finding.method} {finding.spec}",
              file=sys.stderr)
    counts = ", ".join(f"{n} {outcome}" for outcome, n in
                       Counter(f.outcome for f in written["kept"]).most_common())
    print(f"\n{len(written['kept'])} finding(s) -> {arguments.output}" + (f": {counts}" if counts else ""))
    return 0 if written["kept"] else 2


def _cost_by_outcome(run: Path) -> dict:
    """One run's model cost per final outcome (as `cognalys results` shows it) and per PR-ready draft.

    A finding costs the calls made for it (llm.cost.usage_by_finding, the
    attribution `results` shows); one record several findings share (a
    library class bundled by several APKs) is split evenly among them.
    Calls charged to no listed finding (run-wide steps) are reported apart,
    not spread. Each run directory is its own view: development reruns are
    never summed (BANCA_SIMULADA M1, 2026-11-08).

    The per-draft figure divides by drafts, not findings: findings analysed
    as one unit (join_findings_of_one_sequence) share one draft, which
    their records name (pr_draft, the draft's `*_pr.json`).
    """
    import statistics

    from cognalys.llm.cost import run_log_dir, usage
    from cognalys.models import FindingResult, RunResult
    from cognalys.reporting.finding_statistics import read_run

    # api.load_results of one run, keeping the records for their drafts.
    records = read_run(Path(run))
    result = RunResult(runs=(Path(run),), findings=tuple(
        FindingResult.from_record(key, record) for key, record in sorted(records.items())))
    charged = _finding_usage(result)
    costs: dict = {}
    for finding in result.findings:
        entry = charged.get(finding.key)
        value = (0.0 if entry is None else None if entry["cost_usd"] is None
                 else entry["cost_usd"] / entry.get("shared_by", 1))
        costs.setdefault(finding.outcome, []).append(value)
    by_outcome = {}
    for outcome, values in sorted(costs.items(), key=lambda item: (-len(item[1]), item[0])):
        priced = None not in values
        by_outcome[outcome] = {
            "findings": len(values),
            "total_usd": sum(values) if priced else None,
            "mean_usd": statistics.mean(values) if priced else None,
            "median_usd": statistics.median(values) if priced else None,
        }
    total = usage(run_log_dir(run))["cost_usd"]
    attributed = [value for values in costs.values() for value in values]
    ready_findings = result.ready_pull_requests()
    # A ready record without a draft name (none is expected) counts as its own.
    ready = len({records[finding.key].get("pr_draft") or finding.key
                 for finding in ready_findings})
    return {
        "by_outcome": by_outcome,
        "not_attributed_usd": (None if total is None or None in attributed
                               else max(0.0, total - sum(attributed))),
        "total_usd": total,
        "pr_ready_drafts": ready,
        "pr_ready_findings": len(ready_findings),
        # Everything the run spent, also on the findings that ended without one.
        "per_pr_ready_usd": total / ready if ready and total is not None else None,
    }


def _outcome_cost_lines(report: dict) -> list:
    from cognalys.llm.cost import _money

    lines = [f"  {'final outcome':42} {'findings':>8} {'total':>10} {'mean':>10} {'median':>10}"]
    for outcome, entry in report["by_outcome"].items():
        lines.append(f"  {outcome:42} {entry['findings']:8} {_money(entry['total_usd']):>10} "
                     f"{_money(entry['mean_usd']):>10} {_money(entry['median_usd']):>10}")
    lines.append(f"  {'calls charged to no listed finding':42} {'':8} "
                 f"{_money(report['not_attributed_usd']):>10}")
    ready, findings = report["pr_ready_drafts"], report["pr_ready_findings"]
    resolved = f" ({findings} findings)" if findings != ready else ""
    lines.append(f"  run total {_money(report['total_usd'])}; " + (
        f"{ready} PR-ready draft(s){resolved}: {_money(report['per_pr_ready_usd'])} "
        "per PR-ready draft" if ready else "0 PR-ready drafts"))
    return lines


def cost(arguments) -> int:
    """Each run's model calls, in tokens and US dollars; or per final outcome (--by-outcome)."""
    from cognalys.llm.cost import run_log_dir, summary_lines, usage

    if arguments.by_outcome:
        outcomes = {str(run): _cost_by_outcome(run) for run in arguments.runs}
        if arguments.json:
            print(json.dumps(outcomes, indent=2, ensure_ascii=False))
            return 0
        for run, report in outcomes.items():
            print(run)
            print("\n".join(_outcome_cost_lines(report)))
        return 0
    reports = {str(run): usage(run_log_dir(run)) for run in arguments.runs}
    if arguments.json:
        print(json.dumps(reports, indent=2))
        return 0
    for run, report in reports.items():
        print(run)
        print("\n".join(summary_lines(report))
              if report["calls"] or report["cache_hits"] or report.get("corpus_hits")
              else "  no model calls")
    return 0


def research_corpus_command(arguments) -> int:
    """List the research corpus, or import the complete shared passes of recorded runs."""
    from cognalys.llm import research_corpus as corpus
    from cognalys.workspace import workspace_root

    directory = Path(arguments.dir) if arguments.dir else (
        workspace_root() / "outputs" / "research_corpus")
    if arguments.action == "import":
        if not arguments.runs:
            print("research-corpus import: give at least one run directory", file=sys.stderr)
            return 2
        try:
            report = corpus.import_runs(directory, arguments.runs, replace=arguments.replace,
                                        dry_run=arguments.dry_run)
        except ValueError as exc:
            print(f"research-corpus import: {exc}", file=sys.stderr)
            return 2
        if arguments.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        verb = "would store" if arguments.dry_run else "stored"
        skipped = ", ".join(f"{reason} {count}" for reason, count in sorted(report["skipped"].items()))
        print(f"{report['runs']} log folder(s), {report['passes_seen']} research pass(es), "
              f"{report['passes_importable']} importable, {report['keys']} key(s): "
              f"{verb} {report['stored']}, kept {report['kept']}"
              + (f"; skipped: {skipped}" if skipped else ""))
        return 0
    if arguments.runs:
        print("research-corpus list takes no run directory", file=sys.stderr)
        return 2
    rows = corpus.listing(directory)
    digest = corpus.corpus_digest(directory)
    if arguments.json:
        print(json.dumps({"directory": str(directory), "digest": digest, "entries": rows},
                         indent=2, ensure_ascii=False))
        return 0
    older = sum(1 for row in rows if row["age_days"] is None
                or row["age_days"] > corpus.DEFAULT_MAX_AGE_DAYS)
    recorded = sum(row["cost_usd"] or 0.0 for row in rows)
    for row in rows:
        scope = row["scope"] if isinstance(row["scope"], dict) else {}
        age = "?" if row["age_days"] is None else f"{row['age_days']:.1f}d"
        cost_text = "n/a" if row["cost_usd"] is None else f"US$ {row['cost_usd']:.4f}"
        print(f"{row['key'][:12]}  {str(row['created_at'])[:19]} ({age})  {row['origin']:6}  "
              f"{scope.get('specification') or scope.get('kind', '')}  "
              f"{row['sources']} sources  {cost_text}")
    print(f"{len(rows)} entries, {older} older than {corpus.DEFAULT_MAX_AGE_DAYS:g} days, "
          f"US$ {recorded:.2f} recorded to produce them, digest {digest['sha256'][:12]}")
    return 0


def decision_replay(arguments) -> int:
    """One recorded decision request sent again --samples times: 0 sent, 2 refused."""
    from cognalys.evaluation import decision_replay as replaying

    if arguments.add_section and not arguments.current_prompt:
        print("decision-replay: --add-section needs --current-prompt", file=sys.stderr)
        return 2
    try:
        request = replaying.select_request(arguments.run_dir, arguments.stage,
                                           arguments.finding, arguments.call)
        if arguments.current_prompt:
            sections = []
            for item in arguments.add_section or []:
                name, separator, path = item.partition("=")
                if not separator or not name or not path:
                    raise ValueError(f"--add-section {item!r}: expected NAME=PATH")
                try:
                    sections.append((name, json.loads(Path(path).read_text(encoding="utf-8"))))
                except (OSError, ValueError) as exc:
                    raise ValueError(f"--add-section {item!r}: {exc}") from exc
            request = replaying.current_prompt_request(request, arguments.stage, sections)
        report = replaying.replay(request, arguments.stage, arguments.samples,
                                  arguments.max_cost, arguments.llm_service_tier,
                                  arguments.output, dry_run=arguments.dry_run)
    except ValueError as exc:
        print(f"decision-replay: {exc}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(replaying.report_lines(report)))
    return 0


def pr_outcomes(arguments) -> int:
    """Merged, closed and open pull requests, with the maintainers' words."""
    import requests

    from cognalys.prs.outcome_report import follow_pages, refresh, summary_lines
    from cognalys.workspace import workspace_root

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def fetch(url: str):
        # One more attempt after a dropped connection: a single reset turned
        # BombusMod#316 into "unknown" on 2026-10-14.
        for attempt in (1, 2):
            try:
                response = requests.get(url, headers=headers, timeout=30)
                break
            except requests.ConnectionError:
                if attempt == 2:
                    raise
        response.raise_for_status()
        return response

    # A list (comments, reviews, events) is read to its last page.
    outcomes = refresh(workspace_root() / "outputs", follow_pages(fetch))
    if arguments.json:
        print(json.dumps([item.__dict__ for item in outcomes], indent=2, ensure_ascii=False))
    else:
        print("\n".join(summary_lines(outcomes)))
    return 0


def run_status_command(arguments) -> int:
    """One line (or the JSON) on a run's status; with --resume-check, its verdict as exit code."""
    from cognalys.orchestration import run_status

    code, line = run_status.resume_check(arguments.run)
    if arguments.json:
        print(json.dumps({"verdict": line, "exit_code": code,
                          "status": run_status.read_status(arguments.run)},
                         indent=2, ensure_ascii=False))
    else:
        print(f"{arguments.run}: {line}")
    return code if arguments.resume_check else 0


def check(arguments) -> int:
    """What the findings files hold: 0 all usable, 1 some not, 2 none."""
    from cognalys import intake
    from cognalys.sources import write_rows

    taken = intake.read_findings(arguments.findings, arguments.repo)
    for source, target in sorted(taken.renamed.items()):
        print(f"read column {source!r} as {target!r}")
    for problem in taken.problems:
        print(f"PROBLEM {problem.get('source', '')}: {problem['problem']}")
    if taken.problems and any("missing" in problem["problem"] for problem in taken.problems):
        columns = set()
        for path in arguments.findings:
            try:
                columns.update(key for row in intake.file_rows(Path(path))[:50] for key in row if key)
            except (OSError, ValueError):
                continue
        for required, close in intake.suggestions(columns).items():
            print(f"hint: no {required!r} column; is {close!r} it? "
                  f"(rename it, or map it with --column-map)")
    print(f"{len(taken.rows)} usable finding(s), {len(taken.problems)} problem(s)")
    if taken.rows and arguments.output:
        write_rows(taken.rows, arguments.output)
        print(f"written to {arguments.output}")
    if not taken.rows:
        print(COLUMN_HELP)
        return 2
    return 0 if taken.complete else 1


def template(arguments) -> int:
    import csv

    from cognalys import intake

    columns = list(dict.fromkeys(key for row in intake.TEMPLATE_ROWS for key in row))
    with open(arguments.output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(intake.TEMPLATE_ROWS)
    print(f"written {arguments.output}: one finding by class and method, "
          "one by source_file and line")
    print(COLUMN_HELP)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from cognalys import __version__

    parser = argparse.ArgumentParser(prog="cognalys", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"cognalys {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("plan", "list the steps a run would execute"),
                       ("run", "run the pipeline")):
        sub = commands.add_parser(name, help=text, add_help=False, allow_abbrev=False)
        sub.add_argument("--json", action="store_true", help="results as JSON")
        sub.add_argument("--fail-on", choices=FAIL_ON_LEVELS, default="none",
                         help="exit 1 when a finding reaches this outcome: unfixed (a confirmed violation without a ready fix), confirmed (also with a PR ready), any (anything but a recognised false positive); default none")
    # No abbreviations: a pipeline option passed through must never be read
    # as a prefix of one of these (--repo, --spec).
    scan = commands.add_parser("scan", help="run the detectors over a checkout",
                               allow_abbrev=False)
    propose = commands.add_parser("propose-prs", help="scan a checkout and run the pipeline",
                                  add_help=False, allow_abbrev=False)
    for sub in (scan, propose):
        sub.add_argument("--repo", required=True, type=Path, help="a local Git checkout")
        sub.add_argument("--spec", action="append", default=None,
                         help="only this specification's rule (repeatable)")
        sub.add_argument("--json", action="store_true")
    results = commands.add_parser("results", help="each finding's outcome in runs")
    results.add_argument("runs", nargs="+", type=Path)
    results.add_argument("--json", action="store_true")
    results.add_argument("--by-version", action="store_true",
                         help="each run's outcomes summed per library version it recorded "
                              "(commit and dirty, or the src/ hash)")
    results.add_argument("--fail-on", choices=FAIL_ON_LEVELS, default="none",
                         help="exit 1 when a finding reaches this outcome: unfixed (a confirmed violation without a ready fix), confirmed (also with a PR ready), any (anything but a recognised false positive); default none")
    status = commands.add_parser(
        "status", allow_abbrev=False,
        help="whether a run is complete, or what is left and whether it may resume in place")
    status.add_argument("run", type=Path)
    status.add_argument("--resume-check", action="store_true",
                        help="exit 0 complete, 10 resume in place, 11 rerun in a new run_vN "
                             "(the library changed), 12 gave up after 3 passes, 13 still "
                             "running, 14 no status recorded")
    status.add_argument("--json", action="store_true")
    rvsec = commands.add_parser(
        "rvsec-import", allow_abbrev=False,
        help="turn RVSec (rv-android) reports into a findings file the pipeline reads")
    rvsec.add_argument("results", nargs="+", type=Path,
                       help="errors.csv files or rv-experiment results directories")
    where = rvsec.add_mutually_exclusive_group(required=True)
    where.add_argument("--repo", type=Path,
                       help="the app's local checkout, the source of every APK in the results")
    where.add_argument("--sources", type=Path,
                       help="CSV mapping apk to repo_url and commit (RVSec's dataset.csv works)")
    rvsec.add_argument("--rvsec-home", type=Path, default=os.environ.get("RVSEC_HOME"),
                       help="RVSec checkout, for the codes table (default: $RVSEC_HOME)")
    rvsec.add_argument("--specification-set", default="jca_android")
    rvsec.add_argument("--codes", type=Path, help="a codes.csv instead of the checkout's")
    rvsec.add_argument("--apks-dir", type=Path,
                       help="the APKs the reports came from, for bytecode evidence")
    rvsec.add_argument("-o", "--output", type=Path, default=Path("findings.jsonl"))
    fdroid = commands.add_parser(
        "fdroid-sources", allow_abbrev=False,
        help="download F-Droid APKs and find the commit each was built from")
    fdroid.add_argument("packages", nargs="+", help="application ids (org.example.app)")
    fdroid.add_argument("-o", "--output", type=Path, default=Path("rvsec_apks"),
                        help="writes apks/ and sources.csv here")
    building = commands.add_parser(
        "fdroid-build", allow_abbrev=False,
        help="build F-Droid's commits as debug APKs without minification, for RVSec")
    building.add_argument("packages", nargs="+", help="application ids (org.example.app)")
    building.add_argument("-o", "--output", type=Path, default=Path("rvsec_apks"),
                          help="writes apks/ and sources.csv here")
    building.add_argument("--work-dir", type=Path,
                          help="checkouts and Gradle output (default ~/rvsec_builds; keep it off /mnt/c)")
    building.add_argument("--build-timeout", type=int, default=3600,
                          help="seconds Gradle may take per package (default 3600)")
    github = commands.add_parser(
        "github-build", allow_abbrev=False,
        help="build a GitHub Android app with no F-Droid recipe (default branch head) as a "
             "debug APK without minification, for RVSec")
    github.add_argument("repositories", nargs="+", help="https://github.com/owner/name")
    github.add_argument("-o", "--output", type=Path, default=Path("rvsec_apks"),
                        help="writes apks/ and sources.csv here")
    github.add_argument("--work-dir", type=Path,
                        help="checkouts and Gradle output (default ~/rvsec_builds; keep it off /mnt/c)")
    github.add_argument("--build-timeout", type=int, default=3600,
                        help="seconds Gradle may take per repository (default 3600)")
    packaging = commands.add_parser(
        "fdroid-packages", allow_abbrev=False,
        help="derive the app's packages again where sources.csv predates app_packages, "
             "and relabel the findings that are the app's code")
    packaging.add_argument("output", type=Path, help="the folder fdroid-build wrote (sources.csv)")
    packaging.add_argument("--checkout", type=Path,
                           help="a checkout of the commit (default: fetch it into --work-dir)")
    packaging.add_argument("--work-dir", type=Path,
                           help="where commits are fetched (default ~/rvsec_builds/packages)")
    packaging.add_argument("--findings", type=Path, nargs="+",
                           help="findings to relabel (default DIR/findings_all.jsonl)")
    running = commands.add_parser(
        "rvsec-run", allow_abbrev=False,
        help="run RVSec (Docker: instrumentation, emulator, test generation) and import its reports")
    running.add_argument("apks_dir", type=Path)
    running.add_argument("--sources", type=Path, required=True,
                         help="apk -> repo_url, commit (as fdroid-sources writes it)")
    running.add_argument("--tools", default="monkey:fast",
                         help="rv-experiment tools, comma-separated (monkey:fast, ape:sata, ...)")
    running.add_argument("--timeout", default="180", help="seconds per run (comma-separated arms)")
    running.add_argument("--repetitions", type=int, default=1)
    running.add_argument("--specification-set", default="jca_android")
    running.add_argument("--instrumentation", default="dexlib2", choices=("dexlib2", "ajc"))
    running.add_argument("--instrumentation-fallback", default="", choices=("", "dexlib2", "ajc"),
                         help="instrument the APKs the first variant failed on once more with "
                              "this one (default: none)")
    running.add_argument("--explorer-retries", type=int, default=1,
                         help="run a task again when it ended unfinished without reaching any "
                              "method, at most this many times (default 1; 0: never)")
    running.add_argument("--keep-artifacts", action="store_true",
                         help="keep the instrumented APKs, woven dex, lib_tmp and traces "
                              "(default: removed once the reports are converted)")
    running.add_argument("--static-analysis", action="store_true",
                         help="also run RVSec's GATOR static analysis (off: nothing reads it)")
    running.add_argument("--image", default="phtcosta/rvandroid:0.9.5")
    running.add_argument("--cpus", default="4")
    running.add_argument("--memory", default="6g")
    running.add_argument("--work-dir", type=Path,
                         help="where RVSec writes (default ~/rvsec_runs; keep it off /mnt/c)")
    running.add_argument("--rvsec-home", type=Path, default=os.environ.get("RVSEC_HOME"),
                         help="RVSec checkout, for the codes table (default: $RVSEC_HOME)")
    running.add_argument("-o", "--output", type=Path, default=Path("findings.jsonl"))
    checking = commands.add_parser("check", allow_abbrev=False,
                                   help="say what findings files hold and lack, before a run")
    checking.add_argument("findings", nargs="+", type=Path)
    checking.add_argument("--repo", type=Path,
                          help="the checkout the findings are about (resolves file+line)")
    checking.add_argument("-o", "--output", type=Path,
                          help="write the usable findings, in the pipeline's columns")
    example = commands.add_parser("template", help="write an example findings file")
    example.add_argument("-o", "--output", type=Path, default=Path("findings_template.csv"))
    starting = commands.add_parser("init", help="write a run file to fill in")
    starting.add_argument("-o", "--output", type=Path, default=Path("minha_run.toml"))
    starting.add_argument("--force", action="store_true", help="overwrite an existing file")
    publishing = commands.add_parser(
        "publish", allow_abbrev=False,
        help="open the pull requests of findings you reviewed and approved")
    publishing.add_argument("run_dir", type=Path,
                            help="the run directory (or its pull_requests directory)")
    publishing.add_argument("--finding", "--approve-reviewed-finding", dest="finding",
                            action="append", default=[], metavar="ID",
                            help="finding id you approve (repeatable; see the PR preview); "
                                 "nothing is sent to GitHub for a finding not named")
    publishing.add_argument("--project", help="only this APK")
    publishing.add_argument("--without-build-validation", action="store_true",
                            help="also publish when the build never ran or fails "
                                 "the same way without the patch")
    publishing.add_argument("--allow-duplicate-pr", action="store_true")
    publishing.add_argument("--allow-text-problems", action="store_true",
                            help="publish even if the title or body still reads like the "
                                 "pipeline (ids, its vocabulary, 'Fix <method>')")
    publishing.add_argument("--allow-ai-policy", action="store_true",
                            help="skip the note on a repository's AI-assisted contribution policy "
                                 "(never a blocker; a policy where submitting affirms that no AI "
                                 "was used is still printed as a warning)")
    publishing.add_argument("--allow-closed-repository", action="store_true",
                            help="publish although the repository says it accepts no "
                                 "contributions (only when the maintainer invited this one)")
    publishing.add_argument("--allow-changed-upstream", action="store_true",
                            help="publish although the default branch changed the code "
                                 "since the analysed commit")
    publishing.add_argument("--allow-stale-review", action="store_true",
                            help="publish a draft that did not pass the current behaviour "
                                 "review (the refusal prints the command that reruns it)")
    publishing.add_argument("--dry-run", action="store_true",
                            help="say what would be published, publish nothing")
    publishing.add_argument("--refresh-text", action="store_true",
                            help="send the regenerated description to the still-open pull "
                                 "requests of the findings named by --finding (rerun the prs "
                                 "step first); the others print the text they would get")
    differ = commands.add_parser("diff", help="a run's corrections as patches")
    differ.add_argument("run_dir", type=Path)
    differ.add_argument("--finding", action="append", default=[], metavar="ID")
    differ.add_argument("--safe-only", action="store_true",
                        help="only corrections with nothing left to decide")
    differ.add_argument("-o", "--output", type=Path, help="write one .patch per correction here")
    applying = commands.add_parser("apply", allow_abbrev=False,
                                   help="apply a run's corrections to your own checkout")
    applying.add_argument("run_dir", type=Path)
    applying.add_argument("--finding", action="append", default=[], metavar="ID",
                          help="correction to apply (repeatable); naming it is your approval")
    applying.add_argument("--all-safe", action="store_true",
                          help="every correction marked safe (never those marked review)")
    applying.add_argument("--repo", type=Path, default=Path("."),
                          help="your git checkout (default: the current directory)")
    applying.add_argument("--check", action="store_true",
                          help="only say whether each patch applies")
    reviewing = commands.add_parser("review", allow_abbrev=False,
                                    help="go through a run's corrections one by one")
    reviewing.add_argument("run_dir", type=Path)
    reviewing.add_argument("--repo", type=Path,
                           help="your checkout, for (a)pply")
    reviewing.add_argument("--pending", action="store_true",
                           help="only corrections without a recorded decision")
    reviewing.add_argument("--without-build-validation", action="store_true",
                           help="let (p)ublish proceed when the project does not build here")
    outcomes = commands.add_parser("pr-outcomes",
                                   help="merged, closed and open pull requests, with maintainers' words")
    outcomes.add_argument("--json", action="store_true")
    # Its own options are pr_followup's (`cognalys pr-followup --help`).
    commands.add_parser(
        "pr-followup", add_help=False, allow_abbrev=False,
        help="verify review-bot comments on published pull requests and commit a "
             "follow-up locally (dry run by default; pushed only with --approve-followup)")
    fidelity = commands.add_parser(
        "spec-fidelity", help="compare each specification with the CrySL rule it cites",
        description="Compare each specification with the CrySL rule it cites and, with "
                    "--findings/--runs, with the expected values the analyzer recorded on "
                    "findings. Report only: it edits no specification and feeds no prompt, "
                    "gate or decision.")
    fidelity.add_argument("--specifications-dir", type=Path)
    fidelity.add_argument("-o", "--output", type=Path,
                          help="write the records and summary as JSON (and a .md next to it)")
    fidelity.add_argument("--findings", nargs="+", type=Path, metavar="PATH",
                          help="findings files or directories: compare the analyzer's recorded "
                               "expected values with what each specification admits (report only)")
    fidelity.add_argument("--runs", nargs="+", type=Path, metavar="PATH",
                          help="run directories (or directories holding runs): the same "
                               "comparison over analysed records, with their final outcomes")
    fidelity.add_argument("--no-rule", action="store_true",
                          help="skip the comparison with the published CrySL rule (no network)")
    specs = commands.add_parser(
        "specs", allow_abbrev=False,
        help="specification maintenance: review the stored false-positive shapes")
    specs.add_argument("--review-shapes-only", action="store_true", required=True,
                       help="send each stored specification's shapes through the shape "
                            "review (one paid model call per specification with shapes) "
                            "and rewrite only those fields")
    specs.add_argument("--spec", action="append", default=None,
                       help="only this specification (repeatable)")
    specs.add_argument("--specifications-dir", type=Path)
    specs.add_argument("--findings-csv", type=Path,
                       help="review the specifications these findings use first")
    specs.add_argument("--overwrite", action="store_true",
                       help="review again specifications already stamped as reviewed")
    specs.add_argument("--model")
    retrying = commands.add_parser(
        "unpatched", help="write the findings runs left without a fix as a findings file")
    retrying.add_argument("runs", nargs="+", type=Path, help="run directories, oldest first")
    retrying.add_argument("-o", "--output", type=Path, required=True)
    retrying.add_argument("--include", action="append", default=[], metavar="OUTCOME",
                          help="also keep findings with this settled outcome (repeatable), "
                               "e.g. 'contrato externo impede a correção'")
    costing = commands.add_parser("cost", help="what runs' model calls cost, per step")
    costing.add_argument("runs", nargs="+", type=Path)
    costing.add_argument("--json", action="store_true")
    costing.add_argument("--by-outcome", action="store_true",
                         help="each run's cost per final outcome (as `cognalys results` shows "
                              "them) and per PR-ready draft; runs are never summed")
    replaying = commands.add_parser(
        "decision-replay", allow_abbrev=False,
        help="send one recorded adjudication, behaviour-review or extraction request of a finding "
             "again, n times (paid), and report how its decision fields vary")
    replaying.add_argument("run_dir", type=Path, help="the run whose traces hold the request")
    replaying.add_argument("--finding", required=True, metavar="ID",
                           help="the finding id, or a unique part of it "
                                "(Class:::method:::Spec)")
    replaying.add_argument("--stage", required=True,
                           choices=("adjudication", "behavior_review", "evidence_extraction"))
    replaying.add_argument("--samples", required=True, type=int,
                           help="how many times the request is sent (each a paid call)")
    replaying.add_argument("--max-cost", required=True, type=float, metavar="USD",
                           help="stop before a sample that would pass this many US dollars")
    replaying.add_argument("--call", default="", metavar="CALL_ID",
                           help="this recorded call (default: the finding's last of the stage)")
    replaying.add_argument("--llm-service-tier", choices=("auto", "default", "flex"),
                           default="flex",
                           help="OpenAI processing tier of the samples (default flex, as runs)")
    replaying.add_argument("--dry-run", action="store_true",
                           help="rebuild and check the request and estimate its cost; send nothing")
    replaying.add_argument("-o", "--output", type=Path,
                           help="where the samples' traces and decision_replay.json go "
                                "(default RUN_DIR/decision_replay/<time>_<stage>)")
    replaying.add_argument("--current-prompt", action="store_true",
                           help="send the recorded input under the instructions and JSON schema "
                                "this code builds today for the stage (behavior_review, "
                                "evidence_extraction) instead of the recorded ones; the report "
                                "says what it is a variant of")
    replaying.add_argument("--add-section", action="append", metavar="NAME=PATH",
                           help="with --current-prompt and --stage behavior_review: append the "
                                "JSON in PATH to the recorded data block as its last section NAME")
    replaying.add_argument("--json", action="store_true")
    caching =commands.add_parser("cache", help="see or clear the caches runs reuse")
    caching.add_argument("action", choices=("info", "clear"))
    caching.add_argument("--kind", action="append", choices=("llm", "baselines"),
                         help="only this cache (repeatable; default: all)")
    caching.add_argument("--older-than", type=float, default=0, metavar="DAYS",
                         help="clear only entries not used for this many days")
    caching.add_argument("--dry-run", action="store_true",
                         help="say what would be removed, remove nothing")
    researching = commands.add_parser(
        "research-corpus",
        help="list the research corpus, or import shared research answers from recorded runs")
    researching.add_argument("action", choices=("list", "import"))
    researching.add_argument("runs", nargs="*", type=Path, metavar="RUN_DIR",
                             help="import: run directories or prompt-log folders")
    researching.add_argument("--dir", type=Path,
                             help="the corpus directory (default: <workspace>/outputs/research_corpus)")
    researching.add_argument("--replace", action="store_true",
                             help="import: replace an existing valid entry with the recorded pass")
    researching.add_argument("--dry-run", action="store_true",
                             help="import: count what would be stored, write nothing")
    researching.add_argument("--json", action="store_true")
    commands.add_parser("doctor", help="check what this machine can run")

    arguments, pipeline_arguments = parser.parse_known_args(argv)
    from cognalys import api
    from cognalys.workspace import workspace_root

    # Set by the test suite: the workspace's real credentials (a token, the
    # git author of published commits) must not leak into the commits tests
    # make in the same process.
    if not os.environ.get(NO_CREDENTIAL_FILES):
        from cognalys.workspace import credential_directories

        for directory in credential_directories():
            for name in CREDENTIAL_FILES:
                load_env_file(directory / name)

    if arguments.command == "doctor":
        return doctor()
    if arguments.command == "results":
        result = api.load_results(*arguments.runs)
        if arguments.by_version:
            _results_by_version(arguments.runs, arguments.json)
        else:
            _print_results(result, arguments.json, show_versions=True)
        return _fail_on(result, arguments.fail_on, 0)
    if arguments.command == "status":
        return run_status_command(arguments)
    if arguments.command == "rvsec-import":
        return rvsec_import(arguments)
    if arguments.command == "fdroid-sources":
        return fdroid_sources(arguments)
    if arguments.command == "fdroid-build":
        return fdroid_build(arguments)
    if arguments.command == "github-build":
        return github_build(arguments)
    if arguments.command == "fdroid-packages":
        return fdroid_packages(arguments)
    if arguments.command == "rvsec-run":
        return rvsec_run(arguments)
    if arguments.command == "check":
        return check(arguments)
    if arguments.command == "template":
        return template(arguments)
    if arguments.command == "init":
        return init(arguments)
    if arguments.command == "pr-outcomes":
        return pr_outcomes(arguments)
    if arguments.command == "pr-followup":
        from cognalys.prs import pr_followup

        return pr_followup.main(list(pipeline_arguments))
    if arguments.command == "cost":
        return cost(arguments)
    if arguments.command == "decision-replay":
        return decision_replay(arguments)
    if arguments.command == "unpatched":
        return unpatched(arguments)
    if arguments.command == "specs":
        from cognalys.specifications import generate_specifications
        from cognalys.workspace import library_data

        return generate_specifications.main([
            "--review-shapes-only",
            "--specifications-dir",
            str(arguments.specifications_dir or library_data("specifications")),
            *(item for name in arguments.spec or [] for item in ("--spec", name)),
            *(["--csv-file", str(arguments.findings_csv)] if arguments.findings_csv else []),
            *(["--overwrite"] if arguments.overwrite else []),
            *(["--model", arguments.model] if arguments.model else []),
        ])
    if arguments.command == "spec-fidelity":
        from cognalys.evaluation import spec_fidelity

        return spec_fidelity.main(
            [*(["--specifications-dir", str(arguments.specifications_dir)]
               if arguments.specifications_dir else []),
             *(["-o", str(arguments.output)] if arguments.output else []),
             *(["--findings", *map(str, arguments.findings)] if arguments.findings else []),
             *(["--runs", *map(str, arguments.runs)] if arguments.runs else []),
             *(["--no-rule"] if arguments.no_rule else [])])
    if arguments.command == "research-corpus":
        # Run directories given after an option (import --dir D RUN...) are
        # left over by parse_known_args; they are runs too.
        unknown = [item for item in pipeline_arguments if item.startswith("-")]
        if unknown:
            print(f"research-corpus: unknown option(s) {' '.join(unknown)}", file=sys.stderr)
            return 2
        arguments.runs = [*arguments.runs, *map(Path, pipeline_arguments)]
        return research_corpus_command(arguments)
    if arguments.command == "cache":
        from cognalys import cache

        outputs = workspace_root() / "outputs"
        if arguments.action == "info":
            print("\n".join(cache.info(outputs)))
            return 0
        removed, size = cache.clear(outputs, arguments.kind or list(cache.CACHE_KINDS),
                                    arguments.older_than, arguments.dry_run)
        verb = "would remove" if arguments.dry_run else "removed"
        known = f" ({size / 1048576:.1f} MB of LLM cache)" if size else ""
        print(f"{verb} {removed} entr{'y' if removed == 1 else 'ies'}{known}")
        return 0
    if arguments.command == "publish":
        return publish(arguments)
    if arguments.command == "diff":
        return show_diff(arguments)
    if arguments.command == "apply":
        return apply_corrections(arguments)
    if arguments.command == "review":
        return review_run(arguments)
    if arguments.command == "scan":
        from cognalys.detectors import RepositoryTarget

        found = api.scan(RepositoryTarget(local_path=arguments.repo), arguments.spec)
        if arguments.json:
            print(json.dumps({"commit": found.commit, "problems": list(found.problems),
                              "excluded": list(found.excluded),
                              "findings": [finding.to_row() for finding in found.findings]},
                             indent=2, ensure_ascii=False))
        else:
            for finding in found.findings:
                print(f"{finding.spec:24} {finding.class_name}.{finding.method}"
                      f"  {finding.detector.get('source_location', '')}")
            for problem in found.problems:
                print(f"PROBLEM {problem.get('status')}: {problem}")
            for item in found.excluded:
                print(f"excluded ({item['reason'][:60]}): {item['specification']} "
                      f"{item['path']}:{item['line']}")
            print(f"\n{len(found.findings)} findings at {found.commit[:12]}; "
                  f"{len(found.excluded)} match(es) excluded; "
                  f"{'complete' if found.complete else f'{len(found.problems)} problem(s)'}")
        return 0 if found.complete else 1
    if "--help" in pipeline_arguments or "-h" in pipeline_arguments:
        from cognalys.orchestration import run_pipeline
        return run_pipeline.main(["--help"])
    try:
        config = _config(pipeline_arguments)
    except ValueError as exc:
        print(f"cognalys {arguments.command}: {exc}", file=sys.stderr)
        return 2
    if arguments.command == "plan":
        for step in api.plan(config):
            print(f"{step.name:22} {' '.join(step.command)}")
        return 0
    if arguments.command == "run":
        result = api.run(config)
        _print_results(result, arguments.json)
        return _fail_on(result, arguments.fail_on, int(result.exit_code or 0))
    from cognalys.detectors import RepositoryTarget

    try:
        found, result = api.repair_repository(
            RepositoryTarget(local_path=arguments.repo), config, specifications=arguments.spec)
    except api.RepositoryChanged as exc:
        print(f"cognalys propose-prs: {exc}", file=sys.stderr)
        return 2
    print(f"{len(found.findings)} findings at {found.commit[:12]}")
    _print_results(result, arguments.json)
    return int(result.exit_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
