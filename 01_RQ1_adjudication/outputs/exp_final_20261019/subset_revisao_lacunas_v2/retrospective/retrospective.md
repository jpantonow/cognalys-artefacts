# Retrospective: subset_revisao_lacunas_v2

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
  - `generation:semantic:existing_test_expectations_rewritten` — open `error_solutions/coordinated_drafts/org.fedorahosted.freeotp_48.apk/0_encrypt/semantic_validation.json`
    > the patch removes or changes what an existing test expects (3 line(s), e.g. 'Assert.assertArrayEquals(tok.getEncoded(), res.getEncoded());'); those values are a contract the project already pins, so t
  - `generation:semantic:public_api_contract_changed` — open `error_solutions/coordinated_drafts/org.fedorahosted.freeotp_48.apk/0_encrypt/semantic_validation.json`
    > ('public', False, 'void', 'test', ()); ('public', False, 'void', 'testUTF8', ())
  - `generation:semantic:checked_exception_not_propagated` — open `error_solutions/coordinated_drafts/org.fedorahosted.freeotp_48.apk/0_encrypt/semantic_validation.json`
    > BadPaddingException; IllegalBlockSizeException; InvalidKeyException; NoSuchPaddingException

## New failure signatures

- `generation:semantic:existing_test_expectations_rewritten` — 1 run(s)

## Recurring across runs

- `generation:semantic:checked_exception_not_propagated` — 5 run(s)
- `generation:semantic:public_api_contract_changed` — 4 run(s)

## Reappeared after being absent

- `generation:semantic:checked_exception_not_propagated` — 5 run(s)
- `generation:semantic:public_api_contract_changed` — 4 run(s)

## Stage history of findings run before

- org.fedorahosted.freeotp_48.apk/encrypt: adjudication → adjudication → adjudication → adjudication → validated → generation

## Model calls

| Stage | Calls | Cache hits | Minutes | Hung | Failed |
|---|---|---|---|---|---|
| adjudication | 1 | 0 | 0.7 | 0 | 0 |
| code_generation.coordinated_file | 6 | 0 | 3.2 | 0 | 0 |
| evidence_extraction | 1 | 0 | 1.0 | 0 | 0 |
| external_research.research | 2 | 0 | 1.9 | 0 | 0 |
| external_research.synthesis | 2 | 0 | 0.7 | 0 | 0 |
| patch_planning | 1 | 0 | 0.4 | 0 | 0 |
| patch_planning.correction | 1 | 0 | 0.9 | 0 | 0 |

---

Signatures come from deterministic rules over this run's artifacts. A pipeline change is working when its signature stops appearing for the findings it targeted.
