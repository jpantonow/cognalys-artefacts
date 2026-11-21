#!/usr/bin/env python3
"""
Extract Solutions and Create Pull Request - Main Orchestration Script

Workflow:
1. Load and validate configuration
2. Discover and extract solutions from error_solutions/
3. Format solutions with CodeLlama attribution
4. Build consolidated JSON output
5. Create GitHub pull request with formatted solutions
6. Generate execution report

Usage:
    python extract_solutions_to_pr.py [--dry-run] [--output-path PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Import modules
try:
    from cognalys.config import Config
    from solution_extractor import SolutionExtractor
    from solution_formatter import FormattedSolutionBuilder
    from solutions_json_builder import SolutionsJSONBuilder
    from cognalys.prs.pr_generator import GitPullRequestGenerator
except ModuleNotFoundError:
    Config = None
    SolutionExtractor = None
    FormattedSolutionBuilder = None
    SolutionsJSONBuilder = None
    GitPullRequestGenerator = None


def setup_logging(log_file: str = "extraction.log", log_level: str = "INFO") -> logging.Logger:
    """Set up logging configuration."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


def load_config(config_file: Optional[str] = None) -> Optional[Config]:
    """Load configuration file."""
    logger = logging.getLogger(__name__)

    if Config is None:
        logger.error(
            "Required module 'config' was not found. "
            "Ensure config.py and related PR workflow modules are available."
        )
        return None
    
    try:
        config = Config(env_file=config_file or ".env")
        logger.info("Configuration loaded successfully")
        logger.info(f"Settings: {config}")
        return config

    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        return None


def validate_config(config: Config) -> bool:
    """Validate required configuration after CLI overrides."""
    logger = logging.getLogger(__name__)

    is_valid, missing = config.validate()

    if not is_valid:
        logger.error("Configuration validation failed. Missing parameters:")
        for param in missing:
            logger.error(f"  - {param}")
        return False

    logger.info("Configuration validated successfully")
    logger.info(f"Effective settings: {config}")
    return True


def extract_solutions(config: Config) -> Optional[tuple]:
    """Extract solutions from error_solutions directory."""
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 60)
    logger.info("PHASE 1: SOLUTION EXTRACTION")
    logger.info("=" * 60)

    try:
        extractor = SolutionExtractor(base_dir=config.error_solutions_dir)
        solutions, stats = extractor.aggregate_all()

        if not solutions:
            logger.warning("No solutions were extracted")
            return None

        logger.info(f"✓ Extracted {len(solutions)} solutions")
        return solutions, extractor.get_extraction_report()

    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return None


def format_solutions(solutions: list) -> Optional[list]:
    """Format solutions with CodeLlama attribution."""
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 60)
    logger.info("PHASE 2: SOLUTION FORMATTING")
    logger.info("=" * 60)

    try:
        formatter = FormattedSolutionBuilder()
        formatted = formatter.format_all_solutions(solutions)

        logger.info(f"✓ Formatted {len(formatted)} solutions")
        return formatted

    except Exception as e:
        logger.error(f"Formatting failed: {e}")
        return None


def build_consolidated_json(
    formatted_solutions: list,
    config: Config
) -> Optional[Path]:
    """Build and save consolidated JSON output."""
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 60)
    logger.info("PHASE 3: JSON CONSOLIDATION")
    logger.info("=" * 60)

    try:
        builder = SolutionsJSONBuilder(output_dir=config.output_dir)
        output_path = builder.build_and_save(
            formatted_solutions,
            filename="solutions_formatted.json"
        )

        if output_path:
            logger.info(f"✓ Consolidated JSON saved to {output_path}")
            return output_path
        else:
            logger.error("Failed to save consolidated JSON")
            return None

    except Exception as e:
        logger.error(f"JSON consolidation failed: {e}")
        return None


def create_pr(
    solutions_json_path: Path,
    config: Config
) -> Optional[dict]:
    """Create and submit GitHub pull request."""
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 60)
    logger.info("PHASE 4: GITHUB PR CREATION")
    logger.info("=" * 60)

    try:
        # Load solutions JSON
        with open(solutions_json_path, 'r', encoding='utf-8') as f:
            solutions_json = json.load(f)

        # Initialize PR generator
        pr_gen = GitPullRequestGenerator(
            repo_owner=config.target_repo_owner,
            repo_name=config.target_repo_name,
            github_token=config.github_token,
            base_branch=config.base_branch,
            dry_run=config.dry_run
        )

        # Authenticate
        auth_success, auth_msg = pr_gen.authenticate()
        if not auth_success:
            logger.error(f"Authentication failed: {auth_msg}")
            if not config.dry_run:
                return None
            logger.info("Continuing in dry-run mode...")

        # Submit PR
        result = pr_gen.submit_solutions(solutions_json)

        if result and result.get("success"):
            pr_details = result.get("pr_details", {})
            logger.info(f"✓ PR created successfully: {pr_details.get('url')}")
            return result
        else:
            logger.error("Failed to create pull request")
            return None

    except Exception as e:
        logger.error(f"PR creation failed: {e}")
        return None


def generate_execution_report(
    extraction_report: dict,
    pr_result: Optional[dict],
    solutions_count: int,
    config: Config
) -> dict:
    """Generate execution summary report."""
    report = {
        "execution_timestamp": datetime.utcnow().isoformat() + "Z",
        "workflow": "extract-solutions-and-create-pr",
        "status": "SUCCESS" if pr_result and pr_result.get("success") else "PARTIAL",
        "extraction": {
            "total_discovered": extraction_report.get("total_files_discovered", 0),
            "successfully_extracted": extraction_report.get("successfully_extracted", 0),
            "skipped": extraction_report.get("skipped_files", 0),
            "time_seconds": extraction_report.get("extraction_time_seconds", 0)
        },
        "formatting": {
            "total_formatted": solutions_count
        },
        "pull_request": {
            "created": pr_result and pr_result.get("success", False) if pr_result else False,
            "url": pr_result.get("pr_details", {}).get("url") if pr_result else None,
            "dry_run": config.dry_run
        }
    }
    return report


def main():
    """Main execution flow."""
    parser = argparse.ArgumentParser(
        description="Extract CodeLlama solutions and create GitHub pull request"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without submitting PR to GitHub"
    )
    parser.add_argument(
        "--config",
        help="Path to .env configuration file"
    )
    parser.add_argument(
        "--output-path",
        help="Custom output directory path"
    )
    parser.add_argument(
        "--error-solutions-dir",
        help="Override directory containing solution JSON files"
    )
    parser.add_argument(
        "--github-token",
        help="Override GitHub token"
    )
    parser.add_argument(
        "--base-branch",
        help="Override target base branch"
    )
    parser.add_argument(
        "--log-file",
        default="extraction.log",
        help="Log file path (default: extraction.log)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Console/file log level (default: INFO)"
    )

    args = parser.parse_args()

    # Setup logging
    logger = setup_logging(args.log_file, args.log_level)
    logger.info("Starting extract-solutions-and-create-pr workflow...")

    # Load configuration
    config = load_config(config_file=args.config)
    if not config:
        logger.error("Configuration loading failed")
        return 1

    # Override config if command-line args provided
    if args.dry_run:
        config.dry_run = True
    if args.output_path:
        config.output_dir = args.output_path
    if args.error_solutions_dir:
        config.error_solutions_dir = args.error_solutions_dir
    if args.github_token:
        config.github_token = args.github_token
    if args.base_branch:
        config.base_branch = args.base_branch

    if not validate_config(config):
        logger.error("Configuration validation failed")
        return 1

    # Phase 1: Extract solutions
    result = extract_solutions(config)
    if not result:
        logger.error("Extraction phase failed")
        return 1

    solutions, extraction_report = result

    # Phase 2: Format solutions
    formatted_solutions = format_solutions(solutions)
    if not formatted_solutions:
        logger.error("Formatting phase failed")
        return 1

    # Phase 3: Build consolidated JSON
    json_path = build_consolidated_json(formatted_solutions, config)
    if not json_path:
        logger.error("JSON consolidation phase failed")
        return 1

    # Phase 4: Create PR
    pr_result = create_pr(json_path, config)

    # Generate and display execution report
    logger.info("=" * 60)
    logger.info("EXECUTION REPORT")
    logger.info("=" * 60)

    report = generate_execution_report(extraction_report, pr_result, len(formatted_solutions), config)

    report_path = Path(config.output_dir) / "execution_report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)

    logger.info(f"Report saved to: {report_path}")
    logger.info(json.dumps(report, indent=2))

    # Success check
    if pr_result and pr_result.get("success"):
        logger.info("\n✓ Workflow completed successfully!")
        logger.info(f"✓ PR URL: {pr_result.get('pr_details', {}).get('url')}")
        return 0
    else:
        logger.warning("\n⚠ Workflow completed with warnings")
        return 0  # Return 0 even for partial success (extraction worked)


if __name__ == "__main__":
    sys.exit(main())
