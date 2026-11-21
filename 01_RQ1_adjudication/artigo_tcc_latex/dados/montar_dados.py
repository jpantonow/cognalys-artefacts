"""Reconstrói os CSVs de artigo_tcc_latex/dados/ a partir das fontes do repositório.

Rodar na raiz do repositório, no WSL com ~/venv ativo:
    python3 artigo_tcc_latex/dados/montar_dados.py            # usa o retrato dados/filas_rvsec_cognalys_results.json
    python3 artigo_tcc_latex/dados/montar_dados.py --coletar  # antes, refaz `cognalys results <run> --json` por run

Somente leitura sobre data/, outputs/ e artigo_tcc_latex/amostra/inventario.json. Grava em artigo_tcc_latex/dados/:
    campanha_estudo02_109_usos.csv, estudo_de_caso_34_achados.csv, prs_publicados_49.csv, filas_rvsec_apps.csv
"""
import collections
import csv
import glob
import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AMOSTRA = os.path.join("artigo_tcc_latex", "amostra")
DADOS = os.path.join("artigo_tcc_latex", "dados")
REPORT = "outputs/prs_reports/20261118_2258"
RESULTS = os.path.join(DADOS, "filas_rvsec_cognalys_results.json")
EXPERIMENTAL_RUN = re.compile(r"(ab\d+|research|valB)", re.I)

ESTADO_PR = {"merged": "incorporado (merge)", "manual": "incorporado manualmente",
             "maintainer": "fechado pelo mantenedor", "open": "aberto"}


# ---------------------------------------------------------------- utilidades
def rcsv(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def rjson(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def rjsonl(p):
    out = []
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def wcsv(name, rows, cols):
    p = os.path.join(DADOS, name)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
    print(f"{p}: {len(rows)} linhas")


def repo_of_url(u):
    m = re.search(r"github\.com/([^/]+)/([^/#?]+)", u or "")
    if not m:
        return ""
    return f"{m.group(1)}/{m.group(2).removesuffix('.git')}".lower()


def key4(apk, cls, meth, spec):
    return (apk, cls, meth, spec)


def key3(repo, cls, meth, spec):
    return (repo.lower(), cls, meth, spec)


def split_unique_msg(u):
    parts = (u or "").split(":::")
    return (parts[0], parts[1], parts[2]) if len(parts) >= 3 else None


def j(xs):
    return " | ".join(x for x in xs if x)


# ---------------------------------------------------------------- PRs e issues publicados
def load_publications():
    """Índices de PRs e issues publicados, com estado em 18/11 (relatório 20261118_2258)."""
    summ = rjson(f"{REPORT}/summary_rvsec.json")
    eleg = {p["url"]: p["category"] for p in summ["prs"]}
    coll = rjson(f"{REPORT}/collection.json")
    coll_state, pr_created = {}, {}
    for p in coll["prs"]:
        pr = p["pr"]
        coll_state[pr["html_url"]] = ("merged (fora dos 49 elegíveis)" if pr.get("merged_at")
                                      else f"{pr['state']} (fora dos 49 elegíveis)")
        pr_created[pr["html_url"]] = (pr.get("created_at") or "")[:10]

    def pr_state(url):
        if url in eleg:
            st = ESTADO_PR.get(eleg[url], eleg[url])
        else:
            st = coll_state.get(url, "estado não coletado em 18/11")
        return f"{st} (criado {pr_created[url]})" if pr_created.get(url) else st

    inv = rjson(os.path.join(AMOSTRA, "inventario.json"))
    # índice repo+classe+método+spec -> {url: fonte}
    pr_idx = collections.defaultdict(dict)
    pr_idx_apk = collections.defaultdict(set)
    for p in inv["prs"]:
        for c in p.get("identity_candidates", []):
            pr_idx[key3(repo_of_url(p["url"]), c["class"], c["method"], c["spec"])][p["url"]] = "inventario.json"
            pr_idx_apk[key4(c["apk"], c["class"], c["method"], c["spec"])].add(p["url"])
    for f in inv["findings"]:
        for u in f.get("prs", []):
            pr_idx[key3(repo_of_url(u), f["class"], f["method"], f["spec"])].setdefault(u, "inventario.json")
            pr_idx_apk[key4(f["apk"], f["class"], f["method"], f["spec"])].add(u)
    for path in sorted(glob.glob("data/pr_outcomes/*.json")):
        for r in rjson(path):
            u = r.get("pull_request", "")
            if u and r.get("csv_class") and r.get("method_name") and r.get("spec"):
                pr_idx[key3(repo_of_url(u), r["csv_class"], r["method_name"], r["spec"])].setdefault(u, path)
    created_dirs = (glob.glob("outputs/rvsec_*/run*/pull_requests")
                    + glob.glob("outputs/rvsec_*/*/run*/pull_requests")
                    + glob.glob("outputs/rvsec_*/*/*/run*/pull_requests")
                    + glob.glob("outputs/exp_final_20261019/*/pull_requests"))
    for d in created_dirs:
        for c in glob.glob(os.path.join(d, "*_pr_created.json")):
            meta = c.replace("_pr_created.json", "_pr.json")
            try:
                cr = rjson(c)
                mt = rjson(meta) if os.path.exists(meta) else {}
            except Exception:  # noqa: BLE001
                continue
            u = cr.get("html_url", "")
            if u and mt.get("csv_class"):
                pr_idx[key3(repo_of_url(u), mt["csv_class"], mt["method_name"], mt["spec"])].setdefault(u, c)

    # issues
    isum = rjson(f"{REPORT}/issues_summary.json")["issues"]
    iss_info = {i["url"]: i for i in isum}
    iss_idx = collections.defaultdict(dict)
    iss_idx_apk = collections.defaultdict(dict)  # (apk, método, spec), registros sem finding_id
    linked = set()
    created = (glob.glob("outputs/rvsec_*/run*/issues/*_created.json")
               + glob.glob("outputs/rvsec_*/*/run*/issues/*_created.json")
               + glob.glob("outputs/exp_final_20261019/*/issues/*_created.json"))
    for c in created:
        try:
            cr = rjson(c)
        except Exception:  # noqa: BLE001
            continue
        if cr.get("dry_run") or not cr.get("html_url"):
            continue
        k = split_unique_msg(cr.get("finding_id"))
        if k:
            iss_idx[key3(repo_of_url(cr["html_url"]), *k)][cr["html_url"]] = c
            linked.add(cr["html_url"])
            continue
        # sem finding_id: o prefixo do arquivo é a posição do achado no findings_index.json da run
        m = re.match(r"(\d+)_", os.path.basename(c))
        idxp = os.path.join(os.path.dirname(os.path.dirname(c)), "findings_index.json")
        if m and os.path.exists(idxp):
            fl = rjson(idxp).get("findings", [])
            n = int(m.group(1))
            if n < len(fl) and os.path.basename(c)[len(m.group(0)):].startswith(
                    re.sub(r"[^A-Za-z0-9]", "_", fl[n]["method"])):
                iss_idx_apk[(fl[n]["apk"], fl[n]["method"], fl[n]["spec"])][cr["html_url"]] = c
                linked.add(cr["html_url"])

    def iss_state(url):
        i = iss_info.get(url)
        if not i:
            return "estado não coletado em 18/11"
        s = i["state"]
        if s == "closed":
            s += " pelo mantenedor" if i.get("closed_by_maintainer") else f" por {i.get('closed_by') or '?'}"
        return f"{s} (criada {i.get('created', '')})"

    return dict(eleg=eleg, pr_state=pr_state, pr_idx=pr_idx, pr_idx_apk=pr_idx_apk, inv=inv,
                iss_info=iss_info, iss_idx=iss_idx, iss_idx_apk=iss_idx_apk, pr_created=pr_created, iss_linked=linked, iss_state=iss_state,
                coll_state=coll_state)


def match_issue_by_title(pub, repo, cls, meth, spec):
    """Issue-relato sem registro local de criação: vínculo pelo título (repo + método ou classe)."""
    out = []
    simple = cls.split(".")[-1].split("$")[0]
    for u, i in pub["iss_info"].items():
        if i.get("kind") != "report" or u in pub["iss_linked"] or repo_of_url(u) != repo.lower():
            continue
        t = i.get("title", "")
        if (meth not in ("<init>", "<clinit>") and re.search(rf"\b{re.escape(meth)}\b", t)) or \
                re.search(rf"\b{re.escape(simple)}\b", t):
            out.append(u)
    return out


def publications_for(pub, repo, cls, meth, spec, apk=""):
    """PRs/issues pelo repo+classe+método+spec, mais os vínculos por APK (inventário; registros de issue
    sem finding_id), que cobrem repositórios renomeados."""
    k = key3(repo, cls, meth, spec)
    prs = sorted(set(pub["pr_idx"].get(k, {})) | pub["pr_idx_apk"].get(key4(apk, cls, meth, spec), set()))
    issues = sorted(set(pub["iss_idx"].get(k, {})) | set(pub["iss_idx_apk"].get((apk, meth, spec), {})))
    by_title = [u for u in match_issue_by_title(pub, repo, cls, meth, spec) if u not in issues]
    return prs, issues, by_title


def pub_cols(pub, prs, issues, by_title):
    return {
        "prs_publicados": j(prs),
        "estado_prs_17_11": j(f"{u.rsplit('/', 1)[-1]}: {pub['pr_state'](u)}" for u in prs),
        "pr_entre_49_elegiveis": "sim" if any(u in pub["eleg"] for u in prs) else ("não" if prs else ""),
        "issues_publicadas": j(issues + by_title),
        "estado_issues_17_11": j(f"{u.rsplit('/', 1)[-1]}: {pub['iss_state'](u)}" for u in issues + by_title),
        "vinculo_issue": ("registro *_issue_created.json" if issues else "") +
                         (" + " if issues and by_title else "") +
                         ("título da issue (repo + método/classe; não distingue a spec)" if by_title else ""),
    }




# ---------------------------------------------------------------- coleta de `cognalys results`
def coletar_resultados(timeout=180):
    """`cognalys results <run> --json` para cada diretório de run sob outputs/rvsec_* (retomável)."""
    import datetime
    import subprocess
    runs = {p.rstrip("/") for p in glob.glob("outputs/rvsec_*/run*/") if os.path.isdir(p)}
    for pat in ("outputs/rvsec_*/*/*/run_inputs.json", "outputs/rvsec_*/*/*/*/run_inputs.json"):
        for p in glob.glob(pat):
            if "/application_results/" not in p:
                runs.add(os.path.dirname(p))
    data = rjson(RESULTS) if os.path.exists(RESULTS) else {}
    for i, run in enumerate(sorted(runs), 1):
        if data.get(run, {}).get("ok"):
            continue
        rec = {"ok": False, "erro": "", "findings": [],
               "coletado_em": datetime.datetime.now().isoformat(timespec="seconds")}
        try:
            cp = subprocess.run(["cognalys", "results", run, "--json"], capture_output=True, text=True,
                                timeout=timeout)
            if cp.returncode not in (0, 1) or not cp.stdout.strip():
                rec["erro"] = f"exit {cp.returncode}: {cp.stderr.strip()[-300:]}"
            else:
                rec["ok"], rec["findings"] = True, json.loads(cp.stdout).get("findings", [])
        except subprocess.TimeoutExpired:
            rec["erro"] = f"timeout {timeout}s"
        except Exception as e:  # noqa: BLE001
            rec["erro"] = f"{type(e).__name__}: {e}"[:300]
        data[run] = rec
        print(f"[{i}/{len(runs)}] {run} ok={rec['ok']} n={len(rec['findings'])} {rec['erro'][:80]}", flush=True)
        with open(RESULTS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- campanha estudo02 e os 34
def campanha(pub):
    raw = rcsv("data/raw/exp_novo/20260925_misuses-selected.csv")
    tri = rcsv("data/processed/exp_final/triagem_109.csv")
    assert len(raw) == len(tri) == 109
    for a, b in zip(raw, tri):
        assert (a["id"], a["apk"], a["class"], a["method"], a["spec"]) == \
               (b["id"], b["apk"], b["class"], b["method"], b["spec"]), a["id"]
    nat = rcsv("data/processed/exp_final/findings_nativos.csv")
    und = rcsv("data/processed/exp_final/findings_nativos_undecided.csv")
    ef = rcsv("data/processed/exp_final/findings_exp_final.csv")
    cons = rjson("outputs/exp_final_20261019/consolidated/findings.json")
    K = lambda r: key4(r["apk"], r["class"], r["method"], r["spec"])  # noqa: E731
    nat_k, und_k = {K(r) for r in nat}, {K(r) for r in und}
    ef_by = {K(r): r for r in ef}
    cons_by = {K(r): r for r in cons}
    nativos = {K(t) for t in tri if t["veredito"] == "nativo"}
    # a passagem 109 -> 34 é a triagem: os 34 'nativo' são exatamente a entrada e o consolidado
    assert nativos == set(ef_by) == (nat_k | und_k) == set(cons_by), "109 -> 34 não fecha"

    rows109, rows34 = [], []
    for a, t in zip(raw, tri):
        k = K(a)
        entrou = k in ef_by
        r = {c: a[c] for c in ("id", "app", "apk", "class", "method", "spec", "origin", "outcome", "runs",
                               "every_run", "events", "n_tools", "tools", "codes", "labels", "values",
                               "llm_review_verdict", "also_severe")}
        r.update(repo=t["repo"], commit=t["commit"], triagem_veredito=t["veredito"],
                 triagem_motivo=t["motivo"], triagem_nota=t["nota_uso"],
                 entrou_no_estudo_de_caso="sim" if entrou else "não",
                 arquivo_de_entrada=("findings_nativos.csv" if k in nat_k else
                                     "findings_nativos_undecided.csv" if k in und_k else ""))
        if entrou:
            c = cons_by[k]
            r.update(desfecho_outubro=c.get("outcome", ""), desfecho_detalhe=c.get("outcome_detail", ""))
        rows109.append(r)
        if entrou:
            e = ef_by[k]
            prs, iss, byt = publications_for(pub, t["repo"], a["class"], a["method"], a["spec"], a["apk"])
            rows34.append({
                "id_campanha": a["id"], "apk": a["apk"], "repo": t["repo"], "classe": a["class"],
                "metodo": a["method"], "spec": a["spec"], "mensagem": e["message"],
                "outcome_campanha": a["outcome"], "rotulo_detector": e.get("detector_label", ""),
                "local_no_fonte": e.get("detector_source_location", ""), "exploradores_campanha": a["tools"],
                "desfecho_outubro": c.get("outcome", ""), "desfecho_detalhe": c.get("outcome_detail", ""),
                "execucao_consolidada": c.get("run", ""), "decisao": c.get("decision", ""),
                "impacto_seguranca": c.get("security_impact", ""),
                **pub_cols(pub, prs, iss, byt),
            })
    wcsv("campanha_estudo02_109_usos.csv", rows109,
         ["id", "app", "apk", "repo", "class", "method", "spec", "origin", "outcome", "runs", "every_run",
          "events", "n_tools", "tools", "codes", "labels", "values", "llm_review_verdict", "also_severe",
          "triagem_veredito", "triagem_motivo", "triagem_nota", "entrou_no_estudo_de_caso",
          "arquivo_de_entrada", "desfecho_outubro", "desfecho_detalhe", "commit"])
    wcsv("estudo_de_caso_34_achados.csv", rows34,
         ["id_campanha", "apk", "repo", "classe", "metodo", "spec", "mensagem", "outcome_campanha",
          "rotulo_detector", "local_no_fonte", "exploradores_campanha", "desfecho_outubro", "desfecho_detalhe",
          "execucao_consolidada", "decisao", "impacto_seguranca", "prs_publicados", "estado_prs_17_11",
          "pr_entre_49_elegiveis", "issues_publicadas", "estado_issues_17_11", "vinculo_issue"])
    return rows109, rows34


# ---------------------------------------------------------------- filas RVSec próprias (outputs/rvsec_*)
# ---------------------------------------------------------------- planilha 2: filas
def run_dirs_under(unit, all_runs, nested_units):
    out = []
    for r in all_runs:
        if not r.startswith(unit + "/"):
            continue
        if any(r.startswith(n + "/") for n in nested_units if n != unit and n.startswith(unit + "/")):
            continue
        out.append(r)
    return out


def run_time(r):
    for f in ("run_inputs.json", "findings_index.json"):
        p = os.path.join(r, f)
        if os.path.exists(p):
            return os.path.getmtime(p)
    return os.path.getmtime(r)


def is_experimental(r):
    return bool(EXPERIMENTAL_RUN.search(os.path.basename(r)))


def parse_rvsec_out(unit):
    p = os.path.join(unit, "rvsec_run.out")
    st = collections.defaultdict(lambda: {"completed": [], "problem": ""})
    if not os.path.exists(p):
        return None
    for line in open(p, encoding="utf-8", errors="replace"):
        m = re.match(r"task (\S+) rep \d+ (\S+): COMPLETED", line)
        if m:
            st[m.group(1)]["completed"].append(m.group(2).rstrip(":"))
        m = re.match(r"PROBLEM (\S+?):? (.*)", line)
        if m:
            st[m.group(1)]["problem"] = m.group(2).strip()[:200]
    return st



def origem_da_pasta(top, data, gh):
    if re.match(r"rvsec_(new|short|interop|selecao|tls|pcapdroid|misuse\d?|sslctx|ncpasswords|readyou|whobird)(_|$)",
                top) and (not data or data <= "2026-10-18"):
        return "lote manual inicial (12-18/10, driver.sh), antes da fila automática"
    if top.startswith("rvsec_lote"):
        return "lote manual (22/10, go.sh por app)"
    if gh:
        return "fila automática (queue.sh), candidato do GitHub"
    return "fila automática (queue.sh), candidato do F-Droid"


def filas(pub, rows34):
    results = rjson(RESULTS) if os.path.exists(RESULTS) else {}
    all_runs = sorted(results)
    camp_repos = {r["repo"].lower() for r in rcsv("data/processed/exp_final/triagem_109.csv")}
    k34 = {(r["classe"], r["metodo"], r["spec"]) for r in rows34}

    # unidade = onde houve build/exploração (sources.csv ou build.log): a pasta rvsec_* ou uma subpasta de
    # lote. Subpastas só com findings.jsonl + run (reanálises, saídas brutas do RVSec) pertencem à mãe.
    is_unit = lambda d: any(os.path.exists(os.path.join(d, f)) for f in ("sources.csv", "build.log"))  # noqa: E731
    units, infra = [], []
    for top in sorted(glob.glob("outputs/rvsec_*")):
        if not os.path.isdir(top):
            continue
        subs = [d for d in sorted(glob.glob(top + "/*")) if os.path.isdir(d) and is_unit(d)]
        if is_unit(top):
            units.append(top)
        units.extend(subs)
        if not is_unit(top) and not subs:
            infra.append(top)
    unit_set = set(units)

    apps, achados = [], []
    for u in units:
        top = u.split("/")[1]
        m = re.search(r"_(\d{8})$", top)
        data = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else ""
        src = rcsv(u + "/sources.csv") if os.path.exists(u + "/sources.csv") else []
        gh = open(u + "/github.repo").read().strip() if os.path.exists(u + "/github.repo") else ""
        apks_dir = set(os.listdir(u + "/apks")) if os.path.isdir(u + "/apks") else set()
        outst = parse_rvsec_out(u)
        f_all = rjsonl(u + "/findings_all.jsonl")
        f_app_raw = rjsonl(u + "/findings.jsonl")
        f_app = [f for f in f_app_raw if f.get("origin", "app") == "app"]
        runs = run_dirs_under(u, all_runs, unit_set)
        main_runs = sorted((r for r in runs if not is_experimental(r)), key=run_time)
        exp_runs = [r for r in runs if is_experimental(r)]
        apk_list = [s["apk"] for s in src if s.get("apk")]
        for a in sorted(apks_dir) + sorted(outst or {}) + sorted({f["apk"] for f in f_app_raw + f_all}):
            if a.endswith(".apk") and a not in apk_list:
                apk_list.append(a)
        if not apk_list:
            apk_list = [""]
        by_apk_src = {s["apk"]: s for s in src}
        build_failed = os.path.exists(u + "/build.failed")
        build_log = open(u + "/build.log", errors="replace").read() if os.path.exists(u + "/build.log") else ""
        unexpl = open(u + "/rvsec.unexplorable", errors="replace").read().strip()[:200] \
            if os.path.exists(u + "/rvsec.unexplorable") else ""
        origem = origem_da_pasta(top, data, gh)
        for apk in apk_list:
            s = by_apk_src.get(apk, {})
            repo_url = gh or s.get("repo_url", "") or (f_app_raw[0].get("repo_url", "") if f_app_raw else "")
            pkg = (s.get("manifest_package", "").split(";")[0]) or re.sub(r"(\.debug)?_\d+\.apk$", "", apk)
            ap_all = [f for f in f_all if f.get("apk") == apk]
            ap_app = [f for f in f_app if f.get("apk") == apk]
            st = (outst or {}).get(apk)
            if apk and apk in apks_dir:
                built = "sim"
            elif apk and apk in build_log:
                built = "sim (APK já removido de apks/; build.log registra o APK)"
            elif apk and st and st["completed"]:
                built = "sim (APK já removido; inferido da exploração RVSec)"
            elif build_failed:
                built = "não (build.failed)"
            elif not apk:
                built = "não (nenhum APK registrado em sources.csv/build.log)"
            else:
                built = "não encontrado em apks/"
            if st and st["completed"]:
                expl = "sim (" + ",".join(st["completed"]) + ")"
            elif unexpl:
                expl = "não (rvsec.unexplorable: " + unexpl + ")"
            elif st and st["problem"]:
                expl = "não (" + st["problem"] + ")"
            elif ap_all or ap_app:
                expl = "sim (sem rvsec_run.out)"
            elif outst is None:
                expl = "não (sem rvsec_run.out)"
            else:
                expl = "não registrado"
            notas = []
            if repo_of_url(repo_url) in camp_repos:
                notas.append("app também na campanha estudo02")
            if any((f["class"], f["method"], f["spec"]) in k34 for f in ap_app):
                notas.append("achado coincide com um dos 34 (classe+método+spec)")
            desfechos = collections.Counter()
            for f in ap_app:
                outc, det, run_used = "", "", ""
                for r in reversed(main_runs or exp_runs):
                    res = results.get(r)
                    if not res or not res.get("ok"):
                        continue
                    hit = [x for x in res["findings"] if x.get("csv_class") == f["class"]
                           and x.get("method") == f["method"] and x.get("spec") == f["spec"]
                           and x.get("apk", apk) == apk]
                    if hit:
                        outc, det, run_used = hit[0].get("outcome", ""), hit[0].get("detail", ""), r
                        break
                if not runs:
                    outc = "sem cognalys run"
                elif not outc:
                    outc = ("sem resultado legível" if not any(results.get(r, {}).get("ok") for r in runs)
                            else "não analisado nas runs (fora do subconjunto da run)")
                desfechos[outc] += 1
                prs, iss, byt = publications_for(pub, repo_of_url(repo_url) or f.get("repo", ""),
                                                 f["class"], f["method"], f["spec"], apk)
                achados.append({
                    "pasta": u.removeprefix("outputs/"), "apk": apk, "pacote": pkg, "repo_url": repo_url,
                    "origem_da_pasta": origem, "spec": f["spec"], "classe": f["class"], "metodo": f["method"],
                    "mensagem": f.get("message", ""), "explorador": f.get("explorer") or f.get("tool", ""),
                    "desfecho": outc, "desfecho_detalhe": det,
                    "run_do_desfecho": run_used.removeprefix(u + "/") if run_used else "",
                    "prs": prs, "issues": iss + byt,
                })
            apps.append({
                "pasta": u.removeprefix("outputs/"), "apk": apk, "pacote": pkg, "repo_url": repo_url,
                "data": data, "origem_da_pasta": origem, "nota_origem": j(notas),
                "apk_construido": built, "rvsec_explorado": expl,
                "achados_total": len(ap_all) if os.path.exists(u + "/findings_all.jsonl") else "n/d (sem findings_all.jsonl)",
                "achados_do_app": len(ap_app),
                "cognalys_run": "sim" if runs else "não",
                "ultima_run": main_runs[-1].removeprefix(u + "/") if main_runs else "",
                "runs": j(r.removeprefix(u + "/") for r in main_runs),
                "runs_experimentais": j(r.removeprefix(u + "/") for r in exp_runs),
                "runs_sem_resultado_legivel": j(r.removeprefix(u + "/") for r in runs if not results.get(r, {}).get("ok")),
                "desfechos_dos_achados_do_app": j(f"{k}: {v}" for k, v in desfechos.most_common()),
                "prs_publicados": j(sorted({p for a in achados if a["pasta"] == u.removeprefix("outputs/")
                                            and a["apk"] == apk for p in a["prs"]})),
            })
    for t in infra:
        apps.append({"pasta": t.removeprefix("outputs/"), "origem_da_pasta": "scripts/logs das filas (sem app)",
                     "cognalys_run": "não"})
    wcsv("filas_rvsec_apps.csv", apps,
         ["pasta", "apk", "pacote", "repo_url", "data", "origem_da_pasta", "nota_origem", "apk_construido",
          "rvsec_explorado", "achados_total", "achados_do_app", "cognalys_run", "ultima_run",
          "desfechos_dos_achados_do_app", "prs_publicados", "runs", "runs_experimentais",
          "runs_sem_resultado_legivel"])
    return apps, achados, infra


# ---------------------------------------------------------------- os 49 PRs elegíveis
def prs49(pub, rows34, achados):
    inv_prs = {p["url"]: p for p in pub["inv"]["prs"]}
    outcomes = {}
    for path in sorted(glob.glob("data/pr_outcomes/*.json")):
        for r in rjson(path):
            outcomes.setdefault(r.get("pull_request", ""), r)
    rows = []
    for url in pub["eleg"]:
        ip = inv_prs.get(url, {})
        cands = ip.get("identity_candidates", [])
        metas = [m for c in cands for m in c.get("metadata", [])]
        l34 = [r for r in rows34 if url in r["prs_publicados"].split(" | ")]
        lq = [a for a in achados if url in a["prs"]]
        meta_q = [m for m in metas if m.startswith("outputs/rvsec_")]
        if meta_q or (lq and not l34):
            origem = "filas"
            criterio = ("metadados do PR em " + meta_q[0].split("/")[1]) if meta_q else \
                "achado das filas com mesmo repo+classe+método+spec"
        elif l34:
            origem = "estudo_de_caso_34"
            criterio = ("metadados do PR em " + "/".join(metas[0].split("/")[:3])) if metas else \
                "achado dos 34 com mesmo APK ou repo+classe+método+spec"
        else:
            origem = "sem_vinculo"
            criterio = "nenhum achado das filas nem dos 34 com a identidade do PR" + (
                "; metadados do PR em " + "/".join(metas[0].split("/")[:3]) if metas else
                "; sem identidade de achado no inventário" if not cands else "; sem metadados locais")
        # identidade do achado: candidatos do inventário; senão data/pr_outcomes
        oc = outcomes.get(url, {})
        apk = j(sorted({c["apk"] for c in cands}))
        cls = j(sorted({c["class"] for c in cands})) or oc.get("csv_class", "")
        meth = j(sorted({c["method"] for c in cands})) or oc.get("method_name", "")
        spec = j(sorted({c["spec"] for c in cands})) or oc.get("spec", "")
        msg = ""
        for m in metas:
            if m.endswith(".json") and os.path.exists(m):
                try:
                    msg = rjson(m).get("error_message", "") or msg
                except (ValueError, OSError, AttributeError):
                    pass
        if not msg and lq:
            msg = lq[0]["mensagem"]
        if not msg and l34:
            msg = l34[0]["mensagem"]
        pastas = sorted({m.split("/")[1] for m in meta_q} | {a["pasta"].split("/")[0] for a in lq})
        rows.append({
            "url": url, "repo": "/".join(url.split("/")[3:5]), "numero": url.rsplit("/", 1)[-1],
            "estado_18_11": ESTADO_PR.get(pub["eleg"][url], pub["eleg"][url]),
            "criado_em": pub["pr_created"].get(url, ""),
            "estado_registrado_inventario": ip.get("recorded_status", ""),
            "origem": origem, "criterio_origem": criterio,
            "achado_tambem_no_estudo_de_caso": "sim" if l34 else "não",
            "achado_tambem_nas_filas": "sim" if lq else "não",
            "sobrepoe_34_no_inventario": "sim" if ip.get("overlaps_run34") else "não",
            "app": j(sorted({a["pacote"] for a in lq})) or j(sorted({r["apk"] for r in l34})),
            "apk": apk or j(sorted({a["apk"] for a in lq} | {r["apk"] for r in l34})),
            "classe": cls, "metodo": meth, "spec": spec, "mensagem": msg,
            "pasta_rvsec_origem": j(pastas),
            "desfecho_cognalys_nas_filas": j(sorted({f"{a['desfecho']} ({a['run_do_desfecho']})" for a in lq})),
            "id_campanha_34": j(r["id_campanha"] for r in l34),
            "desfecho_outubro_34": j(sorted({r["desfecho_outubro"] for r in l34})),
            "identidade_no_inventario": ip.get("identity_status", ""),
        })
    order = {"estudo_de_caso_34": 0, "filas": 1, "sem_vinculo": 2}
    rows.sort(key=lambda r: (order[r["origem"]], r["repo"].lower(), int(r["numero"])))
    wcsv("prs_publicados_49.csv", rows,
         ["url", "repo", "numero", "estado_18_11", "criado_em", "origem", "criterio_origem",
          "achado_tambem_no_estudo_de_caso", "achado_tambem_nas_filas", "sobrepoe_34_no_inventario", "app", "apk",
          "classe", "metodo", "spec", "mensagem", "pasta_rvsec_origem", "desfecho_cognalys_nas_filas",
          "id_campanha_34", "desfecho_outubro_34", "estado_registrado_inventario", "identidade_no_inventario"])
    return rows


def main():
    import sys
    os.chdir(ROOT)
    os.makedirs(DADOS, exist_ok=True)
    if "--coletar" in sys.argv:
        coletar_resultados()
    pub = load_publications()
    rows109, rows34 = campanha(pub)
    apps, achados, infra = filas(pub, rows34)
    prs = prs49(pub, rows34, achados)
    # conferências impressas (não gravadas): contagens para o README e o relatório
    C = collections.Counter
    print("109 -> triagem:", dict(C(r["triagem_veredito"] for r in rows109)))
    print("34 desfechos:", dict(C(r["desfecho_outubro"] for r in rows34)))
    print("34 com PR:", sum(bool(r["prs_publicados"]) for r in rows34),
          "com issue:", sum(bool(r["issues_publicadas"]) for r in rows34))
    print("49 PRs por origem:", dict(C(r["origem"] for r in prs)))
    print("49 PRs origem x estado:", dict(C((r["origem"], r["estado_18_11"]) for r in prs)))
    print("PRs com sobreposição aos 34 no inventário:", sum(r["sobrepoe_34_no_inventario"] == "sim" for r in prs))
    app_rows = [a for a in apps if a.get("apk") is not None]
    for nome, sel in (("todas", app_rows),
                      ("fila automática", [a for a in app_rows if a["origem_da_pasta"].startswith("fila")])):
        print(nome, "linhas", len(sel), "pacotes", len({a["pacote"] for a in sel}),
              "construído", sum(a["apk_construido"].startswith("sim") for a in sel),
              "explorado", sum(a["rvsec_explorado"].startswith("sim") for a in sel),
              ">=1 achado do app", sum(int(a["achados_do_app"]) > 0 for a in sel),
              "cognalys", sum(a["cognalys_run"] == "sim" for a in sel),
              "achados do app", sum(int(a["achados_do_app"]) for a in sel))
    print("desfechos achados (filas automáticas):",
          dict(C(a["desfecho"] for a in achados if a["origem_da_pasta"].startswith("fila")).most_common()))
    print("desfechos achados (todos):", dict(C(a["desfecho"] for a in achados).most_common()))
    inv = pub["inv"]["counts"]
    print("inventário:", inv["pr_records"], "PRs;", inv["overlapping_pr_findings"], "overlapping;",
          inv["union_findings_resolved"], "achados /", inv["union_apks_resolved"], "APKs; adicionais",
          inv["union_findings_resolved"] - inv["run_findings"])
    extra = [f for f in pub["inv"]["findings"] if not f.get("run34")]
    miss = [f for f in extra if not any(a["classe"] == f["class"] and a["metodo"] == f["method"]
                                        and a["spec"] == f["spec"] and a["apk"] == f["apk"] for a in achados)]
    print("achados adicionais do inventário fora das filas (mesmo APK):", len(miss))
    for f in miss:
        print("   ", f["apk"], f["class"], f["method"], f["spec"], f.get("prs"))


if __name__ == "__main__":
    main()
