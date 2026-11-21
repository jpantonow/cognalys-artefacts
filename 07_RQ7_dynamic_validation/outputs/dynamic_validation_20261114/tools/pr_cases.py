"""Case table for extending the dynamic validation to every published RVSec pull request.

Reads artigo_tcc_latex/amostra/inventario.json ("prs"), each PR's pipeline
metadata (outputs/.../pull_requests/*_pr.json), the run's findings file, the
queue's sources.csv and build.log, and the public GitHub API
(api.github.com/repos/<o>/<r>/pulls/<n>, and the PR's public .diff). Writes
outputs/dynamic_validation_20261114/pr_cases.json with one entry per PR.

Nothing is guessed: a value that the records do not give, or give in more than
one way, is left null and explained in the entry's "problems" (blocking for
the build/classify step). "notes" are non-blocking facts worth knowing.
An entry is "complete" when it has no problem.

GitHub responses are cached under pr_api_cache/ (never refetched once cached).
Unauthenticated by default; GITHUB_TOKEN from the environment is used when set
(never printed). Read-only: nothing is built, run or posted.

Run in WSL:
    cd /mnt/c/Users/Antonow/Documents/tcc_v3 && source ~/venv/bin/activate && \
    python outputs/dynamic_validation_20261114/tools/pr_cases.py
"""
from __future__ import annotations

import csv
import datetime as _dt
import functools
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
VAL = HERE.parent                                  # outputs/dynamic_validation_20261114
ROOT = VAL.parents[1]                              # tcc_v3
OUT = ROOT / "outputs"
CACHE = VAL / "pr_api_cache"
INVENTORY = ROOT / "artigo_tcc_latex" / "amostra" / "inventario.json"
JCA_ANDROID = Path.home() / "external/rvsec/rvsec/rvsec-mop/src/main/resources/jca_android"
csv.field_size_limit(1 << 30)

# The six cases already validated, by the PR each corresponds to
# (pr_correspondence.md, checked 2026-11-15).
VALIDATED = {
    "https://github.com/tokn-authenticator/tokn/pull/138": "tokn_19",
    "https://github.com/michaelWuensch/BitBanana/pull/184": "bitbanana_79",
    "https://github.com/SapuSeven/BetterUntis/pull/515": "untis_29198884",
    "https://github.com/christianrowlands/android-network-survey/pull/154": "networksurvey_114",
    "https://github.com/aivanovski/keepassvault/pull/347": "passnotes_11700",
    "https://github.com/leekleak/traffic-light/pull/238": "trafficlight_38",
}

# Findings files searched when a run does not name its own (older runs), in order.
GLOBAL_FINDINGS = [
    ROOT / "data/processed/exp_final/findings_exp_final.csv",
    *sorted((ROOT / "data/processed/exp_final").glob("findings_*.csv")),
    ROOT / "data/processed/rv_import_findings.csv",
    ROOT / "data/processed/rv_import_unanalysed_20261010.csv",
]

# Package-name tails that are build variants, not the app's name (case naming only).
VARIANT_TAILS = {"debug", "fdroid", "play", "foss", "free", "release"}
# Repository languages whose Android app is normally not a plain Gradle project.
NON_GRADLE_LANGUAGES = {"Dart", "JavaScript", "TypeScript", "Rust", "C#", "Go", "C++", "C"}
NON_GRADLE_MARKERS = {
    "flutter": re.compile(r"\bflutter\b", re.I),
    "react-native": re.compile(r"react-native|@react-native", re.I),
    "cargo/rust": re.compile(r"\bcargo (build|ndk)\b|rustc\b", re.I),
    "expo": re.compile(r"\bexpo-modules\b", re.I),
}
SYNTHETIC_METHOD = re.compile(r"^<|lambda|invokeSuspend|\$")


# ---------------------------------------------------------------- helpers
def wsl_path(path: str) -> Path:
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path or "")
    if m:
        return Path(f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/"))
    return Path(path)


def rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except Exception:
        return str(path)


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    if path.suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return rows
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        return list(csv.DictReader(handle))


def changed_lines(diff: str) -> list[str]:
    """Changed lines without whitespace, sorted (order-insensitive), as pr_correspondence.md compared them."""
    out = []
    for line in (diff or "").splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            out.append(line[0] + re.sub(r"\s+", "", line[1:]))
    return sorted(x for x in out if len(x) > 1)


# ---------------------------------------------------------------- GitHub
_RATE = {"remaining": None, "reset": None}


def _get(url: str, accept: str) -> tuple[int, bytes, dict]:
    headers = {"User-Agent": "dynval-pr-cases", "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read() or b"", dict(error.headers or {})
    except Exception as error:  # network
        return 0, str(error).encode(), {}


def api_json(url: str, cache_name: str) -> tuple[dict | None, str]:
    path = CACHE / cache_name
    if path.is_file():
        return read_json(path)["response"], ""
    if _RATE["remaining"] is not None and _RATE["remaining"] <= 1:
        return None, f"GitHub API rate limit exhausted (resets {_RATE['reset']}); rerun later, cached responses are kept"
    status, body, headers = _get(url, "application/vnd.github+json")
    if "X-RateLimit-Remaining" in headers:
        _RATE["remaining"] = int(headers["X-RateLimit-Remaining"])
        reset = headers.get("X-RateLimit-Reset")
        _RATE["reset"] = (_dt.datetime.fromtimestamp(int(reset), _dt.timezone.utc).isoformat() if reset else None)
    if status != 200:
        return None, f"GET {url} -> HTTP {status}: {body[:160].decode('utf-8', 'replace')}"
    data = json.loads(body)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                                "response": data}, indent=1), encoding="utf-8")
    time.sleep(0.5)
    return data, ""


def public_diff(html_url: str, cache_name: str) -> tuple[str | None, str]:
    path = CACHE / cache_name
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace"), ""
    status, body, _ = _get(html_url + ".diff", "text/plain")
    if status != 200:
        return None, f"GET {html_url}.diff -> HTTP {status}"
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    time.sleep(0.5)
    return body.decode("utf-8", "replace"), ""


# ---------------------------------------------------------------- indexes
def load_validated() -> tuple[dict, dict]:
    """classify.py CASES and build_all.sh CASES of the six validated cases."""
    spec = importlib.util.spec_from_file_location("dynval_classify", VAL / "classify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builds = {}
    text = (HERE / "build_all.sh").read_text(encoding="utf-8")
    block = re.search(r'CASES="\n(.*?)\n"', text, re.S).group(1)
    for line in block.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 5:
            builds[parts[0]] = dict(zip(("case", "repo_url", "commit", "task", "apk_glob"), parts))
    return module.CASES, builds


def index_sources() -> dict[str, list[tuple[str, dict]]]:
    index: dict[str, list] = {}
    for pattern in ("*/sources.csv", "*/*/sources.csv", "*/*/*/sources.csv"):
        for path in OUT.glob(pattern):
            for row in read_rows(path):
                if row.get("apk"):
                    index.setdefault(row["apk"], []).append((rel(path), row))
    return index


BUILD_LINE = re.compile(r"^(\S+\.apk)\s+(\S+) @ ([0-9a-f]+)\s+task (\S+)\s*$")


def index_build_logs() -> dict[str, list[dict]]:
    index: dict[str, list] = {}
    for pattern in ("*/build.log", "*/*/build.log"):
        for path in OUT.glob(pattern):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = BUILD_LINE.match(line.strip())
                if m:
                    entry = {"log": rel(path), "repo_url": m.group(2), "commit_prefix": m.group(3), "task": m.group(4)}
                    if entry not in index.setdefault(m.group(1), []):
                        index[m.group(1)].append(entry)
    return index


def active_specs() -> set | None:
    if not JCA_ANDROID.is_dir():
        return None
    return {p.stem for p in JCA_ANDROID.glob("*.mop")}


# ---------------------------------------------------------------- per-PR pieces
def case_name(apk: str) -> str | None:
    m = re.match(r"^(.+)_(\d+)\.apk$", apk or "")
    if not m:
        return None
    parts = m.group(1).split(".")
    while len(parts) > 1 and parts[-1].lower() in VARIANT_TAILS:
        parts.pop()
    short = re.sub(r"[^a-z0-9]", "", parts[-1].lower())
    return f"{short}_{m.group(2)}" if short else None


def _same_record(hits: list) -> list:
    """Copies of one record (same branch, patch commit and base) count once."""
    keys = {(d.get("branch"), d.get("commit_hash"), d.get("base_commit")) for _, d in hits}
    return hits[:1] if len(keys) == 1 else hits


def choose_metadata(candidates: list[str], head_sha: str | None, head_ref: str | None, pr_url: str,
                    pr_diff: str | None, problems: list, notes: list, loaded_out: list) -> tuple[Path | None, dict | None]:
    paths = [ROOT / c for c in candidates if c.endswith(".json") and (ROOT / c).is_file()]
    loaded = []
    for path in paths:
        try:
            data = read_json(path)
        except Exception:
            continue
        if "diff" in data or "build_validation" in data or "base_commit" in data:
            loaded.append((path, data))
    loaded_out.extend(loaded)
    if not loaded:
        if candidates:
            problems.append(f"no pipeline PR metadata (*_pr.json) among the identity's records: {candidates}")
        else:
            problems.append("the inventory has no identity record for this PR (identity_status missing)")
        return None, None
    if len(loaded) == 1:
        return loaded[0]
    for path, data in loaded:  # the record GitHub answered when the PR was created
        created = path.with_name(path.stem + "_created.json")
        if created.is_file() and read_json(created).get("html_url") == pr_url:
            notes.append(f"{len(loaded)} metadata candidates; chose {rel(path)} (its _created.json names this PR)")
            return path, data
    if head_ref:
        hits = [(p, d) for p, d in loaded if d.get("branch") == head_ref]
        hits = _same_record(hits)
        if len(hits) == 1:
            notes.append(f"{len(loaded)} metadata candidates; chose {rel(hits[0][0])} (branch = PR head ref)")
            return hits[0]
    if head_sha:
        hits = [(p, d) for p, d in loaded if d.get("commit_hash") == head_sha]
        hits = _same_record(hits)
        if len(hits) == 1:
            notes.append(f"{len(loaded)} metadata candidates; chose {rel(hits[0][0])} (commit_hash = PR head)")
            return hits[0]
    if pr_diff:
        hits = [(p, d) for p, d in loaded if changed_lines(d.get("diff", "")) == changed_lines(pr_diff)]
        hits = _same_record(hits)
        if len(hits) == 1:
            notes.append(f"{len(loaded)} metadata candidates; chose {rel(hits[0][0])} (diff identical to the PR's)")
            return hits[0]
        if len({rel(p) for p, _ in hits}) > 1 and len({d.get("base_commit") for _, d in hits}) == 1:
            notes.append(f"{len(hits)} metadata candidates with the PR's diff and the same base commit; chose {rel(hits[0][0])}")
            return hits[0]
    # Not blocking by itself: the fields the records agree on are still used, the rest are reported missing.
    notes.append("several pipeline metadata records and none is the PR's own run (no _created.json, head ref, "
                 f"commit_hash or diff match): {[rel(p) for p, _ in loaded]}; only fields they all agree on are used")
    return None, None


def findings_for(meta_path: Path | None, run_dir: Path | None) -> tuple[list[Path], str]:
    if run_dir is not None:
        inputs = run_dir / "run_inputs.json"
        if inputs.is_file():
            named = read_json(inputs).get("findings_csv")
            if named:
                return [wsl_path(named)], "run_inputs.json"
    return [p for p in GLOBAL_FINDINGS if p.is_file()], "global findings CSVs (the run names no findings file)"


def detector_rows(files: list[Path], apk: str, cls: str, method: str, spec: str) -> list[tuple[Path, dict]]:
    hits = []
    for path in files:
        for row in read_rows(path):
            if (row.get("apk") == apk and row.get("class") == cls and row.get("method") == method
                    and row.get("spec") == spec):
                hits.append((path, row))
    return hits


def pick_build(meta: dict, rvsec_builds: list[dict], problems: list, notes: list) -> dict:
    bv = meta.get("build_validation") or {}
    phases = bv.get("phases") or {}
    out = {"pipeline_validation_task": bv.get("task"), "pipeline_validation_status": bv.get("status"),
           "pipeline_production_task": None, "pipeline_production_status": None, "pipeline_apks": [],
           "rvsec_build_task": None, "rvsec_build_commit_prefix": None, "rvsec_build_log": None,
           "build_task": None, "apk_glob": None, "basis": None}
    production = phases.get("production") or {}
    out["pipeline_production_task"] = production.get("task")
    out["pipeline_production_status"] = production.get("status")
    apks = [Path(a).name for a in production.get("copied_apks") or []]
    out["pipeline_apks"] = apks
    # An APK named after the checkout directory (Gradle's root project name, e.g. <worktree>-debug.apk)
    # gets another name in another checkout: the directory part becomes a wildcard.
    project = production.get("project_name") or ""
    if project:
        apks = ["*" + a[len(project):] if a.startswith(project) else a for a in apks]
    tasks = sorted({b["task"] for b in rvsec_builds})
    if len(tasks) == 1:
        out["rvsec_build_task"] = tasks[0]
        out["rvsec_build_commit_prefix"] = rvsec_builds[0]["commit_prefix"]
        out["rvsec_build_log"] = rvsec_builds[0]["log"]
    elif len(tasks) > 1:
        notes.append(f"the queue built this APK with several tasks: {tasks}")
    if production.get("status") == "success" and apks:
        out["build_task"] = production.get("task")
        out["basis"] = "pipeline's production build (status success, APKs copied)"
    elif out["rvsec_build_task"]:
        out["build_task"] = out["rvsec_build_task"]
        out["basis"] = ("the queue's build of the analysed APK (build.log); the pipeline's production build "
                        f"was {production.get('status') or 'not recorded'} ({production.get('task') or bv.get('task')})")
        notes.append(out["basis"])
    else:
        problems.append("no APK-producing build task: pipeline production build "
                        f"{production.get('status') or 'not recorded'} ({production.get('task') or bv.get('task')}), "
                        "and no queue build.log line for this APK")
        return out
    # APK glob
    if out["build_task"] == production.get("task") and apks:
        candidates = list(apks)
    else:
        candidates = []
    if not candidates:
        problems.append(f"APK name of task {out['build_task']} not recorded (no copied APKs for it); apk_glob unknown")
        return out
    reasons = []
    if len(candidates) > 1:
        flavor_task = out["rvsec_build_task"]
        m = re.search(r"assemble([A-Za-z0-9]*?)Debug$", flavor_task or "")
        flavor = (m.group(1) or "").lower() if m else ""
        if flavor:
            kept = [a for a in candidates if flavor in a.lower().replace("_", "").replace("-", "")]
            if kept:
                candidates = kept
                reasons.append(f"flavour '{flavor}' of the queue's task {flavor_task}")
    if len(candidates) > 1:
        x86 = [a for a in candidates if "x86_64" in a]
        universal = [a for a in candidates if "universal" in a]
        if x86:
            candidates, _ = x86, reasons.append("ABI x86_64 (the emulator's)")
        elif universal:
            candidates, _ = universal, reasons.append("universal ABI split")
    if len(candidates) == 1:
        out["apk_glob"] = candidates[0]
        if reasons:
            notes.append(f"apk_glob {candidates[0]} chosen among {apks} by " + " and ".join(reasons))
    else:
        problems.append(f"task {out['build_task']} builds several APKs {apks} and the records do not say which "
                        "variant RVSec analysed (no queue build.log flavour)")
    return out


def _declaration(method: str) -> re.Pattern:
    """A Kotlin `fun [Receiver.]name(` or a Java `<modifiers> Type name(` line that is not a statement."""
    m = re.escape(method)
    kotlin = r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.?<>]+\.)?" + m + r"\s*\("
    java = (r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|final|synchronized|abstract|"
            r"native|default)\s+)*[\w<>\[\],.?]*[\w>\]]\s+" + m + r"\s*\([^;=]*$")
    return re.compile(f"(?:{kotlin})|(?:{java})")


def method_survives(diff: str, method: str, cls: str) -> tuple[bool | None, str]:
    if not diff:
        return None, "no diff to check"
    if SYNTHETIC_METHOD.search(method or ""):
        return True, f"synthetic or special method name '{method}': not checked against the diff"
    decl = _declaration(method)
    keywords = re.compile(r"^\s*(return|new|throw|else|await|case|yield)\b")
    lines = diff.splitlines()
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---")
                  and decl.search(l[1:]) and not keywords.match(l[1:]))
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++")
                and decl.search(l[1:]) and not keywords.match(l[1:]))
    simple = cls.split(".")[-1].split("$")[0]
    if any(re.match(r"^rename from .*\b" + re.escape(simple) + r"\.(kt|java)$", l) for l in lines):
        return False, f"the diff renames the file of {simple}"
    if removed and not added:
        # Hint only: the functions/methods the diff declares anew in the files where the declaration went.
        any_decl = re.compile(_declaration("PLACEHOLDER").pattern.replace("PLACEHOLDER", r"(\w+)"))
        not_names = {"if", "for", "while", "switch", "catch", "synchronized", "return", "when", method}
        files_with_removal, current, new_names = set(), None, []
        for l in lines:
            if l.startswith("+++ "):
                current = l[6:] if l.startswith("+++ b/") else l[4:]
            elif l.startswith("-") and not l.startswith("---") and decl.search(l[1:]) and not keywords.match(l[1:]):
                files_with_removal.add(current)
        current = None
        for l in lines:
            if l.startswith("+++ "):
                current = l[6:] if l.startswith("+++ b/") else l[4:]
            elif current in files_with_removal and l.startswith("+") and not keywords.match(l[1:]):
                match = any_decl.search(l[1:])
                if match:
                    name = next((g for g in match.groups() if g), None)
                    if name and name not in not_names and name not in new_names:
                        new_names.append(name)
        return False, (f"the diff removes {removed} declaration line(s) of '{method}' and adds none "
                       f"(method renamed or removed); declarations added in that file: {new_names or 'none'}")
    return True, f"declaration of '{method}' in {removed} removed / {added} added diff lines"


# ---------------------------------------------------------------- main
def main() -> int:
    inventory = read_json(INVENTORY)
    validated_cases, validated_builds = load_validated()
    sources = index_sources()
    builds = index_build_logs()
    specs = active_specs()
    inv_findings = {}
    for finding in inventory.get("findings", []):
        for url in finding.get("prs") or []:
            inv_findings.setdefault(url, []).append(finding)

    entries = []
    for pr in inventory["prs"]:
        problems: list[str] = []
        notes: list[str] = []
        url = pr["url"]
        m = re.match(r"https://github.com/([^/]+)/([^/]+)/pull/(\d+)", url)
        owner, repo, number = m.group(1), m.group(2), int(m.group(3))
        slug = f"{owner}__{repo}__{number}"
        record = pr.get("outcome_record") or {}
        entry = {"pr_url": url, "status": pr.get("recorded_status"), "case": None,
                 "already_validated": url in VALIDATED, "validated_case": VALIDATED.get(url)}

        # GitHub
        api, err = api_json(f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}", f"{slug}.json")
        pr_api = None
        if api is None:
            problems.append(err)
        else:
            head_repo = (api.get("head") or {}).get("repo") or {}
            pr_api = {"title": api.get("title"), "state": api.get("state"), "merged": api.get("merged"),
                      "merged_at": api.get("merged_at"),
                      "base_repo": ((api.get("base") or {}).get("repo") or {}).get("full_name"),
                      "base_ref": (api.get("base") or {}).get("ref"), "base_sha": (api.get("base") or {}).get("sha"),
                      "head_repo": head_repo.get("full_name"), "head_ref": (api.get("head") or {}).get("ref"),
                      "head_sha": (api.get("head") or {}).get("sha"), "commits": api.get("commits"),
                      "changed_files": api.get("changed_files"),
                      "repo_language": ((api.get("base") or {}).get("repo") or {}).get("language")}
            if not head_repo:
                notes.append("the PR's head repository (fork) no longer exists; head sha is still reachable as "
                             f"refs/pull/{number}/head of the base repository")
        entry["pr_api"] = pr_api
        pr_diff, err = public_diff(url, f"{slug}.diff")
        if pr_diff is None:
            problems.append(err)
        entry["pr_diff_file"] = rel(CACHE / f"{slug}.diff") if pr_diff is not None else None

        # Pipeline metadata
        candidates = [c for cand in pr.get("identity_candidates") or [] for c in cand.get("metadata") or []]
        loaded: list = []
        meta_path, meta = choose_metadata(candidates, (pr_api or {}).get("head_sha"), (pr_api or {}).get("head_ref"),
                                          url, pr_diff, problems, notes, loaded)
        meta = meta or {}
        info = meta
        if not meta and len(loaded) > 1:  # finding fields every candidate record agrees on
            info = {}
            for key in ("finding_id", "error_message", "spec", "csv_class", "method_name"):
                values = {d.get(key) for _, d in loaded}
                if len(values) == 1 and next(iter(values)):
                    info[key] = next(iter(values))
        vc = validated_cases.get(entry["validated_case"], {}) if entry["validated_case"] else {}
        vb = validated_builds.get(entry["validated_case"], {}) if entry["validated_case"] else {}
        run_dir = meta_path.parent.parent if meta_path else None
        entry["metadata"] = rel(meta_path) if meta_path else None
        entry["run_dir"] = rel(run_dir) if run_dir else None

        ident = (pr.get("identity_candidates") or [{}])[0] if pr.get("identity_candidates") else {}
        apk = ident.get("apk")
        entry["apk"] = apk
        entry["case"] = case_name(apk) if apk else None
        if not apk:
            problems.append("APK unknown (no identity record); case name, packages and finding cannot be derived")

        # Finding identity
        def agree(label, *values):
            vals = [v for v in values if v]
            if not vals:
                problems.append(f"{label} not recorded")
                return None
            if len(set(vals)) > 1:
                problems.append(f"{label} disagrees between records: {sorted(set(vals))}")
                return None
            return vals[0]

        entry["spec"] = agree("spec", record.get("spec"), info.get("spec"), ident.get("spec"))
        entry["class"] = agree("class", record.get("csv_class"), info.get("csv_class"), ident.get("class"))
        entry["method"] = agree("method", record.get("method_name"), info.get("method_name"), ident.get("method"))
        fid_values = {"outcome_record": record.get("finding_id") or None, "pr_metadata": info.get("finding_id") or None,
                      "inventory_findings": sorted({f.get("finding_id") for f in inv_findings.get(url, [])
                                                    if f.get("finding_id")}) or None}
        entry["finding_id_sources"] = fid_values
        fids = {v for v in (fid_values["outcome_record"], fid_values["pr_metadata"]) if v}
        if len(fids) == 1:
            entry["finding_id"] = fids.pop()
            inv = set(fid_values["inventory_findings"] or [])
            if inv and entry["finding_id"] not in inv:
                notes.append(f"the inventory's run34 finding for this PR has another id {sorted(inv)} "
                             "(the PR came from an earlier run)")
        elif len(fids) > 1:
            entry["finding_id"] = None
            problems.append(f"finding_id disagrees: {fid_values}")
        elif fid_values["inventory_findings"] and len(fid_values["inventory_findings"]) == 1:
            entry["finding_id"] = fid_values["inventory_findings"][0]
            notes.append("finding_id taken from the inventory's findings list (no PR metadata)")
        else:
            entry["finding_id"] = None
            problems.append("finding_id not recorded")
        if specs is None:
            entry["spec_active"] = None
            problems.append(f"RVSec spec set not found at {JCA_ANDROID}")
        else:
            entry["spec_active"] = entry["spec"] in specs if entry["spec"] else None
            if entry["spec"] and not entry["spec_active"]:
                problems.append(f"{entry['spec']} is not in RVSec's active jca_android set; a rerun cannot report it")

        # Repository and commits
        repo_url = f"https://github.com/{owner}/{repo}"
        if meta.get("remote_url") and meta["remote_url"].rstrip("/").removesuffix(".git").lower() != repo_url.lower():
            notes.append(f"pipeline remote {meta['remote_url']} differs from the PR's repository (renamed or moved)")
        entry["repo_url"] = repo_url
        src_rows = sources.get(apk, []) if apk else []
        src_values = {(r.get("repo_url"), r.get("commit"), r.get("manifest_package")) for _, r in src_rows}
        entry["sources_csv"] = sorted({p for p, _ in src_rows}) or None
        entry["analysed_commit"] = None
        entry["app_packages"] = None
        entry["packages_source"] = None
        if len(src_values) == 1:
            _, commit, packages = next(iter(src_values))
            entry["analysed_commit"] = commit or None
            if packages:
                entry["app_packages"] = [p for p in packages.split(";") if p]
                entry["packages_source"] = "sources.csv manifest_package"
        elif len(src_values) > 1:
            problems.append(f"sources.csv rows for {apk} disagree: {sorted(src_values)}")
        entry["pipeline_base_branch"] = meta.get("base_branch")
        entry["pipeline_base_commit"] = meta.get("base_commit") or None
        entry["pipeline_patch_commit"] = meta.get("commit_hash") or None
        head = (pr_api or {}).get("head_sha")
        entry["head_sha_is_pipeline_commit"] = (head == entry["pipeline_patch_commit"]) if head and entry["pipeline_patch_commit"] else None
        pipe_diff = meta.get("diff") or ""
        entry["pr_diff_matches_pipeline_diff"] = (changed_lines(pipe_diff) == changed_lines(pr_diff)
                                                  if pipe_diff and pr_diff is not None else None)

        # Detector code and message
        entry["pr_message"] = info.get("error_message") or None
        entry["detector_code"] = None
        entry["message"] = None
        entry["detector_source"] = None
        entry["detector_candidates"] = None
        if apk and entry["spec"] and entry["class"] and entry["method"]:
            files, how = findings_for(meta_path, run_dir)
            rows = detector_rows(files, apk, entry["class"], entry["method"], entry["spec"])
            if not any(r.get("detector_code") for _, r in rows) and how == "run_inputs.json":
                files = [p for p in GLOBAL_FINDINGS if p.is_file()]
                rows = detector_rows(files, apk, entry["class"], entry["method"], entry["spec"])
                how = "global findings CSVs (the run's own findings file has no detector code for this site)"
                if rows:
                    notes.append(how)
            entry["detector_candidates"] = sorted({(r.get("detector_code") or "-", r.get("message"))
                                                   for _, r in rows})
            if not entry["analysed_commit"]:
                commits = {r.get("commit") for _, r in rows if r.get("commit")}
                if len(commits) == 1:
                    entry["analysed_commit"] = commits.pop()
            with_code = [(p, r) for p, r in rows if r.get("detector_code")]
            if entry["pr_message"]:
                same = [(p, r) for p, r in with_code if r.get("message") == entry["pr_message"]]
            else:
                same = with_code
            pairs = {(r.get("detector_code"), r.get("message")) for _, r in same}
            if len(pairs) == 1:
                entry["detector_code"], entry["message"] = next(iter(pairs))
                entry["detector_source"] = sorted({rel(p) for p, _ in same})
                if not entry["pr_message"]:
                    notes.append("no PR message recorded; the only detector row at this site and spec was taken")
            elif len(pairs) > 1:
                problems.append(f"several detector codes/messages match: {sorted(pairs)}")
            else:
                at_site = sorted({(r.get("detector_code") or "-", r.get("message")) for _, r in rows})
                if at_site:
                    problems.append("no detector row at this site carries the PR's message "
                                    f"{entry['pr_message']!r} with a detector code ({how}); rows at the site: {at_site}")
                else:
                    problems.append(f"no detector row for {apk} {entry['class']}.{entry['method']} {entry['spec']} ({how})")

        # Build
        bprobs: list[str] = []
        rvsec_builds = builds.get(apk, []) if apk else []
        if meta:
            entry["build"] = pick_build(meta, rvsec_builds, bprobs, notes)
        elif len(loaded) > 1:
            # Several later runs of this finding, none the PR's own: their build is usable when they all agree.
            picks = []
            for _, data in loaded:
                p, n = [], []
                b = pick_build(data, rvsec_builds, p, n)
                if not p:
                    picks.append((b["build_task"], b["apk_glob"], b))
            if picks and len(picks) == sum(1 for _ in loaded) and len({(t, g) for t, g, _ in picks}) == 1:
                entry["build"] = dict(picks[0][2], basis="all metadata candidates agree: " + (picks[0][2]["basis"] or ""))
                notes.append(f"build task/apk glob from {len(loaded)} later runs of this finding, which agree")
            else:
                entry["build"] = None
                bprobs.append("no pipeline build record of the PR's own run, and the later runs' builds "
                              f"disagree or failed: {[(t, g) for t, g, _ in picks]} of {len(loaded)}")
        else:
            entry["build"] = None
            bprobs.append("no pipeline build record: build task and apk glob unknown")
        if bprobs and vb.get("task") and vb.get("apk_glob"):
            notes.append(f"build task/apk glob from the validated case's build_all.sh ({vb['task']}, {vb['apk_glob']}) "
                         "instead: " + "; ".join(bprobs))
            entry["build"] = dict(entry["build"] or {}, build_task=vb["task"], apk_glob=vb["apk_glob"],
                                  basis="validated case's build_all.sh")
            bprobs = []
        problems.extend(bprobs)
        if not entry["app_packages"] and vc.get("packages"):
            entry["app_packages"] = list(vc["packages"])
            entry["packages_source"] = "classify.py CASES (validated case)"
        if not entry["app_packages"] and apk:
            problems.append("app packages not recorded (no sources.csv row for this APK); "
                            f"the APK name's package is only a hint: {re.sub(r'_[0-9]+[.]apk$', '', apk)}")

        # Sites
        entry["base_sites"] = [[entry["class"], entry["method"]]] if entry["class"] and entry["method"] else None
        check_diff = pr_diff if pr_diff is not None else pipe_diff
        survives, why = method_survives(check_diff, entry["method"] or "", entry["class"] or "")
        entry["patched_sites"] = entry["base_sites"] if survives else None
        entry["site_check"] = why
        if vc.get("patched_sites") and entry["pr_diff_matches_pipeline_diff"]:
            entry["patched_sites"] = [list(s) for s in vc["patched_sites"]]
            entry["base_sites"] = [list(s) for s in vc["base_sites"]]
            entry["site_check"] = why + "; sites from classify.py CASES (validated patch = this PR's diff)"
        elif survives is False:
            problems.append(f"patched_sites need reading the diff: {why}")

        # Language / build system
        markers = []
        log = (meta.get("build_validation") or {}).get("log_file")
        if log and wsl_path(log).is_file():
            text = wsl_path(log).read_text(encoding="utf-8", errors="replace")[:5_000_000]
            markers = [name for name, rx in NON_GRADLE_MARKERS.items() if rx.search(text)]
        language = (pr_api or {}).get("repo_language")
        gradle_built = bool(entry.get("build") and entry["build"].get("build_task"))
        entry["language"] = language
        entry["build_markers"] = markers
        if markers or (language in NON_GRADLE_LANGUAGES):
            entry["build_system"] = "non-gradle or hybrid (" + ", ".join(markers + ([language] if language in NON_GRADLE_LANGUAGES else [])) + ")"
            entry["non_gradle"] = True
            problems.append(f"not a plain Gradle Android project: {entry['build_system']}")
        elif gradle_built:
            entry["build_system"] = "gradle-android"
            entry["non_gradle"] = False
        else:
            entry["build_system"] = None
            entry["non_gradle"] = None
            problems.append("build system not established (no Gradle build record)")

        # Commit to build
        entry["build_commit"] = None
        entry["build_commit_basis"] = None
        entry["patch_to_apply"] = None
        if entry["pr_diff_matches_pipeline_diff"] and entry["pipeline_base_commit"]:
            entry["build_commit"] = entry["pipeline_base_commit"]
            entry["build_commit_basis"] = "pipeline base commit; the PR's diff equals the pipeline's"
            entry["patch_to_apply"] = entry["pr_diff_file"]
        elif pr_api and pr_diff is not None:
            compare, err = api_json(
                f"https://api.github.com/repos/{owner}/{repo}/compare/{pr_api['base_sha']}...{pr_api['head_sha']}",
                f"{slug}.compare.json")
            merge_base = ((compare or {}).get("merge_base_commit") or {}).get("sha")
            entry["pr_merge_base"] = merge_base
            if merge_base:
                entry["build_commit"] = merge_base
                entry["build_commit_basis"] = ("merge base of the PR head with its base branch (the PR's .diff is "
                                               "relative to it); the PR diff differs from the pipeline's "
                                               "or no pipeline diff is recorded")
                entry["patch_to_apply"] = entry["pr_diff_file"]
                notes.append("PR diff differs from the pipeline diff (or none recorded): base = merge base, "
                             "patched = merge base + PR diff (equivalently the PR head)")
            else:
                problems.append("commit to build unknown: PR diff differs from the pipeline's and the merge base "
                                f"could not be read ({err or 'no merge_base_commit'})")
        else:
            problems.append("commit to build unknown (no PR API data or diff)")

        # The validated six: cross-check against their recorded configuration.
        if entry["validated_case"]:
            entry["validated_config"] = {"build": vb, "packages": vc.get("packages"), "spec": vc.get("spec"),
                                         "code": vc.get("code"), "message": vc.get("message"),
                                         "base_sites": vc.get("base_sites"), "patched_sites": vc.get("patched_sites")}
            if entry["case"] != entry["validated_case"]:
                notes.append(f"derived case name {entry['case']} differs from the validated case {entry['validated_case']}")
            for field, value in (("code", entry["detector_code"]), ("spec", entry["spec"])):
                if vc.get(field) and value and vc.get(field) != value:
                    problems.append(f"the PR's finding ({field} {value}) is not the validated case's ({vc.get(field)}): "
                                    "the existing validation does not cover this PR's finding")
            if vb.get("commit") and entry["build_commit"] and vb["commit"] != entry["build_commit"]:
                notes.append(f"validated build commit {vb['commit']} differs from this entry's build_commit")
            if entry["pr_diff_matches_pipeline_diff"] is False:
                notes.append("the validated patch is the pipeline's diff, not this PR's diff (pr_correspondence.md)")

        entry["problems"] = problems
        entry["notes"] = notes
        entry["complete"] = not problems
        entries.append(entry)

    # Unique case names (two PRs from one APK get the PR number appended).
    counts = Counter(e["case"] for e in entries if e["case"])
    for e in entries:
        if e["case"] and counts[e["case"]] > 1:
            e["case"] = f"{e['case']}_pr{e['pr_url'].rsplit('/', 1)[1]}"

    summary = {
        "prs": len(entries),
        "complete": sum(e["complete"] for e in entries),
        "complete_by_status": dict(Counter(e["status"] for e in entries if e["complete"])),
        "by_status": dict(Counter(e["status"] for e in entries)),
        "non_gradle": sum(bool(e.get("non_gradle")) for e in entries),
        "already_validated": sum(e["already_validated"] for e in entries),
        "rate_limit_remaining": _RATE["remaining"],
    }
    out = {"generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
           "inventory": rel(INVENTORY), "spec_set": str(JCA_ANDROID), "summary": summary,
           "complete_means": ("repo_url, build_commit, an APK-producing build_task, apk_glob, app_packages, an active "
                              "spec, detector_code, message, base_sites, patched_sites and the PR diff are all known "
                              "and unambiguous, and the project is plain Gradle Android"),
           "cases": entries}
    (VAL / "pr_cases.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    for e in entries:
        print(f"{'OK ' if e['complete'] else '-- '}{e['status'][:6]:6} {e['case'] or '?':32} {e['pr_url'][19:]}")
        for p in e["problems"]:
            print(f"      ! {p[:220]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
