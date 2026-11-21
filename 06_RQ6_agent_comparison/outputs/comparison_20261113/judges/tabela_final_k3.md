# Comparação k3: notas e custo

A = média por unidade da entrega aceitável (1/0). Leituras: protocolo (gabarito_final); autor v2 e v3 (gabarito_v2 / gabarito_v3, PR aceito com r2>=2, r4>=1 e build; regra 4: correção já no destino julgada só pelo patch); patch v2 e v3 (só o diff, r2>=2 e build). PRs aceitos = autor v3 / patch v3.

Custo é referência, não critério de nota. Agentes: equivalente de API a preço de lista (rodaram em assinatura), com a taxa de busca. C: gasto real (Flex) e, entre parênteses, os mesmos tokens a preço de lista. D: gasto real. O: gpt-6.1-sol sem preço em data/llm_prices.json.

| braço | runs | A protocolo | A autor v2 | A patch v2 | A autor v3 | A patch v3 | PRs | PRs aceitos v3 (autor / patch) | US$/run | US$ total |
|---|---|---|---|---|---|---|---|---|---|---|
| S | 80 | 0.90 | 0.93 | 0.95 | 0.97 | 0.97 | 16 | 14 / 14 | 0.12 | 9.38 |
| O | 80 | 0.90 | 0.90 | 1.00 | 0.90 | 1.00 | 15 | 7 / 15 | — | — |
| T | 80 | 0.94 | 0.96 | 0.99 | 0.94 | 0.99 | 23 | 18 / 22 | 0.04 | 3.49 |
| TP | 80 | 0.74 | 0.70 | 0.70 | 0.85 | 0.85 | 38 | 28 / 28 | 0.20 | 15.93 |
| SP2 | 40 | 0.62 | 0.57 | 0.57 | 0.72 | 0.68 | 23 | 13 / 11 | 0.36 | 14.33 |
| C | 80 | 0.47 | 0.81 | 0.82 | 0.96 | 0.93 | 31 | 28 / 25 | 0.10 (0.19 lista) | 8.12 |
| D | 17 | 0.53 | 0.65 | 0.65 | 0.76 | 0.71 | 5 | 3 / 2 | 0.49 | 8.80 (18 runs) |
