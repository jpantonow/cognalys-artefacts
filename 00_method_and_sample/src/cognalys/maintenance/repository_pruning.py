"""Report local clones by associated PR status; never remove repositories.

Callers must hold RepositoryUsageLock while collecting the inventory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urlparse

import requests

from cognalys.solutions.worktree_manager import WorktreeManager


class InventoryError(RuntimeError):
    pass


_PROGRESS = ContextVar("pruning_progress", default=None)


class ProgressReporter:
    """Flush stage changes and heartbeat even while a disk/HTTP call waits."""
    def __init__(self, interval=10):
        self.interval = interval
        self.started = self.changed = time.monotonic()
        self.message = "Iniciando relatório"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def update(self, message):
        with self.lock:
            self.message, self.changed = message, time.monotonic()
            print(f"[relatório +{self.changed - self.started:.0f}s] {message}", flush=True)

    def _heartbeat(self):
        while not self.stop.wait(self.interval):
            with self.lock:
                elapsed = time.monotonic()
                print(f"[relatório +{elapsed - self.started:.0f}s] Em andamento: "
                      f"{self.message} (etapa há {elapsed - self.changed:.0f}s)", flush=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=1)


def progress(message):
    reporter = _PROGRESS.get()
    if reporter is not None:
        reporter.update(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def git(repo, *args):
    if args[0] in {"ls-remote", "status"}:
        progress(f"Git {args[0]}: {Path(repo).name}")
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args], cwd=repo,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=60,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode:
        # Remote URLs/errors may contain credentials; keep diagnostics local.
        raise InventoryError(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def safe_path(path, root):
    """Check lexical ancestors AND resolved containment before inspecting a repository."""
    path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    for part in (path, *path.parents):
        if linked(part):
            raise InventoryError(f"Linked path is not eligible: {part}")
    resolved, base = path.resolve(), root.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise InventoryError(f"Path is not strictly inside allowed root: {path}")
    return resolved


def local_path(value, workspace):
    """Translate Windows/WSL paths only when the mapping is unambiguous."""
    text = str(value).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", text) and os.name != "nt":
        if not Path("/mnt", text[0].lower()).is_dir():
            raise InventoryError("Windows path has no mounted drive mapping")
        text = f"/mnt/{text[0].lower()}/{text[3:]}"
    elif os.name == "nt" and re.match(r"^/mnt/[a-zA-Z]/", text):
        text = text[5].upper() + ":/" + text[7:]
    elif os.name == "nt" and text.startswith("/"):
        raise InventoryError("Unmapped POSIX path in metadata")
    p = Path(text)
    return (p if p.is_absolute() else workspace / p).resolve()


def github_slug(url):
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", url,
    )
    if not match:
        raise InventoryError("Remote is not an unambiguous GitHub repository")
    return match[1].lower()


class GitHub:
    def __init__(self, token):
        if not token:
            raise InventoryError("GITHUB_TOKEN is required to verify PR ownership")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })
        self.login = str(self.get("https://api.github.com/user").get("login", "")).lower()
        if not re.fullmatch(r"[a-z0-9-]+", self.login):
            raise InventoryError("Could not establish authenticated GitHub identity")

    def response(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise InventoryError("Untrusted GitHub API URL")
        try:
            response = self.session.get(url, timeout=(10, 45), allow_redirects=False)
        except requests.RequestException as exc:
            raise InventoryError("GitHub request failed or timed out") from exc
        if response.status_code != 200:
            raise InventoryError(f"GitHub consultation incomplete (HTTP {response.status_code})")
        return response

    def get(self, url):
        try:
            data = self.response(url).json()
        except ValueError as exc:
            raise InventoryError("Invalid GitHub JSON") from exc
        if not isinstance(data, dict):
            raise InventoryError("Unexpected GitHub response shape")
        return data

    def repository(self, slug):
        progress(f"GitHub: identificando {slug}")
        return self.get(f"https://api.github.com/repos/{slug}")

    def pulls(self, slug):
        url = f"https://api.github.com/repos/{slug}/pulls?state=all&per_page=100"
        rows, seen = [], set()
        while url:
            if url in seen:
                raise InventoryError("GitHub pagination cycle")
            seen.add(url)
            progress(f"GitHub: PRs de {slug}, página {len(seen)}")
            response = self.response(url)
            try:
                page = response.json()
            except ValueError as exc:
                raise InventoryError("Invalid GitHub PR JSON") from exc
            if not isinstance(page, list) or any(not isinstance(p, dict) for p in page):
                raise InventoryError("Invalid GitHub PR list")
            rows.extend(page)
            url = response.links.get("next", {}).get("url", "")
        return rows

    def pull(self, slug, number):
        progress(f"GitHub: conferindo {slug}#{number}")
        return self.get(f"https://api.github.com/repos/{slug}/pulls/{number}")


class GitHubSnapshot:
    """Share read-only API results within one inventory/group."""
    def __init__(self, api):
        self.api, self.login = api, api.login
        self.repositories, self.pr_lists, self.prs = {}, {}, {}

    def repository(self, slug):
        if slug not in self.repositories:
            self.repositories[slug] = self.api.repository(slug)
        return self.repositories[slug]

    def pulls(self, slug):
        if slug not in self.pr_lists:
            self.pr_lists[slug] = self.api.pulls(slug)
        return self.pr_lists[slug]

    def pull(self, slug, number):
        key = (slug, number)
        if key not in self.prs:
            self.prs[key] = self.api.pull(slug, number)
        return self.prs[key]


def pr_state(pr):
    if type(pr.get("number")) is not int or pr["number"] < 1:
        raise InventoryError("PR number is missing or invalid")
    if pr.get("state") not in {"open", "closed"} or "merged_at" not in pr:
        raise InventoryError("PR state or merge information is missing")
    if pr["merged_at"] is not None and (not isinstance(pr["merged_at"], str) or not pr["merged_at"]):
        raise InventoryError("Invalid merge timestamp")
    if "merged" in pr and not isinstance(pr["merged"], bool):
        raise InventoryError("Invalid merge flag")
    if pr.get("merged") is True or pr["merged_at"]:
        return "merged"
    return pr["state"]


def metadata_inventory(outputs):
    """Only known artifact directories; never descend into arbitrary clones."""
    records = []
    directories = []
    artifact_names = {"pull_requests", "application_results", "error_solutions"}
    skip = {"cloned_projects", "finding_worktrees", "cleanup_reports", ".git",
            "apks", "built_apks_no_proguard", "baseline_build_cache", "build",
            "builds", "build_validation", "method_backups", "openai_prompt_logs",
            "error_methods", "error_methods_tracked", "external_research_sources",
            "logs", "previews", "issues", "prs_reports", "v2_artifacts"}
    def walk_error(error):
        raise InventoryError(f"Inventory directory could not be read: {error.filename}")

    for current, dirs, _ in os.walk(outputs, followlinks=False, onerror=walk_error):
        here = Path(current)
        if (here / ".git").exists():
            dirs[:] = []
            continue
        kept = []
        for name in dirs:
            child = here / name
            if name in artifact_names:
                directories.append(child)
            elif name not in skip and not name.startswith("."):
                if linked(child):
                    raise InventoryError(f"Linked inventory directory: {child}")
                kept.append(name)
        dirs[:] = kept
    for index, directory in enumerate(sorted(directories), 1):
        if index == 1 or index % 20 == 0:
            progress(f"Inventário: pasta de artefatos {index}/{len(directories)}")
        name = directory.name
        safe_path(directory, outputs)
        files = []
        for current, dirs, names in os.walk(directory, followlinks=False, onerror=walk_error):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for child in dirs:
                safe_path(Path(current) / child, outputs)
            files.extend(Path(current) / n for n in names if (
                (name == "pull_requests" and (n.endswith("_pr.json") or n.endswith("_pr_created.json")))
                or (name == "error_solutions" and n.endswith("_solution.json"))
                or (name == "application_results" and n == "execution_report.json")
            ))
        for p in sorted(files):
            safe_path(p, outputs)
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError) as exc:
                raise InventoryError(f"Unreadable metadata: {p}") from exc
            if name == "pull_requests":
                if p.name.endswith("_pr_created.json"):
                    if not isinstance(data, dict):
                        raise InventoryError(f"Invalid PR receipt: {p}")
                    if data.get("dry_run") is not True:
                        records.append({"kind": "receipt", "data": data, "file": str(p)})
                elif p.name.endswith("_pr.json"):
                    if not isinstance(data, dict):
                        raise InventoryError(f"Invalid PR metadata: {p}")
                    if not all(isinstance(data.get(k), str) and data[k] for k in (
                            "repo_owner", "repo_name", "repository", "branch")):
                        raise InventoryError(f"Incomplete PR metadata: {p}")
                    records.append({"kind": "pr", "data": data, "file": str(p)})
            else:
                if isinstance(data, dict) and "corrected_implementation" in data:
                    meta = data.get("solution_metadata")
                    corrected = data["corrected_implementation"]
                    if not isinstance(meta, dict) or not isinstance(corrected, dict) or not meta.get("apk_name"):
                        raise InventoryError(f"Unidentified generated solution: {p}")
                    if corrected.get("method_body") or corrected.get("coordinated_edits"):
                        pending = {
                            **meta, "corrected_method_body": corrected.get("method_body", ""),
                            "coordinated_edits": corrected.get("coordinated_edits", []),
                            "original_method_body": (data.get("original_error") or {}).get("method_body", ""),
                            "unique_error_id": (data.get("original_error") or {}).get("unique_error_id", ""),
                        }
                        pending["finding_id"] = WorktreeManager.finding_id(pending)
                        pending["solution_revision"] = WorktreeManager.solution_revision(pending)
                        records.append({"kind": "solution", "data": pending, "file": str(p)})
                # Pull out self-identifying solution records, including
                # ones not yet promoted to PR metadata.
                def walk(value):
                    if isinstance(value, dict):
                        if any(value.get(k) for k in ("worktree_path", "base_repository_path")):
                            records.append({"kind": "solution", "data": value, "file": str(p)})
                        for child in value.values():
                            walk(child)
                    elif isinstance(value, list):
                        for child in value:
                            walk(child)
                walk(data)
    return records


def worktrees(repo):
    text = git(repo, "worktree", "list", "--porcelain", "-z")
    paths = []
    for item in text.split("\0"):
        if item.startswith("worktree "):
            paths.append(Path(os.path.abspath(item[9:])))
        elif item.startswith("locked") or item.startswith("prunable"):
            raise InventoryError("Locked or stale worktree registration")
    if not paths or repo.resolve() not in paths:
        raise InventoryError("Cannot establish repository worktree inventory")
    return paths


def identity(repo, api):
    remotes = git(repo, "remote").splitlines()
    if not remotes:
        raise InventoryError("Repository has no remotes")
    slugs = {github_slug(git(repo, "remote", "get-url", remote)) for remote in remotes}
    upstreams = set()
    for slug in slugs:
        info = api.repository(slug)
        if not isinstance(info.get("fork"), bool):
            raise InventoryError("Incomplete repository identity")
        original = info.get("parent") if info["fork"] else info
        full_name = (original or {}).get("full_name", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
            raise InventoryError("Cannot establish upstream repository")
        upstreams.add(full_name.lower())
    if len(upstreams) != 1:
        raise InventoryError("Remotes identify different upstream repositories")
    return upstreams.pop(), slugs


def relevant_records(records, repo, trees, slug, slugs, workspace):
    selected = []
    for record in records:
        data = record["data"]
        match = False
        owner, name = data.get("repo_owner"), data.get("repo_name")
        if owner and name:
            match = f"{owner}/{name}".lower() in slugs | {slug}
        if record["kind"] == "receipt":
            base = ((data.get("base") or {}).get("repo") or {}).get("full_name", "")
            match |= base.lower() in slugs | {slug}
            # Missing identity cannot be safely assigned to another project.
            if not base:
                raise InventoryError("PR receipt is missing base repository identity")
        for key in ("repository", "base_repository_path", "worktree_path"):
            if data.get(key):
                p = local_path(data[key], workspace)
                match |= any(p == tree or p.is_relative_to(tree) for tree in trees)
        match |= data.get("apk_name") == repo.name
        if match:
            selected.append(record)
    return selected


def remote_decision(api, slug, slugs, records):
    """Classify PRs associated with the pipeline or authenticated user."""
    known, branches = set(), set()
    for record in records:
        data = record["data"]
        if record["kind"] == "receipt":
            number = data.get("number")
            if not isinstance(number, int) or number < 1:
                raise InventoryError("Invalid published PR receipt")
            known.add(number)
        if data.get("branch"):
            branches.add(str(data["branch"]))
    associated = {}
    for pr in api.pulls(slug):
        head = pr.get("head") or {}
        head_repo = head.get("repo") or {}
        author = str((pr.get("user") or {}).get("login", "")).lower()
        head_owner = str((head_repo.get("owner") or {}).get("login", "")).lower()
        head_slug = str(head_repo.get("full_name", "")).lower()
        if not author or not isinstance(pr.get("number"), int):
            raise InventoryError("Cannot establish PR author/identity")
        if (pr["number"] in known or author == api.login or head_owner == api.login
                or (head.get("ref") in branches and head_slug in slugs)):
            pr_state(pr)
            associated[pr["number"]] = pr
    # Direct lookup both fills pagination discrepancies and confirms receipts.
    for number in known:
        pr = api.pull(slug, number)
        if pr.get("number") != number:
            raise InventoryError("PR identity mismatch")
        pr_state(pr)
        associated[number] = pr
    prs = [{"number": p["number"], "url": f"https://github.com/{slug}/pull/{p['number']}",
            "status": pr_state(p), "branch": (p.get("head") or {}).get("ref", ""),
            "head_sha": (p.get("head") or {}).get("sha", "")}
           for p in associated.values()]
    if any(p["status"] == "open" for p in prs):
        return "has_open_pr", prs
    if any(p["status"] == "merged" for p in prs):
        return "merged_pr", prs
    return ("closed_pr" if prs else "no_pr"), prs


def has_pending_metadata(prs, records):
    """Flag unpublished local artifacts as information for manual review."""
    published_heads = {(p["branch"], p["head_sha"]) for p in prs if p["branch"] and p["head_sha"]}
    published_revisions = set()
    for record in records:
        d = record["data"]
        if record["kind"] == "pr" and (d.get("branch"), d.get("commit_hash")) in published_heads:
            if d.get("finding_id") and d.get("solution_revision"):
                published_revisions.add((d["finding_id"], d["solution_revision"]))
    for record in records:
        d = record["data"]
        if record["kind"] == "receipt":
            continue
        if ((d.get("branch"), d.get("commit_hash")) in published_heads
                or (d.get("finding_id"), d.get("solution_revision")) in published_revisions):
            continue
        if record["kind"] == "pr" or any(d.get(k) for k in (
                "worktree_path", "corrected_code", "corrected_method_body", "coordinated_edits")):
            return True
    return False



def estimate_bytes(paths):
    size = 0
    for path in paths:
        for current, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if not linked(Path(current) / d)]
            for name in files:
                p = Path(current) / name
                if not linked(p):
                    size += p.stat().st_size
    return size


LABELS = {
    "merged_pr": "PR mergeado",
    "closed_pr": "PR fechado sem merge",
    "no_pr": "Sem PR associado confirmado",
    "has_open_pr": "Existe PR aberto",
    "unknown": "Não verificado",
    "not_assessed": "Consulta pendente",
}
REPORT_CRITERIA = {"merged_pr", "closed_pr", "no_pr"}


def markdown_cell(value):
    import html
    return html.escape(str(value)).replace("|", "&#124;").replace("`", "&#96;").replace("\r", " ").replace("\n", "<br>")


def markdown_report(report):
    rows = report["repositories"]
    listed = [r for r in rows if r.get("action") == "listed"]
    lines = ["# Repositórios para revisão manual", "",
             f"Início (UTC): {report['started_at']}", "",
             f"Fase: {markdown_cell(report['phase'])}", "",
             "Somente consulta: nenhum clone ou worktree é apagado por este script.", "",
             "Critério: PR associado mergeado, fechado sem merge ou ausência confirmada de PR associado. "
             "Qualquer PR associado aberto exclui o projeto da lista, inclusive draft ou aguardando aprovação.", "",
             "A associação considera recibos/metadados do pipeline e PRs do usuário autenticado ou de seu fork. "
             "O status reflete o momento da consulta; alterações locais, stash e commits não publicados não são verificados.", "",
             f"## Repositórios que atendem aos critérios ({len(listed)})", "",
             "| Diretório local | Repositório | Critério | PRs associados | Worktrees adicionais | Observações |",
             "|---|---|---|---|---|---|"]
    for row in listed:
        prs = "<br>".join(f"[#{p['number']}]({p['url']}) ({p['status']})" for p in row["prs"]) or "Nenhum"
        trees = "<br>".join(markdown_cell(p) for p in row.get("worktrees", []) if p != row["path"]) or "Nenhuma"
        notes = "<br>".join(markdown_cell(n) for n in row.get("notes", [])) or "—"
        if "estimated_bytes" in row:
            notes += f"<br>Tamanho estimado: {row['estimated_bytes']} bytes"
        cells = [markdown_cell(row["path"]), markdown_cell(row.get("repository", "")),
                 LABELS[row["decision"]], prs, trees, notes]
        lines.append("| " + " | ".join(cells) + " |")
    if not listed:
        lines.extend(["", "Nenhum resultado confirmado até o momento."])
    excluded = [r for r in rows if r.get("action") not in {"listed", "outside_project_filter"}]
    lines.extend(["", f"## Fora da lista ou não verificados ({len(excluded)})", "",
                  "| Diretório local | Situação | Detalhe |", "|---|---|---|"])
    for row in excluded:
        details = row.get("reason", "")
        if row["decision"] == "has_open_pr":
            details = ", ".join(p["url"] for p in row["prs"] if p["status"] == "open") or details
        lines.append("| " + " | ".join(markdown_cell(v) for v in
                     (row["path"], LABELS[row["decision"]], details)) + " |")
    if report["errors"]:
        lines.extend(["", "## Erros", ""])
        lines.extend("- " + markdown_cell(e) for e in report["errors"])
    return "\n".join(lines) + "\n"


def atomic_report(path, report):
    # Each file is replaced atomically, including during partial inventories.
    for destination, content in (
        (path, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (path.with_suffix(".md"), markdown_report(report)),
    ):
        temp = destination.with_suffix(destination.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(destination)


def assess(repo, api, records, workspace, clones_root, worktrees_root, estimate_size=False, identity_hint=None):
    result = {"path": str(repo), "decision": "unknown", "checked_at": now(), "prs": [], "notes": []}
    try:
        progress(f"Consultando repositório: {repo.name}")
        safe_path(repo, clones_root)
        trees = worktrees(repo)
        slug, slugs = identity_hint if identity_hint is not None else identity(repo, api)
        result.update(repository=slug, worktrees=[str(p) for p in trees])
        matching = relevant_records(records, repo, trees, slug, slugs, workspace)
        decision, prs = remote_decision(api, slug, slugs, matching)
        result.update(decision=decision, prs=prs)
        if has_pending_metadata(prs, matching):
            result["notes"].append("Metadados indicam trabalho local ainda não publicado; revisar manualmente.")
        if estimate_size and decision in REPORT_CRITERIA:
            try:
                for tree in trees:
                    safe_path(tree, clones_root if tree == repo else worktrees_root)
                progress(f"Estimando espaço em disco: {repo.name}")
                result["estimated_bytes"] = estimate_bytes(trees)
            except (InventoryError, OSError) as exc:
                result["notes"].append(f"Tamanho não estimado: {exc}")
    except (InventoryError, OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError) as exc:
        result.update(decision="unknown", reason=str(exc))
    return result


def _prune(workspace, clones_root, worktrees_root, outputs_root, mode="report", project="", api=None, estimate_size=False):
    workspace = Path(workspace).resolve()
    clones_root, worktrees_root, outputs_root = map(Path, (clones_root, worktrees_root, outputs_root))
    report_root = workspace / "outputs" / "cleanup_reports"
    report_root.mkdir(parents=True, exist_ok=True)
    destination = report_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + ".json")
    report = {"started_at": now(), "mode": "report", "requested_mode": mode,
              "repositories": [], "errors": [], "phase": "starting", "estimate_size": estimate_size,
              "markdown_path": str(destination.with_suffix(".md"))}
    def save():
        atomic_report(destination, report)

    def phase(message):
        report.update(phase=message, updated_at=now())
        save()
        progress(message)

    progress(f"Modo report (somente consulta). Markdown: {destination.with_suffix('.md')}")
    if mode in {"preview", "apply"}:
        progress(f"O modo legado {mode} agora apenas gera relatório; nenhum diretório será apagado.")
    save()
    try:
        if mode not in {"report", "preview", "apply"}:
            raise InventoryError("Invalid report mode")
        for root in (clones_root, worktrees_root, outputs_root):
            safe_path(root, workspace)
        if not outputs_root.is_dir():
            raise InventoryError("Outputs inventory root does not exist")
        if clones_root.resolve() == worktrees_root.resolve() or (
                clones_root.resolve().is_relative_to(worktrees_root.resolve())
                or worktrees_root.resolve().is_relative_to(clones_root.resolve())):
            raise InventoryError("Clone and worktree roots must be disjoint")
        if report_root.resolve().is_relative_to(clones_root.resolve()) or report_root.resolve().is_relative_to(worktrees_root.resolve()):
            raise InventoryError("Reports cannot be inside repository roots")
        phase("Lendo inventário de artefatos de todas as runs")
        records = metadata_inventory(outputs_root)
        phase(f"Inventário lido: {len(records)} registros; verificando acesso ao GitHub")
        api = api or GitHub(os.getenv("GITHUB_TOKEN", ""))
        candidates = sorted(clones_root.iterdir()) if clones_root.exists() else []
        candidates = [p for p in candidates if p.is_dir() or linked(p)]
        groups, hints = {}, {}
        identity_api = GitHubSnapshot(api)
        for index, repo in enumerate(candidates, 1):
            phase(f"Identificando clone {index}/{len(candidates)}: {repo.name}")
            item = {"path": str(repo), "decision": "not_assessed", "prs": [], "indexed_at": now()}
            try:
                safe_path(repo, clones_root)
                slug, slugs = identity(repo, identity_api)
                item["repository"] = slug
                hints[str(repo)] = (slug, slugs)
                groups.setdefault(slug, []).append(item)
            except (InventoryError, OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError) as exc:
                item.update(decision="unknown", reason=str(exc))
            report["repositories"].append(item)
            if project and repo.name != project:
                item["action"] = "outside_project_filter"
            save()
        positions = {str(p): i for i, p in enumerate(candidates, 1)}
        for group_index, (slug, items) in enumerate(groups.items(), 1):
            if all(item.get("action") == "outside_project_filter" for item in items):
                continue
            phase(f"Projeto {group_index}/{len(groups)}: {slug}; {len(items)} clone(s)")
            group_api = GitHubSnapshot(api)
            for item in items:
                repo = Path(item["path"])
                phase(f"Repositório {positions[str(repo)]}/{len(candidates)}: {repo.name}")
                item.update(assess(repo, group_api, records, workspace, clones_root,
                                   worktrees_root, estimate_size, hints[str(repo)]))
                save()
            # A PR associated with any local copy belongs to this whole project.
            blockers = [r for r in items if r["decision"] in {"has_open_pr", "unknown"}]
            for item in items:
                if blockers and item["decision"] in REPORT_CRITERIA:
                    blocker = next((r for r in blockers if r["decision"] == "has_open_pr"), blockers[0])
                    item.update(decision=blocker["decision"], reason="Outra cópia do projeto tem PR aberto ou consulta incompleta.")
                if item.get("action") == "outside_project_filter":
                    continue
                item["action"] = "listed" if item["decision"] in REPORT_CRITERIA else "excluded"
                if item["action"] == "listed":
                    progress(f"{LABELS[item['decision']]}: {item['path']}")
            save()
    except KeyboardInterrupt:
        report["interrupted"] = True
        report["errors"].append("Interrompido pelo usuário; resultados parciais estão neste relatório.")
    except (InventoryError, OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError) as exc:
        report["errors"].append(str(exc))
    report["finished_at"] = now()
    report["phase"] = "interrupted" if report.get("interrupted") else "finished"
    report["summary"] = {
        "inventoried": len(report["repositories"]),
        "listed": sum(r.get("action") == "listed" for r in report["repositories"]),
        "unknown": sum(r["decision"] == "unknown" for r in report["repositories"]),
        "estimated_bytes": (sum(r.get("estimated_bytes", 0) for r in report["repositories"]
                                if r.get("action") == "listed") if estimate_size else None),
    }
    save()
    for error in report["errors"]:
        print(f"ERROR: {error}")
    print(f"Markdown: {destination.with_suffix('.md')}")
    print(f"JSON: {destination}")
    print(f"Resumo: {report['summary']}")
    failed = bool(report["errors"]) or bool(report["summary"]["unknown"])
    return (130 if report.get("interrupted") else 1 if failed else 0), report


def prune(workspace, clones_root, worktrees_root, outputs_root, mode="report", project="", api=None, estimate_size=False):
    with ProgressReporter() as reporter:
        token = _PROGRESS.set(reporter)
        try:
            return _prune(workspace, clones_root, worktrees_root, outputs_root,
                          mode, project, api, estimate_size)
        finally:
            _PROGRESS.reset(token)
