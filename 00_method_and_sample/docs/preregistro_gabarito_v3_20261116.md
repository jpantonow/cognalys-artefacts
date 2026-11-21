# Pré-registro: rejulgamento com o gabarito v3 (2026-11-16)

Escrito e com hashes registrados antes de qualquer julgamento com o gabarito v3 (não existe `phase2_v3*`).

## Motivo

A revisão do `gabarito_v2.json` (2026-11-16), feita depois de ver os resultados das leituras v2, achou defeitos no texto que os juízes leem:

1. O v2 tornou PR, ISSUE e NO_ACTION aceitáveis em todas as 20 unidades, mas manteve a justificativa do gabarito do protocolo. Em 17 unidades essa justificativa manda não enviar nada (ou diz que um PR não é aceitável), e em 3 diz que NO_ACTION não é apropriado.
2. O mesmo tipo de mudança (só satisfazer a regra em código sem papel de segurança) é "hygiene" em duas unidades (pr_06ff0c1fc96c, pr_fff072cb8953) e "pointless churn" / "send nothing" em cinco (pr_ac1a8dd4d680, pr_7d98a07e751d, pr_1381cd4ddb3f, co_bdc0d1804c88, co_0a32f516feb2).
3. Duas restrições proíbem um mecanismo, e não um comportamento: exigem `getEntry` (pr_ac1a8dd4d680, pr_1381cd4ddb3f), embora `getKey` devolva a mesma chave.
4. Afirmações de primeiro e segundo juiz se contradizem na mesma justificativa (pr_013d2748f3f8: "nenhum defeito" contra um defeito de provider que o `must_change` exige corrigir).
5. Uma resposta de mantenedor sobrou no v2 (pr_1381cd4ddb3f), que devia ser escrito sem elas.
6. Uma afirmação de fato foi refutada por medição: pr_7d98a07e751d diz que a mudança cosmética "could still be flagged"; o RVSec no APK com o patch (validação dinâmica, `outputs/dynamic_validation_20261114`) não reportou mais a violação. Em networksurvey (pr_ac1a8dd4d680, troca `getEntry` -> `getKey`) a violação também sumiu (3 de 3 repetições); em ANOTHERpass (pr_1381cd4ddb3f) continuou (1 repetição).
7. Desalinhamento com o achado: pr_9215f97597a6 exige proibir 1024 bits, enquanto o RVSec reportou 8192.

## O que muda

`build_gabarito_v3.py` gera `gabarito_v3.json` a partir do v2: remove ou reescreve como restrição cada frase de veredito, anexa a mesma política de entrega a todas as unidades, corrige as restrições dos itens 3 e 5, remove itens duplicados. A análise técnica fica igual. Todas as mudanças, antes e depois: `gabarito_v3_mudancas.md`. Aprovado pelo usuário em 2026-11-16 antes de qualquer execução.

Política anexada: um PR que muda o código só para satisfazer a especificação, sem mudar o comportamento, é aceitável quando correto, respeita os `must_not_break` e não se apresenta como correção de vulnerabilidade; o juiz não especula se o verificador continuaria reportando.

Rubrica (`prompts/phase2.txt`, `prompts/phase2_patch.txt`), cartões (`cards_k3`) e regras de tabulação ficam sem mudança. O RVSec não entra na nota (só os patches do Cognalys foram validados nele; entraria de forma assimétrica); a tabela da validação dinâmica é relatada à parte.

## Execução

- Leitura do autor: `JUDGE_READING=v3 ARMS_TAG=k3 judge.py phase2 --allow-incomplete` -> `phase2_v3_k3`; tabela `CARDS_DIR=cards_k3 PHASE2=phase2_v3_k3 retabulate_author_rule.py`.
- Leitura do patch: `JUDGE_VIEW=patch JUDGE_READING=v3 ARMS_TAG=k3 ...` -> `phase2_v3_k3_patch`; tabela com `READING_RULE=patch PATCH_GABARITO=gabarito_v3.json`.
- Mesmos juízes (opus principal, sol), todos os braços, mesmo critério de nota final.

## Relato

O v3 é a terceira versão do gabarito e foi feito depois de ver resultados (como o v2). Será relatado como leitura adicional ao lado de v2 e do protocolo, com este motivo, nunca no lugar deles; todos os resultados serão relatados, favoráveis ou não.

## Desvio declarado depois dos resultados v3 (2026-11-16, noite)

Visto o resultado da leitura do autor v3, a regra 4 de `retabulate_author_rule.py` mudou: quando o destino já tinha a correção ("correção não aplicável: código mudou upstream"), nada é enviado, então o texto do PR (r4) deixa de contar; o PR é aceito com r2 >= 2. Motivo: a nota cobrava um texto que nenhum mantenedor leria. Vale para todos os braços; só C e D têm cartões assim. Efeito (medido): autor v3 C 0,93 -> 0,96 (25 -> 28 de 31 PRs aceitos, os 3 de pr_1381cd4ddb3f), D 0,71 -> 0,76 (2 -> 3 de 5); autor v2 e as leituras do patch não mudam. Aprovado pelo usuário. Ressalva relatada junto: na leitura do patch o juiz principal deu r2 = 0 a esse patch (`getEntry` -> `getKey` deixa o `load` repetido que o RVSec reporta), e o RVSec no APK com o patch (validação dinâmica, ANOTHERpass) continuou reportando a violação. Tabelas anteriores: `*.bak_pre_upstream_text`. Tabela única com custo (referência, não critério): `tabela_final_k3.py` -> `tabela_final_k3.md`, custo de `../cost_k3.py`.

## Hashes (sha256)

| arquivo (outputs/comparison_20261113/judges/) | sha256 |
|---|---|
| gabarito_v3.json | 769ab11509fef4d37d8f800404c1b93a953463e31f802d710486ec3599922998 |
| gabarito_v3_mudancas.md | 57f74f5ccb6f76d9d2a0f60cfa25dc3549e2506bd7b2d94d26d287e7a9cd2dcc |
| build_gabarito_v3.py | e0853184fd5011a99b065d66500cf9c6524657c0aa8e45335a712057979e5009 |
| gabarito_v2.json | cd4fb54f657cd4c17c9c57eb894ac31317a559fa8d5ca0d6ee49df0c9db99ca4 |
| judge.py | 4031cf01e4667c8e80dc9aca618cbd26300657c7d57e3cbc035fabb0f23dbe9f |
| retabulate_author_rule.py | ab6dd38c99f9f2201e4bba3ffcd3a266d19b0bc86fc034e28a81f9198176ede4 |
| cards.py | 88202d39b5cd8b1ce79279a29f6e7e6dc654b19d73ab5ab07bdd7cac9a9ffbf0 |
| cards_k3/cards.json | 8306fbb1b5cf9745bc2c50c4a8ce7f4ceda438bfd159a2a926bafaea41acb05c |
| cards_k3/key.json | 5e64c53d69ac7658af211a6b06e234a589680230a91a026c4df52fccae7a0d81 |
| prompts/phase2.txt | cf8d84f9e5e9e7a182d04fc2e110c88f89e1174b44f5ce317efb8a30d539d387 |
| prompts/phase2_patch.txt | 84657ae323f074c733537c57653e85327600ca3802fba1bf4556a86b74d66fd4 |
