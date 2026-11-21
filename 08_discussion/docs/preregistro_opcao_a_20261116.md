# Pré-registro: opção A, agentes com o processo completo e o porte para a ponta (2026-11-16)

Escrito, com hashes, antes de qualquer execução nova (nenhum `runs/*/OP_r*`, `runs/*/SP2_r3`, `SP2_r4` ou `runs/*/*/port/` existe).

## Motivo

Os braços de agente faziam uma tarefa menor que o braço C. Os agentes paravam no commit analisado; o Cognalys ainda leva o patch à ponta do branch de destino (merge de três vias; em conflito, um passo de LLM que porta, declara "já corrigido" ou se abstém; compilação na ponta) e paga por isso. A comparação era assimétrica no trabalho e no custo. Decisão do usuário (2026-11-16): a comparação passa a ser entre braços que fazem o processo completo, todos contra C, que fica como está.

## Braços

| braço | CLI | modelo | prompt da fase 1 | réplicas | fase 1 |
|---|---|---|---|---|---|
| TP | Codex | gpt-6.1-terra | prompt_plus.txt | 4 | já executada (pré-registro de 2026-11-14) |
| SP2 | Claude Code | claude-sonnet-5-5 | prompt_plus_sp2.txt | 4 | r1-r2 já executadas; r3-r4 novas |
| OP | Codex | gpt-6.1-sol | prompt_plus_sp2.txt | 4 | nova |
| C | Cognalys | gpt-6.1-terra (Flex) | — | 4 | como julgado (k3 com 4 FALHA trocadas) |

OP usa o prompt do SP2 (sem o pedido de registro ANALYSIS.md, que o Claude Code recusou). Nova ordem: `port_phase/make_order_option_a.py` (apêndice de order_plus.json; backup `order_plus.json.bak_pre_option_a`).

## Fase 2: porte para a ponta (nova, para TP, SP2 e OP)

`port_phase/run_port.py`, para cada execução da fase 1 cuja classe é PR:

1. Ponta por unidade (`port_phase/tips.json`, `prep_tips.py`): o base_commit que C registrou (9 unidades); nas outras 11, a ponta do branch nomeado por C no momento do clone da primeira execução k3 de C na unidade.
2. Checkout da ponta, sem remoto. O patch e o PR.md da fase 1 ficam ao lado (PHASE1_PATCH.diff, PHASE1_PR.md); a fase 1 não muda.
3. Passo determinístico, como o merge de C: `git apply --3way`. Aplicou limpo: o kit compila na ponta, sem agente (o merge de C também não custa).
4. Conflito: o mesmo agente (CLI, modelo, esforço) recebe `port_phase/port_prompt.txt` e escreve PORTED.md, ALREADY_FIXED.md ou ABSTAIN.md. Tokens e custo entram no custo do braço.
5. ALREADY_FIXED é confirmado por verificação determinística (linhas adicionadas presentes na ponta e removidas ausentes, ou arquivos sumidos); sem confirmação vira ABSTAIN, como em C (PORT_ALREADY_FIXED_UNCONFIRMED).
6. PORTED: o kit compila o diff final na ponta, se o agente não compilou esse diff.

Prévia só do passo determinístico (sem agente, sem build; `merge_preview.py`): TP 24 de 38 PRs aplicam, 14 precisam do agente; SP2 16 de 23 e 7. Os conflitos estão nas três unidades em que C também não portou (pr_06ff0c1fc96c, pr_1381cd4ddb3f, pr_fff072cb8953) e em co_bdc0d1804c88.

## Regra de tabulação (igual para todos os braços)

- Os juízes leem o mesmo cartão de antes: diff e texto da fase 1, no commit analisado (como os de C).
- PR da fase 2 "ported": aceito com r2 >= 2, r4 >= 1 e compilação na ponta com sucesso (C: "PR pronto" = compilou na ponta). Falha de compilação na ponta, ou fase 2 "failure": PR recusado (C: "correção gerada, sem PR").
- PR da fase 2 "already_fixed" ou "abstain": tratado como o "correção não aplicável: código mudou upstream" de C (em C, pr_1381cd4ddb3f teve ALREADY_FIXED não confirmado, virou ABSTAIN e ainda assim terminou nesse resultado): regra 4, aceito com r2 >= 2, sem texto e sem compilação.
- Issue e nenhuma ação: 1, como antes. Execução FALHA: 0.
- Custo: fase 1 + fase 2, preço de lista da API (agentes em assinatura), como referência, não critério.
- Leituras relatadas: autor v3 e patch v3 (gabarito_v3.json, mesmos juízes), mais a tabela da fase 2 (portados e compilados na ponta, já corrigidos, abstenções), ao lado dos números de C (20 PRs portados e compilados, 11 não aplicáveis).
- Juízes: só cartões novos (SP2 r3-r4, OP r1-r4); os de TP, SP2 r1-r2 e C já têm notas v3.

## Relato

O desenho anterior (S, O, T sem o processo; TP e SP2 sem porte) sai da tabela e da análise do artigo por decisão do usuário. Uma frase em ameaças à validade declara que ele existiu, por que foi substituído e onde estão os dados; nada é apagado. Todos os resultados da opção A são relatados, favoráveis a C ou não.

## Hashes (sha256)

Gerados por `port_phase/hash_option_a.sh` (arquivo `port_phase/option_a.sha256`) antes da primeira execução; a fila confere antes de começar. Caminhos relativos a `outputs/comparison_20261113/`.

| arquivo | sha256 |
|---|---|
| port_phase/port_prompt.txt | ed86f13bd3a82eb33ebb1d5931aaeced1cb3f65208acf3c02d501fa3d0d41a11 |
| port_phase/run_port.py | efc38fb92a843cdca2b06c7e68ffcf1c77c70d00a33f3f8bc428eb612f52a5fb |
| port_phase/port_driver.py | 7bc3f03f93a6fcc5dc5452691fcde73d8099588b8453024ed0770de782b2f78e |
| port_phase/tips.json | f5ee70741155c4c116cbeef6b228b7f9c2495ebaf8591627d48a0240b66472ed |
| port_phase/prep_tips.py | 49a4411c6408b74d9aa43f99aa2116d0118469e1723ae11822651b5b51abc685 |
| port_phase/prep_tip_repos.py | 86a0b76a0050af4aecc2ba68715cda527f26c691f25461181cbeb98915a50c10 |
| port_phase/make_order_option_a.py | 38a4000a0c786098fb5f31004ad0ad2e1e7458b335a07f0b4989386fe10f18b8 |
| run_one.py | d3d6a9617a4ae021132d9a2bbe5f14e6cb551ff8d695e518b38382bd7352ee67 |
| driver.py | f4a6533b9796b0bce235ebb0a9b451dd9e5d2eabd2dc1600dbe1b080a6b843ef |
| common.py | 500101cdb97f451d22483e4296fbad2be24e16f7b4eded6560586e9c4644114e |
| build_check.py | 7e30676ad06decb26103fa11e7c5b97fc054f8ea6b9b183851552829beff80fb |
| order_plus.json | 5c16560b9f1438188353ee9f94dd9613f9792cef997707216b67f40321e47b8c |
| prompt_plus_sp2.sha256 | 666de41d8af93d8fa626384ede443aeaaad4b762035a71599d64d57df5547fa3 |
| prompt_plus.sha256 | bd650315df342bc72f1ff3eaf2d1eebb738ff2ea37ce6c20bdaf49082735d579 |
| units/*/prompt_plus_sp2.txt (20; sha256 das suas linhas) | b63cab92e255e30e35d7784bc203c868112789b96d07b7d1466ada3d1225b66c |

`build_check.py` mudou só para excluir dos diffs os arquivos da fase 2 (PORTED.md, ALREADY_FIXED.md, ABSTAIN.md, PHASE1_PATCH.diff, PHASE1_PR.md), que nunca existem numa execução da fase 1.

## Acréscimo antes dos juízes: prova de cegueira dos braços da opção A (2026-11-17)

Escrito antes de qualquer cartão da opção A ser gerado ou julgado (fase 1 do OP em 12/80, nenhuma fase 2 além do teste de fumaça, nenhum juiz). Decisão do usuário.

- **Por quê.** Na prova de 14/11 (`judges/blindness_k3`, `blindness_k3_patch`), no cartão inteiro o juiz respondeu "C" em 25 de 40 cartões, 8 deles de C (os 8 de C, mais 8 de O, 5 de T e 4 de D), com confiança média de 0,28 a 0,35; só no patch, nomeou C em 1 de 10. Os braços da opção A seguem o processo de C, inclusive o formato do texto, e podem ficar mais parecidos com C.
- **Como.** A mesma prova (`judge.py blindness`, mesmo `prompts/blindness.txt`, juiz principal, uma sessão por cartão, só o cartão), com a lista de braços trocada pela da opção A (C, TP, SP2, OP; o hash do prompt ajustado entra no log antes da execução). Duas vistas: cartão inteiro, 10 cartões por braço; só o patch, até 10 cartões PR por braço. Sorteio com o salt do kit, como em 14/11. Os cartões de C são os já julgados do k3.
- **Quando.** Depois de gerados os cartões novos e antes de ler qualquer nota dos juízes da opção A.
- **O que se relata.** Por braço e vista: acertos do braço, acertos da família, confiança média, distribuição dos palpites e, para cada braço, a precisão do palpite (dos cartões chamados daquele braço, quantos eram dele). Esta última medida é declarada agora porque a prova de 14/11 mostrou um palpite padrão ("C"); acertar todos os cartões de C não é identificação quando o mesmo palpite cobre os outros braços.
- **Uso.** Descritivo: qualifica a palavra "blind" no artigo; não muda notas nem a regra de tabulação.

## Desvio datado: S, T e O voltam ao artigo como bloco declarado (2026-11-17)

Revoga a seção "Relato" acima (decisão do usuário de 2026-11-16 de tirar S, T e O da tabela e da análise), por decisão do usuário de 2026-11-17, depois do parecer de revisão que leu a retirada como relato seletivo. Escrito antes de qualquer nota da opção A.

- A RQ6 passa a ter duas partes: (a) um agente de código basta quando recebe o achado e o repositório? Respondida por S, T e O, só fase 1, contra C. (b) Executar o processo como pipeline acrescenta algo a um agente instruído a seguir o mesmo processo? Respondida por TP, SP2 e OP, fases 1 e 2, contra C.
- Os dois blocos ficam na mesma tabela, nas mesmas leituras, com as assimetrias declaradas: S, T e O não fizeram o porte para a ponta e não receberam a política do Cognalys para correções só de regra; C pagou e fez o porte.
- Leituras relatadas: autor v3 (com a regra 4) e patch v3 (decisão do usuário de 2026-11-17). As leituras anteriores (protocolo e v2) não entram na tabela; uma frase declara que existiram, por que foram substituídas (erros nos gabaritos, descritos em `docs/preregistro_gabarito_v3_20261116.md`) e onde estão os dados.

## Desvio datado: o braço OP sai da opção A (2026-11-17, 15:37)

Decisão do usuário de 2026-11-17, tomada antes de qualquer cartão, juiz ou nota do OP.

- **Motivo.** A cota da assinatura do Codex se esgotou às 13:38 na 26.ª execução da fase 1 do OP (unidade co_737920a862cd, réplica 2). Faltavam 55 das 80 execuções. Com a entrega em 20/11, o OP não terminaria a tempo, e a mesma cota ainda é necessária para a fase 2 do TP.
- **O que muda.** A RQ6(b) passa a ser respondida por TP e SP2 contra C. As 25 execuções da fase 1 do OP (20 da réplica 1 e 5 da réplica 2) ficam guardadas nos dados e não entram em tabela, juiz, prova de cegueira, custo ou texto, exceto por uma frase que declara este desvio. A prova de cegueira da seção anterior passa a usar C, TP e SP2.
- **Como.** A fila `queue_option_a.sh` foi parada. `port_phase/queue_option_a_no_op.sh` roda só a fase 2, com `ARMS_ONLY=TP,SP2`. Os hashes de `option_a.sha256` foram conferidos de novo no início e continuam iguais; nenhum arquivo do kit mudou.
- **O que não muda.** O braço O, do primeiro bloco, continua na RQ6(a). Ele já tinha terminado, e retirá-lo depois de conhecidos os resultados seria relato seletivo.

## Desvios datados do julgamento da opção A (2026-11-17, noite)

1. **Sessão separada para SP2 r3–r4.** `judge.py phase2` julga uma unidade inteira numa sessão, com a chave formada pelos hashes dos cartões da unidade. Acrescentar cartões faria reavaliar todas as unidades e sobrescrever as notas v3 que já existiam.
   - Os 40 cartões novos (SP2 r3–r4) foram julgados por `judges/judge_option_a_new.py`, com uma sessão por unidade e juiz contendo só esses cartões.
   - O script usa os mesmos prompts, esquemas, pós-checagens, gabarito v3 e executor de `judge.py`. Saída em `phase2_v3_k3_sp2r34[_patch]/`.
   - Nenhuma nota existente foi refeita.
   - Diferença em relação ao protocolo: esses cartões foram avaliados lado a lado entre si, e não ao lado dos outros cartões da unidade.
2. **Teste da tabulação durante a prova de cegueira.** Às 18:57, com a prova de cegueira em execução, um teste de `tabulate_option_a.py` imprimiu os números do TP já ajustados pelo porte e os do SP2 r1–r2.
   - A amostra e o prompt da prova já estavam fixados e registrados desde as 18:54.
   - As notas do SP2 r3–r4 ainda não existiam.
   - A prova não usa notas.
