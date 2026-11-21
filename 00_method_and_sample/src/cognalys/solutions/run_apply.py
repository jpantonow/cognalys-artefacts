import argparse
import logging

from cognalys.config.apply_solutions_config import ApplySolutionsConfig
from cognalys.solutions.solution_applicator import SolutionApplicator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply generated solution JSON files to cloned projects."
    )
    parser.add_argument(
        "--error-solutions-dir",
        default="error_solutions",
        help="Directory containing *_solution.json files (default: error_solutions)",
    )
    parser.add_argument(
        "--cloned-projects-dir",
        default="cloned_projects_sample",
        help="Directory containing cloned projects (default: cloned_projects_sample)",
    )
    parser.add_argument(
        "--output-dir",
        default="application_results",
        help="Directory for reports and backups (default: application_results)",
    )
    parser.add_argument(
        "--worktrees-dir",
        default="finding_worktrees",
        help="Directory containing one Git worktree per finding.",
    )
    parser.add_argument(
        "--backup-dir",
        default="method_backups",
        help="Backup directory, relative to --output-dir unless absolute (default: method_backups)",
    )
    parser.add_argument(
        "--no-backups",
        action="store_true",
        help="Do not save source-file backups before patching.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate mapping and report what would be patched without changing files.",
    )
    parser.add_argument(
        "--approve-review-only-for-validation",
        action="store_true",
        help=(
            "Apply review-only patches exclusively to finding worktrees for "
            "build/test validation. This does not authorize publication."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Apply only the first N discovered solutions. 0 means no limit.",
    )
    parser.add_argument(
        "--git-user-name",
        default="jpantonow",
        help="Git user name stored in the execution config/report.",
    )
    parser.add_argument(
        "--git-user-email",
        default="jpantonow@gmail.com",
        help="Git user email stored in the execution config/report.",
    )
    parser.add_argument(
        "--port-model", default="",
        help=(
            "Model that ports a patch whose code changed on the base branch "
            "(solutions.base_port). Empty disables porting."
        ),
    )
    parser.add_argument("--port-endpoint", default="https://api.openai.com/v1")
    parser.add_argument("--port-timeout", type=int, default=600)
    parser.add_argument("--port-max-output-tokens", type=int, default=16384)
    parser.add_argument(
        "--port-reasoning-effort", default="high",
        choices=("none", "low", "medium", "high", "xhigh"),
    )
    parser.add_argument("--port-prompt-log-dir", default="")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Console log level (default: INFO)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    config = ApplySolutionsConfig(
        error_solutions_dir=args.error_solutions_dir,
        cloned_projects_dir=args.cloned_projects_dir,
        output_dir=args.output_dir,
        worktrees_dir=args.worktrees_dir,
        save_backups=not args.no_backups,
        backup_dir=args.backup_dir,
        dry_run=args.dry_run,
        approve_review_only_for_validation=(
            args.approve_review_only_for_validation
        ),
        max_solutions=args.limit,
        git_user_name=args.git_user_name,
        git_user_email=args.git_user_email,
        port_model=args.port_model,
        port_endpoint=args.port_endpoint,
        port_timeout=args.port_timeout,
        port_max_output_tokens=args.port_max_output_tokens,
        port_reasoning_effort=args.port_reasoning_effort,
        port_prompt_log_dir=args.port_prompt_log_dir,
    )

    applicator = SolutionApplicator(config)
    report = applicator.apply_solutions()

    print("\nDONE")
    print(report)

    return 0 if report.get("workflow_status") in {
        "SUCCESS", "PARTIAL", "SUCCESS_WITH_NO_ACTION",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
