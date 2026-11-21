#!/usr/bin/env python3

import argparse
import logging
from pathlib import Path

from cognalys.prs.pr_generator import PrGenerator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate PR metadata JSON files from an application execution report."
    )
    parser.add_argument(
        "--execution-report",
        default="application_results/execution_report.json",
        help="Path to SolutionApplicator execution_report.json.",
    )
    parser.add_argument(
        "--output-dir",
        default="pull_requests",
        help="Directory where *_pr.json files will be written.",
    )
    parser.add_argument(
        "--branch-prefix",
        default="fix",
        help="Prefix for generated branches (default: fix).",
    )
    parser.add_argument(
        "--commit-template",
        default="Fix {method_name}",
        help=("Commit message template. Available: {method_name}, {class_name}, "
              "{apk_name}. Left at its default, the commit message is the pull "
              "request's title."),
    )
    parser.add_argument(
        "--title-template",
        default="Fix {method_name}",
        help="PR title template. Available: {method_name}, {class_name}, {apk_name}.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Generate only the first N PR metadata files. 0 means no limit.",
    )
    parser.add_argument(
        "--project",
        default="",
        help="Generate PR metadata only for this exact APK/project name.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate metadata without creating branches or commits.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Console log level (default: INFO).",
    )
    parser.add_argument(
        "--pr-issue-style",
        choices=("v1", "v2", "v3"),
        default="v1",
        help="v1 preserves today's PR text unchanged; v2 applies the humanized body.",
    )
    parser.add_argument(
        "--publish-without-build-validation",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help=(
            "DANGEROUS. Also publish this finding when the build never ran "
            "because the repository cannot be built at all -- never when the "
            "build ran and the patch failed to compile. Nothing then "
            "establishes that the patch compiles. Can be repeated."
        ),
    )
    parser.add_argument(
        "--model", default="",
        help=("Model that rewrites a pull request text failing its checks "
              "(e.g. one describing a change the diff does not make). "
              "Without it such a text is only held back."),
    )
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--cache-dir", default="")
    # The run's prompt-log folder: a text rewrite leaves a trace there, so
    # `cognalys cost` and the --max-cost guard count it with the run's calls.
    parser.add_argument("--prompt-log-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    generator = PrGenerator(
        execution_report_path=args.execution_report,
        output_dir=args.output_dir,
        branch_prefix=args.branch_prefix,
        commit_template=args.commit_template,
        title_template=args.title_template,
        max_prs=args.limit,
        dry_run=args.dry_run,
        project=args.project,
        style=args.pr_issue_style,
        publish_without_build_validation=(
            args.publish_without_build_validation
        ),
        text_model={
            key: value for key, value in {
                "model": args.model, "endpoint": args.endpoint,
                "timeout": args.timeout, "max_output_tokens": args.max_output_tokens,
                "reasoning_effort": args.reasoning_effort or None,
                "cache_dir": Path(args.cache_dir) if args.cache_dir else None,
                "prompt_log_dir": Path(args.prompt_log_dir) if args.prompt_log_dir else None,
            }.items() if value
        } if args.model else None,
    )

    summary = generator.generate_prs()

    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
