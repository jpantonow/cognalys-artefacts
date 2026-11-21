> Especificação de Defeitos achados na validação do D1, escrita em 2026-11-10 para ser implementada por uma sessão Opus 5.5 seguindo docs/PLANO_FABLE.md (desenho no Fable 5.2; revisão adversarial e versão final no Opus 5.5). Os scripts citados ficam em `outputs/design_20261110/scratch/` (não versionado; arquivos acima de 2 MB não foram copiados, e os caches grandes são refeitos pelos próprios scripts). As perguntas da seção 9 esperam a resposta do usuário antes da implementação das partes que dependem delas.

# Especificação vfix: defeitos de pipeline achados na leitura da validação paga do D1 (2026-11-10)

Marcação dos números:

- (M) foi medido por script. O script vem nomeado ao lado e fica em uma de duas pastas:
  - `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\vfix_final\` (scripts `f*`);
  - `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\vfix_design\` (scripts `s*` e `p*`, da primeira versão).
- (E) é estimativa, e a base vem dita.

A varredura `vfix_design/s01_index.py` e `s02_scan.py` (que gera `scan_*.jsonl`) leu 628 pastas de execução sob `outputs/`. Dessas, 546 têm log de prompts, num total de 12.319 pedidos. Nada foi editado no repositório nem no worktree, e nenhuma chamada paga foi feita.

## 1. Resumo

| # | Defeito? | O que entra antes da run congelada | Fica com outro item |
|---|---|---|---|
| 1 Histórico | Sim, dois | O planejamento e a geração deixam de ler tentativas que pararam antes de um patch. `--learning-dir` passa a valer também para a leitura. Toda execução comparada usa um store próprio, semeado só com os imports verificados do store compartilhado. A regra de histórico ganha número de versão | — |
| 2 `STORED_DERIVED_VALUE_CHANGED` | Sim, em 1 de 24 unidades | Isenção para construtor alimentado só por material aleatório recém-gerado. Saem só as 3 lições antigas que citam um construtor | — |
| 3 Resposta do mantenedor | Sim, dois | (a) Comentário de bot deixa de ser lido como fala do mantenedor. (b) Resposta inteira, com teto de 2.000 caracteres e corte em fim de frase, **só com o sim do usuário** | A ablação da memória (resto do DL3r) fica para depois da run congelada |
| 4 Arquivo do plano fora do commit | Sim, de registro | Manifesto, log e suposições corrigidos quando a revisão descarta um teste | A1: teste criado é reescrito quando a produção é regenerada |
| 5 Título da issue de contato | Sim | Título neutro quando o registro não tem `impact_class: security_impact` | — |
| 6 `publishable` × `publication_allowed` | Não; é a regra pretendida | Só a frase do log e uma linha no GUIA | — |
| 7 Redação do D1 | Sim, pequena | Duas correções na frase da adjudicação (versão 7) | — |
| 8 Input Leaf | Sim | Nada | Q2Q3 (lista em §2.8) |
| 9 Promoção do Winterhold | A decidir | Nada | Q1 (lista em §2.9). O rascunho vazio do Session fica com o DL6r parte 2 |

Números que decidem:

- **Item 1, isolamento.** As 35 execuções do benchmark que chegaram ao planejamento leram o histórico de outras execuções (M, `f02_history.py`).
- **Item 1, efeito nos pedidos.** Há 3.259 pedidos gravados de planejamento e geração, e 1.447 deles levam histórico (269 unidades). A regra nova muda 504 (114 unidades) e tira a seção inteira de 125 (36 unidades). Os outros 2.755 ficam idênticos (M, `f02_history.py`).
- **Item 1, risco de dispensa.** Não move nenhuma finding para dispensa: a adjudicação não lê o histórico (`finding_adjudication.py:827` e `coordinated_generation.py:1867` só passam as chaves de mantenedor).
- **Item 1, imports.** 60 dos 237 pedidos de planejamento, geração e reparo do benchmark levam imports verificados: Melox 23, RedReader 32, Winterhold 5 (M, `f04_imports.py`). Por isso o store isolado é semeado com eles. Sem a semente, o isolamento também tiraria esses imports.
- **Item 2.** 78 disparos em 24 unidades de 18 execuções. Em 23 unidades o valor vem de entrada guardada ou do usuário. Em 1 unidade (CometOTP v6, 3 disparos) o valor é aleatório recém-gerado (M, `vfix_design/s05_analyze.py`, `s06_sdvc_sim.py`). A isenção simulada libera os 3 rascunhos do CometOTP e nenhum dos outros 26 reproduzíveis.
- **Item 3a.** Os 4 comentários do PR DSub2000#160 são do autor `Copilot`, conta que `KNOWN_BOT_ACCOUNTS` não lista. Esse texto chega como `maintainer_said` em 420 pedidos de 94 unidades: 79 de adjudicação, 77 de planejamento e 264 de geração (M, `f01_answers.py`, `f05_cut_requests.py`).
- **Item 3b.** Das 34 respostas com texto, 13 são cortadas em 400 caracteres. Sem o bot sobram 12 de 33 (M, `f01_answers.py`). 1.214 pedidos, de 207 das 252 unidades que recebem respostas, levam ao menos uma resposta cortada (M, `f05_cut_requests.py`).

Precisa do sim do usuário:

1. **Item 3b.** A razão inteira do mantenedor pode puxar findings para "sem impacto". Isso não dá para medir offline.
2. **Run congelada sem histórico ou com o histórico atual** (§9, pergunta 2).
3. **A revisão da regra publicada** (§9, pergunta 3). É pergunta, não desenho.

O item 3a entra sem o sim. Ele só tira texto de um revisor automático, que `feedback_from_pr` já exclui por desenho (`pr_github.py:99-108`). Os 79 pedidos de adjudicação que mudam são de findings SSLContextSpec de outros repositórios.

Cortado, com motivo:

- **Regenerar o teste criado a cada redraft.** Muda o fluxo de 74 unidades às vésperas da run congelada, e o A1 troca esses rascunhos de qualquer modo.
- **Checagem determinística de "relato movido para outra chamada governada".** Vira fato para a revisão, dentro do Q2Q3.
- **Três grupos em `kept_attempts` e filtro antes da janela do histórico.** O filtro depois da janela foi o que se mediu. O filtro antes traria de volta 19 lições de outubro em 17 findings, da época do pipeline antigo (M, `vfix_review/r02_window.py`).
- **Aposentar por versão todas as lições de `STORED_DERIVED_VALUE_CHANGED`.** 15 das 18 citam `getInstance`, que a isenção não alcança.
- **Limpar as tentativas do benchmark no store de produção.** Só seria preciso se a run congelada fosse "quente".
- **Filtrar a resposta só pelo dono do repositório.** Session#2241 (`mpretty-cyro`, organização `session-foundation`) e openhab#4039 (`mueller-ma`, organização `openhab`) perderiam a resposta. O registro não guarda `author_association`.

## 2. Diagnóstico medido

### 2.1 Item 1: `previous_attempts_of_this_finding`

**Como o histórico é localizado.**

- `learning_context` (`src/cognalys/llm/fix_generation_common.py:103-145`) chama `load_knowledge()` sem diretório (`:126`). Na mesma leitura ele pega `verified_repository_imports` (`:128`) e `previous_attempts_of_this_finding` (`:129-131`).
- `learning_dir` (`src/cognalys/learning/knowledge.py:40-44`) devolve `TCC_LEARNING_DIR` ou `data/pipeline_learning` (que `data_path` resolve na raiz do workspace). O arquivo é `verified_knowledge.json`, único para todas as execuções da máquina.
- `validation/repair.py:943-947` (`_verified_imports`) também chama `load_knowledge()` sem diretório.
- A chave é `finding_key_for` (`knowledge.py:63-68`): os 12 primeiros hexadecimais do sha256 do `unique_error_id`. Não entra pasta, run nem commit. Toda execução da mesma finding, em qualquer pasta, lê e grava a mesma entrada.
- Cada tentativa é identificada só por `run_dir.name`. `kept_attempts` (`retrospective.py:705-729`) substitui a entrada que tem o mesmo nome.
- `--learning-dir` (`run_pipeline.py:359-363`, padrão `data/pipeline_learning`) chega só ao manifesto (`:1874`) e à retrospectiva (`:1972`). Nenhum código exporta `TCC_LEARNING_DIR`, então a leitura ignora a opção.
- O histórico tem dois leitores: o planejamento (`finding_adjudication.py:1786`) e cada rascunho de arquivo (`coordinated_generation.py:2739`).

**(a) O que ele carrega.**

- `finding_history` (`knowledge.py:129-169`) devolve as 3 últimas falhas e os 3 últimos validados, de qualquer etapa. Só o filtro de renomeação (`:107-127`) olha a validade da tentativa.
- No store há 980 tentativas, e 419 chegaram a um patch. As outras: 375 `adjudication`, 97 `not_analyzable`, 41 `plan`, 21 `analysis`, 21 `crashed` (M, `vfix_design/s13_final_filter.py`).
- Nos pedidos gravados há 3.693 entradas de histórico, e 659 delas pararam antes de um patch: 322 `adjudication`, 146 `crashed`, 79 `analysis`, 54 `plan`, 23 `provider_refused`, 35 outras (M, `s13_final_filter.py`).
- O planejador do Melox v4 recebeu como tentativa anterior "OpenAI refused this request's content (server_is_overloaded)" (M, `s13_final_filter.out`).
- O CometOTP v6 recebeu três dispensas escritas sob o rótulo antigo. Os rascunhos da v6 à v8 as repetem em `review_assumptions`.

**(b) Independência.**

- No benchmark há 68 pastas de execução. 35 chegaram ao planejamento, e as 35 leram histórico de outras execuções; 33 nunca planejaram (M, `f02_history.py`, `vfix_design/s05_analyze.py`).
- No Input Leaf, os 5 registros de PR (v2 a v6) têm o mesmo diff. Da v3 em diante, cada planejador leu o título do rascunho validado anterior.
- A dependência também passa pelos imports verificados. 60 dos 237 pedidos de planejamento, geração e reparo do benchmark os levam (M, `f04_imports.py`), e parte deles foi gravada por execuções irmãs. Os imports são fatos do repositório (linhas de um build que passou), não decisões sobre a finding.

**Consequências.**

- **`noise_floor.md`.** Os conjuntos de Amethyst, Herald e IbisWallet, e o do CometOTP antes do D1, valem como estão, porque nunca planejaram. Os de Input Leaf, InterTune, Melox, RedReader e Winterhold, e o "2 de 3" do CometOTP, não são amostras independentes depois da adjudicação.
- **Run congelada.** `TCC_FROZEN_EXPERIMENT` impede a escrita (`retrospective.py:1024`), e o manifesto fixa o hash do store (`experiment_manifest.py:194-201`). A leitura, porém, continua.
- **A1.** Um par só é pareado se os dois braços partirem do mesmo store e nenhum gravar no do outro.

### 2.2 Item 2: `STORED_DERIVED_VALUE_CHANGED`

Como a checagem funciona hoje:

- `changed_stored_derivations` (`src/cognalys/llm/contract_gates.py:285-347`) marca toda derivação de `_DERIVATION_CALL` (`:182-186`) que some do patch quando `derived_value_consumers` diz que o valor é guardado.
- O comentário do módulo (`:178-181`) deixa SecureRandom e a geração de chave de fora "on purpose: nobody re-derives their output". O construtor que embrulha esse material ficou dentro.
- O texto do detalhe é `f"the patch changes {changed}, ..."` (`:338`). `changed` é `SecretKeySpec(...)` para construtor e `MessageDigest.getInstance(...)` para fábrica.

O caso do CometOTP:

- O código é `val raw = generateRandom(Constants.ENCRYPTION_KEY_LENGTH)` seguido de `SecretKeySpec(raw, "AES")`, dentro de `if (!keyFile.exists())`.
- `generateRandom` (`EncryptionHelper.kt:42-46`) preenche um `ByteArray` com `SecureRandom().nextBytes`.
- A frase da issue vem de `_PLAIN_BLOCKERS` (`fix_generation_common.py:492-495`).

Disparos medidos (M, `vfix_design/s05_analyze.py`; a lista está em `sdvc_firings.json`):

| Derivação removida | Apps | Classe |
|---|---|---|
| `MessageDigest.getInstance` ou literal (10 combinações) | RedReader, EinkBro, WiFiAnalyzer, whoBIRD, Whisper | hash de entrada guardada ou comparada |
| `PBEKeySpec`, `SecretKeyFactory` (5) | BitBanana, SMSecure | derivada de senha |
| `Mac.getInstance("HmacSHA1")` (2) | SMSecure | MAC sobre dado guardado |
| `SecretKeySpec(raw,"AES")` (1) | CometOTP v6 | aleatório recém-gerado |

**Lições no store.** 18 tentativas de 12 findings guardam esta lição. Delas, 15 citam `getInstance`, 2 citam `PBEKeySpec(passphrase…)` (SMSecure `run` e `run_v6`) e 1 cita `SecretKeySpec` (CometOTP v6). Só as 3 últimas nomeiam um construtor (M, `f02_history.py`, `vfix_review/r03_sd_lessons.py`).

### 2.3 Item 3: resposta do mantenedor

`_maintainer_response` (`src/cognalys/prs/pr_outcomes.py:225-240`) junta os corpos de `maintainer_feedback` com " | " e corta em `MAX_RESPONSE_CHARACTERS = 400` (`:106`), no meio da palavra. Não olha o autor.

**(a) Bot.**

- `feedback_from_pr` (`pr_github.py:99-140`) descarta bots com `is_bot` (`:90-96`). `KNOWN_BOT_ACCOUNTS` (`:79-83`) lista `copilot-pull-request-reviewer`, mas não `copilot`.
- `data/pr_outcomes/paroj__dsub2000.json` guarda 4 itens do autor `Copilot` no PR #160, que foi incorporado (SSLContextSpec).
- Como `_decided_first` mantém sempre um PR incorporado, esse texto vai para os pedidos de findings SSLContextSpec de outros repositórios: 420 pedidos de 94 unidades (M, `f05_cut_requests.py`).

**(b) Corte.**

- 62 registros; 38 respostas dadas ao modelo; 34 com texto; 13 cortadas. Sem o bot, sobram 33 com texto e 12 cortadas (M, `f01_answers.py`).
- Comprimento das 12 cortadas: de 403 a 2.197 caracteres. Com teto de 2.000, só o Feeder#1210 (2.197) continua cortado.
- Frases perdidas hoje:
  - Session#2241: "We'll hold off merging for now since it doesn't change anything functionally";
  - Amethyst#4642: a medição do IV em 100.000 execuções;
  - rssreader#4: "it wouldn't improve security";
  - SMSecure#83: "does not ... provide a direct security improvement".
- Desfecho das 207 unidades que leram uma resposta cortada: issue 71, PR pronto 51, sem impacto 50, outros 35 (M, `vfix_design/s05_analyze.py`).
- Custo de entregar as respostas inteiras: cerca de +8.600 caracteres por unidade afetada (M, `vfix_design/s08_more.py`). Isso dá de US$ 0,0017 a 0,0022 por finding afetada em standard, e metade em Flex (E: de 4 a 5 caracteres por token, US$ 1 por milhão de tokens de entrada).
- O terceiro autor do Feeder#1210 (`ibrahim-iqbal`) tem papel não verificado. O registro não guarda `author_association`.

**Mover o DL3r?** Sim para o corte, com o sim do usuário. Não para a ablação da memória.

### 2.4 Item 4: arquivo do plano fora do commit

- Em `coordinated_generation.py:3256-3266`, quando a revisão lista um teste em `unrelated_test_edits`, as edições dele saem de `edits`. Nesse caminho:
  - o manifesto não registra o descarte;
  - não há linha de log;
  - `assumptions` (montado em `:3062-3065` a partir de todos os rascunhos) mantém as suposições do teste.
- O motivo é registrado mais adiante. `finding_processing.behavior_review_dropped_tests` (`finding_processing.py:413-433`) relê `behavior_review.json` e acrescenta "the behaviour review found the generated tests assert behaviour the change does not alter" (`:1489-1491`). Faltam o manifesto, o log e a limpeza das suposições.
- Os outros dois caminhos que tiram teste (`:3148-3158` e `_without_rejected_new_tests`, `:2543-2567`) registram o fato, mas também não filtram as suposições.
- Dos 272 registros de PR, 58 têm um arquivo do plano fora de `changed_files`: 16 por rascunho "sem mudança", 12 pelo descarte da revisão e 30 por outros caminhos (M, `vfix_design/s05_analyze.py`).
- 9 registros carregam suposições feitas só pelo rascunho de um arquivo não commitado. O Winterhold v6 tem 2 (M, `vfix_design/p09_session_assump.py`).
- Houve 133 rodadas de redraft, em 74 unidades, que regeneraram a produção e mantiveram um teste criado pelo plano (M, `s05_analyze.py`).

### 2.5 Item 5: título da issue de contato

- `generate_github_previews.py:713-718` grava, para todo registro de PR, a prévia de uma issue de contato com `args.issue_title`.
- `issue_creator.py:494-505` publica com `self.title`. O padrão do título fica em `run_pipeline.py:408`, e a opção é repassada em `:1674-1698`.
- 267 prévias têm o título de segurança (M, `f03b_titles.py`). Dessas, 265 têm registro de PR. O campo `impact_class` está ausente em 255 (registros anteriores ao campo), `impact_not_established` em 5, `security_impact` em 3 e `no_security_impact` em 2.
- Pelo artefato de adjudicação, só 54 das 267 têm impacto confirmado (M, `vfix_design/s08_more.py`).
- Nos registros sem impacto de segurança, o nome do método seria: identificador simples em 219, construtor em 30 e nome sintético em 13 (`lambda$downloadModels$10`, `discoverySslContext$app`, `invokeSuspend`) (M, `f03b_titles.py`).
- 3 issues foram abertas com esse título em setembro: Gallery#1146, BombusMod#317 e wwwjdic#116 (M, `vfix_design/s09_issues.py`).
- `prs/impact_class.py:7-9` declara: "Report only: no gate, decision or text reads it."

### 2.6 Item 6: `publishable` e `publication_allowed`

É a regra pretendida:

- `publication_after_validation` (`utils/disposition.py:310-445`) trata `runtime_behavior` como nota (`:422-425`).
- `publication_allowed` é o veredito da análise (`pr_generator.py:1888`). `publishable` é o veredito depois do build e da revisão (`:1803-1805`).
- `pr_creator.py:1046-1050` exige aprovação explícita de todo rascunho promovido.

Dos 272 registros, 11 são promovidos, e 6 deles têm bloqueio `runtime_behavior` não quitado (M, `s05_analyze.py`). O que engana é a frase "(cleared after validation, with notes)" (`pr_generator.py:1812`): nada validou o comportamento no dispositivo.

### 2.7 Item 7: redação do D1

- A frase da adjudicação (`finding_adjudication.py:295-320`, trecho em `:301`) diz "value what it saw". Porém `outranking_record` (`utils/detector_labels.py:201-212`) omite chaves vazias, e o verificador emite NOBS-03 com `val=''`.
- A frase da extração (`:291`) diz "the record is not evidence and is never cited"; a da adjudicação não diz.
- Os 5 pedidos de adjudicação gravados com a seção citam um id SRC na causa. Só o do CometOTP v6 cita também `SECRETKEYSPEC-NOBS-03` (M, `f06_d1_adj.py` e leitura dos artefatos de adjudicação).
- **Ramo de finding conjunta: exercitado** em `session_writeentry_joint` (M, `vfix_design/p07_joint.py`). O registro do SecretKeySpecSpec chegou às duas extrações e à adjudicação, com o CipherSpec como finding relatada. A resposta foi `confirmed`/`confirmed`/`order`/`non_security`, sem citar o código.

### 2.8 Item 8: Input Leaf (só o que o Q2Q3 deve cobrir)

Medido:

- 116 unidades têm duas ou mais revisões. Em 10 delas, os mesmos arquivos de produção (ignorando espaços) receberam vereditos diferentes: 9 passaram de regressão para limpo e 1 no sentido inverso. Em todas as 10 o veredito divergente é o da última revisão, e 7 terminaram PR pronto (M, `vfix_design/s11_same_file.py`).
- 17 registros de PR acrescentam um `init(null)` que as linhas removidas não tinham. 16 são SSLContextSpec, 15 terminaram PR pronto e 1 foi publicado (M, `s05_analyze.py`).

O Q2Q3 deve cobrir:

1. Um rascunho cujos arquivos resultantes igualam os de um rascunho já revisado na unidade mantém aquele veredito.
2. O registro final guarda as regressões das revisões anteriores do mesmo plano, não só o `regression_count` da última.
3. `concern_verdicts` inclui cada alerta da adjudicação.
4. Uma chamada acrescentada que o analisador relata vira fato para a revisão, nunca portão:
   - entrada opcional `reported_call_shapes` por rótulo em `data/detector_labels.json`;
   - predicado no código: uma linha acrescentada casa com a forma e nenhuma removida casa;
   - o fato `added_calls_the_analyzer_reports` leva o significado do rótulo.

   Não lê linguagem de regra. O Q2Q3 deve medir quantos dos 17 registros com `init(null)` e quantos dos demais registros o fato marcaria, para mostrar que não é triagem disfarçada.

### 2.9 Item 9: Winterhold (só o que o Q1 deve cobrir)

Medido:

- 144 artefatos têm `evidence_gap_promoted`, e em 138 a resposta bruta foi NO_SAFE_FIX.
- Desfecho dos 138: 35 PR pronto, 29 issue, 28 sem impacto, 27 sem correção segura, 15 gerada sem PR, 2 não gerada, 2 sem desfecho (M, `s05_analyze.py`).
- Em 105 dos 138, a resposta bruta dizia causa e violação `unknown` (M, `s08_more.py`).

O Q1 deve cobrir:

1. Se um rascunho promovido que mantém `cause_unconfirmed` conta como "PR pronto" ou ganha balde próprio.
2. A regra de promoção quando a resposta bruta diz causa e violação `unknown`.
3. O texto do `noise_floor.md` sobre o Winterhold.

**Fica com o DL6r parte 2**, no mesmo passo do plano que as partes 2 a 4 do Q1, e não com a regra de promoção: em `session_writeentry_joint`, o rascunho de `PersistentLogger.kt` devolveu zero edições e nomeou `LogSecretProvider`, fora do plano. A unidade virou issue com "I couldn't find a change that keeps the current behaviour intact" (M, `vfix_design/p08_joint2.py`). Um rascunho sem edições que nomeia um arquivo fora do plano é falha do pipeline: vai para reparo ou replano, nunca para o mantenedor.

## 3. Mudanças

São cinco commits. O quinto só entra com o sim do usuário.

### Commit 1: histórico

**`src/cognalys/learning/knowledge.py`**, depois de `VALIDATED_STAGES` (`:35`):

```python
# Where an attempt ended when it went on to a patch. Only these teach a plan
# or a draft anything: an attempt that stopped at the adjudication or at the
# plan is a decision on the finding taken on that run's inputs, which the
# current run takes again, and one that crashed, was refused by the provider
# or could not be analysed says nothing about a patch. CometOTP run_v6..v8
# (2026-11-10): three dismissals written under an older label meaning reached
# the planner and the drafts, which repeated them against the adjudication
# that had just confirmed the finding; Melox run_v4's planner was told
# "OpenAI refused this request's content (server_is_overloaded)".
PATCH_STAGES = frozenset({
    "generation", "generated", "apply", "build", "repair", "validated",
    "published", "revision",
})
# The rule finding_history applies, recorded in run_inputs.json and in an
# experiment's manifest so runs read under different rules can be told apart.
# 2: only attempts that went on to a patch; draft-check refusals retired by
#    DRAFT_CHECK_CHANGES are left out.
HISTORY_RULES_VERSION = 2
# Bumped when a deterministic draft check (llm.semantic_validation,
# llm.contract_gates) changes what it refuses. DRAFT_CHECK_CHANGES names, per
# check code, the version at which it last changed and the detail pattern of
# the refusals that change can affect: a refusal recorded under an older
# version whose detail matches is no longer told to planners and drafts.
DRAFT_CHECK_RULES_VERSION = 1
DRAFT_CHECK_CHANGES: dict[str, dict] = {}
```

Nova função, depois de `_without_stale_rename_signals`:

```python
def _without_stale_check_signals(entry: dict, recorded_version: int) -> dict | None:
    """A history entry without draft-check refusals a later check change retired.

    ``recorded_version`` is the attempt's draft_check_rules_version (1 when the
    attempt predates the stamp). A detail is retired when its check changed
    after that version and the detail matches the change's pattern; the
    signature goes too once no detail of that check remains. An entry left
    with nothing to say and no validated draft is dropped whole.
    """
    details = list(entry.get("details") or [])
    signatures = list(entry.get("failure_signatures") or [])
    for code, change in DRAFT_CHECK_CHANGES.items():
        if recorded_version >= int(change["changed_at"]):
            continue
        signature = f"generation:semantic:{code.lower()}"
        pattern = re.compile(change["detail"])
        details = [item for item in details
                   if not (isinstance(item, dict) and item.get("signature") == signature
                           and pattern.search(str(item.get("detail", ""))))]
        if not any(isinstance(item, dict) and item.get("signature") == signature
                   for item in details):
            signatures = [item for item in signatures if item != signature]
    if ((entry.get("failure_signatures") or entry.get("details"))
            and not signatures and not details and not entry.get("validated_draft")):
        return None
    return {**entry, "failure_signatures": signatures, "details": details}
```

(`import re` no topo, se ainda não houver.)

Em `finding_history` (`:144-169`):

- A janela é tomada como hoje.
- Depois, cada `entry(eligible[index])` passa por dois filtros e é descartada quando `ended_at` não está em `PATCH_STAGES` ou quando `_without_stale_check_signals(entry, int(eligible[index].get("draft_check_rules_version") or 1))` devolve `None`.
- Acrescentar à docstring: "Only attempts that went on to a patch (PATCH_STAGES) are returned; the window is taken over all attempts first, so a later stop at the adjudication can leave fewer than ``limit`` patch lessons (the window measured on 2026-11-10; taking it after the filter brought back 19 lessons of the October pipeline)."

Nova função, para semear stores isolados:

```python
def seeded_store(source: str | Path, target: str | Path) -> Path:
    """A learning store at ``target`` holding only ``source``'s verified imports.

    For executions compared with each other (replicates, benchmark arms, a
    frozen run): each reads no earlier attempt of its findings, and all read
    the same repository facts (import lines a passing build contained). An
    existing store at ``target`` is left as it is, so a resumed execution
    keeps what it recorded.
    """
    path = Path(target) / KNOWLEDGE_FILE
    if not path.exists():
        repositories = read_json(Path(source) / KNOWLEDGE_FILE).get("repositories") or {}
        write_json_atomic(path, {"repositories": repositories, "findings": {}})
    return path.parent
```

**`src/cognalys/learning/retrospective.py`**

- `:88-91`: `PROPOSED_STAGES = PATCH_STAGES`, importado de `knowledge`. O conjunto é idêntico.
- `harvest_knowledge` (`:685-700`): a tentativa ganha `"draft_check_rules_version": DRAFT_CHECK_RULES_VERSION`, logo depois de `rename_rules_version`.
- `kept_attempts` não muda.

**`src/cognalys/orchestration/run_pipeline.py`**

- `:359-363`: `default=None`. Novo `help`: "Directory holding the failure ledger and verified knowledge across runs; the run reads its findings' earlier attempts and verified imports from it and records its own there (default: TCC_LEARNING_DIR, else data/pipeline_learning)."
- Função nova:

```python
def resolved_learning_dir(args: argparse.Namespace, root: Path) -> Path:
    """The learning store this run reads and writes."""
    chosen = (getattr(args, "learning_dir", None)
              or os.environ.get("TCC_LEARNING_DIR", "").strip()
              or "data/pipeline_learning")
    return (root / chosen).resolve()
```

- Depois de `export_service_tier(args)` (`:2308`):

```python
    # Read by learning.knowledge.load_knowledge in the steps' processes: the
    # history and imports a plan, a draft and a repair read come from the
    # store this run writes.
    os.environ["TCC_LEARNING_DIR"] = str(resolved_learning_dir(args, root))
```

- `:1874` e `:1972` passam a usar `resolved_learning_dir(args, root)`.
- Em `start_run_record` (`:2018-2025`):

```python
inputs.setdefault("learning_dir", os.environ.get("TCC_LEARNING_DIR", ""))
inputs.setdefault("learning_history_rules_version", HISTORY_RULES_VERSION)
```

**`src/cognalys/evaluation/experiment_manifest.py:194-201`**: `learning_store` ganha `"history_rules_version": HISTORY_RULES_VERSION`.

**`docs/GUIA_DE_USO.md`**

- Linha da tabela (`:535`): "`--learning-dir` | `data/pipeline_learning` (registro de falhas e fatos verificados entre execuções; a execução lê dali as tentativas anteriores de cada achado e os imports verificados, e grava os seus)".
- Parágrafo depois da tabela:

> Execuções que serão comparadas entre si (réplicas, benchmark, braços de um experimento) usam cada uma o seu `--learning-dir`, semeado por `cognalys.learning.knowledge.seeded_store` com os imports verificados do store compartilhado e nenhuma tentativa. Sem isso, o planejamento e a geração de cada execução leem como terminaram as anteriores do mesmo achado. Só tentativas que chegaram a um patch são lidas.

**Bytes.** Mudam 504 pedidos gravados de planejamento e geração (114 unidades). Desses, 480 levam entrada anterior a um patch e 39 levam lição de construtor do commit 2; os dois casos se sobrepõem. 125 perdem a seção e 2.755 ficam idênticos (M, `f02_history.py`). Todo pedido de pesquisa, extração, adjudicação, revisão e texto fica idêntico. A prova está em §5.

### Commit 2: checagem de derivação guardada

**`src/cognalys/llm/contract_gates.py`**, depois de `_legacy_derivation_kept`:

```python
# What makes a value new each time the code runs. A constructor given such a
# value, an algorithm name and nothing else wraps generated material: nothing
# stored was ever computed from the same inputs, so building it another way
# breaks no stored data. CometOTP loadOrGenerateWrappedKey (run_v6,
# 2026-11-10): three drafts replaced SecretKeySpec(raw, "AES"), raw filled by
# SecureRandom on the branch that runs when no key file exists, with a
# KeyGenerator key, and all three were refused as changing a stored value.
# A constructor that also takes a password, a constant or an expression is
# never exempt: its value can be derived again from what is stored.
_FRESH_RANDOM_SOURCE = re.compile(
    r"\bSecureRandom\b|\.\s*nextBytes\s*\(|\bKeyGenerator\b|\.\s*generateKey\s*\("
    r"|\.\s*generateSeed\s*\(")
_PLAIN_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_NEW_ZERO_ARRAY = re.compile(r"^(?:ByteArray\s*\([^(){}]*\)|new\s+byte\s*\[[^\]]*\])$")
```

Funções novas. A referência executável é `vfix_design/s06_sdvc_sim.py`, que tem as mesmas funções.

- `_initializer(name, scope)`: o inicializador de `val|var name = ...` ou `Tipo name = ...` em `scope`.
- `_function_body(name, source)`: o corpo da função `name` declarada em `source`. Vale para `fun` Kotlin, corpo de expressão ou método Java.
- `_freshly_random(name, scopes, file_source, depth=0)` é verdadeiro em três casos, e falso em qualquer outra forma:
  1. o inicializador casa `_FRESH_RANDOM_SOURCE`;
  2. o inicializador é um array novo zerado e algum escopo tem `nextBytes(name`;
  3. com `depth == 0`, o inicializador é a chamada de uma função declarada em `file_source` cujos `return <nome>` são todos `_freshly_random` (com `depth=1`) dentro do corpo dela, ou cujo corpo de expressão casa `_FRESH_RANDOM_SOURCE`.
- `_only_fresh_random_inputs(derivation, scopes, file_source)`:
  - é falso quando `api == "algorithm"` ou quando `api` contém ponto;
  - para um construtor, cada argumento de nível superior tem de ser literal de algoritmo (`_ALGORITHM_LITERAL.fullmatch`), inteiro ou nome simples `_freshly_random`;
  - exige ao menos um nome.

Em `changed_stored_derivations` (`:320-325`), excluir de `dropped` os itens para os quais `_only_fresh_random_inputs(item, [str(edit.get("original_code", "")), str(error.get("method_body", ""))], file_source)` é verdadeiro.

- `file_source` é o texto de `Path(error["repository_path"]) / edit["source_file_path"]` quando esse arquivo existe e `error.get("repository_includes_patch")` é falso. Nos outros casos é "".
- Docstring: "A constructor fed only an algorithm name and freshly generated random material is not a derivation of anything stored and is not flagged."

**`src/cognalys/learning/knowledge.py`**:

```python
# 2: changed_stored_derivations no longer refuses a constructor whose inputs
#    are an algorithm name and freshly generated random material (CometOTP
#    run_v6, 2026-11-10). Only refusals naming a constructor can have changed;
#    those naming a factory (getInstance) or an algorithm literal stand.
DRAFT_CHECK_RULES_VERSION = 2
DRAFT_CHECK_CHANGES: dict[str, dict] = {
    "STORED_DERIVED_VALUE_CHANGED": {
        "changed_at": 2,
        "detail": r"the patch changes [A-Za-z_$][\w$]*\(",
    },
}
```

**Bytes.**

- Nenhum prompt muda por este commit, fora os 39 pedidos cujo histórico leva uma lição de construtor. Eles já estão contados nos 504.
- No store, 3 tentativas perdem a lição: CometOTP v6, e SMSecure `run` e `run_v6`. As 2 do SMSecure eram corretas, porque `PBEKeySpec` com senha continua marcado. O custo é no máximo um rascunho a mais numa rerun do SMSecure. As outras 15 lições ficam (M, `f02_history.py`).

### Commit 3: registros, título, redação

**`src/cognalys/llm/coordinated_generation.py`**

Função nova, perto de `unplanned_draft_notes`:

```python
def assumptions_without_files(assumptions: List[str], drafts: List[Dict],
                              dropped_paths) -> List[str]:
    """``assumptions`` less those only the drafts of ``dropped_paths`` made.

    Winterhold run_v6 (2026-11-10): the review dropped the generated test and
    the pull-request record still said "WebUser is accessible to the module's
    unit tests". An assumption a kept file's draft also made stays.
    """
    dropped = {str(path) for path in dropped_paths or []}
    gone = {str(value) for draft in drafts if str(draft.get("source_file_path", "")) in dropped
            for value in draft.get("review_assumptions") or []}
    kept = {str(value) for draft in drafts if str(draft.get("source_file_path", "")) not in dropped
            for value in draft.get("review_assumptions") or []}
    return [item for item in assumptions if item not in gone or item in kept]
```

Mudanças nos caminhos que tiram teste:

- Em `:3256-3266`, dentro do `if`, depois de `edits = kept`:
  - `dropped_unrelated = sorted(path for path in unrelated if any(str(e.get("source_file_path", "")) == path for e in removed))`, onde `removed` são as edições tiradas;
  - `manifest["dropped_unrelated_tests"] = dropped_unrelated`, com o manifesto gravado de novo;
  - `self.logger.warning("%s: dropped generated test edits the behaviour review found unrelated to the change: %s", error.get("method_name", ""), ", ".join(dropped_unrelated))`;
  - `assumptions = assumptions_without_files(assumptions, assembled_drafts, dropped_unrelated)`.
  - Nenhuma frase nova: o motivo já chega ao resultado por `behavior_review_dropped_tests`.
- Em `:3152-3158`, antes de acrescentar a frase existente: `assumptions = assumptions_without_files(assumptions, assembled_drafts, dropped_tests)`.
- Em `_without_rejected_new_tests` (`:2543-2567`): o mesmo filtro sobre os rascunhos lidos de `failed.get("drafts")`. Um arquivo ilegível não filtra nada.
- Em `finish` (`:3504`): acrescentar `**({"dropped_unrelated_tests": dropped_unrelated} if dropped_unrelated else {})`.

**`src/cognalys/llm/finding_processing.py:413-433`** (`behavior_review_dropped_tests`): quando `generated.get("dropped_unrelated_tests")` existe, devolver esses caminhos (só os que satisfazem `is_test_path`). Senão, ler a revisão como hoje. Acrescentar à docstring: "The draft's own record (dropped_unrelated_tests) is read first: it is written by the path that dropped them."

`REVIEW_CONTRACT_VERSION` e `generator_contract_version` não mudam.

**`src/cognalys/issues/issue_text.py`**:

```python
_SYNTHETIC_METHOD = re.compile(r"[$<>-]|^(?:invoke|invokeSuspend)$")


def contact_issue_title(metadata: Dict, with_security_impact: str,
                        without_security_impact: str) -> str:
    """The title of the contact-first issue of a pull-request draft.

    ``with_security_impact`` (--issue-title) is used only for a draft whose
    record carries impact_class security_impact (prs.impact_class). Any other
    draft, one recorded before the class existed included, gets
    ``without_security_impact`` with {api} (the specification without its
    "Spec" suffix) and {location} filled: the method when it is a plain
    source name, otherwise its outer class (a constructor, a lambda, a
    coroutine or a mangled Kotlin name would put a bytecode name before a
    maintainer).
    """
```

- `{location}` é o `method_name` quando não casa `_SYNTHETIC_METHOD`.
- Nos outros casos, `{location}` é a classe simples: `csv_class` até o primeiro `$`, último segmento depois do ponto, sem o sufixo `Kt`.

**`src/cognalys/prs/impact_class.py:7-9`**: trocar "Report only: no gate, decision or text reads it." por "No gate or decision reads it; the contact-first issue's title does (issues.issue_text.contact_issue_title), so a draft without a confirmed security impact is not announced as a security issue." `scripts/levantar_prs.py` lê `published_impact_class` e não é afetado.

**`src/cognalys/orchestration/run_pipeline.py`**:

- Opção nova `--issue-title-without-security-impact`, com padrão `"Question about the {api} use in {location} — would you be open to a pull request?"`.
- Repassada às etapas de prévias e de issues junto com `--issue-title` (`:1674-1698`).

**`generate_github_previews.py:713-718`** e **`issue_creator.py:505`**:

- Usam `contact_issue_title(metadata, <título>, <título sem impacto>)`.
- Ganham o argumento `--issue-title-without-security-impact`, com o mesmo padrão.
- As fixtures de `tests/fixtures/golden_commands` são regeneradas pelo próprio teste, com o motivo no commit.

**`pr_generator.py:1812`**: trocar `" (cleared after validation, with notes)"` por `" (review-only draft kept by the verdict after validation; what it could not check is left as notes)"`.

**`docs/GUIA_DE_USO.md`**, onde descreve os campos do registro de PR:

> `publication_allowed` é o que a análise decidiu antes de existir patch; `publishable` é o veredito depois do build e da revisão. Um rascunho só para revisão que o veredito libera fica `publishable: true`, `publication_allowed: false`, `READY_FOR_PUBLICATION_WITH_NOTES`, e continua exigindo `--approve-reviewed-finding`. A issue de contato só leva o título de segurança quando `impact_class` é `security_impact`.

**`finding_adjudication.py:301`**: trocar

```
"check requires and value what it saw. The analyzer that ran is the "
```

por

```
"check requires and value, when the record has one, what it saw. Like "
"DET-001, the record is not evidence: never cite its code or label as an "
"evidence id, in confirmed_cause or anywhere else; cite the source or "
"runtime evidence that bears on the recorded fact, and when only the "
"record shows it, say that the analyzer recorded it. The analyzer that ran is the "
```

E `ADJUDICATION_PROMPT_VERSION = 7` (`:203`), com o comentário:

```
# 7: _REPORTED_BY_DETECTOR_ADJUDICATION_RULE says value is present only when
#    the record has one and that the record is never cited as evidence
#    (CometOTP run_v6, 2026-11-10: confirmed_cause cited
#    SECRETKEYSPEC-NOBS-03 beside SRC-001 and the issue text carried it).
```

**Bytes.**

- Mudam só os 5 pedidos gravados de adjudicação com `reported_by_detector`. A frase da extração não muda.
- As prévias de issue mudam de título em execuções futuras. Os registros gravados não são regenerados.

### Commit 4: comentário de bot não é fala do mantenedor

**`src/cognalys/prs/pr_github.py:79-83`**: acrescentar `"copilot"` a `KNOWN_BOT_ACCOUNTS`, com o comentário "# GitHub's Copilot reviewer appears as the login "Copilot" on review comments (paroj/DSub2000#160, 2026-11-10)". Função nova:

```python
def is_bot_login(login: str) -> bool:
    """The login of a review bot or service account, when only the name is recorded."""
    return str(login or "").lower().endswith("[bot]") or bot_name(login) in KNOWN_BOT_ACCOUNTS
```

**`src/cognalys/prs/pr_outcomes.py`**: função nova `_maintainer_items(record)`, que devolve os itens de `maintainer_feedback` cujo `author` não é `is_bot_login`. O import fica dentro da função, para não puxar `requests` no import do módulo. `_maintainer_response` (`:229-230`) e `repository_policy_closure` (`:220`) passam a usar `_maintainer_items`. Comentário:

```
# A review bot's comments recorded as feedback (paroj/DSub2000#160: four
# Copilot review comments) reached every other repository's SSLContextSpec
# finding as what the maintainer said.
```

**Bytes.** Mudam os 420 pedidos que levam o texto do DSub2000#160: 79 de adjudicação, 77 de planejamento e 264 de geração, em 94 unidades. O registro continua listado como "merged" com `maintainer_said` vazio. Todo outro pedido fica idêntico (M, `f05_cut_requests.py`).

### Commit 5: resposta do mantenedor inteira (só com o sim)

**`src/cognalys/prs/pr_outcomes.py`**: `MAX_RESPONSE_CHARACTERS = 2000` e

```python
def _whole_sentences(text: str, limit: int = MAX_RESPONSE_CHARACTERS) -> str:
    """``text`` up to ``limit`` characters, cut after a sentence and marked.

    session-android#2241 (2026-11-10): the answer reached every prompt cut
    at 400 characters, before "We'll hold off merging for now since it
    doesn't change anything functionally".
    """
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), cut.rfind(" | "))
    return (cut[:end + 1] if end >= limit // 2 else cut).rstrip() + " [...]"
```

A linha `"maintainer_said": _whole_sentences(said)` substitui o fatiamento. Nenhuma frase de prompt muda.

**Bytes.** Mudam os 1.214 pedidos que levam uma das 12 respostas longas. 11 passam inteiras, e o Feeder#1210 é cortado em fim de frase com a marca.

## 4. Testes

**`tests/test_learning.py`, classe `ConsumerTests`**

- `test_attempts_that_stopped_before_a_patch_are_not_fed_back`: tentativas `adjudication`, `plan`, `analysis`, `crashed` e `provider_refused`, mais uma `generation`. O histórico é só a `generation`.
- `test_the_window_is_taken_before_the_patch_filter`: uma `build` seguida de três `adjudication`. O histórico fica vazio, o que documenta a escolha.
- `test_a_refusal_a_changed_check_can_affect_is_not_fed_back`: com `mock.patch.dict(knowledge.DRAFT_CHECK_CHANGES, {"X_CODE": {"changed_at": 2, "detail": r"the patch changes [A-Za-z_$][\w$]*\("}})`:
  - a tentativa sem carimbo perde o detalhe `the patch changes Ctor(a)` e a assinatura;
  - mantém `the patch changes Api.getInstance(a)`;
  - a carimbada com 2 mantém os dois;
  - a que fica vazia some.
- `test_a_recorded_attempt_carries_the_draft_check_rules_version`: `harvest_knowledge`.
- `test_a_seeded_store_holds_imports_and_no_attempts`: `seeded_store` copia `repositories`, deixa `findings` vazio e não sobrescreve um store existente.
- O teste existente `test_rename_failures_learned_under_older_rename_rules_are_not_fed_back` continua passando sem mudança. Se as tentativas dele não estiverem em `PATCH_STAGES`, ajustar `terminal_stage` para `build`.

**`tests/test_run_pipeline_commands.py`**

- `test_the_steps_read_the_learning_store_the_run_writes`.
- `test_without_the_option_the_environment_then_the_default_is_used`.
- `test_run_inputs_record_the_learning_store_and_history_rules`.
- `test_the_neutral_contact_title_is_passed_to_previews_and_issues`.

**`tests/test_experiment_manifest.py`**

- `test_the_manifest_records_the_history_rules_version`.

**`tests/test_contract_gates.py`, classe `StoredDerivationTests`**

- `test_a_constructor_fed_fresh_random_material_is_not_a_stored_derivation`: o caso do CometOTP, com repositório temporário contendo `generateRandom`.
- `test_material_filled_by_next_bytes_in_the_edit_is_fresh`.
- `test_a_constructor_that_also_takes_a_password_is_still_flagged`.
- `test_a_helper_that_derives_from_its_parameter_is_still_flagged`.
- `test_without_the_file_source_a_helper_call_is_not_exempt`.

**`tests/test_semantic_validation_coordination.py`**

- `test_a_test_the_review_drops_as_unrelated_is_recorded_and_its_assumptions_go`: o manifesto e o resultado trazem `dropped_unrelated_tests`, e as suposições do teste somem.
- `test_an_assumption_a_kept_file_also_made_stays`.

**`tests/test_finding_outcomes.py`**

- `test_the_drafts_own_record_of_dropped_tests_is_read_first`: `behavior_review_dropped_tests` devolve `dropped_unrelated_tests` mesmo sem `behavior_review.json`.

**`tests/test_issue_text_rewrite.py`**

- `test_a_draft_without_confirmed_security_impact_gets_the_neutral_contact_title`.
- `test_a_security_impact_draft_keeps_the_configured_title`.
- `test_a_constructor_or_lambda_is_named_by_its_outer_class`: `<init>`, `lambda$downloadModels$10`, `invokeSuspend` e `discoverySslContext$app`.
- `test_a_record_without_impact_class_gets_the_neutral_title`.

**`tests/test_detector_labels.py`, classe `ReportedByDetectorTests`**

- `test_the_adjudication_rule_says_the_record_is_never_cited_and_value_may_be_absent`.
- `test_the_adjudication_prompt_without_a_flagged_record_is_unchanged`.

**`tests/test_maintainer_responses.py`**

- Commit 4: `test_a_bot_comment_is_not_what_the_maintainer_said`. Um registro incorporado só com itens de `Copilot` fica listado com `maintainer_said` vazio.
- Commit 4: `test_a_policy_closure_is_read_from_the_maintainers_words_only`.
- Commit 5: `test_an_answer_within_the_limit_is_given_whole`, com o texto do Session#2241.
- Commit 5: `test_a_longer_answer_is_cut_after_a_sentence_and_marked`.
- Commit 5: `test_a_short_answer_is_unchanged`.

## 5. Checagem offline de aceitação

Os scripts ficam na pasta de rascunho do implementador e rodam no WSL com o venv. Os scripts `f*` e `s*` daqui são a referência.

| Script | O que faz | Esperado |
|---|---|---|
| `acc_history.py` | Importa o `finding_history` implementado e o aplica: (1) a cada chave do store; (2) a cada array `previous_attempts_of_this_finding` gravado, tratado como janela já tomada | Store: findings com histórico caem de 386 para 186. Pedidos: 504 mudam (124 de planejamento, 361 de arquivo, 19 de `shape_retry`), 125 perdem a seção e 2.755 ficam idênticos. 3 tentativas perdem a lição de construtor. Divergência máxima tolerada de ±3 pedidos, pelo filtro de renomeação aplicado antes da janela (M, `f02_history.py`) |
| `acc_history_bench.py` | Para as 12 chaves do experimento, compara byte a byte o `learning_context` antigo e o novo sobre uma cópia do store de produção | Só muda `previous_attempts_of_this_finding`; `verified_repository_imports` e as respostas de mantenedor ficam idênticos. Um resultado diferente é motivo para não fazer o merge |
| `acc_gate.py` | Refaz `dropped` com o código novo sobre os rascunhos gravados das 24 unidades | Dos 45 rascunhos reproduzidos, 29 com derivação removida: 3 isentos (todos do CometOTP v6) e 26 ainda marcados (M, `vfix_design/s06_sdvc_sim.py`) |
| `acc_records.py` | Nos 12 registros de PR com teste descartado pela revisão, suposições antes e depois | Saem as do rascunho descartado (2 no Winterhold v6). Nenhuma de arquivo commitado sai |
| `acc_titles.py` | Recalcula o título das 267 prévias | Lendo o campo do registro: 3 mantêm o título de segurança e 262 recebem o neutro (219 com o método, 30 com a classe por construtor, 13 com a classe por nome sintético) (M, `f03b_titles.py`). Por conferência, lendo o artefato de adjudicação, 54 teriam impacto confirmado (M, `vfix_design/s08_more.py`). Nenhum título neutro contém `$`, `<` ou `lambda` |
| `acc_adjudication.py` | Instruções dos pedidos de adjudicação gravados, contra as novas | Mudam 5, nos dois trechos. Os demais ficam idênticos |
| `acc_bot.py` (commit 4) | `_maintainer_response` antigo e novo sobre `data/pr_outcomes`, e contagem nos pedidos | Muda 1 resposta (DSub2000#160, que fica vazia). 420 pedidos de 94 unidades mudam (M, `f01_answers.py`, `f05_cut_requests.py`) |
| `acc_answers.py` (commit 5) | O mesmo, com o teto novo | 12 respostas mudam: 11 inteiras e 1 com a marca |

Findings movidas por balde, recalculando só o que é determinístico:

- **Dispensa** (falso positivo reconhecido, violação sem impacto de segurança):
  - commits 1 a 3: 0;
  - commit 4: não se calcula offline. Tira texto de bot de 79 pedidos de adjudicação; nada nesse texto é veredito do mantenedor;
  - commit 5: não se calcula offline. As 207 unidades que leram resposta cortada estão hoje em issue 71, PR pronto 51, sem impacto 50.
- **Issue:** 1 execução sai por construção (CometOTP v6, cuja única recusa deixa de existir).
- **Demais baldes:** 0.

Já rodado agora, sem a implementação: `f01_answers.py`, `f02_history.py`, `f03b_titles.py`, `f04_imports.py`, `f05_cut_requests.py`, `f06_d1_adj.py`, e as referências `vfix_design/s06_sdvc_sim.py`, `s08_more.py` e `s13_final_filter.py`. Os resultados são os da tabela.

## 6. Validação paga

A validação roda a partir do worktree `C:\Users\Antonow\Documents\tcc_v3_blockK`, antes do merge. Pré-checagens no WSL:

1. `pgrep -af "cognalys run"` vazio.
2. C: com 12 GB livres.
3. `sha256sum data/pipeline_learning/verified_knowledge.json` anotado.
4. `PYTHONPATH=/mnt/c/Users/Antonow/Documents/tcc_v3_blockK/src python -c "import cognalys; print(cognalys.__file__)"` aponta para o worktree.

**Parte A (commits 1 a 4).** O script `outputs/vfix_validation_20261110/run_isolated.sh` é cópia de `outputs/d1_validation_20261110/run_d1_validation.sh`, com estas diferenças:

- depois do `source`:

```bash
export PYTHONPATH=/mnt/c/Users/Antonow/Documents/tcc_v3_blockK/src COGNALYS_WORKSPACE=/mnt/c/Users/Antonow/Documents/tcc_v3
```

- o marcador passa a ser `.done_$v`;
- depois dos `sed`:

```bash
  python - "$d/run_$v/learning" <<'PY'
import sys
from cognalys.learning.knowledge import DEFAULT_LEARNING_DIR, seeded_store
seeded_store(DEFAULT_LEARNING_DIR, sys.argv[1])
PY
  printf '\n# An execution of its own: it reads no other run'"'"'s attempts and writes none to the shared store.\nlearning-dir = "%s"\n' "/mnt/c/Users/Antonow/Documents/tcc_v3/$d/run_$v/learning" >> "$d/run_$v.toml"
```

- o laço final:

```bash
for v in iso1 iso2; do
  for id in amethyst_gcm cometotp_fp herald_tls ibiswallet_tofu inputleaf_discovery intertune_hash melox_aes redreader_sha1 winterhold_salt; do one $id $v; done
done
for v in iso3 iso4; do one cometotp_fp $v; done
```

Comando:

```
wsl.exe bash -lc "cp /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/vfix_validation_20261110/run_isolated.sh /tmp/ && nohup bash /tmp/run_isolated.sh > /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/vfix_validation_20261110/driver.log 2>&1 &"
```

- **n:** 20 execuções.
- **Custo:** 18 × 0,193 + 2 × 0,146 ≈ US$ 3,8 (E). 0,193 é a média Flex medida no `noise_floor.md`; 0,146 é a média das três execuções do CometOTP na validação do D1. O toml mantém `max-cost = 10` por execução.

O que a parte A valida: o isolamento, a isenção do item 2, o filtro de bot e o comportamento com imports semeados. Ela **não** valida a regra de histórico sobre um store cheio, porque o store isolado não tem tentativas. Essa regra é validada offline por `acc_history.py` e `acc_history_bench.py`.

Aceitação:

1. **Isolamento.** Nenhum pedido de `run_iso*` leva `previous_attempts_of_this_finding`.
   - `run_inputs.json.learning_dir` aponta para o diretório da própria execução, e `learning_history_rules_version` é 2.
   - O sha256 do store compartilhado é o mesmo antes e depois.
   - Os pedidos do Melox, do RedReader e do Winterhold continuam levando `verified_repository_imports`.
2. **Findings que não planejam** (Amethyst, Herald, IbisWallet): desfechos dentro dos conjuntos atuais do `noise_floor.md`.
3. **CometOTP** (4 execuções):
   - adjudicação `confirmed` nas 4;
   - nenhuma recusa por `STORED_DERIVED_VALUE_CHANGED`;
   - nenhum `confirmed_cause` cita código de detector;
   - todo rascunho que terminar PR pronto é lido.
4. **As outras cinco:** os desfechos de `iso1` e `iso2` passam a formar o conjunto de ruído isolado.
   - Com n=2, isso não prova ausência de regressão.
   - Todo desfecho fora do conjunto antigo é lido e explicado.
   - Um build que falhe por import é contado à parte.
5. **Leitura.** Todo diff que virar PR pronto é lido. Onde a revisão descartar um teste, o manifesto traz `dropped_unrelated_tests` e o registro não traz suposição do teste.

Não fazer o merge se:

- alguma finding cair em balde de dispensa fora do conjunto atual;
- algum pedido de planejamento ou geração levar histórico;
- o store compartilhado mudar;
- `acc_history_bench.py` mostrar diferença fora de `previous_attempts_of_this_finding`;
- o RedReader gerar um rascunho que remove o SHA-1 sem caminho legado e a checagem não disparar.

**Parte B (commit 5, com o sim).** O mesmo script com `iso5` e `iso6` para as 9 findings, mais uma execução da unidade do Session em `outputs/vfix_validation_20261110/session_writeentry_b/`. Essa execução usa cópia do `run.toml` de `session_writeentry`, com `run-dir`, clones e worktrees novos e `learning-dir` semeado.

- **n:** 19 execuções.
- **Custo:** 18 × 0,193 + 0,175 ≈ US$ 3,6 (E).

Aceitação da parte B:

- As dispensas não sobem contra `iso1` e `iso2`.
- Toda mudança de desfecho em que a adjudicação, o plano ou a revisão cita texto do mantenedor além do caractere 400 é lida.
- O Amethyst continua fechando pelo IV do provedor.
- O Session cita a frase do #2241. Um desfecho "sem impacto" ali é o efeito pretendido e é relatado ao usuário.

Não fazer o merge do commit 5 se uma finding com impacto confirmado passar a dispensa citando a resposta de outro repositório.

## 7. Riscos, limites conhecidos e o que não fazer

- **Lições que somem.** 200 findings do store deixam de ter histórico em prompts, e 3 tentativas perdem a lição de construtor, 2 delas corretas (SMSecure). O custo é no máximo um rascunho a mais numa rerun, cerca de US$ 0,02 (E, pelo custo de geração por chamada).
- **Janela antes do filtro.** Uma finding com três paradas recentes na adjudicação não vê uma lição de build mais antiga. Isso é escolhido: o filtro antes da janela traria de volta 19 lições do pipeline de outubro em 17 findings (M, `vfix_review/r02_window.py`).
- **Colisão de `run`.** Tentativas continuam identificadas por `run_dir.name`. O store isolado elimina o caso medido.
- **Imports semeados.** Parte deles foi gravada por execuções irmãs do benchmark. São fatos de build, iguais para todas as execuções isoladas, e o hash do store semeado de cada execução fica no manifesto.
- **Isenção do item 2.** É léxica e conservadora: só um nível de função auxiliar, no mesmo arquivo.
- **Bot.** A lista de bots continua por nome. Um bot desconhecido sem o sufixo `[bot]` ainda passaria. O papel de `ibrahim-iqbal` no Feeder#1210 não foi verificado.
- **Teste mantido num redraft** com nome ou comentário do rascunho trocado (Session): fica para o A1.
- **Commit 5.** Não prevê a direção do efeito.

Não fazer:

- triagem pelo rótulo `random-key-material`;
- tirar `SecretKeySpec` de `_DERIVATION_CALL`;
- filtrar respostas pelo dono do repositório;
- usar a contagem de `init(null)` como portão;
- apagar à mão o store de aprendizado;
- aposentar por versão as lições de `getInstance`.

## 8. Passos de implementação

Todos os estágios rodam com Opus 5.5. A ordem geral é: commits 1 a 4, parte A, merge; commit 5 com a parte B se houver sim; depois Q1, Q2Q3, K3, run congelada e A1. Toda réplica dos itens seguintes usa `learning-dir` semeado e compara com os conjuntos isolados.

| # | Passo | Estágio | Depende de / arquivo compartilhado |
|---|---|---|---|
| 1 | `knowledge.py`: `PATCH_STAGES`, `HISTORY_RULES_VERSION`, `DRAFT_CHECK_*`, `_without_stale_check_signals`, filtro em `finding_history`, `seeded_store` | implementar | — |
| 2 | `retrospective.py`: `PROPOSED_STAGES` importado, carimbo em `harvest_knowledge` | implementar | 1 |
| 3 | `run_pipeline.py`: `resolved_learning_dir`, exportação, `run_inputs.json`; `experiment_manifest.py`; GUIA | implementar | `run_pipeline.py` também muda no K3 |
| 4 | Testes do commit 1; `acc_history.py`, `acc_history_bench.py` | implementar / checagem offline | 1 a 3 |
| 5 | `contract_gates.py`: isenção; versão 2 em `knowledge.py`; testes; `acc_gate.py` | implementar / checagem offline | 1 |
| 6 | `coordinated_generation.py` e `finding_processing.py` (item 4) | implementar | o Q2Q3 e o A1 mexem nos mesmos métodos; este passo não muda `REVIEW_CONTRACT_VERSION` |
| 7 | Título da issue de contato (`issue_text.py`, `impact_class.py`, `run_pipeline.py`, prévias, `issue_creator.py`); regenerar as fixtures douradas pelo teste | implementar | — |
| 8 | Frase do log (`pr_generator.py`) e GUIA | implementar | — |
| 9 | Frase da adjudicação e versão 7 | implementar | o Q1 muda o mesmo prompt e passa a versão a 8 |
| 10 | Testes do commit 3; `acc_records.py`, `acc_titles.py`, `acc_adjudication.py` | implementar / checagem offline | 6 a 9 |
| 11 | Filtro de bot (`pr_github.py`, `pr_outcomes.py`), testes, `acc_bot.py` | implementar / checagem offline | o DL3r muda o mesmo arquivo depois |
| 12 | Suíte completa (`python -m pytest -q tests`, com `PYTHONPATH=src` no worktree) | suíte | 1 a 11 |
| 13 | Revisão adversarial dos commits 1 a 4 (correção, segurança de decisão, generalidade) | revisão adversarial | 12 |
| 14 | Parte A a partir do worktree | validação paga | 13 |
| 15 | Leitura da parte A; merge e push só se nenhuma condição de "não fazer o merge" ocorreu; parágrafo novo no `noise_floor.md` | revisão adversarial | 14 |
| 16 | Commit 5, testes, `acc_answers.py`, suíte, revisão | implementar / suíte / revisão adversarial | sim do usuário; 15 |
| 17 | Parte B a partir do worktree; merge do commit 5 só se aprovado | validação paga | 16 |

Parágrafo do `noise_floor.md` (fora do git), no passo 15:

> Independência (2026-11-10, medido nos pedidos gravados). As 35 execuções do benchmark que chegaram ao planejamento leram `previous_attempts_of_this_finding` de outras execuções da mesma finding. Os conjuntos de Input Leaf, InterTune, Melox, RedReader e Winterhold, e o "2 de 3 PR pronto" do CometOTP, não são amostras independentes depois da adjudicação. As réplicas isoladas (`run_iso1`, `run_iso2`, store semeado só com imports verificados) passam a ser a referência.

Mensagens de commit:

1. `Planning and generation read only the earlier attempts of a finding that went on to a patch (knowledge.PATCH_STAGES, the set retrospective.PROPOSED_STAGES already named): the window of three failures and three validated drafts is taken as before and an attempt in it that stopped at the adjudication or at the plan, crashed, was refused by the provider or could not be analysed is left out of previous_attempts_of_this_finding. Each recorded attempt carries draft_check_rules_version, and a refusal a later draft-check change can affect (DRAFT_CHECK_CHANGES: its code and the detail pattern of what changed) is not fed back. --learning-dir now also decides where the steps read the store (TCC_LEARNING_DIR is exported; it was only where the retrospective wrote), run_inputs.json and the experiment manifest record it with HISTORY_RULES_VERSION 2, and knowledge.seeded_store starts a store holding only the shared store's verified imports for executions compared with each other. Offline, over every recorded request under outputs (3,259 planning and generation requests, 1,447 with a history): 504 requests of 114 units change, 125 lose the section, 2,755 are byte-identical; every research, extraction, adjudication, review and text request is unchanged. All 35 baseline executions that reached planning had read sibling executions. Full suite: <N> passed.`
2. `STORED_DERIVED_VALUE_CHANGED no longer refuses a patch for dropping a constructor whose only inputs are an algorithm name and freshly generated random material (a SecureRandom-filled array, a generated key, or the result of a same-file helper that returns one): nothing stored was computed from those inputs. A constructor that also takes a password, a constant or an expression is flagged as before. DRAFT_CHECK_RULES_VERSION 2 retires only the recorded refusals that name a constructor (3 stored attempts); the 15 naming a getInstance factory stand. Offline, over the 24 units and 78 recorded firings: 23 units derive from stored or user input and are unchanged; of the 29 recorded drafts the predicate can be replayed on, the 3 of CometOTP run_v6 pass and the other 26 are still refused. Full suite: <N> passed.`
3. `A test the behaviour review drops as unrelated is recorded (manifest and result dropped_unrelated_tests, read first by behavior_review_dropped_tests, and a log line), and the assumptions only a dropped test's draft made leave the result on every path that drops a generated test (9 recorded pull-request records carried such assumptions). The contact-first issue of a pull-request draft is titled "Potential security issue found" only when the record's impact_class is security_impact; any other draft gets --issue-title-without-security-impact, naming the method or, for a constructor, lambda or mangled name, its outer class (prs.impact_class's docstring now says the title reads it). The adjudication sentence for reported_by_detector says value is present only when the record has one and that the record is never cited as evidence (ADJUDICATION_PROMPT_VERSION 7; 5 recorded requests change, every other is byte-identical). The publication log says a review-only draft was kept by the verdict after validation, and GUIA_DE_USO tells publication_allowed from publishable. Full suite: <N> passed.`
4. `A review bot's comments are no longer given to the prompts as what a maintainer said: "copilot" joins KNOWN_BOT_ACCOUNTS, pr_github.is_bot_login reads a recorded login, and pr_outcomes reads maintainer_said and policy closures from the non-bot feedback only. paroj/DSub2000#160's four Copilot review comments reached 420 recorded requests of 94 units (79 adjudications) as the merged answer for every other repository's SSLContextSpec finding; the merge is still listed, with no words. Full suite: <N> passed.`
5. `A maintainer's answer reaches the prompts whole up to 2,000 characters, cut after a sentence and marked "[...]" beyond that (it was cut at 400, mid-word): 12 of the 33 recorded answers with a maintainer's words were cut, among them session-android#2241 before "We'll hold off merging for now since it doesn't change anything functionally", and 1,214 recorded requests of 207 units carried a cut answer. No prompt sentence changes. Full suite: <N> passed.`

## 9. Perguntas ao usuário

1. **Resposta do mantenedor inteira antes da run congelada (commit 5)?**
   - Hoje, sem o bot, 12 de 33 respostas chegam cortadas, e 82% das unidades que recebem respostas leem ao menos uma cortada.
   - Custa cerca de US$ 0,001 por finding afetada em Flex (E).
   - O efeito provável é menos PR só de regra onde o mantenedor já recusou esse tipo de mudança.
   - O filtro de bot (commit 4) entra de qualquer modo.
   - **Recomendo sim**, com a parte B (US$ 3,6, E) antes do merge.

2. **Run congelada sem histórico ou com o histórico atual?** As três opções:
   - **Sem histórico:** as 46 findings usam `learning-dir` semeado por `seeded_store`. Mantêm os imports verificados e não leem tentativas. É comparável aos agentes, e o artigo declara a escolha.
   - **Totalmente vazio:** perde também os imports. 60 dos 237 pedidos do benchmark perderiam o que hoje levam (M).
   - **Com o histórico atual:** as 12 findings do experimento leriam de 1 a 6 execuções anteriores cada, 7 delas com o título de um rascunho já validado (M, `vfix_design/s12_cohort.py`). Nesse caso, as tentativas do benchmark e da validação do D1 precisam sair do store antes, por um comando a especificar.
   - **Recomendo sem histórico, com os imports semeados.**

3. **Regra publicada do SecretKeySpec.** Não refiz a verificação; os fatos abaixo vêm da primeira versão e foram conferidos pelo revisor adversarial:
   - O checkout do RVSec (`/home/antonow/external/rvsec`, 37efe5e, 2026-10-11) transcreve a revisão 6d844ab, que só admite `preparedKeyMaterial`. `SecretKeySpecSpec.mop:146-148` emite NOBS-03, com `val=''` e `exp='prepared key material'`, quando o material é `RANDOMIZED`.
   - A regra upstream admite `preparedKeyMaterial || randomized` desde o commit db186ff, de 2026-04-02. O `ls-remote` dá master em d4440624.
   - `data/specifications/SecretKeySpecSpec.json` admite as duas. O `spec-fidelity` relata a divergência: 2 linhas importadas e 9 registros analisados (6 falso positivo, 2 PR pronto, 1 issue).

   O que isso significa:
   - O código original do CometOTP cumpre a regra atual. A finding existe porque o analisador está numa revisão antiga.
   - A premissa do D1 continua verdadeira como fato sobre o que o analisador checa. Ela não diz se vale mandar um PR para cumprir um requisito que os autores da regra retiraram.

   Recomendo:
   - não publicar os rascunhos do CometOTP nem o do Session;
   - avisar o RVSec para atualizar a transcrição;
   - manter o D1;
   - decidir depois se o registro de PR deve levar uma nota quando o `spec-fidelity` apontar "a especificação admite, o analisador rejeita" para o código da finding.

## Apêndice: Revisão, o que mudou

| # | Ponto | Decisão | Motivo |
|---|---|---|---|
| 1 | Merge antes da validação | Aceito | O merge virou o passo 15, depois da leitura da parte A. A validação roda do worktree com `PYTHONPATH` e `COGNALYS_WORKSPACE` |
| 2 | O isolamento tira os imports verificados | Aceito | Confirmado: 60 de 237 pedidos (M, `f04_imports.py`). Correção por `seeded_store`, que isola as tentativas e mantém os imports fixos |
| 3 | Copilot como mantenedor | Aceito em parte | Confirmado e medido: 420 pedidos de 94 unidades. Virou o commit 4, independente do sim. O filtro pelo dono foi rejeitado: perderia Session#2241 e openhab#4039 (organizações). Recontagem: 12 cortadas de 33 |
| 4 | Aposentar todas as lições de derivação guardada | Aceito | Agora só se aposentam detalhes que nomeiam um construtor (padrão em `DRAFT_CHECK_CHANGES`): 3 tentativas, 2 delas lições corretas do SMSecure, ditas em §7 |
| 5 | A simulação não implementa a regra | Aceito | Regra trocada para a medida (janela antes, filtro depois). Números refeitos: 504/125/2.755, store 386 para 186. O efeito de staleness ficou registrado |
| 6 | A parte A não valida o commit 1 em produção | Aceito | Escopo da parte A declarado. Acrescentado `acc_history_bench.py` como condição de merge |
| 7 | `impact_class` era "report only"; nomes sintéticos no título | Aceito | Docstring trocada no mesmo commit; `levantar_prs.py` não é afetado. `{location}` usa a classe em 13 de 262 casos. Achado novo: 255 registros não têm o campo |
| 8 | `behavior_review_dropped_tests` já registra o motivo | Aceito | Sem frase nova; o leitor prefere `dropped_unrelated_tests` |
| 9 | Sem carimbo da regra de histórico | Aceito | `HISTORY_RULES_VERSION` no `run_inputs.json` e no manifesto |
| 10 | Frase que pressupõe evidência | Aceito | Texto trocado. Os 5 pedidos gravados citam SRC (M, `f06_d1_adj.py`) |
| 11 | `reported_call_shapes` | Aceito | O Q2Q3 mede quantos registros o fato marcaria |
| 12 | Rótulos de estágio com Fable | Aceito | Tudo com Opus 5.5 |
| 13 | Dono do rascunho vazio do Session | Aceito | DL6r parte 2, no mesmo passo do plano que o Q1 partes 2 a 4, fora da regra de promoção |

O que continua sem verificação:

- O `ls-remote` e o commit db186ff da regra upstream não foram refeitos.
- O papel de `ibrahim-iqbal` no Feeder#1210.
- `acc_records.py`, `acc_bot.py`, `acc_answers.py`, `acc_history_bench.py` e `acc_adjudication.py` dependem da implementação. Os números deles saem dos scripts de referência, não da função implementada.
- A divergência de ±3 pedidos do filtro de renomeação foi estimada, não medida.