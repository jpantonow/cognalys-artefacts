# Pré-registro dos braços "+" da comparação (2026-11-14)

Escrito em 2026-11-14, 17:58 (-03:00), antes de qualquer run dos braços TP e SP. Nessa hora não existia
nenhum diretório `runs/<unidade>/TP_r*` ou `SP_r*`, o braço C (rerun `k3`) estava na terceira run e o
braço D não tinha começado. Este arquivo complementa `preregistro_rejulgamento_20261114.md` e não muda
nada dele.

Revisão às 18:10, ainda sem nenhuma run de TP ou SP: o WSL parou às 17:59 e o braço C voltou às 18:02,
retomando a terceira run. Os hashes abaixo foram recalculados depois disso e não mudaram. A fila agora
confere a ordem C, depois TP/SP, depois D: `plus_arms_queue.sh` e `chain_after_c.sh` só seguem quando
`c_arm_finished.py` diz que o C terminou (todas as runs com `done_`, `needs_rerun_` ou `cap_reached_`, ou o
teto de US$ 30 atingido). Lock livre com C incompleto (WSL reiniciado, reboot) para a fila com código 7,
sem rodar nada.

## Pergunta

Um agente que segue, pelo prompt, o mesmo processo que o Cognalys faz em código continua mais barato
que o Cognalys? E quanto vale o que ele entrega? O par principal é TP x C (mesmo modelo, processo no
prompt ou no código). TP x T e SP x S mostram o efeito do prompt "+" no mesmo agente. SP x D repete a
comparação com outro fornecedor, mas o D vai ser parcial.

## Braços

| braço | agente | modelo | esforço | réplicas | igual a |
|---|---|---|---|---|---|
| TP | Codex CLI | gpt-6.1-terra | high | 4 | T |
| SP | Claude Code | claude-sonnet-5-5 | high | 2 | S |

São as mesmas 20 unidades, as mesmas bases (commit fixado, sem remote), as mesmas ferramentas e flags
de CLI, o mesmo timeout de 60 min e o mesmo `build_check`. Os controles de vazamento também são os de S
e T: HOME novo, só a credencial da assinatura copiada, nenhuma variável secreta e a marcação
`consulted_upstream`. A única diferença é o prompt. A ordem está em `order_plus.json`: réplica por
réplica, unidades e braços sorteados com `random.Random(20261113 + 1000 + r)`. S, O e T estão completos
e não rodam de novo. O `order.json` deles não mudou (sha256 conferido antes e depois da mudança do kit).

## Prompt

`units/<unidade>/prompt_plus.txt` sai de `common.prompt_plus_for`: é o `prompt.txt` com um único bloco
inserido antes de "Decide what, if anything". Fora isso, o texto é igual. O bloco exato, com o caminho do
`build_check` já preenchido (sha256 `7fefa5542009871d0ca70213b29f518c12577ef2d4eab344442b9988a4122b58`,
1376 caracteres, seguido de uma linha em branco):

```
Work in these steps, and keep your working notes in ANALYSIS.md at the repository root. ANALYSIS.md is for you only: it is not one of the files described below, it is not sent to the maintainers, and what you write for them should not refer to it.
1. Research what the rule requires and why, in the platform documentation and on the web.
2. Find every place in this repository that produces, stores, reads or depends on the reported value or object: callers, stored data and how it is read back, servers or peers, tests.
3. Decide whether the report is a real violation, whether it has security impact in this app, and who would be affected by a change.
4. If a change is warranted, plan the smallest change that removes what the report describes while keeping existing data, protocols and supported setups working.
5. Make the change.
6. Review your own diff. For each changed line, look for a concrete scenario through the app's own code where it breaks something: stored data that can no longer be read, a peer that can no longer connect, behaviour the user relies on. Also check that the change does not merely hide the reported call from the checker while the code keeps using the same value. If it does either, fix the change or abandon it.
7. Build with /home/antonow/comparison_20261113/bin/build_check and fix any failure.
8. Write one of the files described below.
```

sha256 de cada `prompt_plus.txt` (também em `outputs/comparison_20261113/prompt_plus.sha256`;
`plus_arms_queue.sh` confere esses valores e para antes da primeira run se algum mudar):

```
18f9724bc59ae7c2d5ad0a04a6fcf50939dfebabce49c1219d0a44ad54b2680e  co_0a32f516feb2
0c2b32959204153c5f1caeb4b2b07f39550cf4c48d9b40b11fc0c38592ebc89e  co_0b7656970a91
df9ba5efefc8cf7afaadc38df5e050fec87efe934345f6d376493580c74d40a5  co_2aaa72c51c14
51df7a10b4470352f72a84cefd07df3bcc04a245c027c088b12a650759c529f7  co_3c8e59dbbfb4
019a699c0dff43c71d71d3b3dc15e071a6833726b37776b31ca6d8c796d15a49  co_737920a862cd
b870b2031876d25d9aa61f87821022da134cce70533a60728aa2158eeb5c6010  co_911173ce3742
dd80a902b5ed67eeb9044429109c99360b2eee7edae35a1be761b36875fd765f  co_9da181fd6978
1c47846e5c6b7f71fa9438fd7ecb05ccf16fc3f24058d0305e71089806954ef8  co_bdc0d1804c88
44fed8d7a957619fe7e07e8892963d2c4b2106e0e8ab760a255d7cfc61f48714  co_ed824f6818ef
798dc138ba8b7c2d6230ecc823b7800e5783f2a151e6fcfb7685b3c608862087  co_f637c4b8467e
aef659a83d308dbfb9a7db641c54d8d624096413643847d9ceea3f42e12857fc  pr_013d2748f3f8
cb1285bccc26d114ce320643d2e4f7233488a677c7ed6b89d3e4412c5c316f78  pr_06ff0c1fc96c
cad7ad5e69d4a87d2c7caa85cd22ec80b00a006d6b3d4c2e35d1718c84196981  pr_1381cd4ddb3f
6c2cc977e2048353bd14c85f84687f51339713a6fd6fcfe74dad4f9d90dd4d36  pr_2b8cb7f6653b
7cd8ffa8495db5af63486382910cd39bf370eb26f8b8fbb181462757f8541663  pr_39bba5f1270f
61e4d0ef2e7f9ada46d0bb796cb4916477ba805621527ac297121c4697d65fff  pr_6d0a3cf73013
091d5b2ad332422b956601cc5eb30ffef65082412f08d8c92479a489f841fd67  pr_7d98a07e751d
c97bdb474ae7e928a6d47d3aa1197de2674e3b461ecb31ca8c743521ae62be31  pr_9215f97597a6
04dc306507522072ec595ca9889c5877214659a9a164dab04e04fe0cc79ef74d  pr_ac1a8dd4d680
76753725d21b193b6e2ec813370562d27217e4d2ccb21482b530d0d3895e4a3e  pr_fff072cb8953
```

O `ANALYSIS.md` é rascunho do agente, não entregável. Ele fica fora do `patch.diff` e do `diff_sha` das
compilações (`build_check.SKIP`), fora de `untracked.txt` e fora do cartão. O arquivo é guardado na run
como `working_notes.md`, só para consulta do autor. O `result.json` registra `prompt_file` e
`prompt_sha256`.

## O que será relatado

Por braço, com todos os braços (S, O, T, TP, SP, C, D) na mesma tabela de `analyze.py`:

- custo por run (média e total) em US$ equivalente de API, com a taxa de busca na web incluída (abaixo);
- a mistura de entregáveis: PR, ISSUE, NENHUMA AÇÃO e FALHA (inclusive timeout), PRs que compilam,
  segundos e estabilidade entre réplicas;
- a parcela aceitável nas três leituras do pré-registro do rejulgamento (protocolo, autor e patch), com
  as mesmas regras e juízes, quando o kit dos juízes incluir TP e SP;
- o custo por entregável aceitável em cada leitura (custo total do braço dividido pelos aceitáveis).
  Assim se responde também ao caso em que o agente "+" sai mais caro que o Cognalys.

Tudo é descritivo, com 20 unidades e 2 a 4 réplicas, sem teste de significância. Os números são
relatados como saírem, inclusive se TP ou SP ficarem mais caros que C ou D.

## Custo no mesmo nível de preço

Os agentes rodam em assinatura e são precificados a preço de lista (`data/llm_prices.json`). O braço C
roda em Flex, que custa metade da lista. Por isso a comparação de custo é relatada nos dois sentidos:

- **Tudo a lista:** agentes como acima; C com seus tokens precificados de novo a lista, mais a taxa de
  busca; D como registrado (a Anthropic não tem Flex).
- **Tudo em Flex:** TP e T com tokens a preço Flex, mais a taxa de busca (a taxa não tem desconto de
  nível); C como registrado. SP, S e D ficam sem valor Flex, porque o modelo não tem preço Flex.

A taxa de busca é US$ 10 por 1.000 buscas, nos dois modelos. No Codex só contam os itens de busca com
ação `search`, a mesma regra que `cost.py` aplica aos traces do Cognalys; `open_page` e `find_in_page`
não contam. No Claude Code o custo informado já inclui a taxa, então ela não é somada de novo. Isso foi
conferido numa run do S: o custo da chamada Haiku de busca é exatamente tokens + US$ 0,01. As colunas
antigas de `analyze.py` (US$ total, US$/run) mantêm o sentido de antes. As novas são "US$ API c/ busca",
"US$ lista/run" e "US$ Flex/run".

## Limites declarados antes

- O timeout de 60 min é o mesmo de S e T. Como o processo "+" é mais longo, pode haver mais timeouts.
  Eles serão relatados como FALHA, como nos outros braços.
- O SP tem 2 réplicas e o D será parcial, então o par SP x D é o mais fraco.
- Os juízes usam as mesmas assinaturas. Os braços rodam antes dos juízes, um de cada vez.
- O prompt "+" pede pesquisa na web, então a chance de achar o PR já publicado no upstream pode subir.
  A marcação `consulted_upstream` é relatada por braço, como em S, O e T.

## Revisão de 14/11, 19h: o prompt "+" com a arquitetura completa do pipeline

Escrita antes de qualquer run de TP ou SP (nenhuma pasta `runs/*/TP_r*` ou `runs/*/SP_r*` existe) e enquanto o
braço C estava em andamento. Substitui o bloco curto acima, a pedido do usuário: para a comparação ser justa, o
prompt "+" deve ter a mesma arquitetura do pipeline.

- **Bloco novo:** `outputs/comparison_20261113/plus_prompt/process_block.txt` (12.077 palavras, sha256
  `a5e595500411afe7265c7813eedf238dfc5e9f46469e3edb2ebde2fcb3e65e70`). Foi escrito a partir de um inventário de
  cada estágio e checagem da biblioteca no commit `fd90351` e revisado por um crítico de completude e de
  vazamento. Tem dez estágios na ordem do pipeline (evidência, pesquisa, fatos e decisão, plano, rascunho,
  checagens, revisão de comportamento, redrafts e replanejamento, build, texto), registrados em `ANALYSIS.md`,
  que não é entregue. A correspondência item a item está em `plus_prompt/mapping.md`, com a lista do que um
  prompt não reproduz (porte para a ponta do branch, corpus de pesquisa, validadores e retentativas impostos
  por código, contextos separados) e por quê.
- **Mesmas entradas:** `$unit_inputs` recebe `plus_prompt/inputs/<unit>.txt`, que traz a regra publicada por
  trás de cada relato e as respostas de mantenedores de outros repositórios à mesma regra, como o braço C as
  recebeu (extraídas dos pedidos gravados do braço C em 8be9f78; idênticas em k3 nas unidades já alcançadas).
  Nada vem do próprio repositório da unidade. O nome de usuário e o hash de commit numa resposta citada viraram
  `@<contributor>` e `<commit>`. sha256 da concatenação dos 20 arquivos de entrada (ordem alfabética):
  `d0bf37dda756984474c42efa1639d50599b4bdfcd036d506fd1af1bdc772d260`.
- **Prompts gerados:** `units/<unit>/prompt_plus.txt` = `prompt.txt` com o bloco inserido antes de
  "Decide what, if anything" (12.815 a 14.054 palavras); `prompt.txt` de S, O e T não mudou. Hashes em
  `prompt_plus.sha256` (sha256 do arquivo: `bd650315df342bc72f1ff3eaf2d1eebb738ff2ea37ce6c20bdaf49082735d579`); a
  fila confere esses hashes antes de rodar.
- **Falha equivalente à do C:** uma `NO_ACTION.md` de TP ou SP cuja primeira linha é
  "Reason: correction not generated" conta como FALHA (nota 0, sem julgamento), como "correção não gerada" no
  braço C.
- **Juízes:** TP e SP entram só nos cards do rerun (`ARMS_TAG=k3`) e nas três leituras pré-registradas; os
  testes de cegueira continuam com os cinco braços originais. No modo padrão, os prompts e as tabelas do
  primeiro julgamento saem idênticos (conferido). sha256 do kit após esta revisão: `judges/cards.py`
  `75f47283fb10ab4fc57ae8c00b0b945b94ae0247c480fec3f88012216f35b702`, `judges/judge.py`
  `89ec66c3b4346b203ad875eab16c974c6ce5c3d59094f013ead0509c5a0cf204`, `judges/tabulate.py`
  `ff6fc376122980a31fe9167f4227e29cd38d762e4a4c622dbf80afaa98fa4c23`, `judges/retabulate_author_rule.py`
  `add1a41d33e7d3f034a14d26ebe64178a7d7f3de410a57d3371edd72ea3deaca`, `common.py`
  `648625b1b919221cf5d2d7e3c56a95cacc3d9a614b0a39be58986fdb8df19580`; prompts dos juízes sem mudança.
- **Limites acrescentados:** o prompt "+" é muito mais longo que o original, então o limite de 60 min pesa mais;
  as pastas de trabalho dos agentes levam o nome da unidade (prefixos `co_`/`pr_`), como em S, O e T.
- **Defeito do pipeline achado nesta preparação (declarado, não corrigido durante os braços):** no braço C, a
  revisão de comportamento do passo de porte para a ponta do branch recebeu, num caso (pr_1381cd4ddb3f, r2, em
  8be9f78), a resposta de mantenedor do próprio repositório da unidade, porque o registro do achado nesse passo
  não traz `repository_url` e a exclusão não age. A revisão não encontrou regressão nem citou essa resposta. O
  `src/` não muda enquanto os braços rodam; a correção fica para depois.
