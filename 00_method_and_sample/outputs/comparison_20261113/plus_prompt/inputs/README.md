# Per-unit inputs for the T+ / S+ arms

Each `<unit>.txt` holds two things the Cognalys arm (C arm, library commit
8be9f78) was given for that unit and the first-round agents (T, S) were not:
the published rule behind each report, and the maintainer responses to earlier
proposals in other repositories. The block's `$unit_inputs` placeholder takes
the file's content. The source is the recorded requests in
`frozen_runs_20261113/c_arm_8be9f78/<unit>/r{1..4}/application_results/openai_prompt_logs/`.

## Revision 2026-11-14 (critic review)

- Added "The published rule behind each report" before the responses. For
  each report, in the order the agent's prompt lists them, it renders the rule
  the C arm's adjudication received: `reported_by_rule` for the primary
  finding, `joint_findings_rules` for the joined ones (verified_detector_
  semantics over data/specifications: source title and URL, governed types,
  call order, events with their matching clause and call shapes,
  detector_semantics, and the shape lists), plus the primary finding's
  `reported_violation` and `reported_violation_section`. The joined findings
  carry no violation kind in the C payload, so none is shown for them. The
  rule sections are byte-identical across every adjudication request of
  r1-r4 of every unit (the script stops otherwise), and identical in c_arm_k3
  for the 12 units it had reached on 2026-11-14 (the 10 co_* units,
  pr_013d2748f3f8, pr_06ff0c1fc96c). This is library data, not holdout
  data: it was wrongly listed below as "pipeline data not supplied".
- The responses' header now names the report whose rule they answer
  ("... for the rule of the report on <Class>.<method> (<Java API>) ...").
  The pipeline computes the responses for the primary finding's rule only, and
  in pr_2b8cb7f6653b and pr_7d98a07e751d that is the Cipher report, which the
  agent's prompt lists second. The old header ("for this rule") let the agent
  apply them to the first report.
- Redacted in maintainer text: GitHub @handles become `@<contributor>` and
  commit hashes become `<commit>`. One handle and one hash each, in the
  CometOTP reply of pr_013d2748f3f8, pr_2b8cb7f6653b and pr_7d98a07e751d. The
  handle leads to the contributor's pull requests on these units'
  repositories, which the agents' open web research could reach (the C arm's
  research is API-scoped and held out). Nothing else in the entries changed;
  rule names in maintainer text ("CrySL rule", "_MessageDigestSpec_") stay
  verbatim, as the C arm saw them.
- A unit with no responses now gets one line naming the report:
  "Earlier proposals, in other repositories, for the rule of the report on
  <...>: none with a maintainer response."
- The previous files and report are kept in
  `tools/prev_20261114_precritic/`; the previous script is
  `tools/write_inputs.py.bak_20261114_precritic`.

## How the files were built

- Script: `tools/write_inputs.py` (helper `tools/survey_lib.py`), run in WSL.
  It is read-only on the frozen runs and writes only this folder. Per-unit
  provenance (every adjudication request file read) is in
  `tools/inputs_report.json`.
- The pipeline puts three keys in the payload (`learning_context`,
  `src/cognalys/llm/fix_generation_common.py`; read for adjudication in
  `finding_adjudication.py`):
  `maintainer_responses_to_this_rule_in_this_repository`,
  `maintainer_responses_to_this_rule_elsewhere` and
  `maintainer_responses_in_this_repository`. Only `..._elsewhere` appears in
  any request of this arm. The two "this repository" keys are absent in every
  request because the runs used the repository holdout.
- Primary source: the `adjudication` requests (also `adjudication.correction`
  and `adjudication.after_evidence`). For every unit the section is
  byte-identical across r1-r4. The script stops if it is not.
- `patch_planning` and `code_generation.coordinated_file` requests carry the
  same entries. For the two SSLContext units (co_737920a862cd,
  pr_6d0a3cf73013) they also carry the Copilot review comments on
  paroj/DSub2000 "Fix <init>". The adjudication does not get these, because
  `as_maintainer_position` strips them, so the plan and the file drafts saw
  more than the adjudication did. They are included in those two files under
  "Automated review comments (not from the maintainer)", the same label the
  pipeline uses.
- The entries are rendered as plain text, one numbered item per entry:
  repository, outcome, proposal title and maintainer text, all verbatim. An
  empty `maintainer_said` is shown as "(no comment)". The entries hold no
  internal ids (no finding, evidence or claim ids). Text quoted from
  maintainers is kept as written, except the redacted GitHub handle and
  commit hash in the CometOTP reply (CipherSpec units, see the revision
  above); rule names such as "MessageDigestSpec" in the rssreader reply stay.
- A unit whose requests carry no section gets the single "none" line naming
  the report. Each such unit had an adjudication request in every run, so the
  section was absent rather than missing from the logs.
- The pipeline caps the list at 4 entries (`MAX_SIMILAR_RESPONSES`). Decided
  outcomes with text come first. The list is computed for the unit's primary
  finding's rule only, so a joint unit (two rules) gets the responses for its
  primary finding's rule, which the header names.

## Per unit

| unit | repository | rule(s) | file content (repositories) |
|---|---|---|---|
| co_0a32f516feb2 | chrismiceli/motp | MacSpec, SecretKeySpecSpec | none |
| co_0b7656970a91 | StellarSand/Password-Monitor | MessageDigestSpec | InterTune, rssreader, WiFiAnalyzer, NotallyX |
| co_2aaa72c51c14 | M66B/FairEmail | MessageDigestSpec | InterTune, rssreader, WiFiAnalyzer, NotallyX |
| co_3c8e59dbbfb4 | CriticalMaps/criticalmaps-android | MessageDigestSpec | InterTune, rssreader, WiFiAnalyzer, NotallyX |
| co_737920a862cd | Giton22/kyllene | SSLContextSpec | openhab-android, DSub2000 (x2), crazyflie-android-client; plus Copilot comments |
| co_911173ce3742 | paroj/DSub2000 | MessageDigestSpec | InterTune, rssreader, WiFiAnalyzer, NotallyX (DSub2000's own SSLContext answers withheld) |
| co_9da181fd6978 | chrismiceli/motp | MessageDigestSpec | InterTune, rssreader, WiFiAnalyzer, NotallyX |
| co_bdc0d1804c88 | VREMSoftwareDevelopment/WifiAnalyzer | MessageDigestSpec | InterTune, rssreader, giggity, NotallyX (own WiFiAnalyzer answer withheld) |
| co_ed824f6818ef | treehouses/remote | IvParameterSpecSpec, SecretKeySpecSpec | none |
| co_f637c4b8467e | ThHSzR/winterhold | PBEKeySpecSpec | none |
| pr_013d2748f3f8 | yogeshpaliyal/KeyPass | CipherSpec | freeotp-android (x2), CometOTP |
| pr_06ff0c1fc96c | ounben/AMARadio | MessageDigestSpec | InterTune, rssreader, WiFiAnalyzer, NotallyX |
| pr_1381cd4ddb3f | jenspfahl/anotherpass | KeyStoreSpec | android-network-survey, traffic-light, tokn, news-android (own ANOTHERpass answer withheld; see the leak below) |
| pr_2b8cb7f6653b | vauth/salty-app | CipherSpec, PBEKeySpecSpec | freeotp-android (x2), CometOTP |
| pr_39bba5f1270f | vitorpamplona/amethyst | GCMParameterSpecSpec | none (own Amethyst answer withheld) |
| pr_6d0a3cf73013 | Ashinch/ReadYou | SSLContextSpec | openhab-android, DSub2000 (x2), crazyflie-android-client; plus Copilot comments |
| pr_7d98a07e751d | thatSFguy/meshcore-mobile-app | CipherSpec, KeyGeneratorSpec | freeotp-android (x2), CometOTP |
| pr_9215f97597a6 | schorschii/Droid_SCEP | KeyPairGeneratorSpec | none |
| pr_ac1a8dd4d680 | christianrowlands/android-network-survey | KeyStoreSpec | ANOTHERpass, traffic-light, tokn, news-android (own answer withheld) |
| pr_fff072cb8953 | Crustack/NotallyX | MessageDigestSpec | InterTune, rssreader, WiFiAnalyzer, ShortMessageSecure (own NotallyX answer withheld) |

## Holdout check

The script searched every recorded request of every run for a
`"repository": "<unit's own owner/repo>"` entry, case-insensitively. It found
one leak.

- pr_1381cd4ddb3f, r2 only, request
  `20261113T193243_199492_16230_0002_code_generation.behavior_review_request.json`.
  This request comes from the later process that ports the patch to the
  branch tip (`solutions.base_port`, pid 16230). That behaviour review's
  `maintainer_responses_to_this_rule_elsewhere` lists jenspfahl/ANOTHERpass
  ("Use KeyStore.getKey to read Android secret keys", merged), which is the
  unit's own repository. The probable cause is that the finding record in
  that process has no `repository_url`, so `responses_to_same_rule` excluded
  nothing.
- Effect on the run: the review returned no regressions, and its rationale
  does not cite the ANOTHERpass answer. The draft was already written and
  reviewed in the main process with the correct, held-out list.
- r1, r3 and r4 of that unit ran a base port but no behaviour review in it.
  The other base-port behaviour reviews (co_737920a862cd, pr_06ff0c1fc96c)
  got the same list as their adjudication.
- These files use the main-process list, so the leaked entry is not here.

## Other inputs the C arm had and these files do not reproduce

The adjudication payload sections, with the number of the 23 r1 adjudication
requests that carry each:

- finding, scope_completeness, case_policy, validated_evidence_extraction,
  external_evidence_catalog (the arm's own web research), valid_evidence_ids,
  reported_by_rule (the rule's text) and rename_safety: in all 23.
- maintainer_responses_to_this_rule_elsewhere: 18.
- app_purpose (from the README): 14.
- joint_findings_rules: 5.
- value_exports: 5.

The agents can rebuild the evidence, the web research and the app's purpose
from the repository and the web. The rule's text (reported_by_rule,
joint_findings_rules, the finding's reported_violation and section) and the
maintainer responses come only from data the pipeline holds; both are now in
these files. The finding's `echo_of` (pr_2b8cb7f6653b: the Cipher key comes
from the PBEKeySpec-reported call) is computed per finding and not
reproduced; the report's own message says the same. No request in this arm
carries `previous_attempts_of_this_finding` or `verified_repository_imports`.
