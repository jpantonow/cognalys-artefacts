"""Classify the dynamic-validation runs (docs/PLANO_VALIDACAO_DINAMICA.md, section 1).

Reads runs/<case>/<variant>_r<rep>/ written by queue.sh (rvsec_run.out, .done,
and rvsec/: tasks.json, errors.csv, the RVSEC lines of each task's logcat) and,
per case, applies the plan's table:

    base                 patched                           result
    violation reproduced method covered, no violation      corrigido
    violation reproduced method covered, violation         nao corrigido
    violation reproduced method not covered                inconclusivo (cobertura)
    violation not reproduced  any                          base nao reproduz (out of the count)

"Violation" is a monitor report with the case's specification at the case's
site (class and method, lambdas and Kotlin synthetic names of that method
included). "Covered" is the method in RVSEC-COV (the methods the instrumented
app ran, as cognalys.integrations.rvsec.reached_methods reads them). A case is
counted "corrigido" only when the patched method ran. Aggregation over the
repetitions: base reproduces when any finished base repetition reports the
violation; the patched APK has the violation when any finished patched
repetition reports it, and is covered when any finished one ran the method.

Writes results.json and results.md next to this file. Run in WSL:
    source ~/venv/bin/activate && python classify.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
from cognalys.integrations import rvsec  # noqa: E402  (read-only use of the library's readers)

REPS = (1, 2, 3)
VARIANTS = ("base", "patched")

# Per case: the specification, the original finding's message and detector code
# (data/processed/exp_final/findings_exp_final.csv), the governed call's site in
# the base APK, and its site(s) in the patched APK. Both APKs are built from the
# commit the pipeline built the patch on, which can differ from the revision
# RVSec analysed (the class can have moved: BetterUntis' DiskCache). A patch that
# renames or splits the method (BitBanana) lists the methods holding the
# governed call after the patch.
CASES = {
    "tokn_19": {
        "packages": ["me.diamondforge.tokn"],
        "spec": "KeyStoreSpec", "code": "KEYSTORE-ORDER-00",
        "message": "the observed call sequence is not one KeyStoreSpec accepts",
        "base_sites": [("me.diamondforge.tokn.security.KeystoreManager", "getOrCreateKey")],
        "patched_sites": [("me.diamondforge.tokn.security.KeystoreManager", "getOrCreateKey")],
        "note": "",
    },
    "bitbanana_79": {
        "packages": ["app.michaelwuensch.bitbanana"],
        "spec": "PBEKeySpecSpec", "code": "PBEKEYSPEC-CONSTR-00",
        "message": "the third argument should be >= 10000",
        "base_sites": [("app.michaelwuensch.bitbanana.util.UtilFunctions", "encodePbkdf2")],
        # The patch renames encodePbkdf2 to encodeLegacyAppLockPbkdf2 (kept, with
        # the old iteration count, only to verify a verifier stored before the
        # patch) and adds encodeCurrentAppLockPbkdf2 (100000 iterations) for new
        # and current verifiers. On a clean install only the current one runs.
        "patched_sites": [("app.michaelwuensch.bitbanana.util.UtilFunctions", "encodeCurrentAppLockPbkdf2"),
                          ("app.michaelwuensch.bitbanana.util.UtilFunctions", "encodeLegacyAppLockPbkdf2"),
                          ("app.michaelwuensch.bitbanana.util.UtilFunctions", "encodePbkdf2")],
        "note": ("encodePbkdf2 runs only from the app-lock password/PIN setup and entry screens; "
                 "the explorers must turn an app lock on. The patch renames the method, so the "
                 "patched sites are encodeCurrentAppLockPbkdf2 / encodeLegacyAppLockPbkdf2."),
    },
    "untis_29198884": {
        "packages": ["com.sapuseven.untis"],
        "spec": "MessageDigestSpec", "code": "MESSAGEDIGEST-ALG-02",
        "message": "expecting one of SHA-256,SHA-384,SHA-512,SHA256,SHA384,SHA512 but found MD5",
        # Analysed revision: com.sapuseven.untis.data.cache.DiskCache; built
        # commit dbb978db: core/data module, com.sapuseven.untis.core.data.cache.DiskCache.
        "base_sites": [("com.sapuseven.untis.core.data.cache.DiskCache", "getCacheFile"),
                       ("com.sapuseven.untis.data.cache.DiskCache", "getCacheFile")],
        "patched_sites": [("com.sapuseven.untis.core.data.cache.DiskCache", "getCacheFile"),
                          ("com.sapuseven.untis.data.cache.DiskCache", "getCacheFile")],
        "note": "BetterUntis normally needs a WebUntis school login; the cache may not be written without one.",
    },
    "networksurvey_114": {
        "packages": ["com.craxiom.networksurvey", "com.craxiom"],
        "spec": "KeyStoreSpec", "code": "KEYSTORE-ORDER-00",
        "message": "the observed call sequence is not one KeyStoreSpec accepts",
        "base_sites": [("com.craxiom.networksurvey.util.CryptoManager", "getOrCreateKey")],
        "patched_sites": [("com.craxiom.networksurvey.util.CryptoManager", "getOrCreateKey")],
        # RVSec could not instrument the first builds (dexlib2 failed on net.bytebuddy's classes; runs
        # base_r*/patched_r*, unexplorable x6). Both variants were rebuilt from the same commit with the
        # same Gradle task and net.bytebuddy excluded (tools/build_networksurvey_nobb.sh, queue_nobb.sh).
        "run_dirs": {"base": "base_nobb", "patched": "patched_nobb"},
        "note": ("APKs rebuilt without net.bytebuddy in both variants (same commit, same Gradle task): RVSec "
                 "could not instrument the first builds (dexlib2 failed on bytebuddy; base_r*/patched_r* "
                 "unexplorable x6). base_nobb_r1 was rerun after the first attempt died of SIGHUP (exit 129) "
                 "after 21 s; that attempt is kept as base_nobb_r1.hup_20261115."),
    },
    "passnotes_11700": {
        "packages": ["com.ivanovsky.passnotes"],
        "spec": "SSLContextSpec", "code": "SSLCONTEXT-NOBS-05",
        "message": ("the TrustManager[] given to SSLContext.init holds a manager of a class the "
                    "application defines and was not observed coming from a TrustManagerFactory"),
        "base_sites": [("com.ivanovsky.passnotes.data.repository.file.webdav.HttpClientFactory", "createHttpClient")],
        "patched_sites": [("com.ivanovsky.passnotes.data.repository.file.webdav.HttpClientFactory", "createHttpClient")],
        "note": ("The violating branch is `BuildConfig.DEBUG && type == UNSECURE`: it runs only after a "
                 "WebDAV client of type UNSECURE is created, which needs a WebDAV server set up in the UI. "
                 "The patch keeps SSLContext.getInstance(\"TLS\") and the hostname verifier; another "
                 "SSLContextSpec message at the same site counts as a violation in the plan's table and "
                 "is shown under 'messages'."),
    },
    "trafficlight_38": {
        "packages": ["com.leekleak.trafficlight"],
        "spec": "KeyStoreSpec", "code": "KEYSTORE-ORDER-00",
        "message": "the observed call sequence is not one KeyStoreSpec accepts",
        # At the built commit getSecretKey reads the key inside keyCache.computeIfAbsent:
        # the call site can be a lambda of getSecretKey.
        "base_sites": [("com.leekleak.trafficlight.database.CryptoManager", "getSecretKey")],
        "patched_sites": [("com.leekleak.trafficlight.database.CryptoManager", "getSecretKey")],
        "note": "",
    },
}

# Kotlin value-class mangling: name-Abc1234.
_MANGLED = re.compile(r"-[A-Za-z0-9_]{7}$")


def site_matches(class_name: str, method: str, sites) -> bool:
    """A report or coverage entry at one of ``sites``: the class itself or one of its
    nested/synthetic classes, and the method itself, a synthetic name built on it
    (getSecretKey$lambda$0), or a lambda class named after it (Outer$getSecretKey$1)."""
    method = _MANGLED.sub("", method or "")
    for owner, wanted in sites:
        if class_name != owner and not class_name.startswith(owner + "$"):
            continue
        if method == wanted or wanted in method.split("$"):
            return True
        if wanted in class_name[len(owner):].split("$"):
            return True
    return False


def run_state(folder: Path) -> str:
    if (folder / ".done").is_file():
        return "done"
    if (folder / ".unexplorable").is_file():
        return "unexplorable"
    if (folder / "rvsec_run.out").is_file():
        return "unfinished"
    return "missing"


def read_run(folder: Path, case: dict, variant: str) -> dict:
    sites = case[f"{variant}_sites"]
    state = run_state(folder)
    result = {"state": state, "violation": False, "same_message": False, "covered": False,
              "covered_by": [], "violating_tools": [], "reports_at_site": 0,
              "messages": [], "tasks": [], "app_methods_reached": None}
    results_dir = folder / "rvsec"
    if state not in ("done",) or not results_dir.is_dir():
        if (folder / ".unexplorable").is_file():
            result["detail"] = (folder / ".unexplorable").read_text(encoding="utf-8", errors="replace")[:300]
        return result
    seen = set()
    for path in rvsec.errors_files([results_dir]):
        for row in rvsec.report_rows(path):
            if row.get("spec") != case["spec"]:
                continue
            if not site_matches(row.get("class", ""), row.get("method", ""), sites):
                continue
            key = (row.get("tool"), row.get("rep"), row.get("unique_msg"))
            if key in seen:
                continue
            seen.add(key)
            result["violation"] = True
            result["reports_at_site"] += 1
            tool = row.get("tool", "")
            if tool not in result["violating_tools"]:
                result["violating_tools"].append(tool)
            message = row.get("message", "")
            if row.get("code") == case["code"] or case["message"] in message:
                result["same_message"] = True
            entry = f"{row.get('class')}.{row.get('method')} [{row.get('code', '')}] {message[:200]}"
            if entry not in result["messages"]:
                result["messages"].append(entry)
    reached_app = set()
    for task in rvsec.task_outcomes(results_dir):
        covered = sorted(f"{cls}::{m}" for cls, m in task["reached"] if site_matches(cls, m, sites))
        if covered:
            result["covered"] = True
            if task["tool"] not in result["covered_by"]:
                result["covered_by"].append(task["tool"])
        reached_app.update((cls, m) for cls, m in task["reached"]
                           if rvsec.code_origin(cls, case["packages"]) == "app")
        result["tasks"].append({"tool": task["tool"], "state": task["state"],
                                "seconds": task["seconds"], "reports": task["reports"],
                                "methods_reached": len(task["reached"]),
                                "site_methods_reached": covered,
                                "error": (task["error"] or "")[:200]})
    result["app_methods_reached"] = len(reached_app)
    return result


def classify(case: dict, runs: dict) -> dict:
    base = [runs[f"base_r{r}"] for r in REPS]
    patched = [runs[f"patched_r{r}"] for r in REPS]
    base_done = [r for r in base if r["state"] == "done"]
    patched_done = [r for r in patched if r["state"] == "done"]
    summary = {
        "base_finished": len(base_done), "patched_finished": len(patched_done),
        "base_reproduces": sum(r["violation"] for r in base_done),
        "base_covered": sum(r["covered"] for r in base_done),
        "patched_violation": sum(r["violation"] for r in patched_done),
        "patched_same_message": sum(r["same_message"] for r in patched_done),
        "patched_covered": sum(r["covered"] for r in patched_done),
        "incomplete": len(base_done) < len(REPS) or len(patched_done) < len(REPS),
    }
    if not base_done or not patched_done:
        result = "pendente"
    elif not summary["base_reproduces"]:
        result = "base não reproduz"
    elif summary["patched_violation"]:
        result = "não corrigido"
    elif summary["patched_covered"]:
        result = "corrigido"
    else:
        result = "inconclusivo (cobertura)"
    summary["result"] = result
    return summary


def cell(run: dict) -> str:
    if run["state"] != "done":
        return run["state"]
    violation = "V" + ("" if run["same_message"] else "*") if run["violation"] else "-"
    covered = "cov" if run["covered"] else "nocov"
    return f"{violation} {covered}"


def main() -> int:
    runs_root = HERE / "runs"
    out = {"plan": "docs/PLANO_VALIDACAO_DINAMICA.md section 1", "cases": {}}
    for name, case in CASES.items():
        dirs = case.get("run_dirs", {})
        runs = {f"{variant}_r{rep}": read_run(runs_root / name / f"{dirs.get(variant, variant)}_r{rep}", case, variant)
                for rep in REPS for variant in VARIANTS}
        out["cases"][name] = {"config": {k: v for k, v in case.items()}, "runs": runs,
                              "summary": classify(case, runs)}
    counted = [c["summary"] for c in out["cases"].values()
               if c["summary"]["result"] not in ("pendente", "base não reproduz")]
    out["totals"] = {
        "cases": len(CASES),
        "corrigido": sum(s["result"] == "corrigido" for s in counted),
        "nao_corrigido": sum(s["result"] == "não corrigido" for s in counted),
        "inconclusivo_cobertura": sum(s["result"] == "inconclusivo (cobertura)" for s in counted),
        "base_nao_reproduz": sum(c["summary"]["result"] == "base não reproduz" for c in out["cases"].values()),
        "pendente": sum(c["summary"]["result"] == "pendente" for c in out["cases"].values()),
    }
    (HERE / "results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    t = out["totals"]
    lines = [
        "# Validação dinâmica: RVSec no APK base e no APK corrigido",
        "",
        "Plano: `docs/PLANO_VALIDACAO_DINAMICA.md`, seção 1. Os dois APKs de cada caso saem do mesmo "
        "commit (o que o pipeline usou para o patch), com a mesma tarefa Gradle; o corrigido tem o diff "
        "do pipeline aplicado sem alteração. RVSec: `monkey:fast,ape:sata`, 600 s, jca_android, 3 repetições.",
        "",
        f"Corrigido: {t['corrigido']}; não corrigido: {t['nao_corrigido']}; inconclusivo (cobertura): "
        f"{t['inconclusivo_cobertura']}; base não reproduz: {t['base_nao_reproduz']}; pendente: {t['pendente']} "
        f"(de {t['cases']} casos).",
        "",
        "| Caso | Spec | Base r1 / r2 / r3 | Corrigido r1 / r2 / r3 | Resultado |",
        "|---|---|---|---|---|",
    ]
    for name, data in out["cases"].items():
        runs = data["runs"]
        lines.append(f"| {name} | {data['config']['spec']} | "
                     + " / ".join(cell(runs[f"base_r{r}"]) for r in REPS) + " | "
                     + " / ".join(cell(runs[f"patched_r{r}"]) for r in REPS) + " | "
                     + data["summary"]["result"] + (" (incompleto)" if data["summary"]["incomplete"]
                                                    and data["summary"]["result"] != "pendente" else "")
                     + " |")
    lines += [
        "",
        "Legenda: `V` violação da spec no método; `V*` violação da spec no método com outra mensagem "
        "(conta como violação na tabela do plano); `-` nenhuma; `cov`/`nocov` método executado ou não "
        "(RVSEC-COV); `missing`/`unfinished`/`unexplorable` execução ausente, interrompida ou que o RVSec "
        "não conseguiu instrumentar/instalar.",
        "",
    ]
    for name, data in out["cases"].items():
        lines.append(f"## {name}")
        lines.append("")
        note = data["config"].get("note")
        if note:
            lines.append(f"Nota: {note}")
            lines.append("")
        for key, run in data["runs"].items():
            if run["state"] != "done":
                lines.append(f"- {key}: {run['state']}" + (f" ({run.get('detail', '').strip()})" if run.get("detail") else ""))
                continue
            tasks = "; ".join(f"{task['tool']} {task['state']} {task['methods_reached']} métodos"
                              + (f" (site: {len(task['site_methods_reached'])})" if task["site_methods_reached"] else "")
                              for task in run["tasks"])
            lines.append(f"- {key}: violação={'sim' if run['violation'] else 'não'}"
                         f" ({', '.join(run['violating_tools']) or '-'}), coberto={'sim' if run['covered'] else 'não'}"
                         f" ({', '.join(run['covered_by']) or '-'}); {tasks}")
            for message in run["messages"][:5]:
                lines.append(f"  - {message}")
        lines.append("")
    (HERE / "results.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:16 + len(CASES)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
