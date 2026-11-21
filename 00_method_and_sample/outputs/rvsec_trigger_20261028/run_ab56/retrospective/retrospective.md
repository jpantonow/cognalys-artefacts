# Retrospective: run_ab56

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

- **com.example.trigger.debug_408.apk/createKeyPair** ended at `generation`
  - `generation:semantic:unintended_behavior_change` — open `error_solutions/coordinated_drafts/com.example.trigger.debug_408.apk/0_createKeyPair/semantic_validation.json`

## Recurring across runs

- `generation:semantic:unintended_behavior_change` — 7 run(s)

## Reappeared after being absent

- `generation:semantic:unintended_behavior_change` — 7 run(s)

## No longer seen when the same finding ran again

- com.example.trigger.debug_408.apk/createKeyPair: `generation:incomplete` (last in run_v6; now ends at `generation`)

## Stage history of findings run before

- com.example.trigger.debug_408.apk/createKeyPair: generation → generation

## Model calls

| Stage | Calls | Cache hits | Minutes | Hung | Failed |
|---|---|---|---|---|---|
| adjudication | 1 | 0 | 1.1 | 0 | 0 |
| code_generation.behavior_review | 1 | 0 | 0.5 | 0 | 0 |
| code_generation.coordinated_file | 2 | 0 | 0.6 | 0 | 0 |
| evidence_extraction | 2 | 0 | 2.0 | 0 | 0 |
| external_research.research | 2 | 2 | 0.0 | 0 | 0 |
| external_research.synthesis | 2 | 2 | 0.0 | 0 | 0 |
| patch_planning | 1 | 0 | 0.4 | 0 | 0 |
| patch_planning.correction | 1 | 0 | 0.9 | 0 | 0 |

---

Signatures come from deterministic rules over this run's artifacts. A pipeline change is working when its signature stops appearing for the findings it targeted.
