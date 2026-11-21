# Retrospective: subset_revisao_lacunas_v4

## Funnel

| Stage | Findings |
|---|---|
| analysed | 1 |
| cause_confirmed | 1 |
| generation_supported | 1 |
| plan_valid | 1 |
| generated | 0 |
| build_passed | 0 |
| published | 0 |

## Coverage

Denominator: 1 finding(s) attributed to this run.

| Measure | Count | Rate |
|---|---|---|
| proposals (a patch exists) | 1 | 100% |
| abstentions | 0 | 0% |
| published | 0 | 0% |

Actions taken: `repair_for_review` 1

Avoidable abstention: **unmeasured** — evals/ground_truth.csv carries no reviewer labels, so whether an abstention was avoidable is unmeasured. This is not a count of zero.

## Where each finding stopped

- **org.fedorahosted.freeotp_48.apk/encrypt** ended at `generation`
  - `generation:semantic:unintended_behavior_change` — open `error_solutions/coordinated_drafts/org.fedorahosted.freeotp_48.apk/0_encrypt/semantic_validation.json`

## Recurring across runs

- `generation:semantic:unintended_behavior_change` — 3 run(s)

## Reappeared after being absent

- `generation:semantic:unintended_behavior_change` — 3 run(s)

## No longer seen when the same finding ran again

- org.fedorahosted.freeotp_48.apk/encrypt: `generation:semantic:checked_exception_not_propagated` (last in subset_revisao_lacunas_v3; now ends at `generation`)
- org.fedorahosted.freeotp_48.apk/encrypt: `generation:semantic:test_placed_in_unrelated_file` (last in subset_revisao_lacunas_v3; now ends at `generation`)

## Stage history of findings run before

- org.fedorahosted.freeotp_48.apk/encrypt: adjudication → adjudication → validated → generation → generation → generation

## Model calls

| Stage | Calls | Cache hits | Minutes | Hung | Failed |
|---|---|---|---|---|---|
| adjudication | 2 | 1 | 1.1 | 0 | 0 |
| adjudication.correction | 4 | 2 | 0.7 | 0 | 0 |
| code_generation.behavior_review | 5 | 0 | 4.0 | 0 | 0 |
| code_generation.coordinated_file | 14 | 0 | 11.2 | 0 | 0 |
| code_generation.coordinated_file.shape_retry | 1 | 0 | 0.5 | 0 | 0 |
| evidence_extraction | 2 | 1 | 0.9 | 0 | 0 |
| external_research.research | 4 | 4 | 0.0 | 0 | 0 |
| external_research.synthesis | 4 | 4 | 0.0 | 0 | 0 |
| patch_planning | 2 | 0 | 1.4 | 0 | 0 |
| patch_planning.correction | 2 | 0 | 2.1 | 0 | 0 |

---

Signatures come from deterministic rules over this run's artifacts. A pipeline change is working when its signature stops appearing for the findings it targeted.
