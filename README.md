# cognalys-artefacts

Data behind the paper *Cognalys: A Python Library Complementing RVSec with
Evidence-Guided Analysis and Repair* (undergraduate thesis, University of
Brasília, 2026). The library itself is at
[jpantonow/cognalys](https://github.com/jpantonow/cognalys).

## Layout

| Folder | Content |
|---|---|
| `paper/` | LaTeX source and PDFs of the paper (English `main`, Portuguese `main_ptbr`); `paper/extended/` holds the longer version without page cuts |
| `00_method_and_sample/` | Case-study inventory and the runs behind the method section |
| `01_RQ1_adjudication/` | October 2026 snapshot: per-finding evidence, adjudications, plans, prompt traces |
| `02_RQ2_build/` | Generated fixes, application at the branch tip, build reports |
| `03_RQ3_pull_requests_and_issues/` | Pull requests, issues, and maintainer responses collected on November 18, 2026 |
| `04_RQ4_false_positives/` | False positives and violations without security impact |
| `05_RQ5_cost/` | Prompt traces and the cost computation |
| `06_RQ6_agent_comparison/` | Comparison units, agent runs, judged cards, the final answer key, judge readings, tabulation scripts |
| `07_RQ7_dynamic_validation/` | RVSec reruns on original and patched APKs: build records, RVSec reports, labels |
| `08_discussion/` | Data cited in the discussion |
| `09_extra_non_rvsec_prs/` | Pull requests for non-RVSec findings (reported apart) |
| `data/paper_data/` | Tables built for the paper (pull requests with origin, case-study findings) |
| `data/sample_inventory/` | Machine-readable case inventory and the script that rebuilds it |
| `preregistrations/` | Pre-registrations of the comparison and of the dynamic validation, with declared deviations and hashes |

Paths inside each folder mirror the original working tree (`outputs/...`,
`docs/...`), so scripts and records that reference those paths still resolve
relative to the folder.

## Notes

- The frozen configuration of the comparison refers to library revisions
  `fd90351` (80 runs) and `3dee64a` (four runs rerun after implementation
  defects were fixed) of the original development repository; the code
  published in `jpantonow/cognalys` is a later revision of the same library.
- Only the final answer key of the comparison is published; it is the one all
  reported readings use.
- APK binaries, cloned third-party repositories, credentials, and the RVSec
  group's raw campaign data are not included. The campaign is described in
  Costa, Miranda, and Bonifácio (manuscript submitted to ACM TOSEM).
- Records are kept as produced (mostly English, some Portuguese).

## License

Creative Commons Attribution 4.0 International (CC BY 4.0), see `LICENSE`.
Pull-request and issue texts written by third parties are public GitHub records
and remain under their authors' terms.
