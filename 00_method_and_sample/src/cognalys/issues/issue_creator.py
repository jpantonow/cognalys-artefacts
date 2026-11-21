#!/usr/bin/env python3
"""Publish GitHub issues from PR metadata or reviewed NO_SAFE_FIX candidates."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path

from cognalys.issues.issue_text import (  # noqa: E402
    PRESENTED_KINDS,
    issue_text_problems,
    rewrite_issue_text,
)
from cognalys.issues.issue_text import issue_title as issue_title_of  # noqa: E402
from cognalys.issues.issue_text import (  # noqa: E402
    DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT, contact_issue_title,
)

from cognalys.utils.finding_source import (  # noqa: E402
    analyzer_name,
    is_analyzer_tool,
    is_dynamic_analysis_tool,
    is_rvsec_finding,
    tool_and_spec_context,
)
from cognalys.utils.pr_text import (  # noqa: E402
    symbol_phrase, text_problems,
)
from cognalys.prs.pr_outcomes import records_publication  # noqa: E402
from cognalys.prs.publication_checks import issue_publication_blockers  # noqa: E402

try:
    import requests
except ModuleNotFoundError:
    requests = None


GITHUB_API = "https://api.github.com"


def issue_approval_name(metadata: dict, metadata_file: Path) -> str:
    """The name --approve-reviewed-finding gives to publish this issue.

    The finding id when the record carries one (a PR record's finding_id, a
    candidate's since 2026-10-30); a candidate written before that is named
    by its APK directory and file stem, e.g. app.apk/3_getOrCreateKey, the
    path the preview lists it under.
    """
    finding = str(metadata.get("finding_id") or metadata.get("unique_error_id") or "").strip()
    if finding:
        return finding
    stem = Path(metadata_file).name.removesuffix(".json")
    return f"{Path(metadata_file).parent.name}/{stem}"


class IssueCreator:
    def __init__(
        self,
        pull_requests_dir: str,
        issue_candidates_dir: str | None,
        issues_dir: str,
        title: str,
        body: str,
        github_token: str | None = None,
        github_api: str = GITHUB_API,
        dry_run: bool = False,
        skip_existing: bool = True,
        limit: int = 0,
        project: str = "",
        style: str = "v1",
        allow_text_problems: bool = False,
        approved_reviewed_findings=None,
        text_model: dict | None = None,
        title_without_security_impact: str = DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT,
        allow_closed_repository: bool = False,
        allow_changed_upstream: bool = False,
    ) -> None:
        self.title_without_security_impact = title_without_security_impact
        self.allow_closed_repository = allow_closed_repository
        self.allow_changed_upstream = allow_changed_upstream
        self.allow_text_problems = allow_text_problems
        # remote_provider's arguments for the model that rewrites a v3
        # candidate's text failing its checks (issue_text); without it such
        # a text is only held back.
        self.text_model = dict(text_model or {})
        self._text_client = None
        # An issue reaches GitHub only for a finding the user named; every
        # other one is written as a dry-run record with the text it would
        # send. This step opened an issue for every candidate of a run that
        # was not a --dry-run (batch6 audit, 2026-10-30).
        self.approved_reviewed_findings = {
            str(value).strip() for value in (approved_reviewed_findings or [])
            if str(value).strip()
        }
        if not self.approved_reviewed_findings:
            dry_run = True
        self.pull_requests_dir = Path(pull_requests_dir)
        self.issue_candidates_dir = (
            Path(issue_candidates_dir) if issue_candidates_dir else None
        )
        self.issues_dir = Path(issues_dir)
        self.issues_dir.mkdir(parents=True, exist_ok=True)
        self.title = title
        self.body = body
        self.github_token = (
            github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        )
        self.github_api = github_api.rstrip("/")
        self.dry_run = dry_run
        self.skip_existing = skip_existing
        self.limit = limit
        self.project = project.strip()
        self.style = style

        if requests is None and not dry_run:
            raise RuntimeError("Missing dependency: requests")
        if not self.github_token and not dry_run:
            raise ValueError(
                "GitHub token not set. Use --github-token, GITHUB_TOKEN, "
                "or GH_TOKEN."
            )

        self.headers = {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        if not self.dry_run:
            self.validate_authentication()

    def github_contents(self, url, params=None):
        """JSON of a GitHub API read, or None when it is missing or fails."""
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def validate_authentication(self) -> None:
        """Fail once, before the publication loop, when credentials are invalid."""
        response = requests.get(
            f"{self.github_api}/user",
            headers=self.headers,
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(
                "GitHub authentication failed before issue publication: "
                f"HTTP {response.status_code}. Update GITHUB_TOKEN or GH_TOKEN "
                "with a valid token that can create issues."
            )
        print(f"Authenticated as: {response.json().get('login', 'unknown')}")

    @staticmethod
    def repository_from_metadata(metadata: dict) -> tuple[str, str, str]:
        host = metadata.get("repo_host", "")
        owner = metadata.get("repo_owner", "")
        repo = metadata.get("repo_name", "")
        if not host or not owner or not repo:
            raise ValueError("PR metadata does not identify the source repository")
        if host.lower() != "github.com":
            raise ValueError(f"Issue publication is GitHub-only: {host}/{owner}/{repo}")
        return host, owner, repo

    @staticmethod
    def source_file_from_metadata(metadata: dict) -> str:
        """Return only the repository-relative source path for the issue body."""
        source_file = metadata.get("source_file", "").strip()

        # Compatibility with PR metadata generated before source_file became
        # a dedicated field.
        if not source_file:
            match = re.search(
                r"\|\s*File\s*\|\s*`([^`]+)`\s*\|",
                metadata.get("body", ""),
            )
            if match:
                source_file = match.group(1).strip()

        if not source_file:
            raise ValueError("PR metadata does not identify the source file")

        repository = metadata.get("repository", "")
        normalized_source = source_file.replace("\\", "/")
        normalized_repository = repository.replace("\\", "/").rstrip("/")
        if normalized_repository and normalized_source.startswith(
            normalized_repository + "/"
        ):
            normalized_source = normalized_source[len(normalized_repository) + 1 :]

        # Older reports may include the cloned-project APK directory even
        # after removing the machine-specific repository prefix.
        apk_marker = ".apk/"
        if apk_marker in normalized_source:
            normalized_source = normalized_source.split(apk_marker, 1)[1]

        return normalized_source.lstrip("/")

    @staticmethod
    def build_issue_body(
        body: str,
        source_file: str,
        spec: str = "",
        method_name: str = "",
        class_name: str = "",
        error_message: str = "",
        tool: str = "",
        style: str = "v1",
    ) -> str:
        """Build the exact issue body sent to GitHub."""
        def visible(value: str) -> bool:
            return bool(value) and value.strip().lower() not in {
                "unknown", "unknownclass", "unknownfile", "unknown_method"
            }

        if style == "v3":
            # Plain paragraphs, like the pull request body (pr_text): what
            # was flagged and where, the analyzer's own words, the question.
            # The method/class/specification/tool table was the pipeline's
            # view of the finding, not what a maintainer needs to answer.
            paragraphs = [
                body.strip(),
                IssueCreator.flagged_sentence(
                    tool, class_name, method_name,
                    source_file if visible(source_file) else "",
                ),
            ]
            if error_message and error_message.lower() != "unknown":
                paragraphs.append(f"> {error_message.strip()}")
            paragraphs.extend([
                "I have a fix for this locally and would like to open a pull "
                "request with it. Would you be open to reviewing it?",
                "Thanks!",
            ])
            return IssueCreator.normalize_public_analysis_disclaimer(
                "\n\n".join(item for item in paragraphs if item)
            )

        details = ["## Analysis context", ""]
        if visible(method_name):
            details.append(f"- **Method:** `{method_name}`")
        if visible(class_name):
            details.append(f"- **Class:** `{class_name}`")
        if visible(source_file):
            details.append(f"- **File:** `{source_file}`")
        if visible(spec):
            details.append(f"- **Specification:** `{spec}`")
        # "web_research" in a CSV's tool column says how the row was gathered,
        # not which analyzer reported it; naming it as the analysis tool
        # would misstate the finding's origin.
        if visible(tool) and is_analyzer_tool(tool):
            details.append(f"- **Analysis tool:** `{analyzer_name(tool)}`")

        if style == "v2":
            glossary = tool_and_spec_context(tool, spec)
            if glossary:
                details.extend(["", glossary])

        if error_message and error_message.lower() != "unknown":
            details.extend([
                "",
                "## Observed behavior",
                "",
                # Worded by what actually produced the row: a candidate a
                # person found by reading the code was never a dynamic run.
                "The dynamic analysis reported:"
                if is_dynamic_analysis_tool(tool) else "The reported finding:",
                "",
                f"> {error_message.strip()}",
            ])

        if style == "v2":
            details.extend([
                "",
                "I have been working locally on a potential solution to "
                "this issue and would like to prepare a pull request with "
                "my changes. Would you be open to receiving and reviewing it?",
                "", "---", "",
                f"*{body.strip()}*",
            ])
            return IssueCreator.normalize_public_analysis_disclaimer(
                "\n".join(details)
            )

        details.extend([
            "",
            "This finding was produced by an automated dynamic analysis tool "
            "and may depend on the application’s execution context.",
            "",
            "However, I have been working locally on a potential solution to "
            "this issue and would like to prepare a pull request with my "
            "changes. Would you be open to receiving and reviewing it?",
        ])
        return IssueCreator.normalize_public_analysis_disclaimer(
            f"{body.rstrip()}\n\n" + "\n".join(details)
        )

    @staticmethod
    def flagged_sentence(
        tool: str, class_name: str, method_name: str, source_file: str,
    ) -> str:
        """One sentence on what flagged which code, as a person would say it."""
        # A maintainer knows the class, not the JVM's constructor name, and a
        # Kotlin file-level function by its own name (pr_text.symbol_phrase).
        where = symbol_phrase(str(class_name or "").split("$", 1)[0], method_name) or "this code"
        if source_file:
            where += f" in `{source_file}`"
        if is_rvsec_finding(tool):
            return (
                "While running the app under RVSec, a runtime checker for how "
                f"apps use the Java crypto and TLS APIs, it flagged {where}."
            )
        if is_analyzer_tool(tool):
            return f"{analyzer_name(tool)} flagged {where}."
        return f"While reading the code, I noticed {where}."

    @classmethod
    def normalize_public_analysis_disclaimer(cls, text: str) -> str:
        """Remove internal analysis disclaimers and repair legacy mojibake."""
        normalized = re.sub(
            r"This finding was produced by an automated dynamic analysis tool "
            r"and may depend on the application.+?s execution context\.",
            "", text,
        )
        normalized = normalized.replace(
            "This report was produced with automated analysis and reviewed "
            "against the available repository source. Additional project "
            "context may change the conclusion.",
            "",
        )
        # Repair legacy UTF-8-as-Windows-1252 fragments in configured issue
        # templates without changing already-correct Unicode text.
        for broken, correct in (
            ("BrasÃ­lia", "Brasília"),
            ("â€™", "’"),
            ("â€”", "—"),
            ("â€“", "–"),
        ):
            normalized = normalized.replace(broken, correct)
        return re.sub(r"\n{3,}", "\n\n", normalized).rstrip()

    @classmethod
    def build_no_safe_fix_body(
        cls, body: str, metadata: dict, style: str = "v1",
    ) -> str:
        """Build a cautious issue for a confirmed finding without a safe PR."""
        source_file = cls.source_file_from_metadata(metadata)
        content = metadata.get("issue_content", {})
        compatibility_policy = metadata.get("compatibility_policy", {})
        migration = metadata.get("migration_assessment", {})
        digest_contract = metadata.get("digest_contract_assessment", {})
        show_cache_note = (
            compatibility_policy.get("allow_cache_invalidation") is True
            and migration.get("usage_kind") == "cache"
            and digest_contract.get("contract_kind") not in {
                "external_backend", "multi_repository",
            }
        )
        next_step = str(content.get("proposed_next_step", "")).strip()
        conditional_next_step = (
            next_step[:1].lower() + next_step[1:] if next_step else ""
        )
        tool = str(metadata.get("tool", "")).strip()
        spec = str(metadata.get("spec", "")).strip()
        if style == "v3":
            # Plain paragraphs, like the pull request body (pr_text). The
            # headed version reached maintainers with an "Analysis context"
            # table and "If a migration is desirable, have a maintainer
            # choose..." -- the pipeline talking to itself (MTG Familiar
            # #669, exp_final_20261019). The cache-permission note is the
            # operator's and stays in the JSON.
            def sentence(value) -> str:
                text = str(value or "").strip()
                return text if not text or text.endswith((".", "!", "?")) else text + "."
            paragraphs = [
                body.strip(),
                cls.flagged_sentence(
                    tool, str(metadata.get("csv_class", "")),
                    str(metadata.get("method_name", "")), source_file,
                ),
                sentence(content.get("finding_summary")),
                " ".join(item for item in (
                    sentence(content.get("why_no_pr")),
                    sentence(content.get("maintainer_question")),
                ) if item),
                sentence(next_step),
                "Thanks!",
            ]
            return cls.normalize_public_analysis_disclaimer(
                "\n\n".join(item for item in paragraphs if item)
            )
        details = ["## Analysis context", ""]
        for label, key in (
            ("Method", "method_name"), ("Class", "csv_class"),
            ("File", "source_file"), ("Specification", "spec"),
            ("Analysis tool", "tool"),
        ):
            value = source_file if key == "source_file" else str(
                metadata.get(key, "")
            ).strip()
            if value and value.lower() not in {
                "unknown", "unknownclass", "unknownfile", "unknown_method"
            }:
                if key == "tool":
                    if not is_analyzer_tool(value):
                        continue
                    value = analyzer_name(value)
                details.append(f"- **{label}:** `{value}`")

        if style == "v2":
            glossary = tool_and_spec_context(tool, spec)
            if glossary:
                details.extend(["", glossary])

        details.extend([
            "", "## What the analysis found", "",
            str(content.get("finding_summary", "")).strip(),
            *(
                [
                    "", "## Compatibility consideration", "",
                    "Invalidating derived, regenerable cache entries was "
                    "considered acceptable for this analysis. That "
                    "permission does not extend to user-owned source data, "
                    "credentials, preferences, or offline-only data.",
                ]
                if show_cache_note
                else []
            ),
            "", "## Why I have not prepared a pull request", "",
            str(content.get("why_no_pr", "")).strip(),
            "", "## Question for the maintainers", "",
            str(content.get("maintainer_question", "")).strip(),
            "", "## Possible next step", "",
            "If a migration is desirable, "
            + conditional_next_step,
        ])
        if style == "v2":
            details.extend(["", "---", "", f"*{body.strip()}*"])
            return cls.normalize_public_analysis_disclaimer(
                "\n".join(details).rstrip()
            )
        details.extend([
            "",
            "This finding was produced by an automated dynamic analysis tool "
            "and may depend on the application’s execution context.",
        ])
        return cls.normalize_public_analysis_disclaimer(
            f"{body.rstrip()}\n\n" + "\n".join(details).rstrip()
        )

    def text_client(self):
        if not self.text_model.get("model"):
            return None
        if self._text_client is None:
            from cognalys.llm.llm_providers import remote_provider
            self._text_client = remote_provider(**self.text_model)
        return self._text_client

    def rewrite_text(self, metadata: dict, metadata_file: Path) -> None:
        """Rewrite a v3 candidate's text that fails its checks, and save it.

        Saved in the candidate itself, so the preview step and a later run
        of this one read the rewritten text instead of paying for it again.
        """
        if self.style != "v3" or not issue_text_problems(metadata, self.body):
            return
        try:
            client = self.text_client()
        except RuntimeError as exc:  # a missing key: the hold applies as before
            print(f"Issue text not rewritten: {exc}")
            self.text_model = {}
            return
        if client is None:
            return
        remaining = rewrite_issue_text(client, logging.getLogger(__name__), metadata,
                                       intro=self.body)
        print(f"{'REWRITTEN' if not remaining else 'NOT REWRITTEN'}: issue text of "
              f"{metadata_file.name}" + (f" still: {'; '.join(remaining)}" if remaining else ""))
        temporary = metadata_file.with_suffix(metadata_file.suffix + ".tmp")
        temporary.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(metadata_file)

    def process(self, metadata_file: Path) -> str:
        with metadata_file.open(encoding="utf-8") as file:
            metadata = json.load(file)

        _, owner, repo = self.repository_from_metadata(metadata)
        source_file = self.source_file_from_metadata(metadata)
        spec = str(metadata.get("spec", "")).strip()
        result_file = self.issues_dir / f"{metadata_file.stem}_issue_created.json"

        if result_file.exists() and self.skip_existing:
            try:
                previous = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
            if self.dry_run or previous.get("dry_run") is not True:
                print(f"SKIPPED: {owner}/{repo} already processed")
                return "skipped"

        if metadata.get("issue_kind") in PRESENTED_KINDS:
            # An issue already opened keeps the text it was opened with.
            if not records_publication(result_file):
                self.rewrite_text(metadata, metadata_file)
            issue_body = self.build_no_safe_fix_body(
                self.body, metadata, style=self.style
            )
            issue_title = issue_title_of(metadata, self.title)
        else:
            issue_body = self.build_issue_body(
                self.body, source_file, spec,
                str(metadata.get("method_name", "")).strip(),
                str(metadata.get("csv_class", "")).strip(),
                str(metadata.get("error_message", "")).strip(),
                str(metadata.get("tool", "")).strip(),
                style=self.style,
            )
            # A pull-request draft's contact-first issue says "security issue"
            # only for a confirmed security impact; a collateral candidate
            # keeps the title it always had.
            issue_title = (self.title if metadata.get("issue_kind")
                           else contact_issue_title(metadata, self.title,
                                                    self.title_without_security_impact))
        if self.style == "v3" and not self.allow_text_problems:
            # The pull request's own text check (pr_text), so an issue cannot
            # reach a maintainer carrying what a PR is refused for.
            problems = text_problems(
                issue_title, issue_body, str(metadata.get("error_message", "")),
                wording_checks=False,
            )
            if problems:
                print(
                    f"BLOCKED: {owner}/{repo}: " + "; ".join(problems)
                    + ". Rewrite the issue_content, or pass --allow-text-problems."
                )
                return "blocked"
        payload = {"title": issue_title, "body": issue_body}
        approval = issue_approval_name(metadata, metadata_file)
        if not self.dry_run and approval in self.approved_reviewed_findings:
            # What the repository says now, read only for an issue about to be
            # opened (publication_checks.issue_publication_blockers).
            blockers = issue_publication_blockers(
                metadata, self.github_contents, api=self.github_api,
                allow_changed_upstream=self.allow_changed_upstream,
                allow_closed_repository=self.allow_closed_repository,
            )
            if blockers:
                print(f"BLOCKED: {owner}/{repo}: " + "; ".join(blockers))
                return "blocked"
        if self.dry_run or approval not in self.approved_reviewed_findings:
            response_data = {
                "dry_run": True,
                "repository": f"{owner}/{repo}",
                "finding_id": approval,
                **payload,
            }
            print(f"DRY RUN: would open an issue on {owner}/{repo}"
                  + ("" if approval in self.approved_reviewed_findings else
                     f"; pass --approve-reviewed-finding {approval} to open it")
                  + f":\n{issue_title}\n\n{issue_body}\n")
            if records_publication(result_file):
                # The opened issue's record stays: issue_cleanup closes it by
                # that number (batch6 review, 2026-10-30).
                print(f"Kept {result_file.name}: it records the opened issue.")
                return "skipped"
        else:
            response = requests.post(
                f"{self.github_api}/repos/{owner}/{repo}/issues",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            if response.status_code != 201:
                raise RuntimeError(
                    f"Issue creation failed for {owner}/{repo}: "
                    f"HTTP {response.status_code} {response.text}"
                )
            response_data = {**response.json(), "finding_id": approval}

        result_file.write_text(
            json.dumps(response_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        url = response_data.get("html_url", "dry-run")
        print(f"SUCCESS: {owner}/{repo} -> {url}")
        return "success"

    def run(self) -> dict[str, int]:
        files = sorted(self.pull_requests_dir.glob("*_pr.json"))
        if self.issue_candidates_dir and self.issue_candidates_dir.exists():
            files.extend(sorted(self.issue_candidates_dir.rglob("*_issue.json")))
        if self.project:
            files = [
                file for file in files
                if file.name.startswith(f"{self.project}_")
                or file.parent.name == self.project
            ]
        if self.limit > 0:
            files = files[: self.limit]

        summary = {"success": 0, "failed": 0, "skipped": 0, "blocked": 0}
        print(f"Found {len(files)} repositories for issue publication")
        for metadata_file in files:
            try:
                status = self.process(metadata_file)
                summary[status] += 1
            except Exception as exc:
                summary["failed"] += 1
                print(f"FAILED: {metadata_file.name}: {exc}")

        print(
            "Issue summary: "
            f"{summary['success']} created, {summary['skipped']} skipped, "
            f"{summary['blocked']} blocked (text problems or the repository's state), "
            f"{summary['failed']} failed"
        )
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create GitHub issues from PR metadata or NO_SAFE_FIX candidates."
    )
    parser.add_argument("--pull-requests-dir", required=True)
    parser.add_argument("--issue-candidates-dir")
    parser.add_argument("--issues-dir", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--issue-title-without-security-impact", default=DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT,
        help=("Title of a pull-request draft's contact-first issue when its record has no "
              "confirmed security impact; {api} and {location} are filled in."))
    parser.add_argument("--body", required=True)
    parser.add_argument("--github-token")
    parser.add_argument("--github-api", default=GITHUB_API)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--project", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reprocess-existing", action="store_true")
    parser.add_argument(
        "--approve-reviewed-finding", action="append", default=[], metavar="FINDING_ID",
        help="open the issue of this reviewed finding (repeatable); every other "
             "issue is a dry run that writes its record and prints its text",
    )
    parser.add_argument(
        "--pr-issue-style",
        choices=("v1", "v2", "v3"),
        default="v1",
        help="v1 preserves the original issue text; v2 the headed humanized body; v3 plain paragraphs.",
    )
    parser.add_argument(
        "--allow-text-problems", action="store_true",
        help="Publish a v3 issue even when its text fails the pull request text check.",
    )
    parser.add_argument(
        "--model", default="",
        help=("Model that rewrites a v3 candidate's text failing its checks "
              "(evidence ids, pipeline vocabulary, bytecode names, rule ids), "
              "saved in the candidate. Without it such a text is only held back."),
    )
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--prompt-log-dir", default="")
    parser.add_argument(
        "--allow-closed-repository", action="store_true",
        help="Open the issue although the repository says it accepts no contributions "
             "(when the maintainer invited it).")
    parser.add_argument(
        "--allow-changed-upstream", action="store_true",
        help="Open the issue although the reported code is no longer on the default branch.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = IssueCreator(
        pull_requests_dir=args.pull_requests_dir,
        issue_candidates_dir=args.issue_candidates_dir,
        issues_dir=args.issues_dir,
        title=args.title,
        title_without_security_impact=args.issue_title_without_security_impact,
        body=args.body,
        github_token=args.github_token,
        github_api=args.github_api,
        dry_run=args.dry_run,
        skip_existing=not args.reprocess_existing,
        limit=args.limit,
        project=args.project,
        style=args.pr_issue_style,
        allow_text_problems=args.allow_text_problems,
        approved_reviewed_findings=args.approve_reviewed_finding,
        allow_closed_repository=args.allow_closed_repository,
        allow_changed_upstream=args.allow_changed_upstream,
        text_model={
            key: value for key, value in {
                "model": args.model, "endpoint": args.endpoint,
                "timeout": args.timeout, "max_output_tokens": args.max_output_tokens,
                "reasoning_effort": args.reasoning_effort or None,
                "cache_dir": Path(args.cache_dir) if args.cache_dir else None,
                "prompt_log_dir": Path(args.prompt_log_dir) if args.prompt_log_dir else None,
            }.items() if value
        } if args.model else None,
    ).run()
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
