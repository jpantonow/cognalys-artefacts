> Especificação da comparação Cognalys × Claude Sonnet 5.5 × GPT 6.1 Sol × GPT 6.1 Terra (agentes), escrita em 2026-11-11 para ser implementada por uma sessão Opus 5.5 seguindo docs/PLANO_FABLE.md. Substitui o desenho de `outputs/agent_baseline_20261108/` (2026-11-08). Os scripts de medição citados ficam em `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\comparacao\` (não versionado). O código lido é o do worktree `C:\Users\Antonow\Documents\tcc_v3_blockK` (HEAD 7161173). Nada foi editado em `src/`, `tests/` ou `docs/`, e nenhuma chamada paga foi feita. As perguntas da seção 9 esperam a resposta do usuário antes das partes que dependem delas.

# Comparação de 2026-11: Cognalys contra três agentes, com réplicas, execuções isoladas e julgamento cego (2026-11-11)

Marcação dos números:

- (M) foi medido por script. O script vem nomeado ao lado e fica em `outputs\design_20261110\comparacao\` (abaixo, `C\`), com a saída em `<script>.out`. Para rodar: `wsl.exe bash -lc "cd /mnt/c/Users/Antonow/Documents/tcc_v3 && source ~/venv/bin/activate && python outputs/design_20261110/comparacao/<script>.py"`.
- (E) é estimativa, e a base vem dita.

Scripts desta especificação: `c01_cohort.py` (coorte da run congelada e estratos), `c02_bench_runs.py` (todas as execuções do Cognalys nas 9 findings do benchmark, desfecho e custo pelo `cognalys cost --json`), `c03_agents_20261108.py` (os agentes de 2026-11-08), `c04_power.py` (poder, a partir do ruído medido), `c05_units.py` (unidades da comparação).

Convenção: "execução" é uma rodada de um braço numa unidade; "unidade" é o que o Cognalys analisa como uma coisa só (findings juntadas por `join_findings_of_one_sequence`); "cartão" é a entrega de uma execução, anonimizada, que o juiz lê.

## 1. Resumo

**Pergunta que o relatório responde.** O que o Cognalys ainda perde para o melhor agente, unidade por unidade, e se cada perda tem um conserto no pipeline ou se o projeto chegou ao teto com este modelo.

**Conjunto de findings.** As unidades da coorte da run congelada que o Cognalys de fato analisa: 28 unidades (32 findings, 24 APKs), das 45 findings distintas da coorte (34 consolidadas e 12 do benchmark, com o RedReader `sha1` nos dois). Ficam fora as 13 findings que o pipeline encerra antes de qualquer chamada ao modelo porque um PR nosso para elas está aberto (5) ou foi incorporado (8) (M, `c01_cohort.py`, `c05_units.py`, estados de 2026-11-08). A run congelada e a comparação partilham a coorte; a comparação usa o pedaço dela em que há o que comparar.

**Braços** (todos em esforço high, com as mesmas informações de entrada):

| Braço | O que roda | Tier servido | Como entra no custo |
|---|---|---|---|
| C | Cognalys no commit congelado (vfix, sobras, Q1, Q2Q3, K3), gpt-6.1-terra | Flex pela chave da API | Cobrado; e tokens a preço de lista standard; e tokens a preço Flex |
| S | Claude Code 2.1.319 com claude-sonnet-5-5 | Assinatura (standard da Anthropic; não há Flex) | Tokens a preço de lista standard, escrita no cache a 1,25x; `total_cost_usd` do CLI como conferência |
| O | Codex CLI 0.167 com gpt-6.1-sol | Assinatura ChatGPT | Tokens a preço de lista standard e a preço Flex (preços do Sol verificados no passo K0) |
| T | Codex CLI 0.167 com gpt-6.1-terra | Assinatura ChatGPT | Tokens a preço de lista standard e a preço Flex |

O braço T usa o mesmo modelo do Cognalys. É ele que separa o efeito do pipeline do efeito do modelo.

**Réplicas.** 4 por braço em toda unidade (448 execuções), e mais 2 em cada braço nas unidades com perda candidata. Com 28 unidades e 4 réplicas, o teste pareado detecta uma diferença média de 15 pontos na taxa de entrega aceitável com poder de 0,92 a 0,95 e uma de 10 pontos com 0,52 a 0,66, com alarme falso de 0,04 a 0,06 (M de simulação, `c04_power.py`, sobre o ruído das execuções isoladas). Uma réplica, como em 2026-11-08, não distingue nada.

**Isolamento.** Nenhuma execução lê o que outra produziu: store de aprendizado semeado só com imports verificados (`seeded_store` do VFIX), um por execução; sem cache de respostas de LLM; corpus de pesquisa do K3 copiado por execução a partir do estado depois da run congelada, com as escritas descartadas; agentes com configuração, HOME e checkout novos por execução, sem memória e sem credencial do GitHub.

**Critérios.** Só qualidade e custo. O tempo é gravado e fica fora do veredito (decisão do usuário, 2026-11-10).

**Qualidade.** Rubrica única para os quatro braços, julgada sem o braço à vista, em duas fases: primeiro o gabarito de cada unidade (que entrega é a certa), lido do código antes de ver qualquer cartão; depois cada cartão. Juízes: Opus 5.5 (principal) e GPT-6.1 Sol (segundo), pelas assinaturas. O usuário revisa todos os 28 gabaritos e julga às cegas 40 cartões; a concordância (kappa de Cohen) decide quanto das notas dos modelos vale. A medida primária é a fração de execuções com entrega aceitável. Ela é relatada em duas leituras do Cognalys: "o que ele publicaria" (rascunhos retidos contam como nada entregue) e "todo rascunho pronto".

**Perdas.** Uma unidade é perda candidata quando o melhor agente tem entrega aceitável em pelo menos 3 de 4 execuções e o Cognalys em no máximo 1 de 4. Depois das 2 réplicas extras, ela é confirmada pelo teste exato. Cada perda confirmada é atribuída à primeira etapa do pipeline em que a execução saiu do caminho certo, e classificada em defeito corrigível, amostragem do julgamento, falta de informação, capacidade do modelo ou referência discutível. "Teto atingido" só é dito se nenhuma perda confirmada cair em defeito corrigível ou falta de informação.

**Custo (E).** Cobrado: cerca de US$ 21 a 30, teto de US$ 40, quase todo do braço C. Agentes e juízes rodam pelas assinaturas: nada cobrado, e o equivalente a preço de lista (cerca de US$ 70 a 150) é só relatado.

**O que mudou em relação a 2026-11-08** (detalhe na §2.1):

| Crítica | Agora |
|---|---|
| Agentes rodaram uma vez; o ruído deles não foi medido | 4 réplicas por braço, mais 2 nas perdas candidatas |
| Execuções do Cognalys liam o histórico umas das outras | Store semeado por execução (VFIX), corpus copiado por execução |
| 3 decisões do Cognalys vieram da run original | Só entram unidades que o Cognalys analisa no commit congelado |
| 5 das 12 referências eram julgamento do autor, sem leitura cega | Gabarito por leitura do código, antes de ver as entregas, revisado pelo usuário |
| Só se julgou "PR ou não PR"; o texto foi julgado pelo checador do próprio Cognalys | Rubrica de patch, texto e justificativa, a mesma para todos, julgada às cegas |
| Agentes sem web e sem build; prompt com nomes internos de regra | Agentes com web e com o mesmo comando de build; regras de texto de publicação no prompt |
| Custo do Sonnet com escrita a preço de entrada; Sol com preço de fonte secundária; tiers diferentes sem dizer | Três colunas de preço por braço, escrita no cache cobrada, preço do Sol verificado antes |
| Vantagem de memória misturada com julgamento | Estrato próprio para as 7 unidades com resposta do mantenedor sobre o mesmo código |
| Tempo comparado | Gravado, fora do veredito |

**O que precisa do sim do usuário (§9):** o conjunto; deixar fora as findings com PR nosso aberto ou incorporado; dar web e build aos agentes; as regras de texto no prompt dos agentes; o número de réplicas; os juízes e a amostra do usuário; agentes e juízes só pelas assinaturas; o estado do corpus; o estrato da memória; o registro no PLANO_FABLE.

## 2. Diagnóstico medido

### 2.1 O que a comparação de 2026-11-08 fez, e o que se criticou

Lido em `outputs/agent_baseline_20261108/` (`RELATORIO.md`, `run_agent.py`, `run_claude_agent.py`, `prepare.py`, `prepare_cognalys.py`, `build_agents.py`, `compare.py`, `findings.json`, `noise_floor.md`, `design_plan.md`) e em `docs/PLANO_SUPERAR_SONNET.md`, `docs/PLANO_IMPLEMENTACAO.md` e `docs/PLANO_FABLE.md`.

- **Como os agentes foram chamados.** Um prompt único (`run_agent.py:35-43`): a linha do relatório do RVSec (especificação, classe, método, mensagem), "corrija e prepare o PR" ou "explique em `NO_PR.md`", "não há rede". Codex com `--ephemeral --ignore-user-config -s workspace-write`; Claude Code com configuração própria, edição e shell só de leitura, `WebSearch,WebFetch` proibidos (`run_claude_agent.py:51-54`). Um checkout por modelo e finding, uma execução.
- **Como foram julgados.** `compare.py:58-61` compara só PR/não PR com a referência; o texto passou pelo `text_problems` do próprio Cognalys (`compare.py:69`), que barraria 3 de 8 textos do Terra, 7 de 7 do Sol e 6 de 6 do Sonnet (M, `c03_agents_20261108.py`). O `RELATORIO.md` diz que boa parte desses problemas veio do prompt, que trazia o nome interno da regra.
- **Build.** Todos os 21 patches compilaram com `cognalys.builds.apk_build` (`build_agents.py`); os agentes não podiam compilar.
- **Cognalys.** Uma run por finding, standard, sem cache. Para CometOTP `unwrap`, TriliumDroid e NotallyX o rerun parou em "correção não aplicável: código mudou upstream" antes de chamar o modelo, e valeu a decisão da run original (`compare.py:26-30`).
- **Independência.** As 35 execuções do benchmark que planejaram leram o histórico de outras (ESPEC_VFIX §2.1). Os conjuntos de ruído de Input Leaf, InterTune, Melox, RedReader e Winterhold não eram amostras independentes.
- **Referências.** 5 das 12 são julgamento do autor, o Winterhold é "discutível", e a do CometOTP `loadOrGenerateWrappedKey` mudou durante o experimento (`findings.json`, `reference_note`).
- **Custo.** O Sonnet foi contado com a escrita no cache a preço de entrada: 0,0953 por finding, contra 0,1239 do `total_cost_usd` do Claude Code (razão 1,30; M, `c03`). O preço do Sol (2 / 0,10 / 10) veio de fontes secundárias (`run_agent.py:25-29`) e não está em `data/llm_prices.json`. O Cognalys rodou em standard; os agentes, pela assinatura.
- **Memória.** Nas 3 findings de PR fechado, o Cognalys acerta pela resposta do mantenedor, não por julgamento (`RELATORIO.md`, ponto 2).

### 2.2 Ruído do Cognalys (base do tamanho das réplicas)

Todas as execuções gravadas das 9 findings do benchmark que chegam ao modelo, sem as `*_overloaded` (M, `c02_bench_runs.py`):

| Conjunto | Execuções | Pares da mesma finding | Desfecho exato diferente | Classe de entrega diferente (PR / issue / nenhuma ação / falha) | PR ou não PR diferente |
|---|---|---|---|---|---|
| Todas (run a run_v8, iso1 a iso6) | 94 | 451 | 229 (51%) | 225 (50%) | 106 (24%) |
| Isoladas (run_iso1 a iso6) | 38 | 63 | 24 (38%) | 24 (38%) | 7 (11%) |
| Isoladas, parte A do VFIX (iso1 a iso4) | 20 | 14 | 4 | 4 | 1 |
| Isoladas, parte B (iso5, iso6; resposta do mantenedor inteira) | 18 | 9 | 5 | 5 | 2 |

O `noise_floor.md` deu 44 de 90 pares (49%) para as 5 execuções de então; a medida de cima o reproduz em escala maior. Nas isoladas a discordância cai para 38% no desfecho exato e 11% em "PR ou não PR". As partes A e B não são o mesmo pipeline (o commit 5 do VFIX entra só na B), então os pares entre elas misturam ruído e mudança.

Desfechos das execuções isoladas por finding (M, `c02`):

| Finding | Isoladas |
|---|---|
| amethyst_gcm | sem impacto 4 |
| cometotp_fp | PR pronto 6 |
| herald_tls | issue 3, sem impacto 1 |
| ibiswallet_tofu | issue 2, sem impacto 2 |
| inputleaf_discovery | PR pronto 2, issue 2 |
| intertune_hash | issue 4 |
| melox_aes | issue 3, sem impacto 1 |
| redreader_sha1 | gerada sem PR 2, PR pronto 1, sem impacto 1 |
| winterhold_salt | sem impacto 2, não gerada 1, issue 1 |

**Custo por execução isolada** (M, `c02`, n = 38): cobrado 0,199 (mediana 0,161, p90 0,285, máximo 0,481); tudo a Flex 0,193; tudo a preço de lista standard, escrita a 1,25x e taxa de busca 0,358. Por classe de entrega, cobrado: nenhuma ação 0,170 (11), PR 0,198 (9), issue 0,204 (15), falha 0,282 (3).

### 2.3 Agentes em 2026-11-08 (uma execução cada)

M, `c03_agents_20261108.py`:

| | Terra | Sol | Sonnet |
|---|---|---|---|
| Concordância com a referência, 12 / as 9 rerodadas | 8 / 5 | 9 / 6 | 10 / 7 |
| PRs propostos / compilaram | 8 / 8 | 7 / 7 | 6 / 6 |
| Textos barrados pelo checador do Cognalys | 3 | 7 | 6 |
| Entrada média (lida do cache) | 513.774 (92%) | 230.231 (88%) | 153.131 (86%) |
| Saída média | 5.205 | 3.008 | 2.695 |
| Custo calculado então, média | 0,0909 | 0,1077 | 0,0953 (CLI: 0,1239) |
| Segundos, mediana | 45,5 | 48 | 23,5 |

A saída dos agentes é de 2,7 a 5,2 mil tokens por finding; a do Cognalys é de cerca de 30 mil (PLANO_SUPERAR_SONNET §1).

### 2.4 A coorte da run congelada e as unidades da comparação

M, `c01_cohort.py` (PRs com o estado gravado em `artigo_tcc_latex/amostra/inventario.json`, retrato de 2026-11-08, e `findings.json` do benchmark; o encerramento antes da análise segue `finding_processing.settled_before_analysis`, `src/cognalys/llm/finding_processing.py:264-322`):

| Estrato | Findings | O que o Cognalys faz na run congelada |
|---|---|---|
| Sem PR nosso para a finding (`no_pr`) | 25 (19 consolidadas, 6 do benchmark) | Analisa, sem resposta de mantenedor sobre esse código |
| PR nosso fechado pelo mantenedor (`our_pr_closed`) | 7 (4 consolidadas, 2 do benchmark, 1 nos dois) | Analisa, com a resposta do mantenedor na memória (Q4 parte 1) |
| PR nosso incorporado (`our_pr_merged`) | 8 (5 consolidadas, 3 do benchmark) | Encerra antes do modelo: "correção não aplicável: código mudou upstream" |
| PR nosso aberto (`our_pr_open`) | 5 (consolidadas) | Encerra antes do modelo: "PR já aberto" |

- 45 findings distintas (o RedReader `sha1` está nos dois conjuntos).
- Os dois estratos analisados somam 32 findings em 28 unidades e 24 APKs; 4 unidades têm duas findings (MTGFam `fetch`, treehouses `encrypt`, cry.otp `hmac_sha`, RedReader `parseConfig`) (M, `c05_units.py`). A junção aqui é aproximada por APK, classe e método; o kit lê a junção real da run congelada (`findings_index.json`).
- Especificações nas 32: MessageDigest 13, SecretKeySpec 6, IvParameterSpec 3, SSLContext 3, GCMParameterSpec 2, e uma cada de SecretKeyFactory, PBEKeySpec, KeyStore, Mac e AlgorithmParameters (M).
- Último desfecho gravado das 25 `no_pr`, de runs de épocas diferentes do pipeline: issue 11, falso positivo 4, PR pronto 4, confirmada sem correção segura 3, evidência insuficiente 2, gerada sem PR 1 (M). Não é referência; mostra que a coorte cobre todos os baldes.

**Por que não as 12 do benchmark sozinhas.** Delas, 3 são encerradas antes do modelo (os PRs incorporados), e sobram 9 unidades. Com 9 a 12 unidades e 4 réplicas, uma diferença de 15 pontos é detectada em 0,49 a 0,58 das vezes e uma de 10 pontos em 0,21 a 0,32 (M, `c04`, n = 12). A coorte inteira dobra o poder e é o conjunto que a run congelada já roda.

**Por que não as 45.** Nas 13 encerradas o Cognalys não produz nada a comparar sem mudar `src/` (§9, pergunta 2).

**Memória nas unidades analisadas.** As 7 de PR fechado (Network Survey `getOrCreateKey`, BetterUntis `getCacheFile`, Amethyst `decrypt`, WiFiAnalyzer `calculateChildType`, InterTune `hash`, Giggity `hashCode`, RedReader `sha1`) dão ao Cognalys a resposta do mantenedor sobre o mesmo código. Elas formam um estrato à parte (§3.9).

### 2.5 Poder (como o número de réplicas foi escolhido)

`c04_power.py` sorteia n unidades de um conjunto de probabilidades por unidade tirado das execuções isoladas, dá k réplicas a cada braço e uma vantagem média Δ ao agente, espalhada por todas as unidades ou concentrada nas mais fracas do Cognalys. O veredito é o bootstrap pareado por unidade (IC de 95% da diferença média acima de 0). Dois conjuntos de probabilidades:

- **ref:** fração de execuções isoladas que concordam com a referência em "PR ou não PR" (média 0,94). Subestima o ruído, porque quase todas as unidades ficam em 1.
- **modal:** fração do desfecho exato mais comum nas execuções isoladas (média 0,79; média de p(1−p) 0,115). É o mais próximo da medida primária, que exige a classe certa e um patch bom.

Poder no conjunto modal (M, `c04_power.out`; alarme falso na coluna Δ = 0):

| n | k | Δ = 0 | Δ = 0,10 (espalhada / concentrada) | Δ = 0,15 | Δ = 0,20 |
|---|---|---|---|---|---|
| 12 | 4 | 0,03 | 0,32 / 0,21 | 0,58 / 0,49 | 0,77 / 0,74 |
| 24 | 4 | 0,06 | 0,61 / 0,37 | 0,89 / 0,79 | 1,00 / 0,97 |
| 30 | 3 | 0,05 | 0,59 / 0,49 | 0,91 / 0,81 | 1,00 / 0,99 |
| 30 | 4 | 0,04 | 0,66 / 0,52 | 0,95 / 0,92 | 0,99 / 1,00 |
| 30 | 5 | 0,03 | 0,79 / 0,63 | 0,99 / 0,94 | 1,00 / 1,00 |

Com 28 unidades, os valores ficam entre as linhas de 24 e 30.

**Chamada de perda por unidade** (agente aceitável em pelo menos k−1 de k, Cognalys em no máximo 1 de k; M, `c04`):

| k | Verdade (1,0; 0,0) | (1,0; 0,25) | (0,9; 0,5) | (0,75; 0,5) | Alarme falso (0,5; 0,5) | (0,75; 0,75) |
|---|---|---|---|---|---|---|
| 3 | 1,00 | 0,84 | 0,49 | 0,42 | 0,24 | 0,14 |
| 4 | 1,00 | 0,74 | 0,29 | 0,23 | 0,10 | 0,04 |
| 5 | 1,00 | 0,63 | 0,17 | 0,12 | 0,04 | 0,01 |
| 6 | 1,00 | 0,53 | 0,10 | 0,06 | 0,01 | 0,00 |

**Decisão:** k = 4 em todos os braços e unidades. Com k = 4, uma perda clara (agente sempre certo, Cognalys sempre errado) nunca escapa, e o alarme falso numa unidade que oscila meio a meio é de 0,10. Por isso a chamada com k = 4 só marca a perda como candidata; 2 réplicas a mais em todos os braços daquela unidade, com o teste exato em k = 6, levam o alarme falso a cerca de 0,01. Réplicas extras em toda unidade (k = 6 em tudo) custariam cerca de US$ 11 a mais no braço C e não mudariam a detecção das perdas claras.

**Limites da simulação.** O ruído dos agentes não foi medido; a simulação supõe que ele é do tamanho do ruído do Cognalys. A primeira rodada de réplicas mede esse ruído, e o relatório refaz a tabela com ele.

## 3. Desenho

### 3.1 Conjunto, ordem e relação com a run congelada

1. Antes da run congelada, `cognalys pr-outcomes` é atualizado uma vez (CLAUDE.md) e o hash de `data/pr_outcomes/` é anotado. A partir daí, nada atualiza esse diretório até o fim da comparação.
2. A run congelada roda a coorte inteira (45 findings) como especificado em PLANO_IMPLEMENTACAO §4 e nas decisões do VFIX e do K3.
3. `select_units.py` lê a run congelada e define as unidades da comparação: toda unidade de `findings_index.json` que não terminou em `settled_before_analysis` (registros em `error_solutions/settled_before_analysis/`) nem em "não analisado". Esperado: 28 unidades (M para o retrato de 2026-11-08; a contagem real sai da run congelada, e uma diferença é relatada, não corrigida).
4. O corpus de pesquisa depois da run congelada é copiado para `corpus_S1/` (com `cognalys research-corpus list --json > corpus_S1.json`). É o estado que toda execução do braço C recebe.
5. O desfecho da run congelada em cada unidade aparece no relatório como "r0", ao lado das réplicas, sem entrar na estatística: ela roda várias findings numa run só e preenche o corpus, então não é uma réplica igual às outras.

### 3.2 Braço C (Cognalys)

- **Commit:** o da run congelada. `prepare.py` confere que `run_inputs.json.library.src_tree_sha256` de cada execução é o mesmo da run congelada; diferente, a execução não conta.
- **Unidade:** um `findings.jsonl` por unidade, com as linhas originais de todas as findings dela (como `prepare_cognalys.py`), para que a junção se forme igual.
- **TOML:** cópia do TOML da run congelada, com estes campos forçados:

```toml
findings = ["/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparacao_YYYYMMDD/units/<unidade>/findings.jsonl"]
run-dir = "/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparacao_YYYYMMDD/units/<unidade>/C_r<r>"
llm-model = "gpt-6.1-terra"
spec-model = "gpt-6.1-terra"
llm-reasoning-effort = "high"
llm-max-output-tokens = 32768
llm-service-tier = "flex"
max-cost = 3
no-llm-cache = true
llm-web-search-cache-ttl-hours = 0
learning-dir = "/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparacao_YYYYMMDD/units/<unidade>/C_r<r>/learning"
llm-research-corpus-dir = "/home/antonow/comparacao_YYYYMMDD/<unidade>/C_r<r>/corpus"
cloned-projects-dir = "/home/antonow/comparacao_YYYYMMDD/<unidade>/C_r<r>/clones"
worktrees-dir = "/home/antonow/comparacao_YYYYMMDD/<unidade>/C_r<r>/worktrees"
validate-build = true
approve-review-only-for-validation = true
generate-previews = true
online-dependency-resolution = true
build-timeout = 3600
```

  `max-cost = 3` cobre o máximo medido por execução isolada (0,481) com folga. Os nomes das opções do K3 e do VFIX são os que eles implementarem; o kit lê o TOML congelado e só troca os caminhos.
- **Antes de cada execução:** `learning` recebe `seeded_store(DEFAULT_LEARNING_DIR, <dir>)` (só imports verificados, como em `outputs/vfix_validation_20261110/run_isolated.sh`); `corpus` recebe uma cópia de `corpus_S1/`; clones e worktrees são apagados (limpeza só local).
- **Sem `--experiment-manifest`**, como a run congelada (decisão do K3: com o manifesto, todo miss de pesquisa volta vazio).
- **Depois de cada execução:** o kit apaga a cópia do corpus. Nenhuma escrita dela volta a `corpus_S1/`.
- **Cobrança:** pela chave em `.env.openai`, nunca impressa.

### 3.3 Braços S, O e T (agentes)

**Checkout por execução.** `git clone --shared --no-checkout` a partir do repositório preparado da unidade (como `prepare.py`), `checkout --detach <commit analisado>`, `git remote remove origin`. Um checkout novo por execução, em `/home/antonow/comparacao_YYYYMMDD/<unidade>/<braço>_r<r>/repo`.

**Ambiente por execução.**
- `HOME` próprio, com só o necessário para o CLI autenticar: cópia das credenciais da assinatura em `CLAUDE_CONFIG_DIR` (S) ou `CODEX_HOME` (O, T) novos.
- Sem `CLAUDE.md`, memória ou configuração de usuário (`--ignore-user-config` no Codex; diretório de configuração vazio no Claude Code).
- `GH_TOKEN`, `GITHUB_TOKEN`, `OPENAI_API_KEY` e `ANTHROPIC_API_KEY` removidos do ambiente. Sem configuração do `gh` no HOME.
- `GRADLE_USER_HOME` aponta para o `~/.gradle` comum: é cache de dependências, não resposta de ninguém.
- Os arquivos do próprio repositório-alvo (um `AGENTS.md` ou `CLAUDE.md` do projeto) ficam, porque são conteúdo do projeto.

**Ferramentas.** Leitura e edição do checkout, shell de leitura, busca web, e um comando de build:

```
/home/antonow/comparacao_YYYYMMDD/bin/build_check
```

Ele roda `cognalys.builds.apk_build` no checkout atual com a task que a validação do Cognalys usa naquele projeto (a mesma de `build_agents.py`; `assembleDebug` salvo exceção gravada) e imprime o status e as últimas 80 linhas do log. É a mesma ferramenta de build do braço C.

**Comando do braço S** (como `run_claude_agent.py`, com as diferenças marcadas):

```
claude -p "<PROMPT>" --model claude-sonnet-5-5 --effort high --output-format json \
  --permission-mode acceptEdits \
  --allowedTools "Read,Edit,Write,Glob,Grep,WebSearch,WebFetch,<READ_ONLY_SHELL>,Bash(/home/antonow/comparacao_YYYYMMDD/bin/build_check:*)"
```

`WebSearch,WebFetch` passam a ser permitidos, e entra o comando de build.

**Comando dos braços O e T:**

```
codex exec --json --skip-git-repo-check --ephemeral --ignore-user-config \
  -m <gpt-6.1-sol | gpt-6.1-terra> -c model_reasoning_effort="high" -s workspace-write \
  -c sandbox_workspace_write.network_access=true -c <chave da busca web> \
  -C <repo> -o <dir>/last_message.txt "<PROMPT>"
```

As chaves de rede, de busca web e de tier do Codex 0.167 não foram verificadas nesta especificação (`codex exec --help` não as lista). O passo K0 as confirma com `--strict-config` num perfil de teste, sem chamada ao modelo.

**Prompt (texto exato, em inglês, igual para os três agentes):**

```
You are contributing to this open-source Android app as an outside contributor. RVSec, a runtime checker for how Android apps use the Java cryptography and TLS APIs, ran the APK built from the current commit and reported:

{for each finding of the unit:}
- Rule: {spec}
  Class: {class}
  Method: {method}
  Message: {message}

Decide what, if anything, the maintainers should receive, and write exactly one of these files at the repository root:
- PR.md, when a code change should be proposed. Edit the source files directly (do not commit). Put the pull request's title on the first line as '# <title>', followed by the description you would submit.
- ISSUE.md, when the maintainers should be told but you should not change the code. Put the issue's title on the first line as '# <title>', followed by the issue text.
- NO_ACTION.md, when nothing should be sent to the maintainers. Explain why.

Write for the maintainers: short plain paragraphs that describe only what your change does and why. Do not use the names of the checker's rules or of compiled classes. If the repository has a pull request template, fill it in and tick only what is true. If it asks about AI use, answer that the contribution is AI-assisted. Say that the project builds only if you ran the build and it passed.

To build the app, run {build_check}. It prints the result and the end of the build log. You may search the web. Do not commit, push, open pull requests or issues, or contact anyone.
```

As regras de texto são as que o Cognalys aplica ao próprio texto (CLAUDE.md, "Publishing"). Os agentes não recebem a memória do Cognalys (respostas de mantenedor de outros PRs): ela é a vantagem que o estrato da §3.9 mede.

**Saída gravada por execução** (como `run_agent.py`): eventos JSON ou `output.json`, `PR.md`/`ISSUE.md`/`NO_ACTION.md`, `patch.diff` (sem esses três arquivos), lista de não rastreados, segundos, uso de tokens, buscas web (contadas nos eventos) e status. Mais de um dos três arquivos, ou nenhum, é "entrega inválida" (classe falha). Tempo limite: 60 min.

**Leitura do repositório upstream pela web.** Um agente com web pode achar o PR fechado ou o commit que corrigiu a finding. `run_agents.py` grava toda URL buscada ou aberta; uma execução que abriu `github.com/<dono>/<repo>/pull`, `/issues` ou `/commit` do projeto-alvo fica marcada "consultou o upstream". O relatório dá os resultados com e sem essas execuções.

### 3.4 Isolamento, conferido

| O que poderia vazar | Como é barrado | Como é conferido (`isolation_check.py`, depois do piloto e no fim) |
|---|---|---|
| Tentativas anteriores da finding (C) | `learning-dir` semeado por execução | Nenhum pedido de planejamento ou geração leva `previous_attempts_of_this_finding`; sha256 de `data/pipeline_learning/verified_knowledge.json` igual antes e depois |
| Respostas de LLM em cache (C) | `no-llm-cache` | 0 `cache_hit` nos traces fora do corpus |
| Pesquisa de outra réplica (C) | Corpus copiado de `corpus_S1/` por execução, cópia apagada depois | Os hits do corpus são todos de chaves que existem em `corpus_S1.json`; `corpus_S1/` igual byte a byte no fim |
| Respostas de mantenedor novas (C) | `pr-outcomes` não roda | Hash de `data/pr_outcomes/` igual ao anotado |
| Arquivos escritos fora do lugar (todos) | — | `find /mnt/c/Users/Antonow/Documents/tcc_v3/data /mnt/c/Users/Antonow/Documents/tcc_v3/outputs -newer <marca> -type f` fora dos diretórios da comparação: lista vazia |
| Memória ou configuração do agente | HOME, configuração e checkout novos | Diretório de configuração de cada execução sem `projects/`, memória ou histórico de outra |
| Publicação | Sem remote, sem token | Nenhuma variável de credencial no ambiente; `git remote -v` vazio |

O cache de prompt dos provedores (prefixo repetido entre réplicas) não devolve respostas, só desconta tokens de entrada. Para que ele não favoreça um braço, a ordem é intercalada (§3.10), e o custo sai também sem desconto de cache (§3.5).

### 3.5 Custo e tier

| Coluna | C | S | O e T |
|---|---|---|---|
| (1) Lista standard, mesma base para todos (**primária**) | Tokens dos traces a 1,00 / 0,05 / 1,25 (escrita) / 5,00 e US$ 0,01 por busca | Tokens do `usage` a 2,00 / 0,20 (leitura) / 2,50 (escrita, 1,25x) / 10,00 e US$ 0,01 por busca | Terra: os preços de lista do terra. Sol: os de lista verificados no K0. Escrita não aparece no uso do Codex: dada como faixa, a preço de entrada e a 1,25x |
| (2) Tier mais barato que o provedor oferece para esse uso | Como servido (Flex, com as quedas para standard que o trace marca) | Igual a (1): a Anthropic não tem Flex para o agente | Tokens a preço Flex (Terra: 0,50 / 0,025 / 0,625 / 2,50; Sol: verificado no K0, ou fica sem a coluna) |
| (3) Como cobrado | `cognalys cost` | 0 (assinatura); `total_cost_usd` do CLI gravado | 0 (assinatura) |
| Corpus pronto | Coluna (1) e (2) também "a frio": somam o `cost_usd` de proveniência de cada entrada servida (K3) | — | — |
| Sem desconto de cache | Todos os tokens de entrada a preço cheio, para os quatro braços | idem | idem |

- O Flex não muda o modelo nem a amostragem, só a prioridade. Por isso o custo de outro tier sai dos mesmos tokens sem rodar de novo.
- O artigo declara o Flex e o corpus pronto sempre que comparar (decisão 4 do PLANO_SUPERAR_SONNET).
- Os preços do `gpt-6.1-sol` e da escrita no cache do Sonnet entram num arquivo de preços do kit (`prices.json`), com a data e a fonte da conferência do K0. `data/llm_prices.json` não muda.
- Por unidade: média das réplicas. Por braço: média das unidades. Também: custo por execução aceitável, e custo por classe de entrega. Razão de custo entre braços com IC de 95% por bootstrap de unidades.

### 3.6 Rubrica

**Classe da entrega (mecânica, não julgada).**

| Braço | PR | ISSUE | NENHUMA AÇÃO | FALHA |
|---|---|---|---|---|
| C, leitura "todo rascunho pronto" | PR pronto (publicável ou só para revisão) | issue para o mantenedor, issue para a biblioteca | falso positivo, sem impacto, confirmada sem correção segura, contrato externo, evidência insuficiente | correção não gerada, gerada sem PR, não analisado, erro |
| C, leitura "o que publicaria" | Só PR pronto publicável | igual | igual | igual, mais o PR pronto só para revisão (retido) |
| S, O, T | `PR.md` com diff não vazio | `ISSUE.md` | `NO_ACTION.md` | nenhum arquivo, mais de um, `PR.md` com diff vazio, tempo esgotado |

**Gabarito por unidade (fase 1).** Conjunto das classes aceitáveis (uma ou mais de PR, ISSUE, NENHUMA AÇÃO), a razão, e as restrições que um patch aceitável tem de cumprir (o que tem de mudar, o que não pode quebrar). Nas 7 unidades de PR fechado, o gabarito parte da resposta do mantenedor.

**Notas por cartão (fase 2), texto exato dado aos juízes:**

```
R2 PATCH (only when the deliverable is a pull request), 0-3:
 3 = a maintainer could merge it as is.
 2 = mergeable after small edits a reviewer would ask for (naming, a test, a comment), without changing the approach.
 1 = the right idea, but it needs substantial rework (wrong place, a missing compatibility path, a partial fix).
 0 = it should be rejected: a wrong fix; a regression reachable through the app's own code; it breaks stored data, a protocol or a supported setup; it changes unrelated behaviour; or it leaves what the finding reports unchanged.
 Give the regression scenario when you give 0 or 1 for a regression.
R4 TEXT (pull request or issue), 0-2:
 2 = accurate and complete: it describes exactly the change and the reason, with no false claim.
 1 = accurate, with minor problems (verbose, internal jargon, a missing caveat).
 0 = it contains a false claim (an edit that is not in the diff, testing or building that did not happen, a wrong security claim) or misleads.
R5 REASON (issue or no action), 0-2:
 2 = the reason is right and specific to this code.
 1 = the reason is right but vague.
 0 = the reason is wrong, missing, or is a tool's failure rather than a judgement about the code.
```

**Build (mecânico).** `build_harness.py` compila todo diff de PR de todos os braços (no braço C, o patch aplicado no commit analisado, o mesmo que a validação compilou) com `cognalys.builds.apk_build` e a task da unidade. Quando falha, compila o commit sem patch uma vez: falha que o projeto já tinha não é cobrada do patch (como `build_agents.py`).

**Execução aceitável (A = 1)**, definida antes de rodar:
- a classe está no gabarito; e
- PR: R2 ≥ 2, o build passa ou falha também sem o patch, e R4 ≥ 1;
- ISSUE: R5 ≥ 1 e R4 ≥ 1;
- NENHUMA AÇÃO: R5 ≥ 1;
- FALHA: nunca.

**Medidas secundárias:** classe certa (R1); média de R2 entre os PRs; build; R4; "PR ruim" (PR com R2 = 0, o que o mantenedor fecharia); "dispensa indevida" (NENHUMA AÇÃO onde o gabarito não a admite, contada à parte por causa da barreira contra falso positivo do CLAUDE.md); "issue sem ação" (ISSUE onde o gabarito não a admite).

**O patch do braço C no commit analisado.** O Cognalys porta o patch para a ponta da branch antes do PR. O cartão usa o patch no commit analisado, para que todos sejam julgados sobre o mesmo código; o resultado do porte é relatado à parte.

### 3.7 Julgamento cego

**Cartões (`cards.py`).** Cada cartão tem a classe, o título, o corpo, o diff (formato unificado, 3 linhas de contexto, arquivos em ordem alfabética) e, para NENHUMA AÇÃO e FALHA, a razão gravada (no braço C, a frase do desfecho e o resumo da adjudicação).

Normalização para cegar:
- tiram-se assinaturas e rodapés ("Generated with", `Co-Authored-By`, linhas de declaração de IA);
- tiram-se os ids de evidência do Cognalys (`SRC-\d+`, `WEB-\d+`, `RUN-\d+`, `TEST-\d+` e os códigos de detector `[A-Z]+-[A-Z]+-\d+`) e os nomes de decisão em maiúsculas (`NO_SAFE_FIX`, `FIX_SUPPORTED`...);
- o nome do arquivo de origem (`PR.md`, pasta do braço) não aparece;
- o id do cartão é aleatório, e a ordem dos cartões de uma unidade é sorteada por juiz.

Um lint do kit falha se algum cartão contém um destes marcadores: `Cognalys`, `Claude`, `Codex`, `Sonnet`, `GPT`, `Terra`, `Sol`, `RVSec rule`, `adjudicat`, `behaviour review`, `evidence packet`, `run_`, `C_r`, `agent_`.

**Fase 1, gabarito.** Uma sessão por unidade e por juiz, num checkout só de leitura no commit analisado. O juiz recebe as linhas do RVSec, o código e, nas unidades de PR fechado, a resposta do mantenedor, e não vê cartão nenhum. Saída em JSON: `acceptable_classes`, `reason`, `must_change`, `must_not_break`. Os gabaritos dos dois juízes vão ao usuário, que fixa o gabarito final das 28 unidades. Isso é feito antes de qualquer cartão ser julgado.

**Fase 2, notas.** Uma sessão por unidade e por juiz, no mesmo checkout, com o gabarito final e todos os cartões da unidade (16, ou 24 com as réplicas extras) sorteados. Cada cartão é pontuado sozinho contra a rubrica. Saída em JSON por cartão: `card_id`, `r2`, `regression_scenario`, `fixes_what_is_reported` (yes/no/unclear), `r4`, `r5`, `notes`. O kit calcula A.

**Juízes.**
- Opus 5.5 pelo Claude Code (assinatura), principal.
- GPT-6.1 Sol pelo Codex (assinatura), segundo.
- Os dois em esforço high, sem web, com leitura do checkout.

Cada juiz é da família de um dos braços (Opus com o Sonnet; Sol é um braço). Por isso:
- o relatório dá as notas de cada juiz em separado;
- o viés de família é medido: a diferença entre as notas que um juiz dá à própria família e as que o outro juiz dá à mesma família.

Nota final de um cartão: a dos dois juízes, quando concordam em A. Quando discordam, o usuário decide os cartões das unidades com perda candidata; nas outras, vale a do juiz principal, e a discordância é relatada.

**Prova de cegueira.** Numa amostra de 40 cartões, uma sessão separada (Opus) recebe só o cartão e responde qual dos quatro braços o escreveu. Acerto bem acima de 25% quer dizer que o estilo denuncia o braço, e o relatório diz isso.

**Amostra do usuário.** 40 cartões, sorteados por estrato:
- 10 por braço;
- todos os cartões das unidades com perda candidata entram primeiro, até 20;
- o resto ao acaso.

O usuário julga às cegas, com o mesmo formulário (`user_form.md` exportado, `user_labels.csv` importado).

**Concordância:**
- kappa de Cohen em A e em R1 entre os dois juízes (todos os cartões), e entre o usuário e a nota final (os 40);
- kappa ponderado quadrático em R2;
- concordância dos gabaritos dos dois juízes antes da revisão do usuário.

| kappa(usuário, nota final) em A | O que vale |
|---|---|
| ≥ 0,6 | As notas dos modelos valem em todo o relatório |
| 0,4 a 0,6 | Valem, mas todos os cartões das unidades com perda candidata são julgados pelo usuário |
| < 0,4 | O veredito sai só das notas do usuário nas unidades com perda candidata, e o relatório diz que o julgamento por modelo não se sustentou |

### 3.8 Duas leituras do Cognalys

- **"O que publicaria":** só o PR pronto publicável conta como PR; um rascunho retido (só para revisão) conta como FALHA. É o que um mantenedor receberia, já que nada sai sem aprovação.
- **"Todo rascunho pronto":** todo PR pronto conta como PR. Mostra a qualidade da geração sem o portão de publicação.
- Os agentes têm uma leitura só: toda entrega deles seria enviada.
- Os rascunhos retidos são julgados nas duas leituras (o cartão existe). A diferença entre as leituras mostra quanto o portão de publicação tira de bom e de ruim.

### 3.9 Estatística e veredito

- `X[a][u]` = fração de execuções aceitáveis do braço a na unidade u.
- **Primária:** a média de X por braço sobre as 28 unidades, nas duas leituras do Cognalys.
- **Diferença Cognalys − agente:** bootstrap pareado por unidade (10.000 reamostras), IC de 95%, correção de Holm para os três agentes.
- **"Melhor agente":** o de maior média. O IC de "Cognalys − melhor" reescolhe o melhor dentro de cada reamostra, para não ignorar a seleção.
- **Estratos:** as 21 unidades `no_pr` (julgamento) e as 7 `our_pr_closed` (memória), em separado. Uma afirmação sobre julgamento sai só das 21.
- **Veredito de qualidade:** "perde" se o limite superior do IC de C − melhor < 0; "ganha" se o limite inferior > 0; senão "sem diferença detectada", com a largura do IC dita. Não se declara equivalência.
- **Custo:** relatado ao lado, nas colunas da §3.5, com a razão C / melhor agente e o custo por execução aceitável. Qualidade não se troca por custo: o custo é o segundo critério e não compensa uma perda.
- **Tempo:** segundos por execução em cada braço, gravados e relatados num apêndice, fora do veredito.

### 3.10 Perda por unidade e atribuição a uma etapa

**Chamada.**
1. Perda candidata: algum agente com A em ≥ 3 de 4 e o Cognalys em ≤ 1 de 4 (leitura "o que publicaria").
2. Réplicas extras: mais 2 execuções de cada braço nessa unidade.
3. Perda confirmada: teste exato de Fisher unilateral, p < 0,05, com k = 6 (por exemplo 5 de 6 contra 1 de 6).
4. Também se lista a "perda de qualidade": as duas entregas são aceitáveis, mas R2 do melhor agente é maior em ≥ 1 ponto na média.
5. O mesmo é feito no sentido inverso ("ganhos" do Cognalys).

**Atribuição** (`attribute.py`, depois revisada por uma leitura do Opus e pelo usuário). Para cada execução não aceitável do Cognalys numa perda confirmada, a primeira etapa em que ela saiu do caminho certo, nesta ordem:

| Etapa | Sinal nos artefatos da execução |
|---|---|
| Extração / evidência | O fato de código que o gabarito cita (arquivo ou método) não está em nenhuma fonte do pacote de evidência |
| Pesquisa | A decisão cita uma fonte WEB que o juiz marca como errada |
| Adjudicação | Decisão ou impacto contra o gabarito com o fato no pacote |
| Roteamento | Adjudicação compatível com o gabarito e balde final fora dele (regras de desfecho, promoção, Q1) |
| Memória | O prompt leva resposta de mantenedor de outro repositório e a decisão a cita |
| Plano | A revisão atribui a regressão ao plano, ou o juiz mostra que nenhum rascunho do plano cumpriria o gabarito |
| Rascunho (checagens) | "Correção não gerada" com códigos de checagem determinística sobre um rascunho que o juiz acha aceitável |
| Revisão de comportamento | A revisão barrou um rascunho que o juiz acha aceitável, ou passou um que o juiz nota 0 |
| Build / porte | Build falhou só com o patch, ou o porte para a ponta falhou |
| Texto | A checagem do texto segurou o PR, ou R4 = 0 |
| Qualidade da geração | Nenhuma etapa falhou, e o patch teve R2 menor que o do agente |

**Adjudicação: sistemática ou amostragem.** Quando a etapa é a adjudicação, `cognalys decision-replay` reenvia o pedido gravado 8 vezes (cerca de US$ 0,03 por amostra, PLANO_SUPERAR_SONNET item 1). Se a decisão errada sai em ≥ 7 de 8, ela é sistemática; se troca em ≥ 2 de 8, é amostragem.

**Classe de cada perda confirmada:**

| Classe | Critério | Leitura |
|---|---|---|
| (a) Defeito corrigível | Etapa de checagem determinística, roteamento, texto, porte ou memória | Há o que melhorar no pipeline. O relatório diz quantas unidades a correção recuperaria |
| (b) Amostragem do julgamento | Adjudicação ou revisão que troca no replay | Só se reduz com mais amostras (votação), o que custa. O relatório mede o custo com os replays gravados |
| (c) Falta de informação | Extração / evidência, ou o agente leu um arquivo que o pacote não tinha (as URLs e os arquivos lidos ficam nos eventos do agente) | Há o que melhorar na extração |
| (d) Capacidade do modelo | Adjudicação sistemática com o fato no pacote, o braço T também erra e S ou O acertam | O teto é do modelo, não do pipeline. A alavanca seria trocar o modelo |
| (e) Referência discutível | O usuário muda o gabarito ao ler a perda | Sai da contagem, e o relatório diz isso |

**"Teto atingido"** só se nenhuma perda confirmada for (a) ou (c), nas duas leituras. Senão o relatório lista as correções, cada uma com as unidades que recuperaria, e cada uma vira candidata a especificação própria, com o diagnóstico de sempre.

### 3.11 Kit (a escrever em `outputs/comparacao_YYYYMMDD/`, fora do git)

| Arquivo | Faz |
|---|---|
| `select_units.py` | Unidades a partir da run congelada (§3.1); `units.json` |
| `prepare.py` | Repositório por unidade no commit analisado; `findings.jsonl` e TOMLs do braço C; prompts dos agentes; `bin/build_check`; `prices.json` |
| `run_cognalys.sh` | Uma execução do braço C (semente, cópia do corpus, run, `cognalys cost --json`, limpeza local) |
| `run_codex.py`, `run_claude.py` | Uma execução de agente (ambiente novo, checkout novo, captura, URLs, uso) |
| `driver.sh` | Ordem intercalada: rodada r de todas as unidades antes da r+1; em cada unidade, braços em ordem sorteada; uma pista só; retomável por `.done_<braço>_r<r>`; para com menos de 12 GB livres em C:; para se o cobrado do braço C passar de US$ 40 |
| `build_harness.py` | Build de todo diff de PR (§3.6) |
| `cards.py` | Cartões, normalização e lint |
| `judge.py` | Fase 1, fase 2 e prova de cegueira, pelos CLIs, com JSON validado |
| `user_sample.py` | Formulário do usuário e importação |
| `tabulate.py` | Medidas, bootstrap, custo, chamadas de perda |
| `attribute.py` | Pré-rótulos de etapa, replays, tabela de perdas |
| `isolation_check.py` | §3.4 |
| `RELATORIO.md` | Saída final |

O kit é Python no WSL com o venv, chamado do PowerShell por `wsl.exe bash -lc "..."` a partir de arquivos de script. Os scripts de fila vão para `/tmp` só para rodar, como manda o CLAUDE.md.

## 4. Testes do kit

Não há mudança em `src/` nem em `tests/`. O kit traz `outputs/comparacao_YYYYMMDD/test_kit.py`, rodado com `python -m pytest -q test_kit.py`:

| # | Teste | Afirma |
|---|---|---|
| 1 | `test_unit_selection_skips_settled` | Numa run de exemplo, uma unidade com registro em `settled_before_analysis` não entra; uma com só uma das findings coberta entra |
| 2 | `test_class_mapping_covers_every_outcome` | Todo rótulo de `models.py` e de `finding_statistics.py` (com os do Q1) tem classe nas duas leituras; o retido vira FALHA só em "o que publicaria" |
| 3 | `test_cards_carry_no_arm_marker` | Cartões feitos de `agent*/` de 2026-11-08 e de `run_iso1` passam no lint; um cartão com "Generated with Claude Code" ou `SRC-004` falha |
| 4 | `test_card_order_and_ids_are_random_per_judge` | Dois juízes recebem ordens diferentes; o id não deriva do braço |
| 5 | `test_cost_reproduces_20261108` | O custo do kit para os agentes de 2026-11-08 dá 0,0909 (Terra) e 0,1077 (Sol) com a escrita a preço de entrada, e fica a 2% do `total_cost_usd` do Claude Code no Sonnet com a escrita a 1,25x |
| 6 | `test_cost_reproduces_cognalys_cost` | Coluna (3) do braço C = `cognalys cost --json` das execuções `run_iso*` |
| 7 | `test_acceptable_definition` | Tabela de casos de A (§3.6), incluindo build que falha sem o patch e FALHA |
| 8 | `test_loss_call_and_fisher` | 3/4 contra 1/4 é candidata; 5/6 contra 1/6 é confirmada; 4/6 contra 2/6 não é |
| 9 | `test_bootstrap_reselects_best` | Com três agentes iguais e o Cognalys igual, o IC de C − melhor contém 0 em ≥ 93% de 200 conjuntos simulados |
| 10 | `test_agent_env_has_no_credentials` | O ambiente montado para um agente não tem `GH_TOKEN`, `GITHUB_TOKEN`, `OPENAI_API_KEY` nem `ANTHROPIC_API_KEY`, e o checkout não tem remote |
| 11 | `test_upstream_lookup_flag` | Eventos com uma URL `github.com/<dono>/<repo>/pull/…` do alvo marcam a execução |

## 5. Checagem offline de aceitação

**Já rodado, sem o kit** (M, `C\`):

| Checagem | Resultado | Script |
|---|---|---|
| Coorte e estratos | 45 findings; 25 sem PR, 7 de PR fechado, 8 incorporado, 5 aberto | `c01_cohort.py` |
| Unidades da comparação | 28 unidades, 32 findings, 24 APKs, 4 unidades de duas findings | `c05_units.py` |
| Ruído do Cognalys | Isoladas: 24 de 63 pares com desfecho diferente, 7 em PR ou não PR | `c02_bench_runs.py` |
| Custo por execução isolada | 0,199 cobrado; 0,358 a lista standard | idem |
| Agentes de 2026-11-08 | Tabela da §2.3 | `c03_agents_20261108.py` |
| Poder | Tabelas da §2.5 | `c04_power.py` |

**A rodar com o kit, antes de gastar** (WSL, sem custo):
1. `test_kit.py` inteiro.
2. **Ensaio com os dados de 2026-11-08 e do VFIX.** Cartões das 9 findings rerodadas, com os agentes de 2026-11-08 e `run_iso1`/`run_iso2` no lugar das réplicas. Lint limpo, 4 braços × 2 a 3 cartões por unidade, `tabulate.py` e `attribute.py` rodam até o fim com notas simuladas.
3. **Atribuição sobre casos conhecidos.** Os pré-rótulos de `attribute.py` nas execuções isoladas batem com o que já foi lido:
   - RedReader `run_iso1` e `run_iso2` (gerada sem PR) em "build / porte" ou "texto", conforme `statistics/findings.csv`;
   - Winterhold `run_iso1` (não gerada) em "rascunho (checagens)";
   - Input Leaf `run_iso2` (issue) em "adjudicação" ou "revisão", conforme `behavior_review.json`.
   Um pré-rótulo diferente é lido e explicado antes do piloto.
4. **Findings movidas por balde:** 0 em todos os baldes, dispensa primeiro. A comparação não muda o pipeline: o braço C roda o commit congelado.

## 6. Execução paga

Roda depois da run congelada, no commit dela, com o checkout principal sem run em curso (`pgrep -af "cognalys run"` vazio) e o lock `outputs/.repository_usage.lock` livre. Uma pista só.

### K0. Conferências sem chamada ao modelo

1. As chaves do Codex 0.167 para rede no sandbox, busca web e tier, com `--strict-config` num perfil de teste.
2. O preço de lista e o preço Flex do `gpt-6.1-sol` na página de preços da OpenAI, e a escrita no cache do Sonnet na da Anthropic. Ficam em `prices.json` com data e URL.
3. O Claude Code e o Codex autenticam pelas assinaturas num HOME novo.
4. C: com pelo menos 12 GB livres.

### Piloto

4 unidades: uma de duas findings, uma de PR fechado, uma SSLContext e uma MessageDigest. 1 réplica de cada braço, mais as fases 1 e 2 dos dois juízes sobre elas. Para no primeiro portão que falhar.

- **P1, isolamento (C):** `isolation_check.py` limpo. Nenhum pedido com `previous_attempts_of_this_finding`. A parte shared servida pelo corpus e a parte do caso pesquisada ao vivo (traces `external_research`). `src_tree_sha256` igual ao da run congelada.
- **P2, agentes:** cada execução deixou exatamente um dos três arquivos. `build_check` rodou em pelo menos uma execução de cada agente. A busca web foi usada ou ficou disponível (ferramenta listada nos eventos). Nenhuma escrita fora do checkout, nenhuma credencial no ambiente.
- **P3, cartões:** lint limpo; o diff de cada PR aplica no commit analisado.
- **P4, juízes:** os JSON das fases 1 e 2 passam no schema para todos os cartões; os gabaritos das 4 unidades vão ao usuário.
- **P5, custo:** uso de tokens gravado em toda execução dos quatro braços; as colunas da §3.5 saem para todas.

### Rodada completa

- 28 unidades × 4 braços × 4 réplicas = 448 execuções, intercaladas (§3.11).
- Builds de todos os diffs de PR.
- Fase 1 nas 28 unidades, gabarito do usuário, fase 2.
- Chamadas de perda e réplicas extras (2 por braço) nas unidades candidatas, com a fase 2 refeita nessas unidades.
- `decision-replay` nas perdas atribuídas à adjudicação ou à revisão.
- Amostra do usuário, prova de cegueira, `tabulate.py`, `attribute.py`, `RELATORIO.md`.

### Custo estimado (E)

| Parte | Conta | US$ cobrado |
|---|---|---|
| Piloto, braço C | 4 × 0,20 | ≈ 0,8 |
| Braço C | 28 × 4 × 0,14 a 0,20. 0,199 é a média medida das isoladas (`c02`); 0,14 tira a pesquisa shared servida pelo corpus (0,0566 por finding em v4+v5, M em ESPEC_K3). As 4 unidades de duas findings contam 1,5 vez | ≈ 17 a 24 |
| Réplicas extras, braço C | até 8 unidades candidatas × 2 × 0,20 | ≤ 3,2 |
| `decision-replay` | até 10 perdas × 8 amostras × 0,03 | ≤ 2,4 |
| Agentes S, O, T | Assinaturas: 0 cobrado. Equivalente de lista: 3 × (448/4 + 48) × 0,09 a 0,25 (o custo de 2026-11-08 vezes 1 a 2,5 por web e build) | 0 (≈ 45 a 120 de lista) |
| Juízes | Assinaturas: 0 cobrado. Equivalente de lista: fase 1 com 56 sessões a cerca de 0,3, fase 2 com 56 a cerca de 0,5 a 0,9, mais a prova de cegueira | 0 (≈ 45 a 70 de lista) |
| **Total cobrado** | | **≈ 21 a 30, teto de US$ 40** |

- O teto vale para o cobrado na chave da OpenAI. O `driver.sh` para ao passar dele.
- Se uma assinatura atingir o limite de uso, o kit espera (o tempo não é critério). Nunca troca para a chave da API sem o sim do usuário (§9, pergunta 7). Pela API, agentes e juízes somariam o equivalente de lista acima.
- Tempo de máquina, só para planejar (E): braço C 112 a 160 execuções de 10 a 15 min com build; agentes 336 a 480 execuções de 2 a 10 min com build; builds do kit cerca de 10 h; juízes cerca de 10 h. Ao todo, 3 a 5 dias numa pista.

### Aceitação e "não usar o resultado"

O resultado entra no relatório e no artigo só se:
- P1 a P5 passaram;
- `isolation_check.py` final está limpo;
- toda execução do braço C tem o `src_tree_sha256` congelado;
- os gabaritos foram fixados pelo usuário antes da fase 2;
- o kappa do usuário está na tabela da §3.7 e a regra correspondente foi aplicada.

Uma execução que viola o isolamento é descartada e refeita na mesma unidade e réplica. Se mais de 5% das execuções forem descartadas, o relatório diz isso.

O que vai para o artigo: só números medidos aqui, com o Flex e o corpus pronto declarados, comparações descritas como descritivas por estrato, e nenhuma frase de "decide melhor" sem o IC que a sustente (CLAUDE.md, seção "Paper").

## 7. Riscos, limites conhecidos e o que não fazer

### Riscos

- **O estilo denuncia o braço.** O texto curto do Cognalys e o `PR.md` dos agentes são reconhecíveis. A prova de cegueira mede isso; a rubrica pontua conteúdo, e a nota de texto é a única em que o estilo pesa.
- **Viés de família dos juízes.** É medido (§3.7), e o usuário decide as discordâncias nas unidades que importam.
- **Gabarito circular.** O gabarito sai antes de qualquer cartão, e só o usuário o muda depois, na classe (e) da atribuição, com o motivo escrito.
- **Agente lendo o upstream pela web.** É marcado e relatado com e sem (§3.3). Nas 7 unidades de PR fechado, isso equivale à memória do Cognalys.
- **Limite das assinaturas.** 448 a 624 execuções de agente e cerca de 112 sessões de juiz podem levar dias. O kit é retomável.
- **Ruído dos agentes maior que o do Cognalys.** O poder cai. A primeira rodada mede o ruído, e o relatório refaz `c04` com ele.
- **Coorte enviesada para casos que já deram PR ou issue.** 7 unidades com resposta do mantenedor e 21 sem. Por isso há estratos, e a afirmação sobre julgamento sai só das 21.
- **Os PRs abertos de 2026-11-08 podem ter mudado de estado.** O `pr-outcomes` do passo 1 da §3.1 refaz os estratos, e a contagem real sai da run congelada.

### Limites conhecidos

- 28 unidades de 24 APKs, só do conjunto `jca_android`. Não diz nada sobre outras especificações nem sobre patches grandes (migração do BitBanana).
- Os agentes recebem a finding já mapeada para o código-fonte e o repositório no commit certo, preparados pelo projeto (`RELATORIO.md`, ponto 3). A comparação mede o que acontece depois do mapeamento.
- Não há validação dinâmica: ninguém roda o RVSec no APK com o patch (`docs/PLANO_VALIDACAO_DINAMICA.md`). "Resolve o que a finding relata" é o julgamento do juiz.
- As notas de patch são de modelo, com o usuário numa amostra de 40. Não são a decisão de um mantenedor.
- A simulação de poder supõe o ruído dos agentes igual ao do Cognalys.

### O que não fazer

- Não publicar nada: nenhum PR ou issue de nenhum braço. A publicação continua só pela biblioteca, com aprovação por finding.
- Não rodar `cognalys pr-outcomes` durante a comparação.
- Não deixar o corpus de uma execução voltar a `corpus_S1/`.
- Não usar a chave da API para agentes ou juízes sem o sim do usuário.
- Não comparar o Cognalys em Flex, ou com o corpus pronto, contra os agentes a preço de lista sem dizer.
- Não usar o tempo no veredito.
- Não editar `src/` durante a comparação (CLAUDE.md, "Runs").
- Não mudar o gabarito depois da fase 2, a não ser na classe (e), com o motivo escrito.
- Não juntar as notas dos dois juízes por média de R2 quando eles discordam em A.
- Não usar as execuções da comparação como as da run congelada, nem o contrário (r0 é só referência).

## 8. Passos de implementação

Todos os estágios com modelo usam o Opus 5.5.

| # | Passo | Estágio | Depende de |
|---|---|---|---|
| 1 | Respostas do usuário às perguntas da §9 | decisão | — |
| 2 | `cognalys pr-outcomes`; hash de `data/pr_outcomes/` anotado | preparação | 1; antes da run congelada |
| 3 | Run congelada (fora desta especificação) | run | VFIX, sobras, Q1, Q2Q3, K3 com merge |
| 4 | `select_units.py`, cópia de `corpus_S1/` | implementar | 3 |
| 5 | Kit: `prepare.py`, `run_*`, `driver.sh`, `build_harness.py`, `cards.py`, `judge.py`, `tabulate.py`, `attribute.py`, `isolation_check.py`, `test_kit.py` | implementar | 1 |
| 6 | `test_kit.py`; checagens offline 2 a 4 da §5 | checagem offline | 4, 5 |
| 7 | Revisão adversarial do kit: isolamento, mapeamento de classes, cegueira, custo, chamada de perda | revisão adversarial | 6 |
| 8 | K0 | preparação | 7 |
| 9 | Piloto e portões P1 a P5; gabaritos do piloto ao usuário | validação paga | 8 |
| 10 | Rodada completa dos quatro braços | validação paga | 9 |
| 11 | Fase 1 nas 28 unidades; gabarito fixado pelo usuário | julgamento | 10 (pode começar com 9) |
| 12 | Builds; fase 2; chamadas de perda | julgamento | 10, 11 |
| 13 | Réplicas extras nas candidatas; fase 2 refeita nelas | validação paga | 12 |
| 14 | `decision-replay` nas perdas de adjudicação e revisão | validação paga | 13 |
| 15 | Amostra do usuário; prova de cegueira; kappas | julgamento | 12 |
| 16 | `tabulate.py`, `attribute.py` com leitura do Opus, revisão das atribuições pelo usuário | relatório | 13 a 15 |
| 17 | `RELATORIO.md`; uma linha no `noise_floor.md` com o ruído dos quatro braços; seção no PLANO_FABLE | relatório | 16 |
| 18 | A1 (ESPEC_A1), que roda depois desta comparação | — | 17 |

Arquivos partilhados com outros itens:
- `select_units.py` lê os registros da run congelada; um formato novo de `findings_index.json` vindo do VFIX ou do Q1 entra aqui.
- O mapeamento de classes (§3.6) tem de incluir os rótulos novos do Q1 ("contrato externo impede a correção" e o registro `confirmed_without_safe_fix`).
- O kit lê as opções do K3 e do VFIX pelos nomes que as implementações tiverem.

## 9. Perguntas ao usuário

1. **Conjunto: as 28 unidades da coorte que o Cognalys analisa?**
   - São as findings da run congelada sem PR nosso aberto ou incorporado: 21 sem resposta de mantenedor e 7 de PR fechado (M, retrato de 2026-11-08).
   - As 12 do benchmark sozinhas dão 9 unidades e poder de 0,49 a 0,58 para uma diferença de 15 pontos, contra 0,92 a 0,95 com 28.
   - **Recomendo sim.**

2. **Deixar fora as 13 findings com PR nosso aberto ou incorporado?**
   - Para entrar, o pipeline precisaria de uma opção de avaliação que não as encerre e esconda a resposta do mantenedor sobre a própria finding. É mudança em `src/` depois do commit congelado.
   - Em 2026-11-08, os três agentes acertaram as 3 incorporadas do benchmark.
   - **Recomendo deixar fora**, e citar o resultado de 2026-11-08 como histórico.

3. **Dar busca web e o comando de build aos agentes?**
   - Em 2026-11-08 eles não tinham nenhum dos dois, e o Cognalys tem os dois.
   - A pergunta "o que perde para o melhor agente" pede o agente como um usuário o roda.
   - **Recomendo sim, os dois**, com a marca de "consultou o upstream".

4. **Pôr as regras de texto de publicação no prompt dos agentes, sem a memória?**
   - Sem elas, a nota de texto mede o prompt de 2026-11-08 (que levou ao nome interno da regra), não o agente.
   - A memória fica de fora, porque é o que o estrato de PR fechado mede.
   - **Recomendo sim.**

5. **4 réplicas por braço em toda unidade, mais 2 nas perdas candidatas?**
   - Com k = 4 uma perda clara nunca escapa, e o alarme falso numa unidade meio a meio é de 0,10; com k = 6 nas candidatas cai para cerca de 0,01 (M, `c04`).
   - k = 6 em tudo custaria cerca de US$ 11 a mais no braço C.
   - **Recomendo 4 + 2.**

6. **Juízes e amostra do usuário?**
   - Opus 5.5 principal e GPT-6.1 Sol segundo, pelas assinaturas.
   - O usuário revisa os 28 gabaritos antes da fase 2 (curtos, um por unidade) e julga às cegas 40 cartões. O kappa decide quanto vale a nota dos modelos (§3.7).
   - **Recomendo assim.** A alternativa de o usuário julgar tudo são cerca de 450 cartões.

7. **Agentes e juízes só pelas assinaturas?**
   - É a regra do PLANO_IMPLEMENTACAO §2.
   - O custo de lista é calculado dos tokens, e o tempo a mais por limite de uso não conta.
   - Pela API, seriam cerca de US$ 90 a 190 a mais (E).
   - **Recomendo só assinaturas.**

8. **Corpus do K3: cópia por execução do estado depois da run congelada, sem escrita de volta?**
   - É o "corpus pronto" que o artigo declara.
   - Nenhuma réplica lê a pesquisa de outra réplica; a parte shared fica igual em todas as réplicas do Cognalys, e isso é dito.
   - A pesquisa explicou 0 dos 44 pares divergentes (`noise_floor.md`).
   - A alternativa (corpus desligado) mede a variância da pesquisa, a cerca de US$ 0,06 a mais por execução.
   - **Recomendo a cópia.**

9. **Veredito primário sobre as 28 unidades, com o estrato de memória à parte, e afirmação sobre julgamento só das 21 sem resposta do mantenedor?**
   - Evita repetir o erro de 2026-11-08, em que a vantagem de memória foi lida como julgamento.
   - **Recomendo sim.**

10. **Registrar no PLANO_FABLE ("Refinamentos") que a comparação substitui a de 2026-11-08 e roda entre a run congelada e o A1, com o conjunto e o custo daqui?** O texto é da sessão principal. **Recomendo sim.**

## Apêndice: unidades da comparação (retrato de 2026-11-08, `c05_units.out`)

| APK | Classe | Método | Especificações | Estrato |
|---|---|---|---|---|
| bitbanana_79 | UtilFunctions | encodePbkdf2 | SecretKeyFactory | sem PR (o PBEKeySpec do mesmo método foi incorporado e fica fora) |
| winterhold.debug_8 | WebAccessUsers$Companion | derive | PBEKeySpec | sem PR |
| aegis_81 | CryptoUtils | createCipher | GCMParameterSpec | sem PR |
| networksurvey_114 | CryptoManager | getOrCreateKey | KeyStore | PR fechado |
| mtgfam_99 | MarketPriceFetcher$1 | fetch | IvParameterSpec, SecretKeySpec | sem PR |
| CometOTP.debug_1 | EncryptionHelper | loadOrGenerateWrappedKey | SecretKeySpec | sem PR |
| inputleaf.debug_15 | ServerScanner$Companion | discoverySslContext | SSLContext | sem PR |
| melox.debug_26 | NeteaseLibraryClient | aesEcbEncrypt | SecretKeySpec | sem PR |
| password.monitor_102 | AppModuleKt | appModule$lambda$0$3 | MessageDigest | sem PR |
| password.monitor_102 | HashUtils$Companion$… | invokeSuspend | MessageDigest | sem PR |
| untis_29198884 | DiskCache | getCacheFile | MessageDigest | PR fechado |
| openbible_46 | GettersKt | getChecksum | MessageDigest | sem PR |
| amethyst.debug_464 | SecretEncryption | decrypt | GCMParameterSpec | PR fechado |
| wifianalyzer_71 | AccessPointsAdapterData | calculateChildType | MessageDigest | PR fechado |
| wifianalyzer_71 | GraphWrapper | calculateGraphType | MessageDigest | sem PR |
| criticalmaps_104 | AeSimpleSHA1 | SHA1 | MessageDigest | sem PR |
| herald.debug_1 | ClientCerts_androidKt | tlsContext | SSLContext | sem PR |
| intertune.debug_96 | WidgetStore | hash | MessageDigest | PR fechado |
| email_2322 | Helper | getFingerprint | MessageDigest | sem PR |
| ibiswallet.debug_24 | TofuTrustManager$Companion | createSSLSocketFactory | SSLContext | sem PR |
| dsub2000_217 | Util | md5Hex | MessageDigest | sem PR |
| treehouses.remote_6098 | Encryptor | encrypt | IvParameterSpec, SecretKeySpec | sem PR |
| giggity_769 | Schedule$Item | hashCode | MessageDigest | PR fechado |
| cry.otp_31 | MD5 | `<init>` | MessageDigest | sem PR |
| cry.otp_31 | TOTP | hmac_sha | Mac, SecretKeySpec | sem PR |
| freeotp_48 | EncryptedKey | decrypt | AlgorithmParameters | sem PR |
| redreader_117 | General | parseConfig | IvParameterSpec, SecretKeySpec | sem PR |
| redreader_117 | General | sha1 | MessageDigest | PR fechado |

**Não verificado nesta especificação:**
- os estados atuais dos PRs (o retrato é de 2026-11-08);
- as chaves de configuração do Codex 0.167 para rede, web e tier;
- os preços do `gpt-6.1-sol`;
- se a combinação `no-llm-cache` com o corpus do K3 pesquisa a parte do caso ao vivo (é o portão P1);
- o comportamento das assinaturas sob 600 sessões;
- a junção exata das unidades, que vem da run congelada.
