# apply_solutions_config.py

import os
from dataclasses import dataclass, field


@dataclass
class ApplySolutionsConfig:

    error_solutions_dir: str = "error_solutions"

    cloned_projects_dir: str = "cloned_projects_sample"

    output_dir: str = "application_results"

    worktrees_dir: str = "finding_worktrees"

    save_backups: bool = True

    backup_dir: str = "method_backups"

    dry_run: bool = False

    approve_review_only_for_validation: bool = False

    max_solutions: int = 0

    # Porting a patch whose code changed on the base branch (solutions.
    # base_port). Off unless a model is given: the step costs model calls.
    port_model: str = ""

    port_endpoint: str = "https://api.openai.com/v1"

    port_timeout: int = 600

    port_max_output_tokens: int = 16384

    port_reasoning_effort: str = "high"

    port_prompt_log_dir: str = ""

    git_user_name: str = field(
        default_factory=lambda: os.getenv("TCC_GIT_USER_NAME", "")
    )

    git_user_email: str = field(
        default_factory=lambda: os.getenv("TCC_GIT_USER_EMAIL", "")
    )
