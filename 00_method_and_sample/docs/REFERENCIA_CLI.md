# Referência dos argumentos de linha de comando

[Índice](README.md) · [Exemplos comentados](PIPELINE_DETALHADO.md#16-execução-para-estudo)

Extraída das chamadas `add_argument` dos parsers por análise sintática. Não executa módulos, consulta provedores ou valida credenciais. Defaults abaixo são expressões do código; quando o parser omite default, argparse e a lógica posterior determinam o valor efetivo. Ajuda transcrita preserva o idioma original. Opções de subcomandos (por exemplo `ground_truth.py sample` e `score`) aparecem na mesma seção do arquivo, podendo repetir nomes.

Wrappers sem parser próprio usam o parser do módulo reexportado: `build_apks_no_proguard.py` → `src/cognalys/builds/apk_build.py`; `validate_applied_solutions.py` → `src/cognalys/validation/applied_solutions.py`; `repair_validation_failures.py` → `src/cognalys/validation/repair.py`; `fix_prs.py` → `src/cognalys/prs/pr_feedback.py`.

Não há argumento atual `--llm-architecture-version`. `--pr-issue-style` controla apresentação, e `--llm-context-length` é alias da janela do Ollama. Os defaults Bash estão nos próprios wrappers e podem divergir dos defaults Python.

<a id="scripts-audit-specifications-py"></a>
## [legado/scripts/audit_specifications.py](../legado/scripts/audit_specifications.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--specifications-dir` | `Path(__file__).resolve().parents[1] / 'data' / 'specifications'` | type=Path |  |
| `--write` | `não declarado` | action='store_true' | Write source_verified, shapes_grounded and source_audit back into each specification. Without it this only reports. |
| `--spec` | `''` |  | Audit one spec_id only. |

<a id="scripts-cleanup-pipeline-py"></a>
## [legado/scripts/cleanup_pipeline.py](../legado/scripts/cleanup_pipeline.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--pull-requests-dir` | `não declarado` | type=Path; required=True |  |
| `--confirm-cleanup` | `não declarado` | action='store_true' | Actually mutate GitHub and reset clones. Without this, only preview. |
| `--limit` | `0` | type=int |  |
| `--project` | `''` |  |  |

<a id="scripts-enrich-spec-context-py"></a>
## [legado/scripts/enrich_spec_context.py](../legado/scripts/enrich_spec_context.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--input-dir` | `não declarado` | type=Path; required=True |  |
| `--specifications-dir` | `não declarado` | type=Path; required=True |  |
| `--project` | `''` |  |  |
| `--required-specs-csv` | `não declarado` | type=Path |  |

<a id="scripts-propose-detector-rules-py"></a>
## [legado/scripts/propose_detector_rules.py](../legado/scripts/propose_detector_rules.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--runs` | `'outputs/run_*'` |  | Glob of run directories whose extracted findings are the cases. |
| `--required-specs-csv` | `não declarado` | type=Path | Only the specifications this findings CSV names. |
| `--specifications-dir` | `data/specifications` | type=Path |  |
| `--spec` | `[]` | action='append' | Only this specification (repeatable). |
| `--replace` | `False` | action='store_true' | Regenerate rules this script accepted before. |
| `--retry` | `False` | action='store_true' | Ask again for specifications judged not local or rejected. |
| `--dry-run` | `False` | action='store_true' | Evaluate proposals but write nothing. |
| `--model` | `'gpt-5.6-terra'` |  |  |
| `--endpoint` | `'https://api.openai.com/v1'` |  |  |
| `--timeout` | `600` | type=int |  |
| `--max-output-tokens` | `32768` | type=int |  |
| `--reasoning-effort` | `'high'` |  |  |
| `--prompt-log-dir` | `não declarado` | type=Path |  |

<a id="scripts-evaluate-llm-pipeline-py"></a>
## [legado/scripts/evaluate_llm_pipeline.py](../legado/scripts/evaluate_llm_pipeline.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--manifest` | `Path('evals/manifest.json')` | type=Path |  |
| `--baseline-results` | `não declarado` | type=Path |  |
| `--candidate-results` | `não declarado` | type=Path; action='append'; required=True |  |
| `--trace-dir` | `não declarado` | type=Path |  |
| `--output` | `não declarado` | type=Path; required=True |  |

<a id="scripts-filtro-py"></a>
## [legado/scripts/filtro.py](../legado/scripts/filtro.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `entrada` | `não declarado` | type=Path |  |
| `-o` / `--saida` | `não declarado` | type=Path |  |

<a id="scripts-filtro-v2-py"></a>
## [legado/scripts/filtro_v2.py](../legado/scripts/filtro_v2.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `entrada` | `não declarado` | type=Path |  |
| `-o` / `--saida` | `não declarado` | type=Path |  |

<a id="scripts-filtro-v3-py"></a>
## [legado/scripts/filtro_v3.py](../legado/scripts/filtro_v3.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `entrada` | `não declarado` | type=Path |  |
| `-o` / `--saida` | `não declarado` | type=Path |  |

<a id="scripts-generate-github-previews-py"></a>
## [legado/scripts/generate_github_previews.py](../legado/scripts/generate_github_previews.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--pull-requests-dir` | `não declarado` | type=Path; required=True |  |
| `--issue-candidates-dir` | `não declarado` | type=Path |  |
| `--evidence-reviews-dir` | `não declarado` | type=Path |  |
| `--output-dir` | `não declarado` | type=Path; required=True |  |
| `--issue-title` | `não declarado` | required=True |  |
| `--issue-body` | `não declarado` | required=True |  |
| `--project` | `''` |  |  |
| `--limit` | `0` | type=int |  |

<a id="scripts-generate-specifications-py"></a>
## [legado/scripts/generate_specifications.py](../legado/scripts/generate_specifications.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--csv-file` | `não declarado` | type=Path; required=True |  |
| `--specifications-dir` | `não declarado` | type=Path; required=True |  |
| `--model` | `'gpt-5.6-terra'` |  |  |
| `--endpoint` | `'https://api.openai.com/v1'` |  |  |
| `--timeout` | `600` | type=int |  |
| `--max-output-tokens` | `32768` | type=int |  |
| `--prompt-log-dir` | `não declarado` | type=Path | Directory for JSON records of prompts sent to OpenAI. |
| `--reasoning-effort` | `'high'` | choices=('none', 'low', 'medium', 'high', 'xhigh') |  |
| `--overwrite` | `não declarado` | action='store_true' |  |
| `--project` | `''` |  | Restrict specification discovery to this exact APK/project name. |

<a id="scripts-ground-truth-py"></a>
## [legado/scripts/ground_truth.py](../legado/scripts/ground_truth.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--corpus` | `não declarado` | type=Path; required=True | Corpus CSV to sample from. |
| `--out` | `root / 'evals' / 'ground_truth.csv'` | type=Path |  |
| `--size` | `40` | type=int |  |
| `--seed` | `20260912` | type=int | Fixed so the sample can be redrawn exactly. |
| `--stratify-by` | `'spec'` |  |  |
| `--worksheet` | `root / 'evals' / 'ground_truth.csv'` | type=Path |  |
| `--outputs-dir` | `root / 'outputs'` | type=Path |  |
| `--run-glob` | `'run_*'` |  |  |
| `--json` | `não declarado` | type=Path |  |

<a id="scripts-measure-adjudication-stability-py"></a>
## [legado/scripts/measure_adjudication_stability.py](../legado/scripts/measure_adjudication_stability.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--outputs-dir` | `root / 'outputs'` | type=Path | Where the run directories live (default: outputs/). |
| `--run-glob` | `'run_*'` |  | Which run directories to read in retrospective mode. |
| `--json` | `não declarado` | type=Path | Write the full result here. |
| `--csv` | `não declarado` | type=Path | Write one row per adjudication here. |
| `--repeat` | `0` | type=int | Prospective mode: run one finding this many times first. |
| `--csv-file` | `não declarado` | type=Path | Corpus CSV for --repeat. |
| `--project` | `''` |  | APK to measure, for --repeat. |
| `--destination` | `não declarado` | type=Path | Where --repeat writes its runs (default: outputs/stability_<project>). |
| `--keep-existing` | `não declarado` | action='store_true' | Add to the repetitions already in --destination instead of replacing them. |
| `extra` | `não declarado` | nargs='*' | Further arguments passed through to run_pipeline.py. |

<a id="scripts-prepare-repository-branches-py"></a>
## [legado/scripts/prepare_repository_branches.py](../legado/scripts/prepare_repository_branches.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--projects-dir` | `não declarado` | required=True; type=Path |  |
| `--report` | `não declarado` | required=True; type=Path |  |
| `--project` | `''` |  |  |

<a id="scripts-rank-pr-candidates-py"></a>
## [legado/scripts/rank_pr_candidates.py](../legado/scripts/rank_pr_candidates.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `csv_file` | `não declarado` | type=Path |  |
| `--outputs-dir` | `Path('outputs')` | type=Path |  |
| `--output-csv` | `não declarado` | type=Path; required=True |  |
| `--output-md` | `não declarado` | type=Path; required=True |  |

<a id="scripts-run-apply-py"></a>
## [legado/scripts/run_apply.py](../legado/scripts/run_apply.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--error-solutions-dir` | `'error_solutions'` |  | Directory containing *_solution.json files (default: error_solutions) |
| `--cloned-projects-dir` | `'cloned_projects_sample'` |  | Directory containing cloned projects (default: cloned_projects_sample) |
| `--output-dir` | `'application_results'` |  | Directory for reports and backups (default: application_results) |
| `--worktrees-dir` | `'finding_worktrees'` |  | Directory containing one Git worktree per finding. |
| `--backup-dir` | `'method_backups'` |  | Backup directory, relative to --output-dir unless absolute (default: method_backups) |
| `--no-backups` | `não declarado` | action='store_true' | Do not save source-file backups before patching. |
| `--dry-run` | `não declarado` | action='store_true' | Validate mapping and report what would be patched without changing files. |
| `--approve-review-only-for-validation` | `não declarado` | action='store_true' | Apply review-only patches exclusively to finding worktrees for build/test validation. This does not authorize publication. |
| `--limit` | `0` | type=int | Apply only the first N discovered solutions. 0 means no limit. |
| `--git-user-name` | `'jpantonow'` |  | Git user name stored in the execution config/report. |
| `--git-user-email` | `'jpantonow@gmail.com'` |  | Git user email stored in the execution config/report. |
| `--log-level` | `'INFO'` | choices=('DEBUG', 'INFO', 'WARNING', 'ERROR') | Console log level (default: INFO) |

<a id="scripts-run-pipeline-py"></a>
## [legado/scripts/run_pipeline.py](../legado/scripts/run_pipeline.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--csv-file` | `'data/processed/fdroid_prs_top190.csv'` |  | CSV used by the extraction step. |
| `--enable-filter-v3` | `não declarado` | action='store_true' | Run filtro_v3 as the first step and use its output CSV in all subsequent CSV-consuming steps. Disabled by default. |
| `--filter-v3-output` | `'data/processed/errors_amostragem_v3.csv'` |  | Output CSV produced when --enable-filter-v3 is active. |
| `--filter-v3-input` | `'data/raw/errors.csv'` |  | Raw, unfiltered CSV consumed by filtro_v3. This is intentionally independent from --csv-file. |
| `--enable-rv-import` | `não declarado` | action='store_true' | Run import_rv_misuses as the first step: convert a runtime-verification misuse package into the findings CSV and use it in all subsequent CSV-consuming steps. Disabled by default. |
| `--rv-import-dir` | `'data/raw/exp_novo'` |  | Misuse package directory read by the rv_import step. |
| `--rv-import-output` | `'data/processed/rv_import_findings.csv'` |  | Findings CSV written by the rv_import step. |
| `--cloned-projects-dir` | `'outputs/cloned_projects'` |  | Directory where repositories are cloned and later built. |
| `--extracted-methods-dir` | `'outputs/error_methods_tracked'` |  | Directory where extracted method JSON files are written. |
| `--build-output-dir` | `'outputs/built_apks_no_proguard'` |  | Directory for APK build logs and reports. |
| `--error-solutions-dir` | `'outputs/error_solutions'` |  | Directory where LLM-generated solution JSON files are written. |
| `--application-results-dir` | `'outputs/application_results'` |  | Directory where solution application reports are written. |
| `--worktrees-dir` | `'outputs/finding_worktrees'` |  | Directory containing one independent Git worktree per finding. |
| `--pull-requests-dir` | `'outputs/pull_requests'` |  | Directory where PR metadata JSON files are written. |
| `--publish-prs` | `não declarado` | action='store_true' | Run the optional step that forks, pushes and opens GitHub PRs. |
| `--allow-duplicate-pr` | `False` | action='store_true' | Open a new pull request even when an earlier run already published one for the same finding that may still be open. Forwarded only to the PR publication step. |
| `--gradle-build-cache` | `False` | action='store_true' | Pass --build-cache to the Gradle builds of the validation steps so patched, baseline and repair builds share task outputs. Off by default. |
| `--full-revalidation` | `False` | action='store_true' | Rebuild every finding in the revalidation step, including those that already passed and were not repaired. By default only repaired findings and findings that did not pass are rebuilt. |
| `--approve-reviewed-finding` | `[]` | action='append'; metavar='FINDING_ID' | Authorize publication of a specific human-reviewed finding. Forwarded only to the PR publication step. |
| `--publish-without-build-validation` | `[]` | action='append'; metavar='FINDING_ID' | DANGEROUS. Also publish this finding when the build never ran because the repository cannot be built at all -- never when the build ran and the patch failed to compile. Nothing then establishes that the patch compiles. Can be repeated. |
| `--publish-fix-prs` | `não declarado` | action='store_true' | Commit and push fix_prs revisions to their existing PR branches. |
| `--skip-fix-prs-compile-validation` | `não declarado` | action='store_true' | Do not actually compile a fix_prs revision before publishing it (default: compile it with the same build machinery as validation/revalidation; ktlintCheck alone only checks formatting and would pass on code that does not compile). |
| `--skip-fix-prs-pr-state-check` | `não declarado` | action='store_true' | Do not re-fetch a PR's live state before revising it (default: check, since a PR closed or merged after its local snapshot was written should not be revised or pushed to). |
| `--self-audit-prs` | `não declarado` | action='store_true' | In the fix_prs step, re-check a PR with no maintainer feedback against the pipeline's current deterministic correctness gates and revise whatever they reject (default: off). Lets a gate added after publication repair the pull requests that predate it, instead of waiting for someone to notice the defect. |
| `--fix-ci-failures` | `não declarado` | action='store_true' | In the fix_prs step, also revise a PR with no maintainer feedback to address its own failing CI checks (default: off, since this lets the model touch files outside the original finding's, gated by the same compile/style validation and --publish-fix-prs approval as any other revision). |
| `--publish-issues` | `não declarado` | action='store_true' | Publish one GitHub issue per generated PR metadata file. |
| `--issues-dir` | `'outputs/issues'` |  | Directory where issue publication records are written. |
| `--force-revalidation` | `não declarado` | action='store_true' | Always run validation_fix/revalidation even when validation already succeeded for every solution (default: skip them and go straight to the next step, since there is nothing to fix or reconfirm). |
| `--force-downstream-steps` | `não declarado` | action='store_true' | Always run validation/validation_fix/revalidation/prs/publish/fix_prs/revalidation_fix_prs/issues even when the solutions step applied nothing and the llm step produced no issue candidates (default: skip straight to preview/issues_cleanup, since there is nothing for those steps to act on). |
| `--reprocess-existing` | `não declarado` | action='store_true' | Reprocess publish/issues records already marked as created instead of skipping them (forwarded to pr_creator.py and issue_creator.py, which already support this). |
| `--pr-issue-style` | `'v1'` | choices=('v1', 'v2') | v1 preserves today's PR/issue text unchanged; v2 opts into the humanized, less-templated PR/issue text for side-by-side comparison before switching the default. |
| `--issue-title` | `'Potential security issue found — would you be open to a pull request?'` |  |  |
| `--issue-body` | `"Hi! I'm a Computer Engineering student at the University of Brasília (UnB), working on a research project about how Android apps use security-sensitive APIs."` |  |  |
| `--generate-previews` | `não declarado` | action='store_true' | Generate Markdown previews during a complete pipeline run. |
| `--previews-dir` | `'outputs/previews'` |  | Directory for per-PR and per-issue Markdown previews. |
| `--start-from` | `'spec_generation'` | choices=PIPELINE_STEPS | Start the pipeline from this step. |
| `--only` | `não declarado` | choices=PIPELINE_STEPS | Run only one step. |
| `--skip` | `[]` | action='append'; choices=PIPELINE_STEPS | Skip a step. Can be used more than once. |
| `--limit` | `0` | type=int | Limit projects/solutions/PRs where supported. 0 means no limit. |
| `--project` | `''` |  | Process only this exact APK/project name in every selected step. |
| `--skip-cloning` | `não declarado` | action='store_true' | Reuse repositories already present in --cloned-projects-dir. |
| `--clone-timeout` | `1800` | type=int | Timeout per repository clone attempt, in seconds. An attempt that times out with its objects already fetched is resumed by the next one instead of starting over. |
| `--clone-retries` | `3` | type=int | Number of attempts per repository clone. |
| `--build-variant` | `'debug'` | choices=('debug', 'release') | Android build variant used by the build step. |
| `--build-timeout` | `1800` | type=int | Timeout per Android project build, in seconds. |
| `--build-no-proguard` | `não declarado` | action='store_true' | Run the optional APK build step with ProGuard/R8 disabled. |
| `--validate-build` | `não declarado` | action='store_true' | Compile projects after applying solutions and report it in PRs. |
| `--llm-provider` | `'ollama'` | choices=('ollama', 'openai') | Provider used by the LLM step. |
| `--spec-model` | `'gpt-5.6-terra'` |  | OpenAI model used to generate missing specification JSONs. |
| `--overwrite-specifications` | `não declarado` | action='store_true' | Regenerate specification JSONs that already exist. |
| `--disable-llm-web-search` | `não declarado` | action='store_true' | Disable OpenAI web search during evidence analysis and fix generation. |
| `--llm-model` | `'codellama:7b'` |  | Ollama model used by the LLM step. |
| `--llm-endpoint` | `'http://localhost:11434'` |  | Ollama endpoint used by the LLM step. |
| `--llm-timeout` | `300` | type=int | Timeout per Ollama request, in seconds. |
| `--ollama-context-length` / `--llm-context-length` | `16384` | dest='ollama_context_length'; type=int | Ollama physical context window in tokens. The old --llm-context-length name remains as a compatibility alias. |
| `--repository-context-budget` | `140000` | type=int | Logical character budget for deterministic repository evidence. |
| `--llm-max-output-tokens` | `-1` | type=int | Ollama num_predict (default: -1, no fixed output cap). |
| `--no-llm-resume` | `não declarado` | action='store_true' | Reprocess all method JSON files in the LLM step. |
| `--llm-cache-dir` | `não declarado` |  | Persistent LLM response cache directory, relative to the repository root (default: outputs/.llm_cache). It is deliberately outside the per-run output directory so responses survive across runs; use --no-llm-cache to disable reuse entirely. |
| `--openai-prompt-log-dir` | `não declarado` |  | Directory for JSON logs of every prepared OpenAI prompt. Defaults to APPLICATION_RESULTS_DIR/openai_prompt_logs for this pipeline run. |
| `--no-llm-cache` | `não declarado` | action='store_true' | Disable the per-request LLM cache. |
| `--llm-web-search-cache-ttl-hours` | `168.0` | type=float | How long a cached web-research answer stays usable (default: 168, one week). Research is the analysis step's most expensive stage and repeats verbatim across runs of the same finding; 0 refetches every time. |
| `--llm-retries` | `2` | type=int | HTTP retries for an ordinary LLM call (default: 2). Each retry re-runs a full generation, so the budget is deliberately small. |
| `--llm-web-search-retries` | `1` | type=int | HTTP retries for a web-research call (default: 1). Each retry re-runs the entire research pass, so this budget is much smaller than the one used for ordinary calls. |
| `--llm-workers` | `1` | type=int | Findings analysed concurrently in the LLM step (default: 1, sequential). Findings are independent analyses that spend nearly all their time waiting on the provider. |
| `--llm-reasoning-effort` | `'high'` | choices=('none', 'low', 'medium', 'high', 'xhigh') |  |
| `--llm-validation-mode` | `'balanced'` | choices=('strict', 'balanced', 'relaxed') |  |
| `--allow-cache-invalidation` | `não declarado` | action='store_true' | Permit regeneration of confirmed disposable cache entries. |
| `--migration-policies-file` | `'data/migration_policies.json'` |  | Case-specific migration policies keyed by unique_error_id. |
| `--dry-run` | `não declarado` | action='store_true' | Run supported steps in dry-run mode. |
| `--approve-review-only-for-validation` | `não declarado` | action='store_true' | Apply review-only solutions only in finding worktrees for validation; never authorizes publication. |
| `--skip-preflight` | `não declarado` | action='store_true' | Skip the pre-run checks (declared dependencies importable, pyflakes over src and scripts). They take seconds and guard a run that takes hours; skip them only knowingly. |
| `--continue-on-error` | `não declarado` | action='store_true' | Continue with the next step even if one step fails. |
| `--ignore-step-errors` | `não declarado` | action='store_true' | Continue after failed steps and finish with exit code 0. Failures are still reported in the final summary. |
| `--cleanup` | `não declarado` | action='store_true' | Only preview cleanup of PRs, fork branches, and local clones. |
| `--confirm-cleanup` | `não declarado` | action='store_true' | With --cleanup, actually close PRs, delete branches, and reset clones. |
| `--cleanup-issues` | `não declarado` | action='store_true' | Only preview closing issues previously created by the pipeline. |
| `--confirm-issue-cleanup` | `não declarado` | action='store_true' | With --cleanup-issues, actually close the GitHub issues. |

<a id="scripts-run-prs-py"></a>
## [legado/scripts/run_prs.py](../legado/scripts/run_prs.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--execution-report` | `'application_results/execution_report.json'` |  | Path to SolutionApplicator execution_report.json. |
| `--output-dir` | `'pull_requests'` |  | Directory where *_pr.json files will be written. |
| `--branch-prefix` | `'fix'` |  | Prefix for generated branches (default: fix). |
| `--commit-template` | `'Fix {method_name}'` |  | Commit message template. Available: {method_name}, {class_name}, {apk_name}. |
| `--title-template` | `'Fix {method_name}'` |  | PR title template. Available: {method_name}, {class_name}, {apk_name}. |
| `--limit` | `0` | type=int | Generate only the first N PR metadata files. 0 means no limit. |
| `--project` | `''` |  | Generate PR metadata only for this exact APK/project name. |
| `--dry-run` | `não declarado` | action='store_true' | Generate metadata without creating branches or commits. |
| `--log-level` | `'INFO'` | choices=('DEBUG', 'INFO', 'WARNING', 'ERROR') | Console log level (default: INFO). |
| `--pr-issue-style` | `'v1'` | choices=('v1', 'v2') | v1 preserves today's PR text unchanged; v2 applies the humanized body. |
| `--publish-without-build-validation` | `[]` | action='append'; metavar='FINDING_ID' | DANGEROUS. Also publish this finding when the build never ran because the repository cannot be built at all -- never when the build ran and the patch failed to compile. Nothing then establishes that the patch compiles. Can be repeated. |

<a id="scripts-scan-fdroid-pmd-py"></a>
## [legado/scripts/scan_fdroid_pmd.py](../legado/scripts/scan_fdroid_pmd.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--input` | `Path('data/raw/fdroid_prs_top400.csv')` | type=Path |  |
| `-n` / `--limit` | `não declarado` | type=int; required=True | Quantidade de projetos únicos. |
| `--offset` | `0` | type=int | Pula projetos únicos no início do CSV. |
| `--output` | `Path('data/raw/errors_pmd.csv')` | type=Path |  |
| `--projects-dir` | `Path('outputs/pmd_projects')` | type=Path |  |
| `--raw-dir` | `Path('outputs/pmd_raw')` | type=Path |  |
| `--rulesets` | `DEFAULT_RULESETS` |  |  |
| `--minimum-priority` | `3` | type=int; choices=range(1, 6) |  |
| `--timeout` | `300` | type=int | Timeout do PMD por projeto. |
| `--clone-timeout` | `180` | type=int |  |
| `--pmd` | `'pmd'` |  | Executável PMD 7. |
| `--no-clone` | `não declarado` | action='store_true' | Não clona projetos ausentes. |
| `--normalize-only` | `não declarado` | action='store_true' | Reprocessa os XMLs em --raw-dir sem executar PMD novamente. |

<a id="src-builds-apk-build-py"></a>
## [src/cognalys/builds/apk_build.py](../src/cognalys/builds/apk_build.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--projects-dir` | `'outputs/cloned_projects'` |  | Folder containing one subdirectory per cloned APK/project. |
| `--output-dir` | `'outputs/built_apks_no_proguard'` |  | Where generated APKs, logs and reports will be written. |
| `--variant` | `'debug'` | choices=('debug', 'release') | Build variant. Debug is usually unsigned and already non-minified. |
| `--task` | `None` |  | Custom Gradle task. Overrides --variant, for example ':app:assembleDebug'. |
| `--additional-task` | `[]` | action='append' | Additional Gradle task to run in the same invocation. Can be repeated, for example assembleDebugAndroidTest or testDebugUnitTest. |
| `--timeout` | `1800` | type=int | Timeout per project in seconds. |
| `--limit` | `0` | type=int | Build only the first N projects. 0 means no limit. |
| `--project` | `[]` | action='append' | Build only project directory names containing this text. Can be repeated. |
| `--refresh-dependencies` | `não declarado` | action='store_true' | Pass --refresh-dependencies to Gradle. |
| `--clean-first` | `não declarado` | action='store_true' | Run Gradle clean before the requested build task. |
| `--skip-submodules` | `não declarado` | action='store_true' | Do not initialize Git submodules before building. By default, declared submodules are synchronized and initialized recursively. |
| `--disable-minification` | `True` | action='store_true' | Disable ProGuard/R8 and resource shrinking with an init script. Enabled by default for every build. |
| `--dry-run` | `não declarado` | action='store_true' | Print what would be built without running Gradle. |
| `--android-sdk` | `None` |  | Android SDK path to pass to Gradle as ANDROID_HOME/ANDROID_SDK_ROOT. On WSL this is often /mnt/c/Users/<user>/AppData/Local/Android/Sdk. |
| `--java-home` | `None` |  | JDK path to pass to Gradle as JAVA_HOME. On Windows, Android Studio's bundled JDK is often C:\Program Files\Android\Android Studio\jbr. |

<a id="src-extraction-extract-methods-single-csv-py"></a>
## [src/cognalys/extraction/extract_methods_single_csv.py](../src/cognalys/extraction/extract_methods_single_csv.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--csv-file` | `não declarado` | required=True | CSV único com repo_url, apk_url, class e method |
| `--output-dir` | `não declarado` | required=True | Diretório de saída para logs, tracking e cloned_projects |
| `--methods-output-dir` | `não declarado` | required=True | Diretório onde os JSONs dos métodos extraídos serão salvos |
| `--skip-cloning` | `não declarado` | action='store_true' | Reutiliza clones já existentes em OUTPUT_DIR/cloned_projects |
| `--reset` | `não declarado` | action='store_true' | Reseta todos os repositórios em OUTPUT_DIR/cloned_projects |
| `--clone-timeout` | `1800` | type=int | Timeout em segundos para cada tentativa de git clone. Uma tentativa que estoura o tempo com os objetos ja baixados e retomada pela seguinte, em vez de recomecar do zero. |
| `--clone-only` | `não declarado` | action='store_true' | Clona/mapeia os repositórios sem executar a extração. |
| `--clone-retries` | `3` | type=int | Número de tentativas para cada git clone |
| `--project` | `''` |  | Processa somente o nome exato deste APK/projeto |

<a id="src-extraction-track-methods-single-csv-py"></a>
## [src/cognalys/extraction/track_methods_single_csv.py](../src/cognalys/extraction/track_methods_single_csv.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--csv-file` | `não declarado` | required=True | CSV with at least class/method and repository identity columns. |
| `--clone-dir` | `não declarado` | required=True | Directory containing repositories already cloned by extract_methods_single_csv.py. |
| `--output-dir` | `'single_csv_tracking'` |  | Directory for logs, tracking JSON and summary. |
| `--methods-output-dir` | `'single_csv_methods_tracked'` |  | Directory where per-method JSON files will be written. |
| `--log-level` | `'INFO'` | choices=('DEBUG', 'INFO', 'WARNING', 'ERROR') | Console log level. |
| `--limit` | `0` | type=int | Process only the first N CSV rows. 0 means no limit. |

<a id="src-issues-issue-cleanup-py"></a>
## [src/cognalys/issues/issue_cleanup.py](../src/cognalys/issues/issue_cleanup.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--issues-dir` | `não declarado` | type=Path; required=True |  |
| `--confirm-issue-cleanup` | `não declarado` | action='store_true' |  |
| `--github-token` | `não declarado` |  |  |
| `--github-api` | `GITHUB_API` |  |  |
| `--limit` | `0` | type=int |  |
| `--project` | `''` |  |  |

<a id="src-issues-issue-creator-py"></a>
## [src/cognalys/issues/issue_creator.py](../src/cognalys/issues/issue_creator.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--pull-requests-dir` | `não declarado` | required=True |  |
| `--issue-candidates-dir` | `não declarado` |  |  |
| `--issues-dir` | `não declarado` | required=True |  |
| `--title` | `não declarado` | required=True |  |
| `--body` | `não declarado` | required=True |  |
| `--github-token` | `não declarado` |  |  |
| `--github-api` | `GITHUB_API` |  |  |
| `--limit` | `0` | type=int |  |
| `--project` | `''` |  |  |
| `--dry-run` | `não declarado` | action='store_true' |  |
| `--reprocess-existing` | `não declarado` | action='store_true' |  |
| `--pr-issue-style` | `'v1'` | choices=('v1', 'v2') | v1 preserves today's issue text unchanged; v2 applies the humanized body. |

<a id="src-llm-generate-error-fixes-py"></a>
## [src/cognalys/llm/generate_error_fixes.py](../src/cognalys/llm/generate_error_fixes.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--provider` | `'ollama'` | choices=('ollama', 'openai') | LLM provider (default: ollama) |
| `--model` | `'codellama:7b'` |  | Ollama model to use (default: codellama:7b) |
| `--endpoint` | `'http://localhost:11434'` |  | Ollama endpoint (default: http://localhost:11434) |
| `--timeout` | `120` | type=int | Ollama request timeout in seconds (default: 120) |
| `--ollama-context-length` / `--context-length` | `16384` | dest='context_length'; type=int | Ollama physical context window in tokens (default: 16384). --context-length remains as a compatibility alias. |
| `--repository-context-budget` | `140000` | type=int | Logical character budget for deterministic repository evidence selection. |
| `--pr-issue-style` | `'v1'` | choices=('v1', 'v2') | v1 preserves today's PR/issue text unchanged; v2 composes a human maintainer summary for the per-file-drafts coordinated path instead of the deterministic placeholder-prone one. |
| `--max-output-tokens` | `-1` | type=int | Ollama num_predict (default: -1, generate until completion or the context limit) |
| `--reasoning-effort` | `'high'` | choices=('none', 'low', 'medium', 'high', 'xhigh') | Reasoning effort for OpenAI models. |
| `--validation-mode` | `'balanced'` | choices=('strict', 'balanced', 'relaxed') | Evidence strictness; hard code checks always remain enabled. |
| `--allow-cache-invalidation` | `não declarado` | action='store_true' | Allow confirmed disposable cache entries to become cache misses during a safe algorithm migration. Never applies to user data. |
| `--web-search` | `não declarado` | action='store_true' | Allow the OpenAI model to research additional authoritative sources when local context is incomplete. |
| `--migration-policies-file` | `não declarado` | type=Path | Optional JSON object keyed by unique_error_id with explicit, case-specific migration authorization and constraints. |
| `--validation-feedback-dir` | `não declarado` | type=Path | Optional build-validation directory. Persisted compiler diagnostics for the same finding are added to a corrective regeneration pass. |
| `--input-dir` | `'error_methods_tracked'` |  | Input directory with error JSONs |
| `--output-dir` | `'error_solutions'` |  | Output directory for solutions |
| `--cache-dir` | `não declarado` | type=Path | LLM response cache directory (default: REPO_ROOT/outputs/.llm_cache). It must not live under OUTPUT_DIR: that directory is per-run, so a cache placed there is recreated empty every run and never hits. |
| `--prompt-log-dir` | `não declarado` | type=Path | Directory for one JSON record per prepared OpenAI request. Records include instructions, input and schema, never API keys. |
| `--no-cache` | `não declarado` | action='store_true' | Disable the persistent per-request LLM cache |
| `--web-search-cache-ttl-hours` | `168.0` | type=float | How long a cached web-research answer stays usable (default: 168, one week). Research is the pipeline's most expensive stage and repeats verbatim across runs of the same finding; 0 disables its cache and refetches every time. |
| `--workers` | `1` | type=int | Findings analysed concurrently (default: 1, sequential). Each finding is an independent analysis that spends nearly all of its time waiting on the provider, so raising this multiplies throughput; results are still consumed in submission order. |
| `--retries` | `2` | type=int | HTTP retries for an ordinary call (default: 2). Every call here re-runs a full generation at the configured reasoning effort, so this budget is far below what a cheap request would use. |
| `--web-search-retries` | `1` | type=int | HTTP retries for a web-research call (default: 1). Each one re-runs the whole research pass, so this is deliberately far below the retry budget used for ordinary calls. |
| `--resume` | `True` | action='store_true' | Skip already-processed errors |
| `--no-resume` | `não declarado` | action='store_false'; dest='resume' | Reprocess all errors |
| `--dry-run` | `não declarado` | action='store_true' | Show what would be processed without making API calls |
| `--verbose` | `não declarado` | action='store_true' | More detailed logging |

<a id="src-prs-extract-solutions-to-pr-py"></a>
## [src/cognalys/prs/extract_solutions_to_pr.py](../src/cognalys/prs/extract_solutions_to_pr.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--dry-run` | `não declarado` | action='store_true' | Run without submitting PR to GitHub |
| `--config` | `não declarado` |  | Path to .env configuration file |
| `--output-path` | `não declarado` |  | Custom output directory path |
| `--error-solutions-dir` | `não declarado` |  | Override directory containing solution JSON files |
| `--github-token` | `não declarado` |  | Override GitHub token |
| `--base-branch` | `não declarado` |  | Override target base branch |
| `--log-file` | `'extraction.log'` |  | Log file path (default: extraction.log) |
| `--log-level` | `'INFO'` | choices=('DEBUG', 'INFO', 'WARNING', 'ERROR') | Console/file log level (default: INFO) |

<a id="src-prs-pr-creator-py"></a>
## [src/cognalys/prs/pr_creator.py](../src/cognalys/prs/pr_creator.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--pull-requests-dir` | `PULL_REQUESTS_DIR` |  | Directory containing *_pr.json files (default: pull_requests). |
| `--github-token` | `None` |  | GitHub token. Defaults to GITHUB_TOKEN environment variable. |
| `--github-api` | `GITHUB_API` |  | GitHub API base URL (default: https://api.github.com). |
| `--fork-wait-seconds` | `120` | type=int | Maximum seconds to wait for a new fork to accept a push (default: 120). |
| `--base-branch` | `None` |  | Override PR base branch. Default auto-detects upstream HEAD. |
| `--limit` | `0` | type=int | Process only the first N PR metadata files. 0 means no limit. |
| `--project` | `''` |  | Publish PR metadata only for this exact APK/project name. |
| `--dry-run` | `não declarado` | action='store_true' | Write *_created.json dry-run records without forking, pushing or creating PRs. |
| `--reprocess-existing` | `não declarado` | action='store_true' | Reprocess PR metadata even when *_created.json already exists. |
| `--allow-duplicate-pr` | `False` | action='store_true' | Open a new pull request even though an earlier run already published one for the same finding that may still be open. |
| `--approve-reviewed-finding` | `[]` | action='append'; metavar='FINDING_ID' | Explicitly authorize publication of one human-reviewed finding. Requires successful build validation and a concrete commit. Can be repeated. |
| `--publish-without-build-validation` | `[]` | action='append'; metavar='FINDING_ID' | DANGEROUS. Also authorize this finding when the build never ran -- an unbuildable repository, not a failing patch. Nothing then establishes that the patch compiles, and the maintainer's CI is the first build it sees. Never accepted for a build that ran and failed. Must accompany --approve-reviewed-finding for the same id. Can be repeated. |

<a id="src-prs-pr-feedback-py"></a>
## [src/cognalys/prs/pr_feedback.py](../src/cognalys/prs/pr_feedback.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--pull-requests-dir` | `não declarado` | required=True |  |
| `--projects-dir` | `não declarado` | required=True |  |
| `--worktrees-dir` | `'outputs/finding_worktrees'` |  | Root for isolated per-PR checkouts (see prepare_pr_checkout): reuses the finding worktree the PR was created from when still present, or creates one here next to it. Never mutates --projects-dir's shared checkout, which other pipeline steps expect to stay on the repository's default branch. |
| `--output` | `não declarado` | required=True |  |
| `--project` | `''` |  |  |
| `--model` | `'gpt-5.6-terra'` |  |  |
| `--endpoint` | `'https://api.openai.com/v1'` |  |  |
| `--timeout` | `600` | type=int |  |
| `--max-output-tokens` | `32768` | type=int |  |
| `--prompt-log-dir` | `não declarado` | type=Path | Directory for JSON records of prompts sent to OpenAI. |
| `--reasoning-effort` | `'high'` | choices=('none', 'low', 'medium', 'high', 'xhigh') |  |
| `--dry-run` | `não declarado` | action='store_true' |  |
| `--publish` | `não declarado` | action='store_true' | Commit the revised method and push the existing PR branch. |
| `--commit-message` | `'Address pull request feedback for {method_name}'` |  |  |
| `--skip-validation` | `não declarado` | action='store_true' | Skip repository-provided ktlintCheck validation. |
| `--build-script` | `ROOT / 'scripts' / 'build_apks_no_proguard.py'` | type=Path | Build script used to actually compile the revised project. |
| `--build-variant` | `'debug'` | choices=('debug', 'release') |  |
| `--build-timeout` | `1800` | type=int | Gradle build timeout in seconds (separate from --timeout, which is for the OpenAI request). |
| `--skip-compile-validation` | `não declarado` | action='store_true' | Skip actually compiling the revised project. ktlintCheck alone (see --skip-validation) only checks formatting and would pass on a revision that does not compile at all; do not disable this for a --publish run without a specific reason. |
| `--skip-pr-state-check` | `não declarado` | action='store_true' | Skip re-fetching the pull request's current state before revising it. Without this check, a PR closed or merged after its local snapshot was written would still be revised and, with --publish, pushed to. |
| `--self-audit` | `não declarado` | action='store_true' | When a PR has no maintainer feedback, re-check the published patch against the pipeline's current deterministic correctness gates and revise anything they reject. Lets a gate added after publication repair the pull requests that predate it, without waiting for someone to notice and comment. |
| `--fix-ci-failures` | `não declarado` | action='store_true' | When a PR has no maintainer feedback, also check its own CI checks and revise to address any that failed. Only relevant for the revision step (see --revalidate for compiling and publishing); off by default because it lets the model touch files -- build scripts, dependency versions -- outside the original finding's file, gated by the same compile/style validation and --publish approval as any other revision. |
| `--revalidate` | `não declarado` | action='store_true' | Run as the explicit revalidation_fix_prs step instead of the fix_prs revision step: read --output as an existing report, compile and style-check (and, with --publish, commit and push) every APPLIED entry, then overwrite --output with the result. Does not call OpenAI. Mirrors validation being its own step after solutions in the main pipeline, rather than compiling and publishing being buried inside the same run that revises. |

<a id="src-validation-applied-solutions-py"></a>
## [src/cognalys/validation/applied_solutions.py](../src/cognalys/validation/applied_solutions.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--build-script` | `não declarado` | type=Path; required=True |  |
| `--projects-dir` | `não declarado` | type=Path; required=True |  |
| `--output-dir` | `não declarado` | type=Path; required=True |  |
| `--execution-report` | `não declarado` | type=Path; required=True |  |
| `--variant` | `'debug'` | choices=('debug', 'release') |  |
| `--timeout` | `1800` | type=int |  |
| `--project` | `''` |  |  |
| `--limit` | `0` | type=int |  |
| `--dry-run` | `não declarado` | action='store_true' |  |
| `--fail-on-validation-error` | `não declarado` | action='store_true' | Return non-zero when any patched build remains non-passing. |
| `--publish-without-build-validation` | `[]` | action='append'; metavar='FINDING_ID' | Do not abort the run when this finding's build fails for an infrastructure reason: the operator has already approved publishing it without compile validation, so the failure is expected. Has no effect on a build that ran and failed. Can be repeated. |
| `--baseline-cache-dir` | `None` | type=Path | Directory for reusing baseline (unmodified-project) test builds keyed by repository+commit+task set, shared across findings and runs. Defaults to a 'baseline_build_cache' directory next to --projects-dir. |

<a id="src-validation-repair-py"></a>
## [src/cognalys/validation/repair.py](../src/cognalys/validation/repair.py)

| Argumento | Default declarado | Restrições/ação | Ajuda declarada |
|---|---|---|---|
| `--execution-report` | `não declarado` | type=Path; required=True |  |
| `--output-dir` | `não declarado` | type=Path; required=True |  |
| `--model` | `'gpt-5.6-terra'` |  |  |
| `--endpoint` | `'https://api.openai.com/v1'` |  |  |
| `--timeout` | `600` | type=int |  |
| `--max-output-tokens` | `16384` | type=int |  |
| `--prompt-log-dir` | `não declarado` | type=Path | Directory for JSON records of prompts sent to OpenAI. |
| `--reasoning-effort` | `'high'` | choices=('none', 'low', 'medium', 'high', 'xhigh') |  |
| `--project` | `''` |  |  |
| `--limit` | `0` | type=int |  |
| `--dry-run` | `não declarado` | action='store_true' |  |

