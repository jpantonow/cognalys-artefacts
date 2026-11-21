# Comparação 2026-11-13: agentes (S, O, T) e Cognalys (C)

Descritivo; qualidade fica para os juízes cegos. US$ dos agentes = equivalente de API (rodaram em assinatura); US$ do C = gasto real (Flex). O sem preço na tabela: custo nulo.

| braço | modelo | runs | PR | ISSUE | sem ação | PR compila | unid. c/ algum PR | unid. estáveis | mediana linhas PR | mediana s | US$ total | US$/run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S | claude-sonnet-5-5 | 80 | 16 | 13 | 51 | 16 | 6/20 | 13 | 49.0 | 28.0 | 9.3804 | 0.1173 |
| O | gpt-6.1-sol | 80 | 15 | 2 | 63 | 15 | 5/20 | 17 | 161 | 52.0 | None | None |
| T | gpt-6.1-terra | 80 | 23 | 8 | 49 | 23 | 8/20 | 12 | 84 | 29.0 | 3.2993 | 0.0412 |
| C | gpt-6.1-terra (Flex) | 80 | 29 | 12 | 39 | 29 | 10/20 | 12 | 8 | None | 9.6016 | 0.12 |
| D | claude-sonnet-5-5 (Cognalys) | 40 | 11 | 5 | 24 | 11 | 7/20 | 14 | 8 | None | 21.9408 | 0.5485 |

Por unidade (réplicas em ordem; P = PR, I = issue, - = sem ação):

| unidade | S | O | T | C | D |
|---|---|---|---|---|---|
| co_0a32f516feb2 | PPPP | --P- | PP-P | -P-- | I- |
| co_0b7656970a91 | ---- | ---- | ---- | ---- | -- |
| co_2aaa72c51c14 | ---- | ---- | ---I | ---- | -- |
| co_3c8e59dbbfb4 | --I- | ---- | I--- | -I-- | -- |
| co_737920a862cd | ---- | ---- | P--- | PP-- | -- |
| co_911173ce3742 | ---- | ---- | -III | ---I | -- |
| co_9da181fd6978 | ---- | ---- | ---- | ---- | -- |
| co_bdc0d1804c88 | ---- | ---- | ---- | IIII | II |
| co_ed824f6818ef | -III | -I-I | IIPI | ---- | -P |
| co_f637c4b8467e | ---- | ---- | ---- | PIP- | -- |
| pr_013d2748f3f8 | ---- | -PP- | PPP- | -P-P | -I |
| pr_06ff0c1fc96c | P-PP | ---- | ---- | PPPP | P- |
| pr_1381cd4ddb3f | P-P- | ---- | ---- | PPPP | PP |
| pr_2b8cb7f6653b | IIII | PPPP | PPPP | PPPP | PP |
| pr_39bba5f1270f | ---- | ---- | ---- | ---- | -- |
| pr_6d0a3cf73013 | IIII | PPPP | PPPP | IPIP | PP |
| pr_7d98a07e751d | --PP | ---- | ---- | PPPP | -P |
| pr_9215f97597a6 | P-IP | PPPP | PPPP | -III | -I |
| pr_ac1a8dd4d680 | ---- | ---- | ---- | PPPP | PP |
| pr_fff072cb8953 | PP-P | ---- | P-PP | ---- | -- |
