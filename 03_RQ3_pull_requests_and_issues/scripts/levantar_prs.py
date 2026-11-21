"""Read-only, paginated GitHub inventory for the TCC PR report."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
REPORTS = ROOT / "outputs/prs_reports"
# Set by --date (YYYYMMDD, default today): each survey keeps its own snapshot.
STAMP = dt.date.today().strftime("%Y%m%d")
OUT = REPORTS / STAMP


def token():
    value = os.environ.get("GITHUB_TOKEN", "").strip()
    if value:
        return value
    path = ROOT / ".env.github"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"(?:export\s+)?GITHUB_TOKEN\s*=\s*(.*)", line.strip())
            if match:
                return match[1].strip().strip("\"'")
    return ""


TOKEN = token()


def get(url):
    headers = {"User-Agent": "Cognalys-PR-report", "Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
                return json.load(response), response.headers.get("Link", "")
        except urllib.error.HTTPError as error:
            if error.code in (429, 502, 503) and attempt < 2:
                time.sleep(2 + attempt * 2)
                continue
            raise RuntimeError(f"GitHub HTTP {error.code}: {url}") from None


def pages(url):
    rows = []
    while url:
        data, links = get(url)
        rows.extend(data)
        match = re.search(r'<([^>]+)>; rel="next"', links)
        url = match[1] if match else ""
    return rows


def search():
    OUT.mkdir(parents=True, exist_ok=True)
    query = urllib.parse.urlencode({"q": "author:jpantonow is:pr created:>=2026-01-01", "per_page": 100,
                                    "sort": "created", "order": "desc"})
    first, _ = get("https://api.github.com/search/issues?" + query)
    rows = first["items"]
    page = 2
    while len(rows) < first["total_count"]:
        batch, _ = get("https://api.github.com/search/issues?" + query + "&page=" + str(page))
        if not batch["items"]:
            raise RuntimeError("Empty pagination before total_count")
        rows.extend(batch["items"])
        page += 1
    if first.get("incomplete_results"):
        raise RuntimeError("GitHub returned incomplete search results")
    snapshot = {"queried_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "author": "jpantonow", "year": 2026, "total_count": first["total_count"], "items": rows}
    (OUT / "search.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Authenticated:", bool(TOKEN), "PRs:", len(rows), flush=True)
    for row in rows:
        print(row["state"], "merged" if row.get("pull_request", {}).get("merged_at") else "",
              row["html_url"], row["title"], flush=True)


def collect():
    snapshot = json.loads((OUT / "search.json").read_text(encoding="utf-8"))
    def inspect(item):
        slug = item["html_url"].split("github.com/", 1)[1].replace("/pull/", "__").replace("/", "__")
        target = OUT / "prs" / (slug + ".json")
        # Reuse only records fetched in this report invocation's current search snapshot.
        if target.exists():
            cached = json.loads(target.read_text(encoding="utf-8"))
            if cached["queried_at_utc"] >= snapshot["queried_at_utc"]:
                return cached
        api = item["pull_request"]["url"]
        live, _ = get(api)
        row = {"queried_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "pr": live,
               "events": pages(item["events_url"] + "?per_page=100"),
               "comments": pages(live["comments_url"] + "?per_page=100"),
               "reviews": pages(api + "/reviews?per_page=100")}
        target.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return row
    (OUT / "prs").mkdir(exist_ok=True)
    results, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(inspect, row): row["html_url"] for row in snapshot["items"]}
        for future in concurrent.futures.as_completed(futures):
            try:
                row = future.result()
                results.append(row)
                print("OK", row["pr"]["html_url"], flush=True)
            except Exception as error:
                failures.append({"url": futures[future], "error": str(error)})
                print("FAILED", futures[future], str(error), flush=True)
    (OUT / "collection.json").write_text(json.dumps({"prs": results, "failures": failures}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Collected", len(results), "Failures", len(failures), flush=True)


def classify(row):
    pr = row["pr"]
    closed = [e for e in row["events"] if e.get("event") == "closed"]
    actor = (closed[-1].get("actor") or {}) if closed else {}
    if pr.get("merged"):
        return "merged", (pr.get("merged_by") or {}).get("login", "")
    if pr["state"] == "open":
        return "open", ""
    if pr["html_url"] == "https://github.com/BombusMod/BombusMod/pull/316":
        return "manual", actor.get("login", "")
    if actor.get("login", "").lower() == "jpantonow":
        return "author", actor["login"]
    if actor.get("type") == "Bot" or actor.get("login", "").endswith("[bot]"):
        return "bot", actor.get("login", "")
    return ("maintainer" if actor else "unknown"), actor.get("login", "")


CATEGORY_LABEL = {"merged": "mergeado", "manual": "incorporado manualmente",
                  "maintainer": "fechado pelo mantenedor", "open": "aberto",
                  "author": "fechado pelo autor", "bot": "fechado por bot"}


# Not maintainer decisions: withdrawn by the author, or closed by a bot.
OUTSIDE_THE_SURVEY = {"author", "bot"}


def changes_since_previous(rows, rvsec_only=False):
    """What moved since the latest earlier snapshot's summary (summary_rvsec.json
    for the RVSec report, falling back to summary.json for snapshots before it)."""
    names = ("summary_rvsec.json", "summary.json") if rvsec_only else ("summary.json",)
    earlier = []
    for directory in sorted(path for path in REPORTS.iterdir() if path.is_dir() and path.name < STAMP):
        found = next((directory / name for name in names if (directory / name).exists()), None)
        if found:
            earlier.append(found)
    if not earlier:
        return []
    previous = json.loads(earlier[-1].read_text(encoding="utf-8"))
    before = {item["url"].lower(): item["category"] for item in previous["prs"]}
    moved, new = [], []
    for row in sorted(rows, key=lambda r: r["pr"]["html_url"].lower()):
        url = row["pr"]["html_url"]
        repo, number = url.split("github.com/", 1)[1].split("/pull/")
        name = f"[{repo}#{number}]({url})"
        now = classify(row)[0]
        if now in OUTSIDE_THE_SURVEY:
            continue
        if url.lower() not in before:
            new.append(f"{name} ({CATEGORY_LABEL[now]})")
        elif before[url.lower()] != now:
            moved.append(f"- {name}: {CATEGORY_LABEL[before[url.lower()]]} → {CATEGORY_LABEL[now]}.")
    shown = previous.get("report_date", earlier[-1].parent.name)
    lines = [f"## Mudanças desde o levantamento anterior ({shown})", ""]
    lines += moved or ["- Nenhum PR já levantado mudou de situação."]
    if new:
        lines.append(f"- Novos PRs ({len(new)}): " + "; ".join(new) + ".")
    return lines + [""]


# Where each PR's finding came from: the `tool` of its finding, read from the
# publication record's run (pr_tools.json, written by origins()). These were
# published from runs whose records are not under outputs/ any more; their
# tool was found in another run's execution report.
TOOL_OVERRIDES = {
    "https://github.com/quantumbadger/redreader/pull/1373": "ape",
    "https://github.com/waboodoo/http-shortcuts/pull/576": "ape",
    "https://github.com/lneugebauer/nextcloud-cookbook/pull/204": "ape",
    "https://github.com/sapuseven/betteruntis/pull/515": "ape",
    "https://github.com/woheller69/omweather/pull/187": "static:manual",
    "https://github.com/woheller69/whobird/pull/144": "rvsec",
}
NOT_RVSEC_TOOLS = {"static:manual", "static:semgrep", "web_research", "manual", "manual_review"}
ORIGIN_LABEL = {"static:manual": "candidato manual", "static:semgrep": "Semgrep",
                "web_research": "pesquisa web"}


def origins():
    """pr_tools.json: the finding tool of every PR with a publication record under outputs/;
    pr_impact.json: its impact class (cognalys.prs.impact_class)."""
    import subprocess
    from cognalys.prs.impact_class import published_impact_class
    files = subprocess.run(["find", str(ROOT / "outputs"), "-path", "*/.claude", "-prune", "-o",
                            "-path", "*pull_requests*", "-name", "*_pr_created.json", "-print"],
                           capture_output=True, text=True).stdout.split()
    tools, impacts = {}, {}
    for path in files:
        try:
            record = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        match = re.search(r"https://github\.com/[^\"/]+/[^\"/]+/pull/\d+", json.dumps(record))
        if record.get("dry_run") or not match:
            continue
        impacts.setdefault(match[0].lower(), set()).add(published_impact_class(Path(path)))
        report_path = Path(path).parent.parent / "application_results" / "execution_report.json"
        try:
            details = json.loads(report_path.read_text(encoding="utf-8")).get("per_solution_details", [])
        except (OSError, ValueError):
            details = []
        name = Path(path).name
        tool = next((s.get("tool") for s in details if s.get("apk_name") and name.startswith(s["apk_name"])
                     and str(s.get("method_name", "")).strip("<>") in name), None)
        if tool:
            tools.setdefault(match[0].lower(), set()).add(tool)
    (OUT / "pr_tools.json").write_text(json.dumps({k: sorted(v) for k, v in sorted(tools.items())}, indent=1) + "\n",
                                       encoding="utf-8")
    (OUT / "pr_impact.json").write_text(json.dumps({k: sorted(v) for k, v in sorted(impacts.items())}, indent=1)
                                        + "\n", encoding="utf-8")
    print("origins:", len(tools), "impact classes:", len(impacts))


def finding_tool(url, tools):
    url = url.lower()
    if url in TOOL_OVERRIDES:
        return TOOL_OVERRIDES[url]
    found = tools.get(url) or []
    if not found:
        raise RuntimeError(f"No recorded finding origin for {url}; add it to TOOL_OVERRIDES")
    return found[0]


def is_rvsec(url, tools):
    return finding_tool(url, tools) not in NOT_RVSEC_TOOLS


IMPACT_LABEL = {"security_impact": "com impacto de segurança",
                "no_security_impact": "sem impacto de segurança (correção da regra)",
                "impact_not_established": "impacto não estabelecido",
                "not_recorded": "não registrada"}


def impact_of(url, impacts):
    """The impact class of a PR from pr_impact.json; not_recorded when no record has one."""
    known = sorted(set(impacts.get(url.lower()) or []) - {"not_recorded"})
    if len(known) > 1:
        raise RuntimeError(f"Publication records of {url} disagree on the impact class: {known}")
    return known[0] if known else "not_recorded"


def incorporated_by_impact(prs, impacts):
    """Incorporated PRs (merged + manual) of a summary's `prs` list, by impact class."""
    from collections import Counter
    counts = Counter({name: 0 for name in IMPACT_LABEL})
    counts.update(impact_of(item["url"], impacts) for item in prs
                  if item["category"] in ("merged", "manual"))
    return dict(counts)


def report(rvsec_only=False):
    from collections import Counter
    data = json.loads((OUT / "collection.json").read_text(encoding="utf-8"))
    search_data = json.loads((OUT / "search.json").read_text(encoding="utf-8"))
    if data["failures"] or len(data["prs"]) != search_data["total_count"]:
        raise RuntimeError("Incomplete collection; do not publish complete-report counts")
    rows = sorted(data["prs"], key=lambda r: r["pr"]["created_at"], reverse=True)
    if len({r["pr"]["id"] for r in rows}) != len(rows):
        raise RuntimeError("Duplicate PR identity in collected results")
    collected = len(rows)
    other_incorporated = []
    by_impact = {}
    if rvsec_only:
        # Only the PRs of RVSec findings; of the others, the incorporated ones
        # are counted in a note, and the open or closed ones are left out.
        tools = json.loads((OUT / "pr_tools.json").read_text(encoding="utf-8"))
        rows = [r for r in rows if classify(r)[0] not in OUTSIDE_THE_SURVEY]
        others = [r for r in rows if not is_rvsec(r["pr"]["html_url"], tools)]
        other_incorporated = [r for r in others if classify(r)[0] in ("merged", "manual")]
        rows = [r for r in rows if is_rvsec(r["pr"]["html_url"], tools)]
        impact_path = OUT / "pr_impact.json"
        if not impact_path.exists():
            raise RuntimeError("No pr_impact.json; rerun the origins stage")
        by_impact = incorporated_by_impact(
            [{"url": r["pr"]["html_url"], "category": classify(r)[0]} for r in rows],
            json.loads(impact_path.read_text(encoding="utf-8")))
    counts = Counter(classify(r)[0] for r in rows)
    if counts["unknown"]:
        raise RuntimeError("Unresolved closure actors")
    descriptions = {}
    for line in (ROOT / "docs/relatorio_prs.md").read_text(encoding="utf-8").splitlines():
        match = re.search(r"\((https://github.com/[^)]+/pull/\d+)\)", line)
        if match and line.startswith("|"):
            descriptions[match[1].lower()] = line.split("|")[2].strip()
    descriptions.update({
        "https://github.com/leekleak/traffic-light/pull/238": "Lê chaves secretas do AndroidKeyStore com getKey",
        "https://github.com/tokn-authenticator/tokn/pull/138": "Lê chaves do AndroidKeyStore com getKey",
        "https://github.com/christianrowlands/android-network-survey/pull/154": "Usa getKey ao recuperar a chave de criptografia",
        "https://github.com/michaelwuensch/bitbanana/pull/184": "Migra verificadores do bloqueio do app para PBKDF2-HMAC-SHA256",
        "https://github.com/jenspfahl/anotherpass/pull/103": "Usa getKey para recuperar chaves secretas do Android",
        "https://github.com/schorschii/droid_scep/pull/2": "Valida tamanhos de chave RSA antes do enrollment",
        "https://github.com/domi04151309/homeapp/pull/114": "Opção nas configurações para aceitar certificados não confiáveis no WebView",
        "https://github.com/freeotp/freeotp-android/pull/521": "Teste de ida e volta da chave derivada por SecretKeyFactory",
        "https://github.com/freeotp/freeotp-android/pull/524": "Exige chave AES antes de inicializar a cifração",
        "https://github.com/itzskyeyt/intertune/pull/52": "SHA-256 nos hashes das capas do widget",
        "https://github.com/paroj/dsub2000/pull/178": "Trust managers da plataforma nas conexões HTTPS",
        "https://github.com/patzly/grocy-android/pull/1012": "Inicializa o SSLContext TLS uma única vez",
        "https://github.com/patzly/grocy-android/pull/1013": "Inicializa keystores ausentes antes de gravar entradas de chave",
        "https://github.com/vitorpamplona/amethyst/pull/4642": "IVs aleatórios na cifração AES-GCM",
        "https://github.com/woheller69/omweather/pull/187": "Impede que a restauração de backup escreva fora da pasta de destino",
        "https://github.com/yogeshpaliyal/keypass/pull/1252": "Usa o AndroidKeyStore explicitamente na geração da chave AES",
    })
    notes = {
        "christianrowlands/android-network-survey/154": "O mantenedor considera getEntry e getKey equivalentes para essa chave; a mudança só satisfaz a regra CrySL e deixaria outra cópia do código diferente.",
        "christianrowlands/android-network-survey/149": "Superado pelo rework v2: LocalBroadcastManager foi removido e os fragments modificados deixaram de existir.",
        "VREMSoftwareDevelopment/WiFiAnalyzer/618": "O mantenedor considera o MD5 um checksum sem papel de segurança neste uso.",
        "javalc6/rssreader/4": "O checksum detecta corrupção acidental; o mantenedor não considera a troca por SHA-256 uma melhoria de segurança nesse contexto.",
        "woheller69/whoBIRD/144": "O mantenedor considera MD5 suficiente para a verificação dos modelos.",
        "SapuSeven/BetterUntis/515": "MD5 gera nomes de cache; o mantenedor também rejeita contribuições geradas por IA.",
        "QuantumBadger/RedReader/1373": "O mantenedor questiona a premissa de segurança do uso do SHA-1 e a mudança de algoritmo mantendo o nome sha1.",
        "Wilm0r/giggity/543": "O mantenedor já havia feito mudança equivalente; considerou a limpeza útil.",
        "HiddenRamblings/TagMo/765": "A depuração de WebView é intencional.",
        "anenasa/androidtv-news/14": "A depuração de WebView é intencional.",
        "Chiu-xaH/HFUT-Schedule/109": "Concorda que o token não é necessário, mas prefere preservar código que funciona por estabilidade.",
        "omaawr/tuisku/11": "O projeto não aceita código assistido por IA; o mantenedor também questionou a necessidade da canonicalização proposta.",
        "idunnololz/summit/54": "O projeto não está aceitando PRs; o fechamento não é um parecer técnico sobre o patch.",
        "bitcraze/crazyflie-android-client/103": "A revisão pediu remover o helper TLS obsoleto; a versão revista foi mergeada.",
        "hanjoongcho/aaf-easydiary/253": "O mantenedor agradeceu a correção de Zip Slip, testou e fez o merge.",
        "nextcloud/news-android/1716": "Merge confirmado no GitHub.",
        "paroj/DSub2000/160": "Merge confirmado; sugestões automáticas não são revisão humana de segurança.",
        "Waboodoo/HTTP-Shortcuts/576": "Merge confirmado no GitHub.",
        "lneugebauer/nextcloud-cookbook/204": "Merge confirmado no GitHub.",
        "mretallack/SlimBook/3": "Merge após revisão.",
        "jimvixx/ShortMessageSecure/83": "A revisão preservou HmacSHA1 e passou a explicitar o tamanho de 160 bits, em vez de trocar unilateralmente o algoritmo.",
        "ounben/AMARadio/122": "O mantenedor aceitou a troca do digest usado nos nomes dos arquivos de cache.",
        "leekleak/traffic-light/238": "Merge confirmado; o mantenedor agradeceu.",
        "tokn-authenticator/tokn/138": "Merge confirmado; o colaborador responsável agradeceu.",
        "BombusMod/BombusMod/316": "Incorporação manual confirmada em comentário; a mensagem de commit foi reescrita. O PR está fechado e merged=false na API.",
        "spacecowboy/Feeder/1210": "O mantenedor se opõe ao impacto na compatibilidade com sites. Um revisor externo apoia a proposta; isso não equivale à aprovação do mantenedor.",
        "Domi04151309/HomeApp/114": "Aceitar certificados autoassinados era intencional; a revisão pôs essa aceitação atrás de uma opção nas configurações, como o mantenedor sugeriu, e foi mergeada.",
        "freeotp/freeotp-android/521": "Merge de um patch só de teste: o mantenedor considerou o teste válido. O código de produção do finding não mudou, então não conta como correção do uso.",
        "jenspfahl/ANOTHERpass/103": "O mantenedor agradeceu, disse não conhecer a prática e que o código atual não expõe vulnerabilidade; aprovou e fez o merge.",
        "ItzSkyeYT/InterTune/52": "O hash só nomeia os arquivos de capa em cache, sem papel de segurança; trocar exigiria migrar arquivos sem ganho.",
        "paroj/DSub2000/178": "Retirado pelo autor depois de o mantenedor observar que pular a verificação de certificados é o propósito da opção, e que o caminho padrão já é seguro.",
        "vitorpamplona/amethyst/4642": "O provedor já gera um IV aleatório quando init recebe só a chave; o mantenedor demonstrou isso no JDK 21. O patch também alterava a implementação desktop.",
        "woheller69/omweather/187": "Marcado como not planned: para o mantenedor, só um backup adulterado pelo próprio usuário chegaria ao caso.",
        "yogeshpaliyal/KeyPass/1252": "Só comentários automáticos (CodeRabbit e boas-vindas); sem revisão humana registrada.",
        "Valent-in/Auto-Shine/12": "O mantenedor pediu um aviso (\"Wrong numeric value\") em vez de ignorar a entrada inválida; a revisão acrescentou o aviso e o PR foi mergeado.",
        "freeotp/freeotp-android/524": "Correção no código de produção (chave AES exigida, conversão PBKDF2 para SecretKeySpec AES) com testes de compatibilidade; o mantenedor testou backup/restore antes do merge.",
        "jimvixx/ShortMessageSecure/89": "O mantenedor observou que o SHA-1 só gera IDs estáveis do RecyclerView, sem papel criptográfico, e considerou a troca por SHA-256 uma melhoria razoável; pediu ajustar a mensagem do AssertionError, a revisão fez o ajuste e ele mergeou.",
        "SilentCoderHere/aihub/279": "Mergeado pelo mantenedor sem comentário ou revisão registrados.",
        "gigabytedevelopers/CometOTP/114": "O mantenedor agradeceu o achado e a descrição; como o master só aceita commits assinados, rebaseou e assinou o commit do lado deles, mantendo a autoria.",
        "FliegendeWurst/TriliumDroid/177": "O mantenedor agradeceu a limpeza; o código removido vinha de uma versão anterior da lógica.",
        "Crustack/NotallyX/1167": "O mantenedor aprovou (\"thanks for the improvement, LGTM\") e fez o merge; o CodeRabbit comentou, sem revisão humana além da aprovação.",
        "FossifyOrg/Gallery/1145": "Fechado automaticamente: o projeto exige vínculo com issue marcada como help wanted. Não conta como decisão humana sobre o patch.",
    }
    def esc(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")
    def display_date(value):
        if not value:
            return "—"
        # UTC dates suffice for an unambiguous event timeline; the report date is client-local.
        return value[:10]
    eligible = sum(counts[k] for k in ("merged", "manual", "maintainer", "open"))
    incorporated = counts["merged"] + counts["manual"]
    shown_date = f"{STAMP[6:8]}/{STAMP[4:6]}/{STAMP[:4]}"
    lines = ["# Levantamento de pull requests — Cognalys / TCC", "",
             f"Atualizado em **{shown_date}**. Consulta autenticada e paginada à API do GitHub para PRs de **@jpantonow criados em 2026**, com conferência individual de estado, merge, eventos de fechamento, comentários e revisões formais. As {len(rows)} consultas individuais foram concluídas sem falhas.", "",
             "## Resumo", "", "| Situação atual | PRs |", "|---|---:|",
             f"| Merge pelo GitHub | {counts['merged']} |", f"| Incorporado manualmente, sem merge do PR | {counts['manual']} |",
             f"| Fechado pelo mantenedor, sem incorporação identificada | {counts['maintainer']} |", f"| Aberto | {counts['open']} |",
             f"| **Total** | **{eligible}** |", "",
             f"São **{incorporated} mudanças incorporadas**: {counts['merged']} merges e {counts['manual']} incorporação manual. Os {eligible} PRs são uma contagem de contribuições, não de findings independentes; o cruzamento com os findings precisa manter as sobreposições.", "",
             "Entram os PRs decididos pelos mantenedores ou ainda abertos. PRs fechados pelo próprio autor (tentativas retiradas ou substituídas) e fechados por bot não são decisões dos mantenedores e ficam fora do levantamento. Em PRs fechados sem merge, o ator foi lido no último evento de fechamento. As datas das tabelas são as datas UTC registradas pelo GitHub.", ""]
    if rvsec_only:
        merged_note = (f" (obs.: {len(other_incorporated)} PRs não RVSec incorporados)"
                       if other_incorporated else "")
        lines = ["# Levantamento de pull requests do RVSec — Cognalys / TCC", "",
                 f"Atualizado em **{shown_date}**. Consulta autenticada e paginada à API do GitHub para PRs de **@jpantonow criados em 2026**, com conferência individual de estado, merge, eventos de fechamento, comentários e revisões formais. As {collected} consultas individuais foram concluídas sem falhas.", "",
                 "Entram somente os PRs cujos achados vieram do RVSec: o app executado sob o RVSec, com ape, monkey, humanoid, DroidBot ou ARES conduzindo a interface. A origem é a coluna `tool` do achado de cada PR, lida no registro de publicação e no relatório de execução da run. PRs de candidatos manuais, de regras Semgrep e de pesquisa web ficam fora; os que foram incorporados aparecem só como observação.", "",
                 "## Resumo", "", "| Situação atual | PRs do RVSec |", "|---|---:|",
                 f"| Merge pelo GitHub | {counts['merged']}{merged_note} |",
                 f"| Incorporado manualmente, sem merge do PR | {counts['manual']} |",
                 f"| Fechado pelo mantenedor, sem incorporação identificada | {counts['maintainer']} |", f"| Aberto | {counts['open']} |",
                 f"| **Total** | **{eligible}** |", "",
                 f"São **{incorporated} mudanças incorporadas** de achados do RVSec: {counts['merged']} merges e {counts['manual']} incorporação manual. Os {eligible} PRs são uma contagem de contribuições, não de findings independentes.", ""]
        if other_incorporated:
            listed = "; ".join(
                f"[{r['pr']['html_url'].split('github.com/', 1)[1].replace('/pull/', '#')}]({r['pr']['html_url']}) "
                f"({ORIGIN_LABEL.get(finding_tool(r['pr']['html_url'], tools), finding_tool(r['pr']['html_url'], tools))})"
                for r in other_incorporated)
            lines += [f"Observação: {len(other_incorporated)} PRs de outras origens foram incorporados e não entram nas contagens: {listed}.", ""]
        lines += ["PRs fechados pelo próprio autor (tentativas retiradas ou substituídas) e fechados por bot não são decisões dos mantenedores e ficam fora. Em PRs fechados sem merge, o ator foi lido no último evento de fechamento. As datas das tabelas são as datas UTC registradas pelo GitHub.", ""]
        lines += ["## Incorporados por classe de impacto", "",
                  "| Classe de impacto (adjudicação) | PRs incorporados |", "|---|---:|"]
        lines += [f"| {IMPACT_LABEL[name]} | {by_impact[name]} |" for name in IMPACT_LABEL]
        lines += ["", "A classe é o campo `runtime_security_impact` da adjudicação do achado, gravado no registro do PR ou, nos PRs publicados antes desse registro, lido na adjudicação da run que publicou o PR. Sem adjudicação encontrada, ou quando a adjudicação não avaliou o impacto (`unknown`), fica \"não registrada\".", ""]
    lines += changes_since_previous(rows, rvsec_only)
    sections = [("merged", "Mergeados no GitHub"), ("manual", "Incorporado manualmente"),
                ("maintainer", "Fechados pelos mantenedores sem merge"), ("open", "Abertos")]
    for category, title in sections:
        selected = [r for r in rows if classify(r)[0] == category]
        lines += [f"## {title} ({len(selected)})", ""]
        if category == "open":
            lines += ["| PR | Mudança | Aberto em | Revisão / observação |", "|---|---|---|---|"]
        else:
            lines += ["| PR | Mudança | Responsável / data | Observação |", "|---|---|---|---|"]
        for row in selected:
            pr = row["pr"]
            repo, number = pr["html_url"].split("github.com/", 1)[1].split("/pull/")
            identity = f"{repo}/{number}"
            note = notes.get(identity)
            if note is None:
                human = [c for c in row["comments"] + row["reviews"]
                         if c.get("user", {}).get("login") != "jpantonow" and c.get("user", {}).get("type") != "Bot"]
                if category == "open":
                    note = "Sem comentário humano ou revisão formal de terceiros registrados." if not human else "Há interação humana registrada; ver conversa e revisões do PR."
                elif category == "author":
                    note = "Retirado pelo autor; não classificado como rejeição do mantenedor."
                else:
                    note = "Ver eventos e conversa do PR."
            event_date = display_date(pr.get("merged_at") or pr.get("closed_at"))
            actor = classify(row)[1]
            when = display_date(pr["created_at"]) if category == "open" else f"@{actor} · {event_date}"
            desc = descriptions.get(pr["html_url"].lower(), pr["title"])
            lines.append(f"| [{repo}#{number}]({pr['html_url']}) | {esc(desc)} | {when} | {esc(note)} |")
        lines.append("")
    lines += ["## Leitura dos resultados", "",
              "- Aceitação pelo mantenedor e merge não comprovam, sozinhos, correção de segurança ou eliminação do relato original do detector.",
              "- Fechamento humano reúne motivos diferentes: contexto sem risco demonstrado, comportamento intencional, mudança já feita upstream, compatibilidade e política de contribuição.",
              "- Os casos de Android Network Survey #154 e Tokn/Traffic Light/ANOTHERpass mostram respostas distintas a mudanças relacionadas ao AndroidKeyStore; não devem ser tratados como uma regra geral de aceitação ou rejeição.",
              "- FreeOTP #521 foi mergeado, mas só acrescenta um teste; não deve ser contado como correção do uso reportado.",
              "- PRs abertos têm resultado de adoção pendente. Revisão externa e comentário automático ficam separados de aprovação do mantenedor.", "",
              "## Fontes e reprodução", "",
              "Cada link das tabelas aponta para o PR consultado. O estado `merged` foi conferido no recurso do PR; o ator de fechamento veio dos eventos da issue. BombusMod foi separado com base na confirmação explícita de incorporação manual do mantenedor.", "",
              f"- [Busca paginada e contagem de origem](../outputs/prs_reports/{STAMP}/search.json)",
              f"- [Estados, eventos, comentários e revisões coletados](../outputs/prs_reports/{STAMP}/collection.json)",
              f"- Registros individuais: `outputs/prs_reports/{STAMP}/prs/`.",
              f"- Script: `scripts/levantar_prs.py --date {STAMP}`, etapas `search`, `collect` e `report`. A coleta usa somente leituras do GitHub e não publica comentários nem altera PRs.", ""]
    target = ROOT / (f"docs/relatorio_prs_rvsec_{STAMP}.md" if rvsec_only else f"docs/relatorio_prs_{STAMP}.md")
    target.write_text("\n".join(lines), encoding="utf-8")
    (OUT / ("summary_rvsec.json" if rvsec_only else "summary.json")).write_text(json.dumps({"report_date": f"{STAMP[:4]}-{STAMP[4:6]}-{STAMP[6:]}", "counts": dict(counts), "eligible": eligible,
                                                 **({"incorporated_by_impact": by_impact} if rvsec_only else {}),
                                                 "prs": [{"url": r["pr"]["html_url"], "category": classify(r)[0], "actor": classify(r)[1]} for r in rows]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target, dict(counts), "eligible", eligible)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["search", "collect", "origins", "report", "report-rvsec"])
    parser.add_argument("--date", default=STAMP, help="snapshot YYYYMMDD (default today)")
    args = parser.parse_args()
    STAMP, OUT = args.date, REPORTS / args.date
    {"search": search, "collect": collect, "origins": origins, "report": report,
     "report-rvsec": lambda: report(rvsec_only=True)}[args.stage]()
