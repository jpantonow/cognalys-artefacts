# Pré-registro do rejulgamento da comparação (2026-11-14)

Escrito em 2026-11-14, 17:45 (-03:00), antes de existir qualquer resultado do rerun dos braços C e D
(tag `k3`). Nessa hora o braço C tinha começado (17:36) e a primeira das 80 runs estava em andamento
(`run_status.json` com `running`). Nenhuma run tinha terminado. O braço D ainda não tinha começado.
Nenhum cartão `cards_k3/` existia, e nenhum juiz tinha sido chamado para o rerun.

Revisão às 17:58 (-03:00), depois de uma revisão do kit: só as salvaguardas da leitura do patch (seção
3) e o sha256 de `retabulate_author_rule.py`. A regra de pontuação não mudou. Nessa hora duas runs do
braço C tinham terminado (17:50 e 17:56). A saída delas não foi lida, nenhum cartão `cards_k3/` existia
e nenhum juiz tinha sido chamado.

## O que motiva

No primeiro julgamento a prova de cegueira mostrou que os juízes reconheciam os cartões do Cognalys
(8 de 8 do braço C), sobretudo pelo estilo da descrição do PR e pelo texto do registro do pipeline nos
cartões ISSUE e NENHUMA AÇÃO. Então as notas do C não foram cegas. A leitura do autor foi definida
depois de vistos os resultados. Este arquivo fixa, antes dos resultados, o que será julgado e relatado.

## Unidades e cartões

As 20 unidades da comparação são todas julgadas de novo. Os cartões vêm de
`ARMS_TAG=k3 python $J/cards.py` (S, O e T iguais aos primeiros; C e D do rerun; mesmo sal, mesmos ids),
e o lint da §3.7 tem de passar. Comandos da raiz do repositório no WSL com o venv,
`J=outputs/comparison_20261113/judges`. Juízes: opus (principal) e sol.

## As três leituras relatadas

1. **Protocolo** (gabarito `gabarito_final.json`, rubrica completa da §3.6):
   ```
   ARMS_TAG=k3 python $J/judge.py phase2
   cd $J && CARDS_DIR=cards_k3 PHASE2=phase2_k3 python tabulate.py          # tabela_k3.md/.json
   ```
2. **Autor** (gabarito `gabarito_v2.json`). Regras: (1) o gabarito é agir agora, e um PR é entregável
   aceitável em todo achado; (2) nenhuma ação não perde ponto pela razão; (3) uma issue não perde ponto
   pelo texto; (4) uma correção que a ponta já tinha ("correção não aplicável: código mudou upstream") é
   julgada só pelo achado, sem contar o build da ponta; e PR rejeitado por mantenedor não é penalizado
   (o gabarito v2 é escrito sem a resposta do mantenedor). PR aceitável quando r2 >= 2, r4 >= 1 e
   compilou. FALHA = 0. Nota final: os dois juízes concordam, senão o opus.
   ```
   JUDGE_READING=v2 ARMS_TAG=k3 python $J/judge.py phase2
   cd $J && CARDS_DIR=cards_k3 PHASE2=phase2_v2_k3 python retabulate_author_rule.py   # tabela_regra_autor_v2_k3.md/.json
   ```
3. **Patch** (gabarito `gabarito_v2.json`, prompt `prompts/phase2_patch.txt`). Os juízes veem só os
   cartões de PR: o achado, o diff contra o commit analisado e se o autor compilou aquele diff, sem
   título nem descrição. Notas: R2, `fixes_what_is_reported` e `regression_scenario`. PR aceitável quando
   r2 >= 2 e compilou (ou a regra 4 se aplica). ISSUE e NENHUMA AÇÃO = 1, FALHA = 0. Nota final: os dois
   juízes concordam, senão o opus.
   ```
   JUDGE_VIEW=patch JUDGE_READING=v2 ARMS_TAG=k3 python $J/judge.py phase2
   cd $J && READING_RULE=patch CARDS_DIR=cards_k3 PHASE2=phase2_v2_k3_patch python retabulate_author_rule.py
   # tabela_regra_autor_v2_k3_patch.md/.json
   ```
   Salvaguardas da leitura do patch: o script só lê registros da visão do patch (`view: "patch"`,
   `PHASE2` terminando em `_patch`) julgados com o gabarito `gabarito_v2.json` da unidade, e aborta se
   houver outro. Um cartão de PR sem nota final (sem registro, falha do juiz, sha desatualizado) é contado
   na coluna "PRs sem nota", e a tabela não é gravada enquanto houver algum: a fase 2 é refeita para ele.
   Se ainda assim faltar nota, a tabela é gravada com `ALLOW_UNSCORED=1`, a contagem é relatada, e uma
   unidade sem nenhum registro da visão do patch fica fora de todos os braços.

As três leituras são relatadas juntas, por braço (C nas duas leituras, "todo rascunho pronto" e "o que
publicaria"), seja qual for o resultado. Nenhuma é escolhida depois de ver as outras.

## Provas de cegueira

- **Cartão inteiro:** 40 cartões julgados, 8 por braço, sorteados com semente. Uma sessão do opus por
  cartão, sem ferramentas (`prompts/blindness.txt`).
  ```
  JUDGE_VIEW=full ARMS_TAG=k3 python $J/judge.py blindness                 # blindness_k3/
  cd $J && python blindness_summary.py blindness_k3 --cards cards_k3
  ```
- **Visão do patch:** só cartões de PR, até 10 por braço, sorteados com semente (menos se o braço tiver
  menos, e o log registra o que ficou de fora). Os cartões vão sem título nem descrição, com o mesmo
  prompt de cegueira.
  ```
  JUDGE_VIEW=patch ARMS_TAG=k3 python $J/judge.py blindness                # blindness_k3_patch/
  cd $J && python blindness_summary.py blindness_k3_patch --cards cards_k3
  ```

Critério relatado: a fração de cartões atribuídos ao braço certo, por braço, nas duas provas (e, como
apoio, a fração atribuída à família certa, C/D contra S/O/T). Nenhum limiar é declarado. A fração é
relatada como medida, e nenhuma leitura é descartada por causa dela.

## Arquivos fixados (sha256 ao fim da preparação)

| arquivo (em `outputs/comparison_20261113/judges/`) | sha256 |
|---|---|
| `gabarito_final.json` | `3bd5740bb68d83a22defa70c77a197348fd66dd427951baf57974c2d0746c814` |
| `gabarito_v2.json` | `cd4fb54f657cd4c17c9c57eb894ac31317a559fa8d5ca0d6ee49df0c9db99ca4` |
| `prompts/phase2.txt` | `cf8d84f9e5e9e7a182d04fc2e110c88f89e1174b44f5ce317efb8a30d539d387` |
| `prompts/phase2_patch.txt` | `84657ae323f074c733537c57653e85327600ca3802fba1bf4556a86b74d66fd4` |
| `prompts/blindness.txt` | `e6c44233f9dc4946702e5ea5a36e35b8cb1c87e44cd63d8989afd621f4421a1a` |
| `judge.py` | `444ba3cd6df9e506f702abfe409520b468525e246d757cdda36cc41072b83fc9` |
| `cards.py` | `a217bb5e6ad9d4ff685da355228e84bd60c0e3c2484e4f29f0294a48460dc535` |
| `tabulate.py` | `89235280aa2eda676d053befa150d429f7e43f8d60edba2cace847cb830907da` |
| `retabulate_author_rule.py` | `a853ef35732939c29d26c8a9783ec90c67484b1812626e93a51eea30bcac712e` |

Conferir: `cd $J && sha256sum gabarito_final.json gabarito_v2.json prompts/phase2.txt prompts/phase2_patch.txt prompts/blindness.txt judge.py cards.py tabulate.py retabulate_author_rule.py`.
Qualquer mudança nesses arquivos depois daqui é registrada como desvio deste pré-registro.
