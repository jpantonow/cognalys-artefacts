# Pipeline-to-prompt mapping for the T+ / S+ process block

`process_block.txt` is meant to be inserted before "Decide what, if anything" in the agents' prompt. This file maps each inventory item to the rule numbers of that block: G1-G6 are the general rules, and N.M is stage N, rule M.

**Sources.** Sections A-C follow the three inventories the workflow handed over: evidence and research, adjudication, and plan and generation. The plan-and-generation inventory arrived truncated after its value-exports item, and no inventories came for the behaviour review, the deterministic checks, redraft/replan, the build or the text. Sections D-H cover those areas from reading `src/` directly (read-only):
- `coordinated_generation.py`: drafter prompt, behaviour review prompt, redraft loop, replan triggers;
- `semantic_validation.py`, `contract_gates.py` and `patch_effect_checks.py`: the checks;
- `validation/repair.py`: the build repair;
- `finding_adjudication.py` and `utils/pr_text.py`: PR summary prompt, issue prompt, text checks.

**Marks.** "partly" in a row means the instruction is in the block but data the pipeline computes or holds is not supplied. "+ inputs" means the data is supplied in the unit's inputs file (`inputs/<unit>.txt`). "W1-W3" are the Stage 6 warnings, which never fail a draft. Section I lists what was left out, and why.

**Wiring.** The block ends with the placeholder `$unit_inputs` (it was `$maintainer_responses`), which takes the whole content of `inputs/<unit>.txt`: first the published rule behind each report, then the maintainer responses. 9.1 still refers to "the build command given at the end of this prompt", which the base prompt supplies. The preamble's "60 minutes" must match `AGENT_TIMEOUT` in common.py (3600 s today).

## Revision 2026-11-14: critic review

Pre-revision copies: `process_block.txt.bak_20261114_precritic`, `mapping.md.bak_20261114_precritic`, `inputs/README.md.bak_20261114_precritic`, `inputs/tools/write_inputs.py.bak_20261114_precritic`, `inputs/tools/prev_20261114_precritic/`.

| # | Point | Verdict | Where |
|---|---|---|---|
| 1 | The C arm gets the rule text in every request | Accepted (checked: `reported_by_rule` in all 23 r1 adjudication requests, `joint_findings_rules` in the 5 joint units, identical across r1-r4 and in c_arm_k3 for the 12 units it has reached) | inputs/<unit>.txt part 1 (write_inputs.py); 2.2 first sentences replaced as proposed; 3.15 states how the rule's unreviewed and non-security shape lists are used (finding_adjudication.py:1389-1459); rows A3, A4, A47, A100, B9, B10, B15, B19 and section I corrected |
| 2 | ISSUE after drafts is stricter in the block | Accepted, with the condition corrected: finding_is_reportable returns true when the violation is confirmed or source-established or the impact is confirmed, and otherwise unless the violation is not_confirmed or the impact non_security. A confirmed violation with no security impact therefore keeps its issue, which the critic's wording ("and you did not judge it 'no security impact'") would have dropped. persist_blocked_issue_candidate also requires a confirmed, grounded cause | 3.31 ISSUE item (a), (b1), (b2); 8.8 (d) refers to (b2) |
| 3 | Stage 6 warnings made hard failures | Accepted. USER_* codes moved to W1 with the critic's sentence; old 6.19 (API_21) deleted, since it fires only under a case policy that is {} in every C-arm request; dispose test moved to W2; TLS filter test is W3. Stage 6 renumbered 6.1-6.26; every reference in the block and this file updated | Stage 6, 5.10, 8.1 |
| 4 | Responses of two joint units belong to the second report | Accepted | write_inputs.py header "... for the rule of the report on <Class>.<method> (<Java API>) ..."; the block's closing line points to "the rule named in their header" |
| 5 | SP cannot run some instructions | Block side accepted: "run gradlew dependencies" removed from 1.20; the test-running sentences of 9.1 removed (9.1 now says the build command does not run tests; 10.14 kept); 8.8 revert sentence reworded as proposed, plus "If your tools cannot delete a file, name it in ANALYSIS.md"; 9.3 points to 8.8. Harness side (build_check `--tests`, SP allowlist, dropping patch.diff for ISSUE/NO_ACTION) not done here: those files are outside this revision. See follow-ups | 1.20, 8.8, 9.1, 9.3 |
| 6 | 60-minute timeout against the process | Accepted, second option (declared addition): the preamble's time-budget sentence | Preamble |
| 7 | Same failure scores differently | Block side accepted: 10.22 makes the first line "Reason: <name>", and 3.31 names the reasons "the words before its colon". Mapping "Reason: correction not generated" to FALHA in judges/cards.py is a harness change, listed in the follow-ups | 3.31, 10.22 |
| 8 | Holdout leak through research | Accepted: 2.5 "the affected library's release notes or fix commit"; G6 added. Neutral directory names for TP/SP (run_one.py:209, work/<unit>/<arm>_r<k>/repo, "pr_" prefix) are a harness change, listed in the follow-ups; until then it must be declared | 2.5, G6 |
| 9 | 3.14 routing sentence states an expected answer | Accepted as proposed | 3.14; 8.6 now says "as a violation without security impact" (_close_provider_iv_replacement), and 3.31 cites 8.6 for the provider-IV reason |
| 10 | 5.2 contradicts 4.9 | Accepted as proposed (coordinated_generation.py:3311-3313 verified) | 5.2; row C-N3 |
| 11 | Library defects end differently | Accepted; "defect in third-party code" dropped from the NO_ACTION reasons | 3.31 library item |
| 12 | Backend digest migration path missing | Accepted with one wording change: "which would have to accept a new value" in place of "which could accept", because the gate requires backend_change_required (finding_processing.py:908-927) | 3.6 Digest, first item; rows B70 and H |
| 13 | AI sentence from contribution guidelines | Accepted (pr_generator.py:1326-1336, 1422-1424 verified) | 10.15 |
| 14 | 9.2 re-reviews more than the pipeline | Accepted. Stage 6 is still repeated: repair.py:1029 re-runs semantic validation | 9.2 |
| 15 | 10.4 could allow the rule's name | Accepted as proposed | 10.4 |
| 16 | git diff hides new files | Accepted as proposed | Stage 6 heading |
| 17 | 10.17 misses jargon entries | Accepted. The contextual words (allowlist, compliant, compliance) carry the pipeline's own condition: banned unless the code itself uses the word | 10.17 |
| 18 | Contributor handle in the inputs | Accepted: redacted to "@<contributor>" and "<commit>", recorded in inputs/README.md and counted per unit in inputs_report.json. "CrySL rule" and "_MessageDigestSpec_" stay verbatim | inputs |
| 19 | Two headers say the same thing | Accepted: the block's closing line now only orders the two parts; each part carries its own header | closing line |
| 20 | Pre-registration and arm version | Not done here (outside the files of this revision); required before any TP/SP run, see follow-ups. The k3 check is extended: rule sections and maintainer responses are identical between c_arm_8be9f78 and c_arm_k3 for all 12 units k3 has reached (10 co_*, pr_013d2748f3f8, pr_06ff0c1fc96c); the other 8 pr_* units must be rechecked when k3 reaches them | - |

No point was rejected outright. Points 2 and 12 were applied with a corrected condition. The harness parts of 5, 7, 8 and 20 are left as follow-ups, because this revision changes only plus_prompt/.

**Additions that are not pipeline behaviour (declared).** The 60-minute time budget (preamble). G6, which states for the agents the holdout the pipeline enforces by configuration. "If your tools cannot delete a file, name it in ANALYSIS.md" (8.8). "It builds the app; it does not run tests" (9.1), which describes the harness tool.

**Harness follow-ups required before any TP/SP run.**
1. Write a timestamped revision of docs/preregistro_bracos_mais_20261114.md. Replace common.PLUS_BLOCK with process_block.txt, substituting `$unit_inputs` per unit from inputs/<unit>.txt. Regenerate units/*/prompt_plus.txt and prompt_plus.sha256, and check them against plus_prompt/sha256.txt.
2. judges/cards.py: an agent NO_ACTION whose first line is "Reason: correction not generated" becomes FALHA, as the C arm's "correção não gerada" does.
3. run_one.py/build_check.py: for ISSUE/NO_ACTION deliverables, either drop patch.diff or let SP delete the files it created (SP's allowlist has no rm or git restore). Optionally add a build_check `--tests` mode that runs the same phases for TP and SP.
4. Give the TP/SP working directories neutral names (no "pr_"/"co_" unit prefix), or declare the prefix.
5. Recheck the per-unit inputs against c_arm_k3 for the remaining pr_* units.

**Context check behind several "left out" rows.** I parsed the r1 `patch_planning` requests of the C arm (`frozen_runs_20261113/c_arm_8be9f78`, 17 requests). None of them carried values for `previous_attempts_of_this_finding`, `verified_repository_imports`, `parameter_policies`, `reporting_detector_site`, `case_migration_policy` or `co_reported_findings`. Leaving that data out of the agents' prompt therefore matches what Cognalys actually received in this comparison.

## A. Evidence and research

| # | Pipeline behaviour (short) | Where | Block rule |
|---|---|---|---|
| 1 | Wider repository context (callers, overrides, tests, contracts) before any decision | finding_processing.py:669-690; finding_context.py:94-137 | 1.10-1.13, 1.22 |
| 2 | Whole reported file as top-priority exact source | evidence_packet.py:422-441 | 1.1 |
| 3 | Verified rule as required evidence; scope text from another app marked | evidence_packet.py:443-450 | 2.2 + inputs (the C arm's `reported_by_rule`, rendered per report; another app's text flagged by one sentence of 2.2, not per item) |
| 4 | Each joint finding's own verified rule | evidence_packet.py:452-463 | 2.2 + inputs (`joint_findings_rules`, rendered under each joined report) |
| 5 | Order of governed calls against the rule's order, with its limits | evidence_packet.py:465-488 | 1.6 |
| 6 | Every use of a field assigned from a governed call | evidence_packet.py:490-526 | 1.6 |
| 7 | Where the method's value goes through callers | evidence_packet.py:528-544 | 1.12 |
| 8 | Stored-value contract: every writer and reader; incomplete scope when a reader is missing | finding_context.py:283-299 | 1.13, 3.23, 1.23 |
| 9 | Storage keys with no reader | persisted_value_readers.py | 1.13, 3.23 |
| 10 | Deliberate-insecurity reading (suppressions, configuration, comments, gates) | deliberate_insecurity.py | 1.14 |
| 11 | Deliberate insecurity never refutes the violation | finding_adjudication.py:575-579 | 3.21 |
| 12 | Rename-safety assessment and the files it must edit | finding_context.py:164-188 | 1.21, 3.33, 4.11 |
| 13 | Typed declarations receiving the method's value | finding_context.py:218-253 | 1.12, 4.10 |
| 14 | In-process runtime-discriminator assessment | evidence_packet.py:576-590 | 1.12, 3.5 |
| 15 | Related files mandatory by scope; tests rank high | evidence_packet.py:405-419, 592-608 | 1.10-1.16, 1.22 |
| 16 | Callers from the semantic index, with a lexical fallback | evidence_packet.py:610-651 | 1.10 |
| 17 | Ambiguous call relations with every candidate | evidence_packet.py:653-664 | 1.10 |
| 18 | Related tests | evidence_packet.py:666-682 | 1.16 |
| 19 | Definitions of in-repository callees | evidence_packet.py:684-697 | 1.9 |
| 20 | Constants and enums fixing a governed value | evidence_packet.py:699-710 | 1.7 |
| 21 | Project documentation as evidence | evidence_packet.py:712-725 | 1.17, 3.21, 10.20 |
| 22 | Layer conversions and setting round-trips | evidence_packet.py:727-752 | 1.13, 3.23 |
| 23 | Construction sites of the reported class | evidence_packet.py:754-768 | 1.11 |
| 24 | Manifest declaration of the component | evidence_packet.py:770-781 | 1.15, 3.6 |
| 25 | Key/IV/salt/seed built from compiled-in values | evidence_packet.py:115-132 | 1.8 |
| 26 | Constant material violates a randomness requirement | finding_adjudication.py:554-564 | 3.12 |
| 27 | Runtime measurement and what it can or cannot establish | evidence_packet.py:81-112 | G5 (partly: only the message's values reach the agent) |
| 28 | Build context: SDK levels, variants | evidence_packet.py:806-814 | 1.15 |
| 29 | Dependency context (resolution, direct or transitive) | evidence_packet.py:815-844 | 1.20 (partly: no cached artifact records) |
| 30 | Dependency or deprecation grounding | evidence_packet.py:846-869 | 1.20 |
| 31 | Repository replacement idioms and the deprecated API's usage contract | finding_context.py:255-281 | 1.20, 4.12 |
| 32 | A replacement not yet written is not missing evidence | finding_adjudication.py:566-574 | 3.28 |
| 33 | APK bytecode for classes missing from the checkout | evidence_packet.py:894-920 | left out (I) |
| 34 | META-INF library versions from the APK | evidence_packet.py:921-935 | left out (I) |
| 35 | Ranking and 140k character budget | evidence_packet.py:937-993 | 1.22 (reading order only; budget left out) |
| 36 | Evidence ids, trust levels, hashes | evidence_packet.py:995-1007 | G1 (partly) |
| 37 | Exact input sizes and cipher block size | source_facts.py:1208-1222 | 1.18, 3.26 |
| 38 | Writers of shared state | source_facts.py:1482-1496 | 1.18, 3.26 |
| 39 | Value reaching a security-relevant sink | source_facts.py:1141-1155 | 1.12 |
| 40 | Scope completeness per area | evidence_packet.py:1033-1085 | 1.23 |
| 41 | Absence in scope is not confirmed absence | llm_contracts.py:190-198 | G2 |
| 42 | Other findings in the class; echo marker | generate_error_fixes.py:198-227 | 3.30, 4.7 (partly: the harness lists only the unit's findings) |
| 43 | Co-reported finding's concluded outcome passed on | generate_error_fixes.py:230-310 | 4.7 (partly: no state across findings) |
| 44 | Joining findings of one sequence into a unit | generate_error_fixes.py:434-621 | 3.30 (partly: the harness gives the unit as joined) |
| 45 | Vendored file signal | source_facts.py:52-66 | 1.5, 10.13 |
| 46 | Resolved by simple name only; app-caller attribution | evidence_packet.py:1121-1130 | 1.2, 1.5 |
| 47 | Violated predicate and its rule section | evidence_packet.py:1131-1139 | 1.3 + inputs (the primary finding's `reported_violation` and `reported_violation_section`; joined findings carry none in the C payload) |
| 48 | Raw detector report as context only | evidence_packet.py:1140-1165 | G5 |
| 49 | Parameter policies stated upfront | evidence_packet.py:1170-1174 | 3.10 for the message's allowlist; policy data left out (absent in C-arm requests) |
| 50 | Specification externally anchored | evidence_packet.py:1184-1191 | 2.2 |
| 51 | Derived fact: call binding | evidence_facts.py:237 | 1.4 |
| 52 | Derived fact: detector rerun on the checkout | evidence_packet.py:240-249 | left out (I) |
| 53 | Derived fact: artifact ownership | evidence_facts.py:95 | 1.5 |
| 54 | Derived fact: specification applicability | evidence_facts.py:169 | 1.4 |
| 55 | Derived fact: caller contract | evidence_facts.py:605 | 1.10 |
| 56 | Derived fact: editable text reaching an unguarded parser | evidence_facts.py:874 | 1.19 |
| 57 | Derived fact: origin of key/IV/salt bytes | evidence_packet.py:269-274 | 1.8 |
| 58 | Derived fact: IV provenance (provider-random) | evidence_packet.py:275-279 | 1.8, 3.14 |
| 59 | Derived fact: parameter producers | evidence_packet.py:280-287 | 1.7 |
| 60 | Derived fact: key algorithm mismatch | evidence_packet.py:330-334 | 1.8 |
| 61 | Derived fact: checker's own code at the reporting line | fix_generation_common.py:171-184 | left out (I); 2.2 (published rule) is the nearest step |
| 62 | Derived fact: policy evidence (certificate chains, recomputed digests) | evidence_packet.py:293-302 | left out (I) |
| 63 | Derived-fact statuses are limits, not negatives | evidence_packet.py:303-311 | G3 |
| 64 | Value exports, input labels, app purpose | fix_generation_common.py:355-427 | 1.12, 1.17, 3.23 |
| 65 | Specification-family policy documents | research_questions.py:417-439; data/llm_policies | 3.5, 3.6 (common, TLS, digest, cipher and key-generation rules copied in plain words) |
| 66 | Extraction as its own stage before any decision | finding_adjudication.py:521-531 | 3.1 |
| 67 | Predicates stated positively, three statuses | llm_contracts.py:126-146 | 3.2 |
| 68 | Reasons for unknown; detector modelling never a reason | llm_contracts.py:147-173 | 3.2 |
| 69 | Rule section per predicate | llm_contracts.py:174-186 | 3.2 |
| 70 | Claims with epistemic status, flows, relations, open questions | llm_contracts.py:106-113 | G1, 3.1, 3.3 |
| 71 | Family-specific assessment fields | llm_contracts.py:199-253 | 3.5 |
| 72 | Citations required; sanitiser downgrades | adjudication_rules.py:179-212 | G1 (partly: nothing enforces it) |
| 73 | Function contract (purpose, counterpart, activation, dependence with quotes) | finding_adjudication.py:449-477 | 3.4 |
| 74 | Function contract verified, kept from adjudication | adjudication_rules.py:215-337 | 3.4 (partly: kept apart by instruction only) |
| 75 | Analyzer record outranks published rule text | finding_adjudication.py:424-448 | 3.10 (partly: no label catalogue) |
| 76 | Extractor's needs-more-evidence list | llm_contracts.py:270-273 | 3.3 |
| 77 | Exactly one extra extraction round | finding_context.py:388-488 | 3.3 (partly: "one focused follow-up search") |
| 78 | Requests classified: source or runtime | evidence_requests.py:30-238 | 3.3 |
| 79 | Runtime-only requests answered from the exploration record | evidence_requests.py:601-651 | 3.3 (partly: coverage not supplied; interpretation rule kept) |
| 80 | Android resource requests | evidence_requests.py:654-857 | 1.15 |
| 81 | Named file requests | evidence_requests.py:415-455 | 3.3 |
| 82 | Call-binding requests | evidence_requests.py:541-598 | 1.4 |
| 83 | Ownership, applicability, callers, value producers on request | evidence_requests.py:865-972 | 1.4, 1.5, 1.7, 1.10 |
| 84 | Type declared in a differently named file | evidence_requests.py:344-391 | 1.2 |
| 85 | Every source line using a member | evidence_requests.py:458-520 | 1.6, 1.10 |
| 86 | Test and build requests | evidence_requests.py:154-160 | 1.15, 1.16 |
| 87 | Size caps on supplementary evidence | evidence_requests.py:162-171 | left out (I) |
| 88 | Declined requests recorded with reasons | evidence_requests.py:975-991 | 3.3 |
| 89 | Numbering of fetched entries | evidence_requests.py:1168-1187 | G1 (partly) |
| 90 | Fetch-and-retry after an evidence-blocked "no safe fix" | finding_context.py:323-386 | 3.29 |
| 91 | Web research when enabled | finding_adjudication.py:611 | Stage 2 |
| 92 | Rule questions only for anchored specifications | finding_adjudication.py:617-655 | 2.2 |
| 93 | Three API-scoped questions | research_questions.py:356-414 | 2.3 |
| 94 | Exact matching clause of the rule | research_questions.py:233-257 | 2.2 |
| 95 | Advisory questions | finding_adjudication.py:686-710 | 2.5 |
| 96 | Lifecycle questions (deprecation, dependency) | research_questions.py:41-130 | 2.5 |
| 97 | Non-literal getInstance argument resolved from official sources | finding_adjudication.py:656-738 | 1.7 |
| 98 | Shared part and case part | finding_adjudication.py:739-785 | 2.1 (partly: no sharing across findings) |
| 99 | Case question: does a standard fix the value? | research_questions.py:260-272 | 2.4 |
| 100 | Verified detector semantics and false-positive shapes | research_questions.py:151-230 | 2.2, 3.15 + inputs (detector_semantics, unreviewed and non-security shapes as the C arm got them; no rule of these units has reviewed shapes) |
| 101 | Family policy in research | finding_adjudication.py:774, 783 | 3.6 |
| 102 | Researcher instructions (official sources, no exploitation, untrusted) | finding_adjudication.py:786-807 | 2.6, 2.7; G6 and 2.5 ("the affected library's") keep the research off this repository's later history (partly: stated, not enforced) |
| 103 | Research output schema | llm_contracts.py:81-94 | 2.7 |
| 104 | Two passes: brief, then synthesis | llm_providers.py:2428-2508 | 2.7 (partly) |
| 105 | Research reasoning effort | llm_providers.py:678-700 | left out (I) |
| 106 | Research failure handling | finding_adjudication.py:830-860 | 2.7 |
| 107 | Merge and de-duplicate sources by URL | research_questions.py:309-353 | G1 (partly) |
| 108 | External evidence establishes documentation, never this code | finding_adjudication.py:1273-1288 | 2.8 |
| 109 | Detector allowlist outranks web rule text | finding_adjudication.py:1297-1307 | 3.10 |
| 110 | Research corpus across runs | research_corpus.py | left out (I) |
| 111 | First research answer wins within a finding | llm_providers.py:2443-2453 | 2.1 |
| 112 | Corpus tooling | research_corpus.py:237-549 | left out (I) |
| 113 | Learning context (imports, earlier runs, maintainer responses) | fix_generation_common.py:106-168 | 3.32 + inputs part 2 (partly: only the other-repository responses the C arm received) |
| 114 | Maintainer positions only (bots removed) | pr_outcomes.py:285-315 | 3.32 (the harness did the filtering) |
| 115 | Weighing rules for maintainer responses | fix_generation_common.py:234-252 | 3.32, 7.16 |
| 116 | Adjudication decides from the validated extraction | finding_adjudication.py:1003-1064 | 3.1 (partly) |
| 117 | Artifacts per finding (packet, research, extraction, requests) | finding_adjudication.py:884-891 | Preamble (ANALYSIS.md sections) |

## B. Adjudication

| # | Pipeline behaviour (short) | Where | Block rule |
|---|---|---|---|
| 1 | Extraction and adjudication as two calls | finding_adjudication.py:521-582, 1003-1017 | 3.1 (partly: one context) |
| 2 | Positive predicates, status, reason, rule section | llm_contracts.py:126-189 | 3.2 |
| 3 | Never undecided because of detector modelling | llm_contracts.py:156-173 | 3.2 |
| 4 | Sanitiser and forced fallback to unknown | adjudication_rules.py:179-433 | G1, 3.34 (partly) |
| 5 | Detector report is context; runtime value; constant material | finding_adjudication.py:539-564 | G5, 3.12 |
| 6 | Missing evidence is not negative evidence | finding_adjudication.py:565 | G2 |
| 7 | Separate judgements for cause, violation, impact, compatibility, scope | llm_contracts.py:392-412 | 3.7 |
| 8 | Very high bar for dismissals | adjudication_rules.py:448-548 | 3.15 (partly: self-applied) |
| 9 | Reviewed false-positive shapes | finding_adjudication.py:1376-1388 | inputs render them when present; no rule of these 20 units has any (all their shapes are unreviewed) |
| 10 | Unreviewed shapes never dismiss; a match that literally breaks the rule is a violation without security impact | finding_adjudication.py:1389-1404 | 2.2, 3.15 + inputs |
| 11 | A shape matches only on a property from evidence | finding_adjudication.py:1405-1420 | 3.15 |
| 12 | Predictable generator | finding_adjudication.py:269-282 | 3.13 |
| 13 | A compliant form exists, or an external contract fixes the value | finding_adjudication.py:1422-1430 | 3.17 |
| 14 | Null or omitted platform default | finding_adjudication.py:1430-1450 | 3.18 |
| 15 | Non-security violation shapes still get a change | finding_adjudication.py:1451-1459 | 3.15, 3.17, 3.27 + inputs (the shape lists of MacSpec, MessageDigestSpec, KeyStoreSpec, KeyGeneratorSpec) |
| 16 | Collateral violations of other requirements | finding_adjudication.py:1460-1472; issue_candidates.py:381-446 | 3.9, 3.31 |
| 17 | Matched event established from source | finding_adjudication.py:1479-1499 | 3.8 |
| 18 | Rule overmatch (other type or arity) | finding_adjudication.py:1522-1539 | 3.16 (partly: no deterministic arity route) |
| 19 | Verified rule is authoritative on what the detector matches | finding_adjudication.py:417-422 | 2.2 + inputs |
| 20 | Detector rendered no message | finding_adjudication.py:1185-1197 | 3.11 |
| 21 | Analyzer record outranks alternatives in the rule text | finding_adjudication.py:478-507 | 3.10 (partly) |
| 22 | Allowlist authority; each list for its own call | finding_adjudication.py:1297-1307 | 3.10 |
| 23 | Detector's own source at the reporting line | finding_adjudication.py:1050 | left out (I) |
| 24 | The finding is only the check raised | finding_adjudication.py:1115-1122 | 3.9 |
| 25 | Cause about the reported rule section | finding_adjudication.py:1606-1612 | 3.9 |
| 26 | Class resolution; app-caller attribution | finding_adjudication.py:1596-1605 | 1.2, 1.5 |
| 27 | Vendored source is a publication note | finding_adjudication.py:1540-1546 | 1.5, 10.13 |
| 28 | Third-party findings need an authority for an upgrade | finding_adjudication.py:1312-1346 | 3.25 |
| 29 | Derived facts read literally; limit statuses are not negatives | finding_adjudication.py:1547-1595 | 1.4, 1.5, 1.8, 1.10, 1.19, G3 (partly: no bytecode binding) |
| 30 | Flow evidence to a security sink | finding_adjudication.py:1176-1184 | 1.12, G2 |
| 31 | Size evidence | finding_adjudication.py:1263-1272 | 3.26 |
| 32 | Mutation evidence | finding_adjudication.py:1500-1521 | 3.26 |
| 33 | Converter and round-trip evidence; self-describing records | finding_adjudication.py:1347-1368 | 3.23 |
| 34 | Project documentation; never ask what the docs answer | finding_adjudication.py:1369-1375 | 1.17, 3.21, 10.20 |
| 35 | Field-use sequence | finding_adjudication.py:1613-1616 | 1.6 |
| 36 | Deprecated-API replacement evidence; device behaviour is a note | finding_adjudication.py:1242-1262 | 1.20, 3.28, 4.12 |
| 37 | External research in two parts | finding_adjudication.py:603-891 | Stage 2 |
| 38 | TLS guidance | finding_adjudication.py:1289-1311 | 3.6 |
| 39 | Digest fixed by a public standard | finding_adjudication.py:1154-1165 | 3.19 |
| 40 | Impact independent of repair | finding_adjudication.py:1137-1141 | 3.27 |
| 41 | Decision vocabulary per family | llm_contracts.py:279-357 | 3.31 (partly: collapsed to PR, ISSUE, NO_ACTION) |
| 42 | Deterministic validation of a generating decision | adjudication_rules.py:551-668 | 3.34 (partly) |
| 43 | Publication blockers judged against what validation runs | finding_adjudication.py:1208-1227 | 3.28 (partly) |
| 44 | Blocker categories | llm_contracts.py:306-391 | 3.28 |
| 45 | External contract scope | finding_adjudication.py:1142-1153 | 3.24 |
| 46 | Correct-use answer | finding_adjudication.py:1166-1175 | 3.20 |
| 47 | Joined units adjudicated together | finding_adjudication.py:1093-1103 | 3.30 |
| 48 | Echo: the cause is the producer's | finding_adjudication.py:1107-1114 | 3.30 (partly: echo not labelled by the harness) |
| 49 | Peer hardening | fix_generation_common.py:330-351 | 3.22 |
| 50 | Checker view | fix_generation_common.py:272-291 | 4.6 |
| 51 | Provider-generated IV: do not block on the producer, do not make the code supply the IV | finding_adjudication.py:1078-1092 | 3.14 (routing left to the other rules, as in the prompt), 8.6 (partly: no deterministic short-circuit) |
| 52 | Stored value nobody reads | persisted_value_readers.py:321-336 | 3.23 |
| 53 | Deliberate insecurity shapes the fix | deliberate_insecurity.py:590-612 | 3.21 |
| 54 | Value exports and app purpose | fix_generation_common.py:354-427 | 3.23, 1.17 |
| 55 | Renames never block | finding_adjudication.py:285-295 | 3.33 |
| 56 | Case policy; bounded scope | finding_adjudication.py:1198-1207 | 3.24 (partly: no case-policy data; absent in C-arm requests) |
| 57 | Maintainer responses in three groups | finding_adjudication.py:1036-1077 | 3.32 (partly: one group, as in the C arm) |
| 58 | Holdout of the own repository | fix_generation_common.py:106-168 | left out (I); the effect is reproduced by the inputs, and G6 states it for the agent |
| 59 | Blockers the checkout answers are discharged | adjudication_rules.py:743-857 | 3.29 |
| 60 | Correction loop (up to two corrections) | finding_adjudication.py:378-412, 1698-1805 | 3.34 (partly) |
| 61 | Repair blocked only on compatibility goes to review | adjudication_rules.py:1534-1621 | 3.28 |
| 62 | Human-decided blockers promoted to review | adjudication_rules.py:1104-1197 | 3.28 |
| 63 | Correct-use promotion is single-round | adjudication_rules.py:1112-1127 | 3.35, 8.7 |
| 64 | Published-advisory authority | adjudication_rules.py:1200-1316 | 2.5, 3.25 (partly; does not occur for RVSec findings) |
| 65 | Evidence-gap promotion (RVSec observed the call) | adjudication_rules.py:1370-1531 | 3.29, 3.31 |
| 66 | Post-adjudication evidence fetch | finding_adjudication.py:1806-1871 | 3.29 |
| 67 | API-migration promotion | migration_policy.py:628- | 3.28, 4.12 (partly; outside the crypto specifications) |
| 68 | Incomplete scope demotes to review | adjudication_rules.py:671-740 | 3.28 |
| 69 | Review-only label normalisation | adjudication.py:195-225 | left out (I) |
| 70 | Digest-decision gates, including the review-only backend and multi-repository migrations (BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY, MULTI_REPOSITORY_MIGRATION_SUPPORTED: contract, rollout strategy, PR note) | finding_processing.py:780-934; disposition.py:45-64 | 3.6 (Digest, first item) |
| 71 | Skip when our PR is open or merged, or the file is not on the base branch | finding_processing.py:202-260 | left out (I) |
| 72 | Deterministic labels; word-match triage retired | false_positive_rules.py | 3.15 (partly) |
| 73 | Final routing of findings stopped before any draft (an issue only with confirmed impact: blocked_without_confirmed_impact) | finding_processing.py:346, 1131-1258 | 3.31 (b1) |
| 74 | Issue gate (confirmed cause, grounded, reportable, GitHub, library repository) | issue_candidates.py:44-536 | 3.31 (a), (b2), library item (the default-branch check is left out, I) |
| 75 | Non-security and cosmetic findings still become PRs | finding_adjudication.py:1137-1141, 1294-1296 | 3.27 |
| 76 | Outcomes after drafts (correct use, shortfall, else persist_blocked_issue_candidate gated by finding_is_reportable) | finding_processing.py:1352-1398; issue_candidates.py:79-93 | 8.8, 3.31 (b2) |
| 77 | Outcome categories in the statistics | finding_statistics.py:695-870 | 3.31, 10.22 (the "Reason:" first line names the category) |
| 78 | Publication verdict after validation | disposition.py:222-454 | 9.3, 10.13 (partly) |
| 79 | Joining findings into units | generate_error_fixes.py:392-589 | 3.30 (partly) |
| 80 | Only the app's own packages are analysed | integrations/rvsec.py:334-340 | left out (I) |
| 81 | Lambda and coroutine resolution | extract_methods_single_csv.py:265-459 | 1.2 |
| 82 | Kotlin name resolution | extract_methods_single_csv.py:2707-2710, 311-383 | 1.2 |
| 83 | Kotlin Multiplatform source sets | extract_methods_single_csv.py:462-489; disposition.py:214-220 | 1.2, 6.26 |

## C. Plan and generation (inventory, as far as it reached)

| # | Pipeline behaviour (short) | Where | Block rule |
|---|---|---|---|
| 1 | Plan only when generation is supported | finding_adjudication.py:1948-1961 | 4.1 |
| 2 | Allowed test roots: the module's unit-test set | finding_adjudication.py:1963-1983 | 4.2 |
| 3 | Planning input sections | finding_adjudication.py:1984-2065 | 4.1 and Stage 1 (partly) |
| 4 | At least one edit, or the reasons each candidate fails | finding_adjudication.py:2072-2077 | 4.1 |
| 5 | Joint-unit planning | finding_adjudication.py:2078-2088 | 3.30, 4.7 |
| 6 | Grouping into joint units | generate_error_fixes.py:434-520 | 3.30 (partly) |
| 7 | Peer hardening in planning | fix_generation_common.py:337-351 | 3.22 |
| 8 | Joint-finding effect rule | fix_generation_common.py:318-327 | 4.4, 6.9 |
| 9 | Replan input after a regression | finding_adjudication.py:2031-2032 | 8.2 (a)(b) (partly: self-review) |
| 10 | Replan input after a no-effect verdict | finding_adjudication.py:2033-2036 | 8.2 (c) |
| 11 | Replan input after draft failures | finding_adjudication.py:2037-2041 | 8.2 (d) |
| 12 | Typed consumers | typed_consumers.py:421-429 | 4.10 |
| 13 | Co-reported findings in the same class | finding_adjudication.py:2109-2122 | 4.7 |
| 14 | Only files with exact source; new file only a test | finding_adjudication.py:2122-2125 | 4.2 |
| 15 | Tests optional; when to add one | finding_adjudication.py:2125-2133 | 4.13 |
| 16 | No edits to unrelated tests | finding_adjudication.py:2133-2135 | 4.13, 6.24 |
| 17 | No test source set: a reviewer note | finding_adjudication.py:2135-2140 | 4.13 |
| 18 | Tests use only declared libraries | finding_adjudication.py:2140-2145 | 4.13, 6.24 |
| 19 | Multi-file needs two files; edits cite claims | finding_adjudication.py:2146-2148 | 4.1 (partly) |
| 20 | No comment-only edits | finding_adjudication.py:2148-2150 | 4.14, 6.2 |
| 21 | Minimal scope; the new value in place | finding_adjudication.py:2155-2171 | 4.3 |
| 22 | Parameter policies | contract_gates.py:541-680 | left out (I): the instruction needs per-case policy data, absent in C-arm requests |
| 23 | No conditional edits | finding_adjudication.py:2172-2177 | 4.5 |
| 24 | Each edit changes what the reported constraint checks | finding_adjudication.py:2177-2179 | 4.4 |
| 25 | Make the checker's condition pass | finding_adjudication.py:2180-2184 | left out (I) |
| 26 | Persisted object formats | finding_adjudication.py:2184-2191 | 4.9 |
| 27 | Runtime-monitor rule | fix_generation_common.py:203-231 | 4.6 |
| 28 | Same-parts carrier | fix_generation_common.py:260-269 | 4.6 |
| 29 | Checker-view rule | fix_generation_common.py:280-291 | 4.6 |
| 30 | Comment preservation | fix_generation_common.py:301-309 | 5.9, 6.25, 8.4 |
| 31 | Value exports | fix_generation_common.py:355-366 | 3.23, 10.9 |
| N1 | The drafter never sees invariants or forbidden changes | adjudication.py:381-398 | 5.1 (kept for stage 7) |
| N2 | No-test reason asked for, but no field in the schema | finding_adjudication.py; llm_contracts.py:456-473 | 4.13 (the reason is recorded in ANALYSIS.md) |
| N3 | Drafter: "Generate all edits required for target_file by target_plan ... Persisted file-format behavior is outside the authorized change" | coordinated_generation.py:3311-3313 | 5.2 ("only as far as the plan's required edits do"). The drafter gets both sentences, so the plan's migration edits (4.9) are authorised; the earlier wording contradicted 4.9 in a single context |

## D. Rest of the plan prompt and the drafter prompt (read from source)

| Pipeline behaviour | Where | Block rule |
|---|---|---|
| No-reader guidance in planning | finding_adjudication.py:2197 | 3.23 |
| Deliberate-insecurity guidance in planning | finding_adjudication.py:2198 | 3.21 |
| Dropped-input invariant | fix_generation_common.py:388-395 | 4.8 |
| Deprecated migration: recommended mechanisms, deviations named | finding_adjudication.py:2200-2213 | 4.12 |
| Keep entry-point names unless they state the removed behaviour | finding_adjudication.py:2213-2216 | 4.11 |
| Rename rule; plan every reference | finding_adjudication.py:2216-2218 | 3.33, 4.11 |
| Previous attempts of the finding | finding_adjudication.py:2220-2222 | 4.15 (partly: within the session only) |
| Maintainer responses in planning | finding_adjudication.py:2222-2231 | 3.32 |
| Stored verifier migration (readers and writers agree, emergency values, after authentication) | finding_adjudication.py:2234-2242 | 4.9 |
| Plan validation (path has exact source, claims, at least one edit, no blockers, symbols exist, stored scope complete) | semantic_validation.py:7471-7578 | 4.1, 4.2 (partly) |
| Drafter: no_change_required for a file that already fits; reported file always edited | coordinated_generation.py:3135-3145 | 5.3 |
| Drafter: test imports limited to the declared classpath | coordinated_generation.py:3140-3146 | 4.13, 5.10 |
| Drafter: build-file edits keep the declaration style | coordinated_generation.py:3152-3157 | 5.4 |
| Drafter: compatibility doubts are notes, not abstention | coordinated_generation.py:3158-3160 | 3.28, 10.13 |
| Drafter: keep public signatures; rename route order 1-2-3; no unused helper | coordinated_generation.py:3160-3176 | 4.11, 5.5 |
| Drafter: rename locals embedding the removed value; same visibility | coordinated_generation.py:3176-3181 | 5.5 |
| Drafter: deprecated invariants; no swallowed Future exceptions | coordinated_generation.py:3181-3185 | 4.12, 5.6 |
| Drafter: runtime monitor, carriers, comments, joint effect | coordinated_generation.py:3186-3189 | 4.4, 4.6, 5.9 |
| Drafter: verified imports from passing builds | coordinated_generation.py:3190-3192 | left out (I): absent in C-arm requests (holdout) |
| Drafter: maintainer-response shaping (setting or debug condition, persisted checksum) | coordinated_generation.py:3194-3206 | 3.32 |
| Drafter: no hand-built queue over a not-recommended mechanism | coordinated_generation.py:3207-3209 | 4.12 |
| Drafter: checked throws compilable; Kotlin internal or private | coordinated_generation.py:3212-3217 | 5.5, 5.6 |
| Drafter: declaration scope comes from brace nesting | coordinated_generation.py:3218-3229 | 5.7 |
| Drafter: no owner kept alive; dispose replaced wrappers; package seam over reflection | coordinated_generation.py:3230-3234 | 5.8, 5.10 |
| Drafter: delete edit kind; verbatim original_code; one file per call | coordinated_generation.py:3147-3151, 3234-3237 | left out (I): edit-format mechanics |
| Drafter: tamper tests assert the cause; lifecycle dispose test; known-value assertions | coordinated_generation.py:3238-3241; fix_generation_common.py:374-381 | 5.10 |
| Drafter: stale test files, fix only diagnosed errors | coordinated_generation.py:3242-3256 | 5.10, 9.2 |
| Drafter: production values only as plan names | coordinated_generation.py:3260-3268 | 4.3 |
| Drafter: runtime discriminator, remove the digest, keep mappings | coordinated_generation.py:3269-3274 | 3.6, 6.18 |
| Drafter: no equality on types without equals | coordinated_generation.py:3275-3279 | 5.10 |
| Drafter: call only what the sibling API surface shows | coordinated_generation.py:3280-3293 | 5.8 |
| Drafter: a retry's failure codes are mandatory | coordinated_generation.py:3298-3305 | 8.1 |
| Kept files' reliance on declarations | coordinated_generation.py:566-575 | 8.1 |

## E. Deterministic checks (code to block rule)

| Check code(s) | Block rule |
|---|---|
| REPORTED_VALUE_UNTOUCHED, REPLACEMENT_VALUES_NOT_APPLIED | 6.1, 9.4 |
| COMMENT_ONLY_CHANGE | 6.2 |
| STATEMENT_WITHOUT_EFFECT, GOVERNED_CALL_RESULT_DISCARDED, DISCARDED_CONSTRUCTION | 6.3 |
| WRAPPED_SAME_OBJECT, SAME_PARTS_APP_CARRIER, SAME_PARTS_APP_KEY, SAME_OBJECT_REBUILT | 6.4 |
| FALLBACK_TO_ORIGINAL | 6.5 |
| SAME_VALUE_UNOBSERVED_API | 6.6 |
| DELEGATING_ALIAS_CALL | 6.7 |
| CONSTANT_KEY_MATERIAL_KEPT | 6.8 |
| JOINT_FINDING_EDIT_WITHOUT_EFFECT | 6.9 |
| EXPLICIT_DEFAULT_CHANGE_TOUCHES_MORE | 6.10 |
| USER_SETTING_REMOVED, USER_OPTION_NEUTRALIZED, CALLER_SUPPLIED_VALUE_OVERRIDDEN, REJECTS_CALLER_SUPPLIED_VALUE (MAINTAINER_DECISION_CODES, demoted to warnings with maintainer_decides, semantic_validation.py:6403-6421) | W1, 10.12; whether it is a regression: 7.6, 7.8 |
| DROPPED_INPUT_NO_LONGER_CONTRIBUTES | 6.11 |
| STORED_VALUE_READERS_NOT_UPDATED, STORED_DERIVED_VALUE_CHANGED, SHIPPED_CIPHERTEXT_UNREADABLE, SHIPPED_CIPHERTEXT_REMOVED, STORED_VALUE_SCOPE_INCOMPLETE | 6.12, 8.2 (e) |
| TLS_CONTEXT_VERSION_PINNED, PINNED_NEGOTIATED_PROTOCOL, VALUE_STILL_CONFIGURES_OTHER_API, ACCEPT_ALL_STILL_DEFAULT, PROTOCOL_FILTER_REDUNDANT_FOR_CLIENT, TLS_LEGACY_PROTOCOL_FILTER_MISSING | 6.13 |
| TLS_PROTOCOL_FILTER_CONTRACT_NOT_TESTED (warning, semantic_validation.py:6045-6057) | W3, 4.13 |
| PROTOCOL_MANDATED_ALGORITHM | 6.14 |
| REQUIRED_PARAMETER_SPEC_REMOVED, CALLER_IV_REFUSED_BY_KEYSTORE_KEY, PROVIDER_IV_REPLACED | 6.15, 8.6 |
| EMPTY_STORE_MANAGERS | 6.16 |
| DUPLICATED_ADJACENT_STATEMENT | 6.17 |
| API_21_RUNTIME_VALIDATION_REQUIRED (warning, only under case policy must_support_api_21) | left out (I) |
| RUNTIME_DISCRIMINATOR_STILL_USES_DIGEST, RUNTIME_DISCRIMINATOR_BEHAVIOR_NOT_TESTED, KNOWN_APPLICATION_VARIANTS_NOT_TESTED, UNVERIFIED_BEHAVIORAL_MAPPING_LITERAL, LEGACY_DISCRIMINATOR_MAPPING_REQUIRES_REVIEW, CURRENT_VARIANTS_COVERED_LEGACY_MAPPING_UNRESOLVED | 6.18, 3.6 |
| NAME_CONTRADICTS_BEHAVIOR | 6.19 |
| UNAUTHORIZED_RENAME, RENAME_REFERENCES_NOT_UPDATED, RENAME_PARTIAL_FAMILY, RENAME_TARGET_ALREADY_DECLARED, RENAME_CALL_REBINDS, REDUNDANT_CALLER_RENAME | 6.20, 8.3 |
| PUBLIC_API_CONTRACT_CHANGED, UNNECESSARY_PUBLIC_API_EXPANSION, TEST_SEAM_VISIBILITY_WIDENED | 6.21 |
| SYMBOL_NOT_DECLARED_IN_REPOSITORY, CONSTRUCTOR_NOT_DECLARED_IN_REPOSITORY, DECLARATION_SCOPE_MISMATCH, CHECKED_EXCEPTION_NOT_PROPAGATED, OBSOLETE_CHECKED_EXCEPTION_CATCH, REPLACED_SENSITIVE_WRAPPER_NOT_DISPOSED, EXECUTOR_SUBMIT_DISCARDS_EXCEPTIONS | 6.22 |
| EXCEPTION_MESSAGE_CONTRACT_MISMATCH | 6.23 |
| TEST_IMPORT_NOT_ON_TEST_CLASSPATH, TEST_IN_OTHER_MODULE, TEST_PLACED_IN_UNRELATED_FILE, TEST_CALLS_PRIVATE_MEMBER, REFLECTION_ONLY_TEST_SEAM, TEST_READS_PRODUCTION_SOURCE, TEST_ASSERTS_ONLY_CONSTANTS, EQUALITY_ASSERTED_ON_IDENTITY_TYPE, EXISTING_TEST_EXPECTATIONS_REWRITTEN, AUTH_FAILURE_CAUSE_NOT_ASSERTED | 6.24, 8.5 |
| OWNER_LIFECYCLE_NOT_TESTED (warning, semantic_validation.py:6208) | W2 |
| Removed-comment check (regenerates; a warning on the last attempt) | 6.25, 8.4 |
| Changed file in a source set the APK does not compile; outside-plan path | 6.26 |
| CURRENT_PARAMETER_BELOW_POLICY | left out (I): parameter-policy data |
| UNINTENDED_BEHAVIOR_CHANGE (behaviour review regression) | Stage 7, 8.2 |

## F. Behaviour review (behavior_review_system_prompt, coordinated_generation.py:1250-1660; review_patch_behavior 2421-2620)

| Pipeline behaviour | Block rule |
|---|---|
| Read the whole file; follow callers and branches; compare before and after | 7.1 |
| Definition of a regression and its two exclusions | 7.2 |
| Invented actors, earlier failure paths, downgrades, platform against docs, the new form while old data reads, unchanged code, style, allowed renames | 7.3 |
| derived_value_consumers: persisted, transmitted or fixed values stay protected | 7.4 |
| Invariants, forbidden changes, adjudication concerns; a verdict per concern | 7.5 |
| "What the fix removes": five kinds of evidence; option-gated path | 3.22, 7.6 |
| Unbounded persistent growth | 7.7 |
| User choice changed or option removed | 7.8 |
| Constraints elsewhere; key_provider_constraints (AndroidKeyStore randomized encryption) | 7.9 |
| persisted_object_formats, persisted_outside_preferences, value exports, a read path accepting old inputs | 7.10, 3.23 |
| dropped_inputs | 7.11, 4.8 |
| patch_has_no_effect and its catalogue; a kept forbidden call for earlier data | 7.12, 10.10 |
| Per-finding effect (findings_without_effect) | 7.13 |
| unrelated_test_edits, edits_for_other_findings | 7.14 |
| explicit_default_only, beyond_reported_argument | 7.15, 10.8 |
| Maintainer response against a plan premise | 7.16 |
| maintainer_decision_required and its kind | 7.17, 10.12 |
| current_code_is_correct_use | 7.18, 8.8 (a) |
| regression_source: plan or draft | 7.19 |
| previous_regressions | 7.20 |
| No-reader and deliberate-insecurity guidance in the review | 3.23, 3.21 |
| Rename allowed by the check is never a regression | 7.3, 3.33 |
| Parameter policy overrides invariants | left out (I) |
| edits_already_on_base | left out (I): the base-branch tip |
| Review retry when the review did not run (B4) | left out (I): provider mechanics |

## G. Redraft and replan (coordinated_generation.py:1790-2420; fix_generation_common.py:28)

| Pipeline behaviour | Block rule |
|---|---|
| MAX_SEMANTIC_VALIDATION_RETRIES = 2, so three drafts per plan; retry only the files named, with the codes as mandatory requirements | 8.1 |
| Kept files keep the declarations they call | 8.1 |
| Early replan when two drafts in a row regress because of the plan (V1 restricted, 2026-11-09) | 8.2 (a) |
| Replan after the last draft regresses | 8.2 (b) |
| Replan at once after a no-effect verdict; without security impact if the new plan's drafts fail | 8.2 (c), 8.8 (b) |
| Replan after draft failures (after salvaging a new test) | 8.2 (d), 8.5 |
| Stored-value replan when readers lie outside the plan | 8.2 (e) |
| One replan per unit in total (replanned_once); the new plan keeps every attempt; a refused new plan leaves the first plan's attempts running (_replan_refusal) | 8.2 |
| Rename-only failure: one extra attempt on the non-rename route | 8.3 |
| A draft whose only fault is a removed comment is kept | 8.4 |
| A draft without its rejected new test is salvaged | 8.5 |
| _close_provider_iv_replacement | 8.6 |
| single_round_generation after a correct-use promotion | 8.7 |
| _close_discarded_construction; final routing after drafts | 8.8 |
| Per-file shape retry (two attempts) | left out (I) |
| Pipeline-failure routing (outside paths, missing exact sources, stall watch) | left out (I) |

## H. Build (validation/repair.py, applied_solutions.py) and text (finding_adjudication.py:2400-2790; utils/pr_text.py; issue prompt)

| Pipeline behaviour | Block rule |
|---|---|
| Gradle build of the patched module, its unit tests, androidTest compilation | 9.1 (partly: build_check runs only the unit's build task; neither "+" arm runs tests, see I) |
| Failure attribution: generated patch or pre-existing debt | 9.2 |
| One repair correction (MAX_REPAIR_CORRECTIONS = 1); repair system prompt and read-only constraints; never rebuild the old type; semantic validation of the repaired patch (repair.py:1029) | 9.2 |
| Behaviour re-review of a repaired patch only when the repair edited a file beyond the plan (disposition.repaired_patch_review_blocks) | 9.2 |
| Source-level recheck that the reported predicate is gone | 9.4, 6.1 |
| No PR without a passing build (publication verdict) | 9.3 |
| Summary: title in the imperative, under 70 characters; 2-3 paragraphs under 150 words, first person, no headings | 10.1, 10.2 |
| Lead with today's code; name the Java API; what the statements do with the value | 10.3 |
| why_the_value_is_a_problem; rule-only justification check | 10.4 |
| No vulnerability without confirmed impact | 10.5 |
| Describe coordinated_edits (the final diff); also_resolves; no claims about other findings; source names, not bytecode names | 10.6 |
| rule_accepts and allowlist misstatements | 10.7 |
| makes_default_explicit | 10.8 |
| new_written_format / earlier_data_still_reads | 10.9 |
| KEPT_CALL_TEXT_RULE | 10.10 |
| user_visible_effects (restated in own words; copying a review note is rejected) | 10.11, 10.17 |
| maintainer_choice | 10.12 |
| Publication considerations; stale value names note; vendored note | 10.13 |
| "Say it builds only when it built"; never "tested on a device" | 10.14 |
| PR template filled; AI-assisted; review-bot defaults | 10.15 |
| "AI assistance was used for this contribution." when the repository's guidance asks (pr_generator.py:1326-1336, 1422-1424) | 10.15 |
| Backend rollout sentence after the digest contract's PR note (pr_generator.py:1421) | 3.6 (the PR states the rollout) |
| RVSec named as analyzer; never automated | 10.16 |
| PIPELINE_JARGON (pr_text.py:62-83), evidence citations, rule vocabulary (_RULE_VOCABULARY; _CONTEXTUAL_RULE_VOCABULARY only when the diff does not use the word) | 10.17 (every PIPELINE_JARGON entry and the rule vocabulary listed) |
| Generic-title check | 10.1, 10.18 |
| Issue presentation prompt (title, finding_summary, why_no_pr, maintainer_question, proposed_next_step, under 150 words) | 10.18, 10.19 |
| An issue about library code goes to the library's repository (issue_repository: upstream_library) | 3.31 (library item) |
| Issue: read the docs first, never ask what they answer | 10.20 |
| Text rewritten when a check fails; holding is the last resort | 10.23 |
| Repository "no AI" policy is a note, not a blocker | 3.32 |
| Persona sentences ("I found this while running the app under RVSec", the student introduction) | left out: the base prompt sets the agent's voice as an outside contributor; adding the persona would change the shared base prompt |

## I. Left out (cannot be a prompt instruction, or the data is not supplied), and why

| Item | Why it is left out |
|---|---|
| Porting to the base-branch tip: `edits_already_on_base`, skipping when our PR is open or merged or the file is missing on the base branch, no issue for a statement gone from the default branch | The agents work at the analysed commit. Showing them the tip would reveal later fixes, our merged PRs among them, and hand them information the comparison does not give any arm at decision time. |
| Research corpus, its import and listing tooling, cross-run reuse of shared research | A fresh agent session has no memory across runs. Both "+" arms research live. |
| Research reasoning effort, model settings, prompt caching, two-pass provider workaround | Run configuration, matched by running the arms at high effort, not by prompt text. |
| Code-enforced validators and retries: evidence-id sanitiser, adjudication validator and its two correction calls, forced fallback to unknown, schema shape retries, deterministic routers and promotions, label normalisation, behaviour-review retry, stall watch, transient-failure resume | Code that checks or re-asks a model. The block turns their rules into self-checks (3.34, Stage 6, Stage 7); nothing external enforces them on the agent. |
| Separate model contexts: extraction hidden from adjudication, function contract withheld from adjudication, invariants withheld from the drafter | One agent works in one context. The block keeps the order and the separation by instruction (3.1, 3.4, 5.1) but cannot make the agent forget. |
| APK bytecode of classes missing from the checkout; META-INF library versions | The agents get the source checkout only, no APK and no dexdump or javap output. |
| Re-running RVSec on the checkout; RVSec exploration coverage per finding | RVSec cannot run in the agents' environment, and the coverage file is not supplied. 3.3 keeps the interpretation rule only. |
| Checker's own source at the reporting line; analyzer label catalogue | Pipeline data, not supplied per unit. `reporting_detector_site` was absent from every C-arm r1 planning request. (Corrected 2026-11-14: the verified rule itself, its detector semantics and its shape lists are library data the C arm received in every adjudication request; they are now in the per-unit inputs, 2.2, 3.15.) |
| Parameter policies and case migration policies (named-constant minimums) | Per-case policy data. Absent from the C-arm requests, so leaving it out keeps parity. |
| Verified repository imports and previous attempts of the same finding | Learning data from earlier runs. Absent from the C-arm requests (holdout). |
| Holdout configuration | A run setting. The harness reproduces its effect: the per-unit inputs contain only the other-repository responses the C arm received, and G6 asks the agent not to read this repository's later history, pull requests, issues, discussions or releases. |
| Evidence budget, supplementary caps, evidence ids and hashes, merge and de-duplication | Context-window and audit mechanics. Only the reading order (1.22) and the citation habit (G1) transfer. |
| Selecting findings in the app's own packages; joining findings into units | Selection steps shared by all arms. The harness passes the unit already joined (3.30 states the joining rule). |
| Passing co-reported outcomes between findings | State shared across findings. Each agent run sees one unit. |
| API_21_RUNTIME_VALIDATION_REQUIRED (old 6.19) | A warning that fires only when the case policy sets must_support_api_21; `case_policy` is {} in every C-arm request, so it never fired. |
| Unit tests and androidTest compilation during validation (old 9.1 sentence) | build_check runs only the unit's build task, and SP's allowlist has no Gradle or test runner, so an instruction to run tests would apply to TP only. Dropped for both arms; 10.14 keeps "say tests pass only if they ran". A build_check `--tests` mode running the same phases for both arms would restore it (harness follow-up). |
| Policy evidence gathered outside the code (server certificate chains, recomputed digests) | Precomputed by the pipeline for specific cases. Telling the agent to connect to servers would add a step the block does not otherwise reproduce. |
| Leak noted in the inputs summary (pr_1381cd4ddb3f r2, port-to-tip behaviour review carrying the own repository's answer) | A pipeline defect in the port step, which is itself left out. It is not in the per-unit inputs. |
