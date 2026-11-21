# Retrospective: subset_revisao_lacunas_v3

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
  - `generation:semantic:test_placed_in_unrelated_file` — open `error_solutions/coordinated_drafts/org.fedorahosted.freeotp_48.apk/0_encrypt/semantic_validation.json`
    > mobile/src/androidTest/java/org/fedorahosted/freeotp/MasterKeyTest.java does not cover EncryptedKey; Create a dedicated test file named after the target (for example EncryptedKeyTest) in the target's 
  - `generation:semantic:test_placed_in_unrelated_file` — open `error_solutions/coordinated_drafts/org.fedorahosted.freeotp_48.apk/0_encrypt/semantic_validation.json`
    > mobile/src/androidTest/java/org/fedorahosted/freeotp/KeyStoreAccessTest.java does not cover EncryptedKey; Create a dedicated test file named after the target (for example EncryptedKeyTest) in the targ
  - `generation:semantic:checked_exception_not_propagated` — open `error_solutions/coordinated_drafts/org.fedorahosted.freeotp_48.apk/0_encrypt/semantic_validation.json`
    > BadPaddingException; IllegalBlockSizeException; InvalidKeyException; NoSuchAlgorithmException; NoSuchPaddingException

## Recurring across runs

- `generation:semantic:checked_exception_not_propagated` — 6 run(s)
- `generation:semantic:test_placed_in_unrelated_file` — 3 run(s)

## Reappeared after being absent

- `generation:semantic:test_placed_in_unrelated_file` — 3 run(s)

## No longer seen when the same finding ran again

- org.fedorahosted.freeotp_48.apk/encrypt: `generation:semantic:existing_test_expectations_rewritten` (last in subset_revisao_lacunas_v2; now ends at `generation`)
- org.fedorahosted.freeotp_48.apk/encrypt: `generation:semantic:public_api_contract_changed` (last in subset_revisao_lacunas_v2; now ends at `generation`)

## Stage history of findings run before

- org.fedorahosted.freeotp_48.apk/encrypt: adjudication → adjudication → adjudication → validated → generation → generation

## Model calls

| Stage | Calls | Cache hits | Minutes | Hung | Failed |
|---|---|---|---|---|---|
| adjudication | 1 | 0 | 1.4 | 0 | 0 |
| adjudication.correction | 1 | 0 | 0.4 | 0 | 0 |
| code_generation.coordinated_file | 11 | 0 | 5.5 | 0 | 0 |
| evidence_extraction | 1 | 0 | 1.1 | 0 | 0 |
| external_research.research | 2 | 2 | 0.0 | 0 | 0 |
| external_research.synthesis | 2 | 2 | 0.0 | 0 | 0 |
| patch_planning | 1 | 0 | 1.1 | 0 | 0 |

---

Signatures come from deterministic rules over this run's artifacts. A pipeline change is working when its signature stops appearing for the findings it targeted.
