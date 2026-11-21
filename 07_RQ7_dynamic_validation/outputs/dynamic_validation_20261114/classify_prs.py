"""Classify the dynamic validation of the published PRs (docs/preregistro_validacao_prs_20261115.md).

Uses classify.py's functions unchanged (site_matches, read_run, classify, cell) and the
same rule:
    pendente; base não reproduz; não corrigido (violation of the spec at the patched
    site in any repetition); corrigido (no violation and site covered); inconclusivo
    (cobertura).
The cases come from pr_cases_resolved.json (tools/resolve_pr_cases.py) and their runs
from runs/<case>/{base,patched}_r<rep>/ (queue_prs.sh). The repetitions read per case
are those present on disk (rep 1, and rep 2 when a second repetition was run);
classify.REPS is set to them before classify.classify is called, the function itself
is not changed.

Every PR of the population (the 49 entries of pr_cases.json) is listed with one status:
    corrigido / não corrigido / inconclusivo (cobertura) / base não reproduz /
    não compilou / excluído (reason) / não executado / já validado (14/11, 3 repetições).
"não executado" covers a case that is built but has no finished base and patched run
(not run yet, interrupted, or RVSec could not instrument/install it: detail given), and
a case not built yet. After the cut-off (2026-11-18 23:59) it is reported as such.

Writes results_prs.json and results_prs.md. Run in WSL:
    source ~/venv/bin/activate && python outputs/dynamic_validation_20261114/classify_prs.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import classify  # noqa: E402  (functions used unchanged)

VARIANTS = classify.VARIANTS
GROUPS = ("accepted", "open", "closed_by_maintainer")
CUTOFF = datetime(2026, 11, 18, 23, 59)

# Exclusion reasons of the pre-registration, matched against pr_cases.json `problems`.
EXCLUSION_RULES = (
    (r"is not in RVSec's active jca_android set", "spec fora do conjunto jca_android"),
    (r"no identity record|APK unknown \(no identity|no pipeline PR metadata|finding_id not recorded",
     "sem identidade do achado ou sem metadados do pipeline"),
    (r"no detector row", "mensagem sem linha do detector correspondente no site"),
    (r"no APK-producing build task|no pipeline build record|apk_glob unknown|builds several APKs",
     "sem build que tenha produzido APK, ou vários APKs sem registro de qual o RVSec analisou"),
    (r"app packages not recorded", "pacotes do app não registrados"),
    (r"patched_sites need reading the diff", "método renomeado/removido pelo patch"),
)


def exclusion_reason(case: dict) -> str:
    if case.get("case") == "passnotes_11700":
        return ("o achado do PR é outro (mensagem antiga de protocolo TLS), não o validado em 14/11; "
                "esse resultado vale para o patch da run, não para o PR")
    reasons = []
    for problem in case.get("problems") or []:
        for pattern, reason in EXCLUSION_RULES:
            if re.search(pattern, problem) and reason not in reasons:
                reasons.append(reason)
    # The two "fixed by rule" reasons alone never exclude; they appear only beside a real one.
    hard = [r for r in reasons if r not in ("pacotes do app não registrados", "método renomeado/removido pelo patch")]
    return "; ".join(hard or reasons) or "; ".join(case.get("problems") or [])[:200]


def case_config(case: dict) -> dict:
    return {
        "packages": case.get("app_packages") or [],
        "spec": case["spec"], "code": case.get("detector_code") or "", "message": case.get("message") or "",
        "base_sites": [tuple(site) for site in case["base_sites"]],
        "patched_sites": [tuple(site) for site in case["patched_sites"]],
        "note": "; ".join(f"{k} fixed by rule: {v.get('how')}" for k, v in (case.get("filled") or {}).items()),
    }


REMOVED_CALL = "corrigido (chamada removida pelo patch)"


def call_removed_by_patch(rc: dict) -> str:
    """Declared 2026-11-17 (user's decision; docs/preregistro_validacao_prs_20261115.md, desvio 2026-11-17).
    When the patched APK never executes the site, the absence is not a coverage gap if the PR diff deletes the
    governed API's use from the site's file and adds none back there: there is nothing left to execute. The API
    name is the specification's name without its "Spec" suffix (SecretKeySpecSpec -> SecretKeySpec). Returns the
    evidence (the removed lines) or ""."""
    diff_path = HERE.parent.parent / (rc.get("pr_diff_file") or "")
    if not rc.get("pr_diff_file") or not diff_path.is_file():
        return ""
    api = re.sub(r"Spec$", "", rc.get("spec") or "")
    files = {re.sub(r"\$.*", "", site[0]).rsplit(".", 1)[-1] for site in rc.get("base_sites") or []}
    if not api or not files:
        return ""
    removed, added, current = [], [], None
    for line in diff_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("diff --git"):
            name = line.rsplit("/", 1)[-1]
            current = name.rsplit(".", 1)[0] if name.rsplit(".", 1)[0] in files else None
        elif current and line.startswith("-") and not line.startswith("---") and re.search(rf"\b{api}\b", line):
            removed.append(line[1:].strip())
        elif current and line.startswith("+") and not line.startswith("+++") and re.search(rf"\b{api}\b", line):
            added.append(line[1:].strip())
    if removed and not added:
        return "; ".join(removed)[:400]
    return ""


MESSAGE_GONE = "corrigido (mensagem reportada sumiu; restam outras já presentes no original)"


def _codes(run: dict) -> set:
    return {m.group(1) for entry in run.get("messages") or [] for m in [re.search(r"\[([A-Z0-9_-]+)\]", entry)] if m}


def reported_message_gone(runs: dict) -> str:
    """Declared 2026-11-17 (user's decision; desvio 2026-11-17 in the pre-registration). The pre-registered rule
    counts any violation of the specification at the patched site. A case is instead corrected when the reported
    message (its detector code or text) appeared at the site on the original APK, appears in no patched repetition,
    the patched site was covered, and every message left at the patched site was already there on the original
    APK: the PR removed what was reported and added nothing, and the rest is code it did not touch. Returns the
    evidence or ""."""
    base = [r for k, r in runs.items() if k.startswith("base") and r.get("state") == "done"]
    patched = [r for k, r in runs.items() if k.startswith("patched") and r.get("state") == "done"]
    if not base or not patched:
        return ""
    if not any(r.get("same_message") for r in base):
        return ""
    if any(r.get("same_message") for r in patched) or not any(r.get("covered") for r in patched):
        return ""
    base_codes = set().union(*(_codes(r) for r in base))
    left = set().union(*(_codes(r) for r in patched))
    if not left or not left <= base_codes:
        return ""
    return ("a mensagem reportada não aparece em nenhuma repetição do corrigido; as que restam no site ("
            + ", ".join(sorted(left)) + ") já apareciam no original")


# Label corrections (2026-11-18, review of every case left without "corrigido"). Each reads the measured runs, the
# PR diff and the base source; the label the earlier rule gave stays in rule_result.
TEST_ONLY = "sem mudança de produção (o PR só altera testes)"
NOT_OBSERVABLE = ("não verificável (a violação reportada não aparece em nenhum dos dois APKs; "
                  "restam outras já presentes no original)")
MOVED = "não corrigido (a violação reportada passou para outro método do mesmo arquivo)"
GATED = "corrigido (o patch desliga o caminho que executava o site)"
TEST_PATH = re.compile(r"(^|/)(test|androidTest|testDebug|testRelease)/|Test\.(java|kt)$|Tests\.(java|kt)$")


def _diff_files(rc: dict) -> list[str]:
    diff_path = HERE.parent.parent / (rc.get("pr_diff_file") or "")
    if not rc.get("pr_diff_file") or not diff_path.is_file():
        return []
    return [m.group(1) for line in diff_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for m in [re.match(r"diff --git a/(\S+) b/", line)] if m]


def only_tests_changed(rc: dict) -> str:
    """The PR changes no production file: every path in its diff is a test source."""
    files = _diff_files(rc)
    if files and all(TEST_PATH.search(f) for f in files):
        return "; ".join(files)[:300]
    return ""


def reported_not_observed(runs: dict) -> str:
    """'não corrigido' only through other messages: the reported violation (its code or text) is seen in no base and no
    patched repetition, and every message left at the patched site was already at the site on the original APK."""
    base = [r for k, r in runs.items() if k.startswith("base") and r.get("state") == "done"]
    patched = [r for k, r in runs.items() if k.startswith("patched") and r.get("state") == "done"]
    if not base or not patched or any(r.get("same_message") for r in base + patched):
        return ""
    base_codes = set().union(*(_codes(r) for r in base))
    left = set().union(*(_codes(r) for r in patched))
    if not left or not left <= base_codes:
        return ""
    return ("a violação reportada não aparece em nenhuma repetição dos dois APKs; as que restam no site ("
            + ", ".join(sorted(left)) + ") já apareciam no original")


OLD_RULE = ("não verificável (o original executou o site sem violação; a mensagem relatada não é emitida "
            "pela versão do RVSec usada na validação)")


def _messages_emitted() -> set[str]:
    """Every violation message the validation's RVSec runs produced (runs/*/*/findings_all.jsonl)."""
    out = set()
    for path in (HERE / "runs").glob("*/*/findings_all.jsonl"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.add(json.loads(line).get("message") or "")
            except ValueError:
                continue
    return out


def reported_rule_absent(rc: dict, runs: dict, emitted: set[str]) -> str:
    """'base não reproduz' although a base repetition executed the site: the message of the original report (the PR's
    message) is emitted by no run of the whole validation, so the rule set that produced it is not the one measured."""
    message = (rc.get("pr_message") or "").strip()
    covered = [k for k, r in runs.items() if k.startswith("base") and r.get("state") == "done" and r.get("covered")]
    if not message or not covered or any(message in m for m in emitted):
        return ""
    return (f"o site foi executado no original ({', '.join(covered)}) sem violação; a mensagem do relato original "
            f"(\"{message[:90]}\") não aparece em nenhuma execução da validação")


def version_note(name: str, config: dict) -> str:
    """Repetitions with the RVSec image of the original campaign (runs_version/<tag>/<case>/), when there are any."""
    notes = []
    for case_dir in sorted((HERE / "runs_version").glob(f"*/{name}")):
        tag = case_dir.parent.name
        cells = []
        for run_dir in sorted(p for p in case_dir.iterdir() if p.is_dir()):
            variant = run_dir.name.split("_r")[0]
            run = classify.read_run(run_dir, config, variant)
            cells.append(f"{run_dir.name}: {'executou' if run.get('covered') else 'não executou'} o site"
                         + (", violação" if run.get("violation") else ""))
        if cells:
            notes.append(f"com a imagem rvandroid:{tag} ({len(cells)} execuções): " + "; ".join(cells))
    return " | ".join(notes)


def _file_key(cls: str) -> str:
    """Source-file key of a JVM class: outer class, without a Kotlin file-facade 'Kt' suffix."""
    outer = re.sub(r"\$.*", "", cls or "")
    return re.sub(r"Kt$", "", outer)


def moved_in_same_file(name: str, config: dict, runs: dict) -> str:
    """The patched APK never runs the site, but the reported message is reported in a patched repetition at another
    method of the site's source file (the PR moved the call; the violation is still executed)."""
    keys = {_file_key(site[0]) for site in config["base_sites"]}
    hits = set()
    for key, run in runs.items():
        if not key.startswith("patched") or run.get("state") != "done":
            continue
        path = HERE / "runs" / name / key / "findings_all.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("spec") != config["spec"] or _file_key(row.get("class", "")) not in keys:
                continue
            if (config["code"] and row.get("code") == config["code"]) or (config["message"] and config["message"] in row.get("message", "")):
                hits.add(f"{row.get('class')}.{row.get('method')} ({key})")
    return "; ".join(sorted(hits))[:300]


def gated_off_by_patch(rc: dict, name: str) -> str:
    """The patched APK never runs the site because the PR changes the default of a value that guards its only calls:
    in the base source of the site's file, every call of the site method sits under an `if` whose condition names a
    variable whose declared default the diff changes (removed and added declaration lines with different values)."""
    import subprocess
    method = (rc.get("base_sites") or [[None, None]])[0][1]
    commit_file = HERE / "build" / name / "base.commit"
    src = Path.home() / "dynval_builds" / name / "src" / name
    diff_path = HERE.parent.parent / (rc.get("pr_diff_file") or "")
    if not method or not commit_file.is_file() or not src.is_dir() or not diff_path.is_file():
        return ""
    commit = commit_file.read_text().strip()
    keys = {_file_key(site[0]).rsplit(".", 1)[-1] for site in rc.get("base_sites") or []}
    removed, added, current = {}, {}, None
    for line in diff_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("diff --git"):
            path = re.match(r"diff --git a/(\S+) b/", line)
            current = path.group(1) if path and Path(path.group(1)).stem in keys else None
        elif current and re.match(r"^[-+](?![-+])", line):
            decl = re.match(r"^[-+]\s*(?:val|var|private|public|final|static|\w+\s)*\s*(\w+)\s*(?::\s*[\w.<>?]+)?\s*=\s*([^,;]+)", line)
            if decl:
                (removed if line[0] == "-" else added).setdefault(current, {})[decl.group(1)] = decl.group(2).strip()
    for path, before in removed.items():
        changed = {v for v, value in before.items() if v in added.get(path, {}) and added[path][v] != value}
        if not changed:
            continue
        try:
            text = subprocess.run(["git", "-C", str(src), "show", f"{commit}:{path}"], capture_output=True,
                                  text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        lines = text.splitlines()
        calls = [i for i, l in enumerate(lines) if re.search(rf"\b{re.escape(method)}\s*\(", l)
                 and not re.search(rf"\bfun\s+(<[^>]*>\s*)?([\w.<>?]+\.)?{re.escape(method)}\s*\(", l)
                 and not re.search(rf"^\s*(?:(?:public|private|protected|static|final|synchronized)\s+)*[\w<>\[\],.?]+\s+"
                                   rf"{re.escape(method)}\s*\([^)]*\)?\s*(?:\{{|throws|$)", l)]
        guards = []
        for i in calls:
            cond = next((lines[j] for j in range(i - 1, max(i - 4, -1), -1) if re.search(r"\bif\s*\(", lines[j])), "")
            names = changed & set(re.findall(r"\w+", cond))
            if not names:
                return ""
            guards.append(f"{cond.strip()} [{', '.join(f'{n}: {before[n]} -> {added[path][n]}' for n in sorted(names))}]")
        if calls:
            return "; ".join(guards)[:300]
    return ""


def reps_on_disk(name: str):
    folder = HERE / "runs" / name
    reps = {1}
    if folder.is_dir():
        for child in folder.iterdir():
            match = re.fullmatch(r"(?:base|patched)_r(\d+)", child.name)
            if match:
                reps.add(int(match.group(1)))
    return tuple(sorted(reps))


def main() -> int:
    source = json.loads((HERE / "pr_cases.json").read_text(encoding="utf-8"))
    resolved = {c["case"]: c for c in json.loads((HERE / "pr_cases_resolved.json").read_text(encoding="utf-8"))["cases"]}
    # Deviation 5 (2026-11-17): the old-message cases, validated at spec level (pr_cases_oldmsg.json). They leave
    # "excluído" only when they have a result (a finished base and patched run, or a failed build).
    oldmsg_path = HERE / "pr_cases_oldmsg.json"
    oldmsg = ({c["case"]: c for c in json.loads(oldmsg_path.read_text(encoding="utf-8"))["cases"]}
              if oldmsg_path.is_file() else {})
    builds_early = (json.loads((HERE / "build_status_prs.json").read_text(encoding="utf-8"))
                    if (HERE / "build_status_prs.json").is_file() else {})
    for name, rc in oldmsg.items():
        status = builds_early.get(name, {}).get("case_status")
        has_runs = all((HERE / "runs" / name / f"{v}_r1" / ".done").is_file() or
                       (HERE / "runs" / name / f"{v}_r1" / ".unexplorable").is_file() for v in VARIANTS)
        if has_runs or status in ("não compilou", "patch não aplica", "clone falhou"):
            resolved[name] = rc
    builds_path = HERE / "build_status_prs.json"
    builds = json.loads(builds_path.read_text(encoding="utf-8")) if builds_path.is_file() else {}
    earlier_path = HERE / "results.json"
    earlier = json.loads(earlier_path.read_text(encoding="utf-8"))["cases"] if earlier_path.is_file() else {}
    after_cutoff = datetime.now() > CUTOFF
    # Deviation 6 (2026-11-18): pr_cases_desvio6.json. A cases (six excluded PRs) are matched by PR URL (two have
    # another case name than pr_cases.json: passnotes_11700_pr347, httpshortcuts_1104060001) and leave "excluído"
    # only with a result, as in deviation 5; the B entry bitbanana_79 adds rep 4 to the 14/11 runs.
    d6_path = HERE / "pr_cases_desvio6.json"
    d6_cases = json.loads(d6_path.read_text(encoding="utf-8"))["cases"] if d6_path.is_file() else []
    d6_by_url = {c["pr_url"]: c for c in d6_cases if not c.get("reuse_apks")}
    d6_reuse = {c["case"]: c for c in d6_cases if c.get("reuse_apks")}
    for rc in d6_by_url.values():
        name = rc["case"]
        status = builds_early.get(name, {}).get("case_status")
        has_runs = all((HERE / "runs" / name / f"{v}_r1" / ".done").is_file() or
                       (HERE / "runs" / name / f"{v}_r1" / ".unexplorable").is_file() for v in VARIANTS)
        if has_runs or status in ("não compilou", "patch não aplica", "clone falhou"):
            resolved[name] = rc

    emitted = _messages_emitted()
    rows = []
    for order, case in enumerate(source["cases"], 1):
        name = case.get("case")
        row = {"pr_url": case["pr_url"], "pr_status": case["status"], "case": name, "spec": case.get("spec"),
               "pr_cases_order": order}
        d6 = d6_by_url.get(case["pr_url"])
        if d6:
            name = d6["case"]
            row.update({"case": name, "spec": d6.get("spec"), "pr_status": d6["status"],
                        "pr_status_in_pr_cases": case["status"], "deviation": "2026-11-18 desvio 6"})
            if case.get("case") != name:
                row["case_in_pr_cases"] = case.get("case")
        if name in oldmsg:
            row["deviation"] = "2026-11-17 desvio 5"
        if name in resolved:
            rc = resolved[name]
            build = builds.get(name, {})
            row["build_order"] = rc["build_order"]
            row["inclusion"] = rc["inclusion"]
            row["filled"] = rc.get("filled") or {}
            row["case_status"] = build.get("case_status", "não compilado ainda")
            if row["case_status"] in ("não compilou", "patch não aplica", "clone falhou"):
                row["status"] = "não compilou"
                details = [f"{v}: {build.get(f'{v}_status')}" for v in VARIANTS]
                tails = [build.get(f"{v}_log_tail", "") for v in VARIANTS if build.get(f"{v}_log_tail")]
                row["detail"] = "; ".join(details) + (f" ({row['case_status']})" if row["case_status"] != "não compilou" else "")
                row["log_tail"] = tails[-1][-1500:] if tails else build.get("patch_apply_error", "")
                # The Gradle failure line of the base build's log, when it is there.
                log = Path.home() / "dynval_builds" / name / "out_base" / "logs" / "projects" / f"{name}.log"
                wrong = (re.search(r"What went wrong:\n(.+)\n(.*)", log.read_text(encoding="utf-8", errors="replace"))
                         if log.is_file() else None)
                if wrong:
                    row["detail"] += f"; Gradle: {wrong.group(1).strip()} {wrong.group(2).strip()}"[:400]
            elif row["case_status"] != "built":
                row["status"] = "não executado"
                row["detail"] = "APKs não compilados" + (" até o corte" if after_cutoff else " (build pendente)")
            else:
                config = case_config(rc)
                reps = reps_on_disk(name)
                runs = {f"{variant}_r{rep}": classify.read_run(HERE / "runs" / name / f"{variant}_r{rep}", config, variant)
                        for rep in reps for variant in VARIANTS}
                classify.REPS = reps
                summary = classify.classify(config, runs)
                row["reps"] = list(reps)
                row["runs"] = runs
                row["summary"] = summary
                row["cells"] = {key: classify.cell(run) for key, run in runs.items()}
                row["config"] = {k: (list(map(list, v)) if k.endswith("_sites") else v) for k, v in config.items()}
                result = summary["result"]
                if result == "pendente":
                    row["status"] = "não executado"
                    states = sorted({f"{k}: {r['state']}" + (f" ({r.get('detail', '').strip()[:160]})" if r.get("detail") else "")
                                     for k, r in runs.items() if r["state"] != "done"})
                    row["detail"] = "; ".join(states) + (" (até o corte)" if after_cutoff else "")
                else:
                    row["status"] = result
                    base_seen = any(r.get("violation") and r.get("covered") for k, r in runs.items() if k.startswith("base"))
                    patched_never = not any(r.get("covered") or r.get("violation")
                                            for k, r in runs.items() if k.startswith("patched"))
                    if result == "inconclusivo (cobertura)" and base_seen and patched_never:
                        evidence = call_removed_by_patch(rc)
                        if evidence:
                            row["status"] = REMOVED_CALL
                            row["rule_result"] = result
                            row["detail"] = f"pela regra: {result}; o diff do PR remove do arquivo do site: {evidence}"
                    if result == "não corrigido":
                        evidence = reported_message_gone(runs)
                        if evidence:
                            row["status"] = MESSAGE_GONE
                            row["rule_result"] = result
                            row["detail"] = f"pela regra: {result}; {evidence}"
            # Declared deviation 2026-11-17: a case whose original report came from other explorers is also run
            # with them (classify_origin_tools.py); that result is the status, the monkey/ape one is kept beside it.
            origin_path = HERE / "results_origin_tools.json"
            if origin_path.is_file() and name in json.loads(origin_path.read_text(encoding="utf-8")):
                origin = json.loads(origin_path.read_text(encoding="utf-8"))[name]
                row["rule_result_monkey_ape"] = row.get("status")
                row["status"] = origin["summary"]["result"]
                row["origin_tools_cells"] = origin["cells"]
                row["detail"] = (f"com as ferramentas do relato original (droidbot dfs/bfs_greedy, 300 s, "
                                 f"{len(origin['reps'])} repetições): {origin['summary']['result']}; com monkey/ape: "
                                 f"{row['rule_result_monkey_ape']}")
            # Label corrections (2026-11-18): see TEST_ONLY .. GATED above.
            if row.get("runs") or row.get("status") in ("não corrigido", "inconclusivo (cobertura)", "base não reproduz"):
                status, runs_ = row.get("status"), row.get("runs") or {}
                config_ = case_config(rc)
                evidence, new = only_tests_changed(rc), TEST_ONLY
                if not evidence and status == "não corrigido":
                    evidence, new = reported_not_observed(runs_), NOT_OBSERVABLE
                if not evidence and status == "inconclusivo (cobertura)":
                    base_seen = any(r.get("violation") and r.get("covered") for k, r in runs_.items() if k.startswith("base"))
                    patched_never = runs_ and not any(r.get("covered") for k, r in runs_.items() if k.startswith("patched"))
                    if base_seen and patched_never:
                        evidence, new = moved_in_same_file(name, config_, runs_), MOVED
                        if not evidence:
                            evidence, new = gated_off_by_patch(rc, name), GATED
                if not evidence and status == "base não reproduz":
                    evidence, new = reported_rule_absent(rc, runs_, emitted), OLD_RULE
                if evidence:
                    row["rule_result"] = row.get("rule_result") or status
                    row["status"] = new
                    row["detail"] = f"pela regra: {status}; {evidence}"
        elif case.get("already_validated") and name != "passnotes_11700" and not d6:
            summary = (earlier.get(case.get("validated_case") or name) or {}).get("summary", {})
            row["status"] = "já validado (14/11, 3 repetições)"
            row["earlier_result"] = summary.get("result")
            row["detail"] = f"resultado em results.json: {summary.get('result')}"
            # Deviation 6 (2026-11-18) B: a validated case given one more repetition (bitbanana_79, rep 4) is labelled
            # from all its repetitions pooled (classify.classify, classify.CASES config unchanged); the 14/11 label
            # stays beside it (result_14_11).
            reps = reps_on_disk(name)
            if name in d6_reuse and name in classify.CASES and max(reps) > 3:
                config = classify.CASES[name]
                runs = {f"{variant}_r{rep}": classify.read_run(HERE / "runs" / name / f"{variant}_r{rep}", config, variant)
                        for rep in reps for variant in VARIANTS}
                classify.REPS = reps
                summary6 = classify.classify(config, runs)
                if summary6["result"] != "pendente" and all(runs[f"{v}_r{max(reps)}"]["state"] != "missing" for v in VARIANTS):
                    row.pop("earlier_result")
                    row["deviation"] = "2026-11-18 desvio 6"
                    row["result_14_11"] = summary.get("result")
                    row["status"] = summary6["result"]
                    row["reps"] = list(reps)
                    row["runs"] = runs
                    row["summary"] = summary6
                    row["cells"] = {key: classify.cell(run) for key, run in runs.items()}
                    row["detail"] = (f"desvio 6: {len(reps)} repetições juntas ({summary6['result']}); "
                                     f"rótulo de 14/11 com 3 repetições: {summary.get('result')}")
        else:
            row["status"] = "excluído"
            row["reason"] = exclusion_reason(case)
            if name in oldmsg:
                row["reason"] += "; desvio 5 (2026-11-17): validação no nível da especificação ainda sem resultado"
            if d6:
                row["reason"] += "; desvio 6 (2026-11-18): ainda sem resultado"
        # Repetitions with the original campaign's RVSec image, beside the label.
        config_v = classify.CASES.get(name) or (case_config(resolved[name]) if name in resolved else None)
        note = version_note(name, config_v) if config_v and row["status"] != "excluído" else ""
        if note:
            row["version_runs"] = note
            row["detail"] = (row.get("detail") + "; " if row.get("detail") else "") + note
        rows.append(row)

    # PR state from the newest GitHub collection (outputs/prs_reports/<stamp>/summary_rvsec.json); the state in
    # pr_cases.json is kept as pr_status_in_pr_cases when it differs.
    reports = sorted(p for p in (HERE.parent / "prs_reports").glob("*/summary_rvsec.json"))
    if reports:
        state_of = {"merged": "accepted", "manual": "accepted", "maintainer": "closed_by_maintainer", "open": "open"}
        latest = {p["url"]: state_of.get(p["category"]) for p in json.loads(reports[-1].read_text(encoding="utf-8"))["prs"]}
        for r in rows:
            new = latest.get(r["pr_url"])
            if new and new != r["pr_status"]:
                r.setdefault("pr_status_in_pr_cases", r["pr_status"])
                r["pr_status"] = new
                r["pr_status_source"] = str(reports[-1].relative_to(HERE.parent.parent))

    def counts(selected):
        out = {}
        for r in selected:
            out[r["status"]] = out.get(r["status"], 0) + 1
        return out

    new_rows = [r for r in rows if r["case"] in resolved]
    second = [r["case"] for r in sorted(new_rows, key=lambda r: r["build_order"])
              if r["status"] in ("base não reproduz", "inconclusivo (cobertura)") and r.get("reps") == [1]]
    out = {
        "preregistration": "docs/preregistro_validacao_prs_20261115.md",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "after_cutoff": after_cutoff,
        "population": len(rows),
        "counts_all": counts(rows),
        "counts_new_cases": counts(new_rows),
        "counts_new_cases_by_pr_status": {g: counts([r for r in new_rows if r["pr_status"] == g]) for g in GROUPS},
        "second_repetition_candidates": second,
        "second_repetition_command": ('REPS=2 ONLY_CASES="' + " ".join(second) + '" (queue_prs.sh, after every case has rep 1)')
                                     if second else None,
        "prs": rows,
    }
    (HERE / "results_prs.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=list), encoding="utf-8")

    labels = {"accepted": "aceito", "open": "aberto", "closed_by_maintainer": "fechado pelo mantenedor"}
    lines = [
        "# Validação dinâmica dos PRs publicados: RVSec no APK base e no APK corrigido",
        "",
        "Pré-registro: `docs/preregistro_validacao_prs_20261115.md`. Os dois APKs de cada caso saem do mesmo commit "
        "com a mesma tarefa Gradle; o corrigido tem o diff do PR (ou do pipeline, quando iguais) aplicado sem "
        "alteração. RVSec: `monkey:fast,ape:sata`, 600 s, jca_android; uma repetição por caso (segunda só para "
        "inconclusivos). Regra de `classify.py` sem mudança. Resultado relatado como \"a violação reportada deixou "
        "de aparecer no método corrigido\". Desvio declarado em 2026-11-17: quando o APK corrigido nunca executa o "
        "site e o diff do PR remove do arquivo do site o uso da API da especificação sem acrescentar outro, o caso é "
        f"\"{REMOVED_CALL}\" (a regra daria \"inconclusivo (cobertura)\"; as duas leituras ficam em results_prs.json). "
        "Segundo desvio declarado em 2026-11-17: quando a mensagem reportada aparece no original e em nenhuma "
        "repetição do corrigido, o site corrigido foi coberto e toda mensagem que resta nele já aparecia no original, "
        f"o caso é \"{MESSAGE_GONE}\" (a regra daria \"não corrigido\"; as duas leituras ficam em results_prs.json). "
        "Desvio 5 (2026-11-17): os sete PRs excluídos por mensagem em formato antigo (pr_cases_oldmsg.json) são "
        "validados no nível da especificação (qualquer violação da especificação no site; nenhuma mensagem é "
        "comparada, por isso suas células mostram `V*`) e marcados com `deviation`; ficam excluídos até terem "
        "resultado. Desvio 6 (2026-11-18): seis PRs excluídos (pr_cases_desvio6.json) entram com o valor que "
        "faltava fixado por fonte registrada (keepassvault #347 e HTTP-Shortcuts #576 no nível da especificação); "
        "os inconclusivos e de base que não reproduz ganham uma repetição a mais (BitBanana: rep 4 sobre as três "
        "de 14/11), rotulados com todas as repetições juntas. Correções de rótulo (2026-11-18, revisão de todo caso "
        "sem \"corrigido\"; o rótulo anterior fica em `rule_result`): "
        f"\"{TEST_ONLY}\" quando todo arquivo do diff é de teste; "
        f"\"{NOT_OBSERVABLE}\" quando o \"não corrigido\" vem só de mensagens que já estavam no site do original e a "
        "reportada não aparece em nenhum APK; "
        f"\"{MOVED}\" quando o corrigido nunca executa o site e a mensagem reportada aparece em outro método do mesmo "
        "arquivo-fonte; "
        f"\"{GATED}\" quando o corrigido nunca executa o site e toda chamada do site, no código-fonte do original, "
        "está sob um `if` cuja variável tem o valor padrão trocado pelo diff; "
        f"\"{OLD_RULE}\" quando o \"base não reproduz\" tem o site executado no original e a mensagem do relato "
        "original não aparece em nenhuma execução da validação. Execuções com a imagem da campanha original "
        "(runs_version/<tag>/) aparecem no detalhe do caso.",
        "",
        f"População: {len(rows)} PRs. Todos: " + "; ".join(f"{k}: {v}" for k, v in sorted(out["counts_all"].items())) + ".",
        "",
        "Casos novos (" + str(len(new_rows)) + "): " + "; ".join(f"{k}: {v}" for k, v in sorted(out["counts_new_cases"].items())) + ".",
        "",
    ]
    for g in GROUPS:
        c = out["counts_new_cases_by_pr_status"][g]
        lines.append(f"- {labels[g]}: " + ("; ".join(f"{k}: {v}" for k, v in sorted(c.items())) or "-"))
    lines += ["", "| PR | Status do PR | Caso | Spec | Base | Corrigido | Resultado |", "|---|---|---|---|---|---|---|"]
    ordered = sorted(rows, key=lambda r: (r["case"] not in resolved, r.get("build_order") or 0,
                                          GROUPS.index(r["pr_status"]), r["pr_cases_order"]))
    for r in ordered:
        cells = r.get("cells") or {}
        base = " / ".join(cells[k] for k in sorted(cells) if k.startswith("base")) or "-"
        patched = " / ".join(cells[k] for k in sorted(cells) if k.startswith("patched")) or "-"
        status = r["status"] + (f" ({r['reason']})" if r.get("reason") else "")
        if r.get("earlier_result"):
            status += f": {r['earlier_result']}"
        pr = r["pr_url"].replace("https://github.com/", "")
        lines.append(f"| {pr} | {labels[r['pr_status']]} | {r['case'] or '-'} | {r.get('spec') or '-'} | {base} | {patched} | {status} |")
    lines += ["", "Legenda das células: as de `classify.py` (`V` violação no site, `V*` com outra mensagem, `-` nenhuma, "
              "`cov`/`nocov` método executado ou não; `missing`/`unfinished`/`unexplorable`).", ""]
    if second:
        lines += [f"Candidatos à segunda repetição: {', '.join(second)} (`{out['second_repetition_command']}`).", ""]
    for r in ordered:
        if r["case"] not in resolved and not r.get("runs"):  # deviation 6: bitbanana_79's pooled runs are shown too
            continue
        lines.append(f"## {r['case']}")
        lines.append("")
        if r.get("filled"):
            for field, filled in r["filled"].items():
                lines.append(f"- {field} fixado por regra: {filled.get('value')} ({filled.get('how')})")
        if r.get("detail"):
            lines.append(f"- {r['status']}: {r['detail']}")
        if r.get("log_tail"):
            lines.append("- fim do log: `" + r["log_tail"].strip().splitlines()[-1][:300] + "`")
        for key, run in (r.get("runs") or {}).items():
            if run["state"] != "done":
                lines.append(f"- {key}: {run['state']}" + (f" ({run.get('detail', '').strip()})" if run.get("detail") else ""))
                continue
            tasks = "; ".join(f"{t['tool']} {t['state']} {t['methods_reached']} métodos"
                              + (f" (site: {len(t['site_methods_reached'])})" if t["site_methods_reached"] else "")
                              for t in run["tasks"])
            lines.append(f"- {key}: violação={'sim' if run['violation'] else 'não'} ({', '.join(run['violating_tools']) or '-'}), "
                         f"coberto={'sim' if run['covered'] else 'não'} ({', '.join(run['covered_by']) or '-'}); {tasks}")
            for message in run["messages"][:5]:
                lines.append(f"  - {message}")
        lines.append("")
    (HERE / "results_prs.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
