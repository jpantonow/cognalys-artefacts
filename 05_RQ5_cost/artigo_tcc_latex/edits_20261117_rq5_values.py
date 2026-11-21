"""2026-11-17: fill RQ5 with the values of scripts/rq5_cost_snapshot.py (outputs/exp_final_20261019/consolidated/
rq5_cost.json), confirmed by an independent recomputation (all 14 values equal). Window 1: 11 runs of Oct 19-20 up to
subset_bitbanana_scope2; window 2: runs started up to Nov 3 (16 more, incl. outputs/rerun_correcoes_20261022/run)."""
from pathlib import Path
import shutil
tex = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/artigo_tcc_latex/main.tex")
t = tex.read_text(encoding="utf-8")
shutil.copy2(tex, tex.with_name("main.tex.bak_20261117_rq5"))
s = t.index("The prompt traces of the snapshot's runs")
e = t.index("cost is reported with the comparison (\\researchq{6}).", s)
sec = t[s:e]
for old, new in [("\\pending{N} runs)", "11 runs)"), ("\\pending{CALLS}", "569"), ("\\pending{TOTAL}", "35.71"),
                 ("\\pending{MED}", "0.77"), ("\\pending{PR}", "11.00"), ("(\\pending{PR\\%})", "(31\\%)"),
                 ("\\pending{ISS}", "17.98"), ("(\\pending{ISS\\%})", "(50\\%)"), ("\\pending{FP}", "0.82"),
                 ("\\pending{IE}", "0.42"), ("\\pending{K} later runs", "Sixteen later runs"), ("\\pending{ALL}", "53.38"),
                 ("(US\\$7.60 in all)", "(US\\$7.84 in all)")]:
    assert sec.count(old) == 1, old
    sec = sec.replace(old, new)
sec = sec.replace("finding. These are an upper bound:", "finding (computed by \\texttt{scripts/rq5\\_cost\\_snapshot.py}). These are an upper bound:", 1)
t = t[:s] + sec + t[e:]
a = t.index("\\textbf{Answer to \\researchq{5}.}")
b = t.index("\\pending{MED}", a)
t = t[:b] + "0.77" + t[b + len("\\pending{MED}"):]
t = t.replace("RQ5 & Prompt traces of the case study's runs & 34 findings, \\pending{N} runs \\\\",
              "RQ5 & Prompt traces of the case study's runs & 34 findings, 11 runs \\\\", 1)
tex.write_text(t, encoding="utf-8")
print("RQ5 filled")
