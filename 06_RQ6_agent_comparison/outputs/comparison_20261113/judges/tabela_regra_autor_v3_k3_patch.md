# Leitura do patch (2026-11-14)

- Patch reading (2026-11-14): the judges saw each pull request's diff against the analysed commit and whether it was built, without its title and description; issue and no-action cards were not sent.
- A PR is acceptable when r2 >= 2 and it built (or the fix was already on the base tip: "correção não aplicável: código mudou upstream", judged on the finding alone).
- An issue or no action counts 1 whatever its text; a failed run counts 0.
- Final per card: both judges agree, else the principal judge (opus).
- Only patch-view records scored against the pre-registered answer key are read; a PR card without a final score is reported (unscored_prs) and blocks the table unless ALLOW_UNSCORED=1; a unit with PR cards but no patch-view record is left out of every arm.

- Notas: `phase2_v3_k3_patch`, cartões `cards_k3`, gabarito `gabarito_v3.json`; PRs sem nota final: 0; unidades sem registro da visão do patch (fora de todos os braços): nenhuma.

| braço | A | runs | PRs | PRs aceitos | PRs recusados | unidades com PR aceito | PRs sem nota |
|---|---|---|---|---|---|---|---|
| S | 0.97 | 80 | 16 | 14 | 2 | 5 | 0 |
| O | 1.00 | 80 | 15 | 15 | 0 | 5 | 0 |
| T | 0.99 | 80 | 23 | 22 | 1 | 7 | 0 |
| TP | 0.85 | 80 | 38 | 28 | 10 | 8 | 0 |
| SP2 | 0.68 | 40 | 23 | 11 | 12 | 6 | 0 |
| C_all | 0.93 | 80 | 31 | 25 | 6 | 7 | 0 |
| C_publish | 0.91 | 80 | 29 | 24 | 5 | 7 | 0 |
| D | 0.71 | 17 | 5 | 2 | 3 | 2 | 0 |
