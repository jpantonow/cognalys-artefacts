# Retrospective: run_ab56

## Funnel

| Stage | Findings |
|---|---|
| analysed | 2 |
| cause_confirmed | 2 |
| generation_supported | 1 |
| plan_valid | 1 |
| generated | 0 |
| build_passed | 0 |
| published | 0 |

## Coverage

Denominator: 2 finding(s) attributed to this run.

| Measure | Count | Rate |
|---|---|---|
| proposals (a patch exists) | 1 | 50% |
| abstentions | 1 | 50% |
| published | 0 | 0% |

Actions taken: `abstain` 1, `repair_for_review` 1

Avoidable abstention: **unmeasured** — evals/ground_truth.csv carries no reviewer labels, so whether an abstention was avoidable is unmeasured. This is not a count of zero.

## Where each finding stopped

- **org.cryptomator.lite.debug_3157.apk/tryToFindDefaultTrustManager** ended at `adjudication`
  - `adjudication:no_safe_fix` — open `error_solutions/evidence_reviews/org.cryptomator.lite.debug_3157.apk/0_tryToFindDefaultTrustManager_evidence.json`
    > The source invokes `TrustManagerFactory.init(null as KeyStore?)` (TLS-CLAIM-002). The verified rule's Init event requires `generatedKeyStore[keyStore]` evidence for `init(KeyStore)`, which a null plat
- **org.cryptomator.lite.debug_3157.apk/from** ended at `generation`
  - `generation:semantic:unintended_behavior_change` — open `error_solutions/coordinated_drafts/org.cryptomator.lite.debug_3157.apk/1_from/semantic_validation.json`

## Recurring across runs

- `adjudication:no_safe_fix` — 13 run(s)
- `generation:semantic:unintended_behavior_change` — 7 run(s)

## Reappeared after being absent

- `adjudication:no_safe_fix` — 13 run(s)
- `generation:semantic:unintended_behavior_change` — 7 run(s)

## No longer seen when the same finding ran again

- org.cryptomator.lite.debug_3157.apk/tryToFindDefaultTrustManager: `adjudication:no_behavior_preserving_compliant_replacement` (last in run_v9; now ends at `adjudication`)

## Stage history of findings run before

- org.cryptomator.lite.debug_3157.apk/tryToFindDefaultTrustManager: adjudication → adjudication
- org.cryptomator.lite.debug_3157.apk/from: generation → generation

## Model calls

| Stage | Calls | Cache hits | Minutes | Hung | Failed |
|---|---|---|---|---|---|
| adjudication | 2 | 0 | 1.3 | 0 | 0 |
| adjudication.correction | 1 | 0 | 0.4 | 0 | 0 |
| code_generation.behavior_review | 1 | 0 | 0.5 | 0 | 0 |
| code_generation.coordinated_file | 2 | 0 | 0.5 | 0 | 0 |
| evidence_extraction | 4 | 0 | 2.8 | 0 | 0 |
| external_research.research | 4 | 2 | 1.6 | 0 | 0 |
| external_research.synthesis | 4 | 2 | 0.4 | 0 | 0 |
| issue_presentation | 1 | 0 | 0.1 | 0 | 0 |
| patch_planning | 1 | 0 | 0.4 | 0 | 0 |
| patch_planning.correction | 1 | 0 | 6.1 | 0 | 0 |

---

Signatures come from deterministic rules over this run's artifacts. A pipeline change is working when its signature stops appearing for the findings it targeted.
