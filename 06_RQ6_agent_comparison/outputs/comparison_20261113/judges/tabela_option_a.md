# Opção A: tabela (leituras v3)

- (runs/<unit>/<arm>_r<k>/port/result.json).
- 
- Acceptability per run (final per card: both judges agree, else the principal judge opus, as tabulate.final_a):
- FALHA 0; ISSUE / NO_ACTION 1.
- C and the first block (S, O, T): as retabulate_author_rule.py (rule 4 for C's "correção não aplicável").
- TP, SP2 PR: port "ported": author r2>=2, r4>=1 and the kit/agent build at the tip succeeded; patch r2>=2 and built
- at the tip. Port "already_fixed" or "abstain" (an ALREADY_FIXED without deterministic confirmation, or a merge
- that left nothing to port, is recorded as abstain): rule 4, r2>=2. Port "failure": 0.
- A per arm = mean over units of the per-unit share of acceptable runs. Also: PRs proposed, PRs acceptable, units
- with a PR, classes, port outcomes, per-judge A, Cohen's kappa (acceptability, author reading, cards both judges

| braço | runs | PR/ISSUE/NADA/FALHA | unid. com PR | A autor | A patch | PRs | PRs aceitos autor/patch | porte | US$/run lista (fase 1 + porte) | classe igual nas 4 réplicas |
|---|---|---|---|---|---|---|---|---|---|---|
| S | 80 | 16/13/51/0 | 6 | 0.975 | 0.975 | 16 (0 sem nota) | 14 / 14 | — | 0.1173 (0.1173 + 0.0) | 13/20 |
| O | 80 | 15/2/63/0 | 5 | 0.9 | 1.0 | 15 (0 sem nota) | 7 / 15 | — | 0.1254 (0.1254 + 0.0) | 17/20 |
| T | 80 | 23/8/49/0 | 8 | 0.938 | 0.988 | 23 (0 sem nota) | 18 / 22 | — | 0.0436 (0.0436 + 0.0) | 12/20 |
| TP | 80 | 38/16/24/2 | 10 | 0.85 | 0.85 | 38 (0 sem nota) | 28 / 28 | {'clean': 24, 'after_conflict': 4, 'abstain': 8, 'already_fixed': 2} | 0.208 (0.1991 + 0.009) | 17/20 |
| SP2 | 80 | 46/5/27/2 | 15 | 0.8 | 0.7 | 46 (0 sem nota) | 32 / 24 | {'clean': 33, 'after_conflict': 1, 'abstain': 11, 'already_fixed': 1} | 0.3749 (0.3531 + 0.0218) | 14/20 |
| C | 80 | 31/10/39/0 | 9 | 0.963 | 0.925 | 31 (0 sem nota) | 28 / 25 | — | — (— + —) | 16/20 |

Por juiz (A autor / A patch): S: opus 0.975/0.975, sol 0.925/0.975; O: opus 0.9/1.0, sol 0.9/1.0; T: opus 0.938/0.988, sol 0.938/0.988; TP: opus 0.85/0.85, sol 0.85/0.9; SP2: opus 0.8/0.7, sol 0.7/0.75; C: opus 0.963/0.925, sol 0.9/0.975

Concordância (autor v3): {"cohen_kappa_acceptability": 0.81, "cards": 491, "quadratic_kappa_r2": 0.823, "pr_cards": 174, "raw_agreement_acceptability": 0.963}
