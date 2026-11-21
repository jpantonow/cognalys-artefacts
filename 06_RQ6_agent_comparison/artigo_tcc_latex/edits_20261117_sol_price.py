"""2026-11-17: gpt-6.1-sol does have a published list price (developers.openai.com/api/docs/pricing, checked
2026-11-17: 2.00 / 0.10 cached / 2.50 cache write / 10.00 output, web search $10 / 1k calls), now in
data/llm_prices.json. outputs/comparison_20261113/cost_k3.json recomputed: O list mean per run US$0.1254
(80 runs, total 10.03). The paper's "no published price" was wrong."""
from pathlib import Path
import shutil
import sys
tex = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/artigo_tcc_latex/main.tex")
t = tex.read_text(encoding="utf-8")


def once(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:70])
    t = t.replace(old, new)


once("agents are\npriced at list prices with the search fee where the model has one\n(\\texttt{gpt-6.1-sol} had none, so O has no cost), and {\\system} at what the",
     "agents are\npriced at list prices with the web search fee (the agents ran on subscriptions,\nso this is what the same tokens would cost through the API), and {\\system} at what the")
once("list price for every arm\n(\\texttt{gpt-6.1-sol} has no published price); \\system{} was billed",
     "list price for every arm;\n\\system{} was billed")
once("Cost per run, list (US\\$) & 0.19 & 0.04 & 0.12 & --- &",
     "Cost per run, list (US\\$) & 0.19 & 0.04 & 0.12 & 0.13 &")
once("The\nfirst block's agents cost US\\$0.04 (Codex Terra) and US\\$0.12 (Claude Sonnet)\nper run at list price, without a port;",
     "The\nfirst block's agents cost US\\$0.04 (Codex Terra), US\\$0.12 (Claude Sonnet),\nand US\\$0.13 (Codex Sol) per run at list price, without a port;")
once("phase 1 + port}; Codex with GPT-6.1 Sol could not be priced. These",
     "phase 1 + port}. These")
if "--dry" in sys.argv:
    print("dry run ok")
else:
    shutil.copy2(tex, tex.with_name("main.tex.bak_20261117_sol_price"))
    tex.write_text(t, encoding="utf-8")
    print("applied")
