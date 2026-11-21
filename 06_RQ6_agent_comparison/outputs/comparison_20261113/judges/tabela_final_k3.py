"""One table of the k3 comparison: score A under every reading, accepted PRs, and cost as a reference.

Reads tabela_k3.md (protocol), tabela_regra_autor_{v2,v3}_k3[_patch].json (author and patch readings) and
../cost_k3.json (cost_k3.py: SP2 in place of SP, C with its 4 FALHA runs swapped as in cards.py). Cost is not a
scoring criterion: it is shown so every arm's price sits beside its scores.
Writes tabela_final_k3.md. Run (WSL): python3 tabela_final_k3.py
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("S", "O", "T", "TP", "SP2", "C", "D")


def protocol():
    out = {}
    for line in (HERE / "tabela_k3.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (S|O|T|TP|SP2|C_all|D_all) \| \d+ \| [\d.]+ \| ([\d.]+) \|", line)
        if m:
            out[m.group(1).replace("_all", "")] = float(m.group(2))
    return out


def reading(name):
    arms = json.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))["arms"]
    return {a.replace("_all", ""): v for a, v in arms.items() if not a.endswith("_publish")}


def money(x):
    return "—" if x is None else f"{x:.2f}"


def main():
    p = protocol()
    r = {n: reading(f"tabela_regra_autor_{n}") for n in ("v2_k3", "v2_k3_patch", "v3_k3", "v3_k3_patch")}
    cost = json.loads((HERE.parent / "cost_k3.json").read_text())
    md = ["# Comparação k3: notas e custo", "",
          "A = média por unidade da entrega aceitável (1/0). Leituras: protocolo (gabarito_final); autor v2 e v3 "
          "(gabarito_v2 / gabarito_v3, PR aceito com r2>=2, r4>=1 e build; regra 4: correção já no destino julgada "
          "só pelo patch); patch v2 e v3 (só o diff, r2>=2 e build). PRs aceitos = autor v3 / patch v3.", "",
          "Custo é referência, não critério de nota. Agentes: equivalente de API a preço de lista (rodaram em "
          "assinatura), com a taxa de busca. C: gasto real (Flex) e, entre parênteses, os mesmos tokens a preço de "
          "lista. D: gasto real. O: gpt-6.1-sol sem preço em data/llm_prices.json.", "",
          "| braço | runs | A protocolo | A autor v2 | A patch v2 | A autor v3 | A patch v3 | PRs | PRs aceitos v3 "
          "(autor / patch) | US$/run | US$ total |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in ARMS:
        v3, v3p, c = r["v3_k3"][a], r["v3_k3_patch"][a], cost.get(a, {})
        per = money(c.get("cost_usd_api_mean_per_run"))
        if a == "C" and c.get("cost_usd_list_mean_per_run") is not None:
            per += f" ({money(c['cost_usd_list_mean_per_run'])} lista)"
        total = money(c.get("cost_usd_api_total"))
        if c.get("runs") and c.get("runs") != v3["runs"]:
            total += f" ({c['runs']} runs)"
        md.append(f"| {a} | {v3['runs']} | {p.get(a, float('nan')):.2f} | {r['v2_k3'][a]['A']:.2f} | "
                  f"{r['v2_k3_patch'][a]['A']:.2f} | {v3['A']:.2f} | {v3p['A']:.2f} | {v3['prs']} | "
                  f"{v3['good_prs']} / {v3p['good_prs']} | {per} | {total} |")
    (HERE / "tabela_final_k3.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
