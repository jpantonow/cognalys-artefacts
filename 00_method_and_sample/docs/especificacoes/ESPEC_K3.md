> Especificação de K3, escrita em 2026-11-10 para ser implementada por uma sessão Opus 5.5 seguindo docs/PLANO_FABLE.md (desenho e revisão adversarial no Fable 5.2; versão final no Opus 5.5). Os scripts citados ficam em `outputs/design_20261110/scratch/` (não versionado; arquivos acima de 2 MB não foram copiados, e os caches grandes são refeitos pelos próprios scripts). As perguntas da seção 9 esperam a resposta do usuário antes da implementação das partes que dependem delas.

# K3: corpus de pesquisa por escopo (W1) e taxa de busca no guard (P0), especificação para implementação (versão corrigida)

Ponto de partida: HEAD `af71cfe` em `melhorias-pipeline-28-09`. Tudo aqui foi medido offline. Nada em `tcc_v3` nem no worktree foi editado e nenhuma chamada paga foi feita.

Os scripts estão em três pastas, todas sob `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\`:
- `k3_design\`: medidas da primeira versão.
- `k3_review\`: caches refeitos pela revisão, com 4.029 traces de 560 pastas de log. São a base dos números abaixo.
- `k3_final\`: medidas desta correção (`frozen_cohort.py`, `frozen_cohort2.py`, `rerun_review.sh`).

Para rodar: `wsl.exe bash <pasta>/run.sh <script.py>`, ou `wsl.exe bash k3_final/rerun_review.sh`. A marca (M) indica número medido por script; (E) indica estimativa, com a base dela.

## 1. Resumo

**O que cada sigla é (PLANO_CUSTO §2 e §4)**
- **W1.** A parte "shared" da pesquisa externa (as perguntas sobre a API governada e a regra, sem a finding) deixa de ser refeita a cada execução. A resposta fica guardada por escopo num corpus que persiste entre runs e não vence em 7 dias.
- **W3.** Uma chamada de pesquisa em vez de duas: a síntese em JSON sai e o próprio modelo da busca escreve o JSON.
- **W4.** Trocar `web_search_preview` por `web_search`, com allowlist de domínios, `include` das fontes e `max_tool_calls: 8`.
- **P0.** Preços corretos no `cost.py` e no guard do `--max-cost`: escrita em cache a 1,25x, `service_tier` no trace e taxa de US$ 0,01 por busca.

**O que entra**

| Item | Decisão | Número que decide |
|---|---|---|
| W1 | Entra, desligado por padrão (`--llm-research-corpus-dir`). A chave é o próprio pedido. Preenche no miss, e só com resposta completa. Há importação das respostas já gravadas. | A pesquisa shared mais a síntese custam US$ 0,0566 por finding em v4+v5 como cobrado, e 0,0540 com tudo em Flex (M, `k3_review/scopes.py`). Com o corpus pronto, 9 de 9 findings são servidas em v4, v5 e v6 (M, `simulate.py`). O custo fica em 0,136 como cobrado (1,10x o Sonnet) ou 0,133 com tudo em Flex (E: chamadas medidas removidas). Isso fecha 82% da diferença de 0,0689. |
| P0 | Já está implementado (`0b0ee65`). Não há nada a codar. Falta a conferência com o painel, que é do usuário (§9). | Taxa em v4+v5: 2,11 buscas × 0,01 = 0,0211 por finding na parte shared e 0,0028 na parte do caso (M, `scopes.py`). |
| Registro da ferramenta de busca no trace (`web_search_tool`) | Entra. É a única parte do W4 que entra. | Sem esse registro, uma troca futura de ferramenta não invalidaria o corpus. |
| Corpus no manifesto do experimento e nas tabelas do artigo | Entra. | Sem isso, duas runs congeladas com corpora diferentes "batem" (`experiment_manifest.py:208-226`), e a tabela de custo conta um hit do corpus entre as chamadas sem separá-lo (`paper_tables.py:124-157`, `:296-315`). |

**O que é cortado ou adiado**

| Item | Decisão | Motivo |
|---|---|---|
| W3 | Cortado de antes da run congelada. | Depois do W1 sobra só a síntese do caso, 0,0017 por finding (M), abaixo do piso de 0,005. Exige sondagem paga, muda quem escreve o JSON e obrigaria a refazer o corpus. |
| W4 (ferramenta nova, allowlist, `max_tool_calls`) | Cortado de antes da run congelada. | A economia é perto de zero (PLANO_CUSTO §4) e não há checagem offline. A allowlist corta o que a pesquisa pode ler, o que é corte de pesquisa. |
| Comando `fill` ao vivo | Cortado. | O preenchimento no miss e a importação cobrem o caso pelo caminho real do código. |
| Corpus com 2 amostras (união) | Cortado, como já estava em PLANO_CUSTO §9. | |

**O que precisa do sim do usuário (detalhes na §9)**
1. **Idade máxima de 90 dias com o corpus ligado.** Hoje o cache de pesquisa vale 7 dias. A entrada gravada mais velha tem 21 dias (M). As entradas da sombra terão de 5 a 8 dias, então nada aqui mede idades acima de 21 dias.
2. **Ligar o corpus na fila de produção.** O ganho ali é quase zero: dos 41 passes shared ao vivo da fila de novembro, 2 seriam servidos (M, `k3_review/simulate.py`).
3. **Conferência do P0 no painel.** Em 2026-11-09 foram 53 ações `search` (US$ 0,53) contra 93 itens (US$ 0,93). Em 11-10, 30 (US$ 0,30) contra 50 (US$ 0,50) (M, `fee_days.py`).
4. **A run congelada roda sem `--experiment-manifest`?** Com o manifesto, `TCC_FROZEN_EXPERIMENT` faz todo miss de pesquisa voltar vazio (`llm_providers.py:2441-2451`). Com `no-llm-cache`, a parte do caso nunca é pesquisada, com ou sem K3.

**Findings movidas para dispensa pela mudança: 0** em todos os baldes. Nenhuma decisão gravada é recalculada e nenhum prompt muda. A exposição medida está na §5.

**A linha entre reaproveitar e pesquisar menos**
- **Permitido:** servir a resposta gravada de um pedido idêntico byte a byte. "Idêntico" significa mesmas perguntas, regra, política, modelo, esforço, instruções, schema e ferramenta, e a resposta tem de ter vindo de dois passes completos.
- **Não permitido, e o desenho não faz:**
  - casar escopos "parecidos" ou agrupar por especificação;
  - servir a parte do caso;
  - servir entrada feita com outro esforço;
  - baixar esforço, contexto de busca ou número de ações;
  - pular a pesquisa num miss. Todo miss pesquisa por inteiro, no esforço da run.

## 2. Diagnóstico medido

**Causa no código**
- **Duas chamadas por pedido.** Um pedido de pesquisa faz `.research`, com web search, e depois `.synthesis`, em JSON (`src/cognalys/llm/llm_providers.py:2350-2392`). Cada chamada fica só no cache de respostas: a pesquisa com TTL (`:2412-2430`, `:2868-2869`), a síntese sem TTL.
- **O benchmark desliga esse cache.** `--no-llm-cache` zera o `cache_dir` (`generate_error_fixes.py:1272-1274`). O benchmark roda com `no-llm-cache = true` e `llm-web-search-cache-ttl-hours = 0` (`outputs/agent_baseline_20261108/cognalys/*/run_v5.toml`), então paga a parte shared toda vez.
- **A parte shared já é independente da finding** (`finding_adjudication.py:481-516`). O pedido leva `question_scope` (com `rule_sha256`, `research_questions.py:277-306`), as perguntas, `spec_policy` e `reported_by_rule`. O system prompt é fixo (`:524-537`) e há um único ponto de chamada (`:546-559`).
- **Uma resposta incompleta volta como texto.** `:2866-2875` devolve `output_text` qualquer que seja `data["status"]`. Só o memo de replay exige `completed` (`:2870-2873`). O trace de uma resposta `incomplete` tem `status: "success"` (`:2814`) e `response_status` à parte (`:2841`). Na Anthropic, `stop_reason == "max_tokens"` só gera aviso (`:3342-3346`). `docs/GUIA_DE_USO.md:592-593` diz que resposta cortada nunca é reaproveitada, e o corpus tem de seguir essa regra.
- **O memo de replay é por chamada** (`:705-760`, `:2244-2269`). Um hit do corpus feito no nível dos dois passes não entraria nele, então um replano poderia ler outra resposta shared.
- **O guard já cobra a taxa de busca:**
  - `cost.py:50-63` e `:187-201`;
  - `llm_providers.py:2792-2812`;
  - o guard refeito a partir dos traces (`cost.py:600-619`);
  - `data/llm_prices.json`.
- **A loja de aprendizado vale para a run inteira.** `fix_generation_common.py:126` chama `load_knowledge()`, que lê `TCC_LEARNING_DIR` ou `data/pipeline_learning` (`learning/knowledge.py:40-44`). A retrospectiva escreve no `--learning-dir` (`run_pipeline.py:359-363`, `:1970-1972`). Duas runs em sequência não são independentes depois da adjudicação (`noise_floor.md`, CometOTP v7/v8).

**Contagens sobre todos os registros** (caches da revisão: 560 pastas de log, 4.029 traces, de 2026-10-19 a 11-10)

*Passes shared, chaves e escopos*
- **Passes shared:** 606, com 191 amostras frias completas (M, `scopes.py`).
- **Resposta completa:** todos os 193 passes de pesquisa shared pagos e as 192 sínteses shared pagas têm `response_status == "completed"` (M, `status_scan.py`). Exigir passe completo rejeita 0 dos registros. O buraco existe no código, mas nenhum registro o atinge.
- **Chaves:** 119 chaves distintas. 15 têm 2 ou mais amostras frias, somando 88 amostras (M).
- **Escopos ao longo do tempo:** 55 pares (especificação, mensagem). 33 tiveram mais de uma chave: 17 por modelo, 8 por modelo e esforço, 3 por esforço e 5 só pelo texto (M, `k3_design/scopes.py`).
- **Escopo e chave nas runs recentes:** desde 2026-11-08, cada escopo (especificação, tipo, mensagem) tem uma chave só (8 escopos, 0 com duas; M, `k3_final/frozen_cohort2.py`).
- **Benchmark:** as 9 findings usam 7 chaves, idênticas de `run` a `run_v8` (2026-11-08 a 11-10; M). Duas chaves são lidas por duas findings cada: `c64073be` (IbisWallet e Input Leaf) e `6128fe99` (InterTune e RedReader). Se só o benchmark preencher, 2 de 9 são servidas (M).

*Custo por finding em v4+v5, como cobrado (M)*

| Chamada | US$ |
|---|---|
| Pesquisa shared | 0,0470 (taxa: 0,0211) |
| Síntese shared | 0,0096 |
| Pesquisa do caso | 0,0069 |
| Síntese do caso | 0,0017 |

No v6, shared mais síntese somam 0,0606.

*Ganho por população*
- **Produção:** a fila de novembro teve 41 passes ao vivo, dos quais 2 seriam servidos. O benchmark com corpus pronto tem 71 de 71 servidos (M, `simulate.py`).
- **Coorte da run congelada:** 34 findings consolidadas (as de `exp_final_20261019`) e as 12 do benchmark dos agentes (M, `frozen_cohort2.py`).
  - 43 têm passe shared gravado. `cometotp_unwrap`, `trilium_kmf` e `notallyx_md5` não têm.
  - As 43 caem em 23 escopos distintos.
  - Se só a run congelada preencher o corpus, 20 de 43 são servidas.
  - Se a importação dos registros desde 11-08 valer (pedido shared igual no commit congelado), 7 escopos cobrem 19 findings (10 consolidadas e 9 do benchmark), e no total 27 de 43 são servidas.

*Provas de que a importação reproduz o que a run calcularia*
- O `merge_research_parts` da biblioteca refaz 503 de 503 `*_external_research.json` a partir das sínteses (M, `identity.py`).
- Em 1.929 de 1.929 passes gravados valem três igualdades (M, `prompt_identity.py`):
  - instruções da pesquisa = system + constante;
  - entrada da síntese = prompt + cabeçalho + resposta + constante;
  - schema = `external_research_schema()` de hoje.
- As constantes e a ferramenta OpenAI não mudaram no histórico do arquivo. A ferramenta da Anthropic aparece em `de1c4d3` (2026-10-28), e os 34 traces de pesquisa Anthropic gravados estão como `prepared`, então nenhum é importável (M, `git log -S`, `status_scan.py`).

*Forma das amostras e quem as cita*
- Nas amostras frias o mínimo é 3 fontes e 7 fatos; nenhuma vem vazia.
- O Jaccard de páginas entre amostras da mesma chave tem média 0,57 e mínimo 0,30 (M, `samples.py`).
- 149 unidades terminam em falso positivo, sem impacto ou NO_SAFE_FIX. 32 delas têm um predicado decidido que cita fonte WEB shared, e 0 se apoiam só nela. Entre as outras 352 unidades, 5 têm um predicado apoiado só em WEB shared (M, `pred_gap.py`).

## 3. Mudanças

**O que não muda:** nenhuma frase de prompt, nenhum schema de resposta do modelo e nenhum arquivo em `data/`. `CONTEXT_BUILDER_VERSION`, `ADJUDICATION_PROMPT_VERSION`, `trace_version` e `MANIFEST_VERSION` ficam como estão.

**Constante nova:** `FORMAT_VERSION = 1` em `research_corpus.py`.

### 3.1 `src/cognalys/llm/research_corpus.py` (novo, módulo folha)

Importa só `cognalys.llm.cost` e a biblioteca padrão, nunca `llm_providers`.

```python
FORMAT_VERSION = 1
SUPERSEDED_DIRECTORY = "superseded"
MEMO_PREFIX = "research_corpus:"
# The two passes of a research request (generate with json_mode and web_search).
RESEARCH_PASS_INSTRUCTIONS = (
    "\n\nFor this research pass, use web search as needed. "
    "Return a factual research brief and include direct source "
    "URLs in the text. Do not omit uncertainty or source limits."
)
SYNTHESIS_RESULT_HEADER = "\n\nWEB RESEARCH RESULT:\n"
SYNTHESIS_PASS_INSTRUCTIONS = (
    "\n\nProduce the originally requested JSON using the "
    "research above as additional evidence. Preserve direct "
    "source URLs and do not add claims unsupported by either "
    "the original input or the research result."
)
IDENTITY_FIELDS = ("provider", "endpoint", "model", "reasoning_effort", "max_output_tokens",
                   "system_prompt", "prompt", "json_schema", "schema_name",
                   "research_instructions", "synthesis_header", "synthesis_instructions",
                   "web_search_tool")
# The tool a provider sent before a research call's trace recorded it (request
# field "web_search_tool"). OpenAI's is unchanged in the file's history; the
# Anthropic tool appeared in de1c4d3 (2026-10-28), and no Anthropic research
# pass recorded before the field was completed.
TOOL_BEFORE_TRACE_FIELD = {
    "openai": {"type": "web_search_preview", "search_context_size": "medium"},
    "anthropic": {"type": "web_search_20260209", "name": "web_search"},
}
# Stop reasons of an Anthropic answer that ended on its own.
ANTHROPIC_COMPLETE_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})
```

**Funções**

- `canonical(value) -> str`: `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`.
- `request_identity(**fields) -> dict`: exige exatamente `IDENTITY_FIELDS` e devolve `json.loads(canonical(fields))`. A ida e volta por JSON faz tuplas virarem listas, de modo que a identidade calculada em tempo de execução e a lida do disco são iguais.
- `entry_key(identity) -> str`: sha256 de `canonical({"format_version": FORMAT_VERSION, "request": identity})`.
- `pass_completed(metadata: dict) -> bool`: verdadeiro só nestes casos.
  - `delivery == "sent_to_openai"`, `response_status == "completed"` e sem `degenerate_output_stopped`.
  - `delivery == "sent_to_anthropic"` e `stop_reason in ANTHROPIC_COMPLETE_STOP_REASONS`.
  - `delivery in {"cache_hit", "finding_replay"}`. Essas respostas já foram entregues como resposta. Elas contam como completas para o memo da finding, nunca para gravar no corpus.
- `storable(response: str) -> bool`: verdadeiro só se o texto é um objeto JSON cuja lista `sources` tem ao menos um objeto com `url` não vazio. É uma checagem de forma, não de conteúdo. Rejeita 0 das 184 amostras frias (M).
- `recorded_response_text(record: dict) -> str`: o texto de um `*_response.json`, na mesma regra do provedor (`llm_providers.py:2866-2881`).
  - Usa `response.output_text` quando não vazio.
  - Senão, junta com `"\n"` os `output[].content[]` de tipo `output_text`.
  - Senão (forma Anthropic), junta os `content[]` de tipo `text` e aplica `strip()`.
- `recorded_pass_identity(research_request, synthesis_request, research_metadata) -> Optional[tuple[dict, str]]`: devolve `(identidade, research_text)` ou `None`.
  - **Igualdades exigidas** (senão devolve `None`):
    - `research_request["instructions"] == synthesis_request["instructions"] + RESEARCH_PASS_INSTRUCTIONS`;
    - `synthesis_request["input"]` começa com `research_request["input"] + SYNTHESIS_RESULT_HEADER` e termina com `SYNTHESIS_PASS_INSTRUCTIONS`;
    - `endpoint`, `model`, `reasoning_effort` e `max_output_tokens` são iguais nos dois pedidos.
  - **De onde vem cada campo da identidade:**

    | Campo | Origem |
    |---|---|
    | `provider` | `research_metadata["delivery"]`: `sent_to_openai` vira `"openai"`, `sent_to_anthropic` vira `"anthropic"`; outro valor devolve `None` |
    | `endpoint`, `model`, `reasoning_effort`, `max_output_tokens` | `research_request` |
    | `system_prompt` | `synthesis_request["instructions"]` |
    | `prompt` | `research_request["input"]` |
    | `json_schema`, `schema_name` | `synthesis_request` |
    | `research_instructions`, `synthesis_header`, `synthesis_instructions` | as três constantes |
    | `web_search_tool` | `research_request.get("web_search_tool")` ou `TOOL_BEFORE_TRACE_FIELD[provider]` |

  - **`research_text`:** o trecho de `synthesis_request["input"]` entre o prefixo e o sufixo. Assim o texto é o que a síntese leu de fato.
- `corpus_digest(directory) -> dict`: `{"path": str(directory), "entries": n, "sha256": ...}`. O sha256 é calculado sobre as linhas `"<key>:<response_sha256>\n"` das entradas válidas, em ordem de chave. Num diretório ausente devolve `{"path": ..., "entries": 0, "sha256": ""}`.

**`class ResearchCorpus(directory, max_age_days=90.0, logger=None)`**

- **`valid(entry, identity) -> bool`**: um predicado único, usado por `get` e por `put`. Exige todas as condições:
  - `format_version == 1`;
  - `entry["key"] == entry_key(identity)`;
  - `canonical(entry["request"]) == canonical(identity)`;
  - `response` não vazio e `sha256(response) == response_sha256`;
  - quando `max_age_days > 0`, idade de `created_at` dentro do limite. Um `created_at` ilegível conta como vencido, como `LLMResponseCache._expired` (`llm_providers.py:835-852`).
- **`get(identity) -> Optional[dict]`**:
  - lê `<directory>/<entry_key>.json` e devolve a entrada se `valid`;
  - senão devolve `None` e conta `misses` (e `expired` quando a causa é a idade).
- **`put(identity, scope, response, research_text, provenance, origin="run", created_at=None, replace=False) -> str`**:
  - devolve `"refused"` se `not storable(response)`;
  - devolve `"kept"` se a entrada existente é `valid` e `replace` é falso. Uma run nunca troca a amostra;
  - senão, move a entrada antiga para `superseded/<key>_<created_at em %Y%m%dT%H%M%S>.json`, escreve a nova num arquivo temporário seguido de `os.replace` e devolve `"stored"`;
  - `created_at` vale `datetime.now().isoformat(timespec="seconds")` por padrão (hora local sem fuso, como nos traces). A idade é calculada com o mesmo relógio.
- **`stats()`**: `{"hits", "misses", "expired", "stored", "kept", "refused"}`.

**`import_runs(directory, paths, replace=False, dry_run=False) -> dict`**

- **Caminhos aceitos:** um diretório de run (via `cost.run_log_dir`) ou uma pasta que já contém `*_metadata.json`. Um caminho que não existe é erro de argumento.
- **Pareamento:** os pares `.research`/`.synthesis` são casados por `parent_call_id`.
- **Um passe só é importado quando todas as condições valem.** Cada falha conta no motivo entre parênteses:
  - `context.stage` do pedido é `"external_research.shared"` (`not_shared`);
  - a pesquisa tem `status == "success"` e `delivery in cost.PAID_DELIVERIES` (`not_paid`);
  - a síntese tem `status == "success"` (`failed`);
  - `pass_completed` é verdadeiro nos dois metadados (`not_completed`);
  - `recorded_pass_identity` não é `None` (`identity_mismatch`);
  - a resposta da síntese é `storable` (`not_storable`).
- **Uma amostra por chave:** fica o passe de `completed_at` mais recente (regra cega ao conteúdo), considerando todos os caminhos de uma mesma chamada.
- **Datas e origem:** `created_at` é o `completed_at` da síntese; `origin` é `"import"`.
- **Retorno:** `{"runs", "passes_seen", "passes_importable", "keys", "stored", "kept", "skipped": {reason: n}}`.

**`listing(directory) -> list[dict]`**: para cada entrada, `key`, `created_at`, `age_days`, `origin`, `scope`, `sources` (contagem) e `cost_usd`.

**Formato da entrada** (`<key>.json`, indentado):

```json
{"format_version": 1, "key": "<sha256>", "created_at": "2026-11-10T08:14:02",
 "origin": "run", "scope": {"kind": "governed_api_and_rule", "specification": "...", "...": "..."},
 "request": {"provider": "openai", "endpoint": "...", "model": "...", "reasoning_effort": "high",
             "max_output_tokens": 32768, "system_prompt": "...", "prompt": "...",
             "json_schema": {}, "schema_name": "external_research_v2",
             "research_instructions": "...", "synthesis_header": "...",
             "synthesis_instructions": "...",
             "web_search_tool": {"type": "web_search_preview", "search_context_size": "medium"}},
 "response": "<synthesis text, verbatim>", "response_sha256": "<sha256>",
 "research_text": "<research brief, verbatim>",
 "provenance": {"research_call_id": "...", "synthesis_call_id": "...", "source": "<log folder>",
                "response_model": "...", "web_search_calls": 3, "cost_usd": 0.0573}}
```

**O que invalida uma entrada, sem lista de versões:**
- Qualquer byte diferente em prompt, system, instruções dos dois passes, schema, especificação (`rule_sha256`, `reported_by_rule`, `spec_policy`), modelo, esforço, limite de saída, endpoint, provedor ou ferramenta gera outra chave.
- A idade é controlada por `max_age_days`.
- O `service_tier` fica fora da chave, como no cache (`llm_providers.py:2507-2512`).

### 3.2 `src/cognalys/llm/cost.py`

- **Constante nova**, depois de `REPLAY_DELIVERY` (`:43`):

  ```python
  # A research answer served from the research corpus: nothing was sent or
  # billed; counted apart from the response cache's hits.
  RESEARCH_CORPUS_DELIVERY = "research_corpus"
  ```

- **`usage()` (`:421-442`):** logo depois do bloco de `REPLAY_DELIVERY` (`:429-432`), acrescentar `if record.get("delivery") == RESEARCH_CORPUS_DELIVERY:`. O bloco faz `entry["corpus_hits"] = entry.get("corpus_hits", 0) + 1` nas três entradas e segue com `continue`. `_empty()` não muda, então o relatório de uma run sem corpus fica idêntico.
- **`summary_lines()` (`:572-582`):** depois da parte dos replays, acrescentar `+ (f", {report['corpus_hits']} served by the research corpus" if report.get("corpus_hits") else "")`.
- **Função pública nova `trace_call_cost(path, record, prices=None) -> Optional[float]`:** `_billing_cost` de `_trace_counts(path, record, True)` mais o das `_failed_response_billings(record)`.
- **O que não muda:** `usage_by_finding` e o guard. Um trace do corpus não está em `deliveries`, então custa 0.

### 3.3 `src/cognalys/llm/llm_providers.py`

- **Imports.**
  - De `cost`: `PAID_DELIVERIES` e `RESEARCH_CORPUS_DELIVERY`.
  - De `research_corpus`: as três constantes de passe, `MEMO_PREFIX`, `request_identity`, `entry_key` e `pass_completed`.
- **Nível de módulo:** `_LAST_TRACE = threading.local()`.
- **`_finish_trace` (`:2172`):** como primeira linha, `_LAST_TRACE.call = {"call_id": call_id, "delivery": str(metadata.get("delivery", "")), "completed": pass_completed(metadata)}`, antes do `return` antecipado. `_start_trace` não muda.
- **`OpenAIProvider`.**
  - Atributos de classe novos: `research_corpus = None` e `WEB_SEARCH_TOOL = {"type": "web_search_preview", "search_context_size": "medium"}`.
  - Em `:2495-2498`: `payload["tools"] = [dict(self.WEB_SEARCH_TOOL)]`. Os bytes enviados são os mesmos.
  - Em `:2465-2467`, quando `web_search`: `request_fields["web_search_tool"] = dict(self.WEB_SEARCH_TOOL)`.
- **`AnthropicProvider`.** Em `:3234-3238`, passar `request_fields={"web_search_tool": dict(self.WEB_SEARCH_TOOL)} if web_search else None`. O `WEB_SEARCH_TOOL` dessa classe já existe (`:3033`).
- **`generate` das duas classes** ganha `research_corpus_scope=None` no fim da assinatura. O ramo `if json_mode and web_search` da `AnthropicProvider` (`:3173-3182`) repassa esse argumento ao `super()`.
- **O ramo `if json_mode and web_search` da `OpenAIProvider` (`:2350-2392`) passa a ser:**

```python
research_instructions = (system_prompt or "") + RESEARCH_PASS_INSTRUCTIONS
corpus = self.research_corpus if research_corpus_scope is not None else None
identity = memo_key = None
memo = active_replay_memo()
if corpus is not None:
    identity = request_identity(
        provider=self.provider_name, endpoint=self.endpoint, model=self.model,
        reasoning_effort=self.reasoning_effort, max_output_tokens=self.max_output_tokens,
        system_prompt=system_prompt or "", prompt=str(prompt), json_schema=json_schema,
        schema_name=schema_name if json_schema else None,
        research_instructions=RESEARCH_PASS_INSTRUCTIONS,
        synthesis_header=SYNTHESIS_RESULT_HEADER,
        synthesis_instructions=SYNTHESIS_PASS_INSTRUCTIONS,
        web_search_tool=dict(self.WEB_SEARCH_TOOL))
    memo_key = MEMO_PREFIX + entry_key(identity)
    # Within one finding the first shared answer wins (a replan reads what
    # the first pass read), whether it was served or researched.
    found = memo.get(memo_key) if memo is not None else None
    if found is not None:
        return self._replay_two_pass(found, prompt, system_prompt, json_schema,
                                     schema_name, trace_stage, trace_context)
    entry = None if getattr(_CACHE_READ_STATE, "bypass", False) else corpus.get(identity)
    if entry is not None:
        call_id = self._start_trace(
            prompt, system_prompt, json_mode, web_search, json_schema, schema_name,
            RESEARCH_CORPUS_DELIVERY, trace_stage, trace_context, parent_call_id)
        self._finish_trace(call_id, {"output_text": entry["response"]}, {
            "status": RESEARCH_CORPUS_DELIVERY, "delivery": RESEARCH_CORPUS_DELIVERY,
            "latency_ms": 0,
            "research_corpus": {"key": entry["key"], "created_at": entry["created_at"],
                                "origin": entry.get("origin", ""),
                                "response_sha256": entry["response_sha256"]}})
        if memo is not None:
            memo.put(memo_key, entry["response"], call_id)
        return entry["response"]
trace_group = ...                      # unchanged
_LAST_TRACE.call = {}
research = self.generate(prompt, research_instructions, json_mode=False, web_search=True, ...)
research_call = dict(getattr(_LAST_TRACE, "call", {}) or {})
synthesis_prompt = prompt + SYNTHESIS_RESULT_HEADER + research + SYNTHESIS_PASS_INSTRUCTIONS
_LAST_TRACE.call = {}
answer = self.generate(synthesis_prompt, system_prompt, json_mode=True, ...)
synthesis_call = dict(getattr(_LAST_TRACE, "call", {}) or {})
if (corpus is not None and research and answer
        and research_call.get("completed") and synthesis_call.get("completed")):
    if memo is not None:
        memo.put(memo_key, answer, synthesis_call.get("call_id", ""))
    if research_call.get("delivery") in PAID_DELIVERIES and not _frozen_experiment():
        corpus.put(identity, research_corpus_scope, answer, research,
                   self._research_provenance(research_call, synthesis_call))
return answer
```

- **`_replay_two_pass(found, ...)`:** traça como `_replay` (`:2257-2269`), com `delivery` e `status` `"finding_replay"`, `replayed_call_id` e `budget.record_replay()`, no `trace_stage` do nível de dois passes. Devolve o texto.
- **`_research_provenance(research_call, synthesis_call)`:** lê os dois `*_metadata.json` em `self.prompt_log_dir`, quando existe, e devolve os campos de `provenance` da §3.1. `cost_usd` vem de `cost.trace_call_cost`, ou é `None` sem log.

**Consequências do desenho**
- **O que vira entrada:** só uma pesquisa enviada neste passe, com os dois passes completos. Resposta incompleta, degenerada, do cache de TTL ou de replay não vira entrada.
- **Experimento congelado (`TCC_FROZEN_EXPERIMENT`):** o corpus é servido e nada é escrito. O miss segue a recusa que já existe (`:2441-2451`).
- **Custo do hit:** não chama `guard.check()`, `guard.add()` nem `budget.charge()`.
- **`--llm-cache-bypass-finding`:** pula a leitura do corpus para aquela finding, então ela pesquisa ao vivo. A entrada válida fica e a nova resposta não a substitui. O bypass serve para reanalisar uma finding do zero, e pesquisar ao vivo nunca é pesquisar menos.

### 3.4 `src/cognalys/llm/finding_adjudication.py:546-559`

Antes da chamada:

```python
corpus_scope = {}
if part == "shared" and getattr(self.provider, "research_corpus", None) is not None:
    corpus_scope = {"research_corpus_scope": research_sections["question_scope"]}
```

e `self.provider.generate(..., **corpus_scope)`.

Sem corpus, a chamada tem os mesmos argumentos de hoje e os dublês de teste não mudam. A mudança vale para todo tipo de escopo, inclusive `dependency_lifecycle`, e não depende de CrySL.

### 3.5 `src/cognalys/llm/generate_error_fixes.py`

**Opções novas**, depois de `--web-search-cache-ttl-hours` (`:1190-1200`):
- `--research-corpus-dir` (`type=Path`, sem padrão), help: `"Directory of research answers kept per scope across runs. A shared research request found there (same request, not older than --research-corpus-max-age-days) is served from it; one researched live with both passes complete is stored there. Independent of --no-cache and of --web-search-cache-ttl-hours. Default: no corpus."`
- `--research-corpus-max-age-days` (`type=float`, `default=90.0`), help: `"How long a research corpus entry stays usable (default: 90; 0 never expires)."`

**Ligação ao provedor.** Depois de criar o provedor (`:1304-1316`), se `args.research_corpus_dir` e o provedor é remoto:
- `provider.research_corpus = ResearchCorpus(args.research_corpus_dir, args.research_corpus_max_age_days, analysis_logger)`;
- imprimir `Research corpus: <dir> (<n> entries, max age <d> days)`, só quando ligado.

**Linha de estatísticas.** Depois da linha do cache (`:1363-1367`), quando ligado: `analysis_logger.info("Research corpus: %d served, %d researched and stored, %d kept, %d expired", ...)`.

### 3.6 `src/cognalys/orchestration/run_pipeline.py`

**Opções novas**, depois de `--llm-web-search-cache-ttl-hours` (`:646-656`):
- `--llm-research-corpus-dir`, help: `"Research corpus directory, relative to the repository root: answers to shared research requests kept across runs and served to an identical request (default: none). Not a cache: --no-llm-cache and --llm-web-search-cache-ttl-hours do not affect it."`
- `--llm-research-corpus-max-age-days` (`type=float`, `default=90.0`), help: `"Days a research corpus entry stays usable (default: 90; 0 never expires)."`

**`_command_llm` (`:1312`).** Depois do bloco de cache (`:1358-1368`), se `args.llm_research_corpus_dir`, acrescentar `["--research-corpus-dir", str(paths.root / args.llm_research_corpus_dir), "--research-corpus-max-age-days", str(args.llm_research_corpus_max_age_days)]`. Sem a opção o comando é idêntico e as fixtures douradas não mudam.

**`prepare_frozen_experiment` (`:1848`).** Passar `research_corpus=corpus_digest(paths.root / args.llm_research_corpus_dir) if args.llm_research_corpus_dir else None` a `capture`.

**Custo impresso.** Em `print_model_cost` (`:1004`) e em `cli.py:950`, a condição passa a ser `report["calls"] or report["cache_hits"] or report.get("corpus_hits")`.

### 3.7 `src/cognalys/evaluation/experiment_manifest.py`

- `capture` (`:170`) ganha `research_corpus: Optional[Dict] = None`. Quando não é `None`, o manifesto recebe `"research_corpus": dict(research_corpus)`. O campo se chama `research_corpus` porque `corpus` já é o CSV de findings.
- `_COMPARED` (`:208`) ganha `("research_corpus", "sha256")`. Sem a opção o campo falta dos dois lados e `verify` vê `None == None`. Manifestos antigos continuam batendo com runs sem corpus.

### 3.8 `src/cognalys/learning/retrospective.py:548-554`

Antes do ramo `elif status not in {"", "success"}`, acrescentar `elif status == "research_corpus": entry["corpus_hits"] = entry.get("corpus_hits", 0) + 1`. Sem isso o hit seria contado como falha. O dicionário inicial (`:542-545`) não muda.

### 3.9 `src/cognalys/evaluation/paper_tables.py`

- **`:124-157`:** somar `corpus_hits = sum(int(entry.get("corpus_hits", 0) or 0) for entry in performance.values())` e devolver `"model_corpus_hits": corpus_hits`.
- **`cost_table` (`:296-315`):** só quando algum run tem `model_corpus_hits > 0`:
  - inserir a coluna `"Research corpus"` depois de `"Replays"`;
  - acrescentar à nota a frase `" Research corpus: shared research requests answered from the research corpus (an answer kept from an earlier run), likewise counted among the model calls and not sent."`

  Sem hits, a tabela e a nota ficam idênticas.

### 3.10 `src/cognalys/cli.py`

Comando novo ao lado de `cache` (`:1345`):

    cognalys research-corpus list   [--dir DIR] [--json]
    cognalys research-corpus import RUN_DIR... [--dir DIR] [--replace] [--dry-run] [--json]

- `--dir` tem padrão `<workspace>/outputs/research_corpus`.
- `list` imprime uma linha por entrada (`key[:12]`, data e idade, origem, especificação, fontes, custo). A linha final é `N entries, M older than 90 days, US$ X recorded to produce them, digest <sha256[:12]>`.
- `import` imprime o relatório de `import_runs`.
- O corpus **não** entra em `cache.CACHE_KINDS` (`cache.py:19`).

### 3.11 `docs/GUIA_DE_USO.md`

Na seção "Cache e retomada" (`:572`):
- uma linha na tabela para cada opção nova;
- um parágrafo dizendo:
  - o que é o corpus e do que a chave é feita;
  - que a parte do caso nunca é servida;
  - que só passes completos são gravados;
  - que `--no-llm-cache` e o TTL não o afetam;
  - que `--llm-cache-bypass-finding` pula a leitura;
  - que, dentro de uma finding, a primeira resposta shared vale também para o replano;
- a ordem de consulta: memo da finding, corpus, cache de TTL, replay por chamada, ao vivo;
- que apagar a entrada força nova pesquisa;
- o trace `research_corpus` e o campo `research_corpus` do manifesto;
- a tabela abaixo;
- uma seção `cognalys research-corpus`.

| Cache de LLM | TTL | Corpus | Parte shared | Parte do caso |
|---|---|---|---|---|
| ligado | 168 h | desligado | cache até 7 dias, senão ao vivo | cache ou ao vivo |
| `--no-llm-cache` | qualquer | desligado | sempre ao vivo | ao vivo |
| ligado | 168 h | ligado | corpus até a idade máxima; senão cache de TTL; senão ao vivo, e o ao vivo completo grava | cache ou ao vivo |
| `--no-llm-cache` | 0 | ligado | corpus; senão ao vivo, que grava | sempre ao vivo |

### O que muda byte a byte e como provar

- **Corpus desligado:** nenhum pedido ao provedor muda. Os registros de request e metadata das chamadas de pesquisa ganham `web_search_tool`. Manifesto, relatório de custo e tabelas do artigo ficam idênticos.
- **Corpus ligado:** num hit, os pedidos `.research` e `.synthesis` da parte shared deixam de ser enviados. Os outros pedidos são idênticos aos de uma run que tivesse recebido ao vivo aquela mesma resposta.
- **Provas:**
  - os testes da §4, com strings literais;
  - `prompt_identity.py`, 1.929 de 1.929;
  - `identity.py`, 503 de 503;
  - `test_golden_step_commands.py` sem fixture nova.

## 4. Testes

**`tests/test_research_corpus.py`** (novo, `unittest`, no estilo de `tests/test_llm_response_cache.py`)

*`ResearchCorpusTests`*
- `test_round_trip_serves_the_stored_text_byte_for_byte`
- `test_any_changed_request_field_is_another_key`: um `subTest` por campo de `IDENTITY_FIELDS`.
- `test_a_schema_holding_a_tuple_still_hits_after_storing`: a ida e volta pelo JSON garante o hit.
- `test_an_entry_older_than_the_maximum_age_is_refused_and_zero_never_expires`
- `test_an_entry_whose_text_was_edited_is_refused`
- `test_a_valid_entry_is_never_replaced_and_an_expired_one_is_superseded`
- `test_an_answer_without_a_source_or_that_is_not_json_is_not_stored`
- `test_pass_completed_reads_openai_status_anthropic_stop_reason_and_degenerate_stops`
- `test_entries_are_listed_with_scope_age_sources_and_cost`
- `test_the_digest_depends_on_keys_and_texts_only`

*`ResearchCorpusImportTests`* (os traces vêm de um `OpenAIProvider` com sessão falsa)
- `test_an_imported_pass_is_served_to_the_run_that_asks_the_same_request`
- `test_the_most_recent_pass_of_a_key_is_kept_across_paths_of_one_call`
- `test_case_passes_failed_passes_and_cache_hits_are_not_imported`
- `test_an_incomplete_pass_is_not_imported`: com `response_status: "incomplete"`, o motivo `not_completed` aparece.
- `test_an_existing_entry_is_kept_unless_replace`
- `test_a_dry_run_writes_nothing`
- `test_a_trace_without_the_tool_field_takes_the_tool_of_its_time`

*`RunOptionTests`*
- `test_the_corpus_options_reach_the_llm_step_only_when_given`

**`tests/test_llm_response_cache.py`**, classe nova `ResearchCorpusProviderTests` (reusa `FindingReplayTests._Session` e `_provider`)
- `test_without_a_corpus_the_two_passes_are_sent_as_before`: compara `instructions`, `input` e `tools` com literais.
- `test_a_miss_researches_and_stores_and_the_next_request_is_served`: no hit nada é enviado, o trace tem `delivery` e `status` `research_corpus` e o guard registra uma cobrança só.
- `test_an_incomplete_research_answer_is_not_stored`: resposta da pesquisa com `status: "incomplete"`.
- `test_without_a_scope_the_corpus_is_not_consulted`
- `test_a_hit_does_not_depend_on_the_response_cache_or_its_ttl`
- `test_another_effort_or_model_misses`
- `test_research_answered_by_the_ttl_cache_or_a_replay_is_not_stored`
- `test_a_frozen_experiment_is_served_and_stores_nothing`
- `test_a_replan_reads_the_first_shared_answer_of_its_finding`: dentro de `finding_replay_scope`, a entrada é trocada no disco entre duas chamadas, e a segunda devolve a primeira resposta com trace `finding_replay`.
- `test_cache_reads_bypassed_skip_the_corpus_and_keep_the_entry`
- `test_a_research_trace_records_its_web_search_tool`

**Outros arquivos**
- `tests/test_anthropic_provider.py`: `test_the_corpus_scope_reaches_the_shared_two_pass_branch`.
- `tests/test_external_research_resilience.py`: `test_only_the_shared_part_names_its_corpus_scope_and_only_with_a_corpus`.
- `tests/test_llm_cost.py`: `test_a_corpus_hit_is_counted_apart_and_costs_nothing`. Verifica `calls`, `cache_hits`, `corpus_hits`, a frase de `summary_lines`, `usage_by_finding` e que o guard refeito dos traces fica com `spent` sem o hit.
- `tests/test_learning.py`: `test_a_corpus_hit_is_counted_apart_in_llm_performance`.
- `tests/test_paper_tables.py`:
  - `test_the_cost_table_is_unchanged_without_corpus_hits`;
  - `test_the_cost_table_reports_corpus_hits_in_their_own_column`.
- `tests/test_experiment_manifest.py`:
  - `test_a_manifest_without_a_research_corpus_matches_an_old_one`;
  - `test_two_research_corpora_with_different_entries_are_different_experiments`.
- `tests/test_cli_ops.py`:
  - `test_research_corpus_list_and_import`;
  - `test_clearing_the_caches_leaves_the_research_corpus`.
- `tests/test_golden_step_commands.py`: sem mudança. Tem de passar com as fixtures atuais.

Se algum teste existente compara o registro de request inteiro de uma chamada com web search, ele recebe o campo novo, e a expectativa é atualizada no próprio teste. Fixture dourada nunca se edita à mão.

## 5. Checagem offline de aceitação

### Já rodado, sem a implementação

| Checagem | Resultado | Script |
|---|---|---|
| As três constantes reproduzem os pedidos gravados | 1.929 de 1.929 (M) | `prompt_identity.py` |
| A síntese shared basta para refazer o registro de pesquisa | 503 de 503 (M) | `identity.py` |
| Passes completos entre os importáveis | 191 de 191 (M) | `status_scan.py` |
| Importação simulada | 191 passes importáveis, 118 entradas, US$ 20,39 gravados, entrada mais velha com 21 dias (M) | `simulate.py` |
| Benchmark com corpus pronto | 9 de 9 servidas em v4, v5 e v6 (M) | `simulate.py` |
| Coorte da run congelada | 23 escopos para 43 findings; 20 servidas com preenchimento só pela run; 27 com a importação, se o pedido não mudar (M) | `k3_final/frozen_cohort2.py` |
| Sombra | 35 de 36 unidades têm entrada vinda da produção, com 5 a 8 dias de idade (M) | `shadow_keys.py` |

### Portão no nível do claim

Fonte: `units.py`, depois `claims.py`, `residual_stats.py`, `nofix_residual.py` e `pred_gap.py`, sobre os caches da revisão.

**O que conta como "as mesmas afirmações".** Toda fonte shared que a decisão cita aparece na outra amostra da mesma chave de um destes modos:
- a mesma página (host mais caminho, sem a versão do Java SE);
- um fato que afirma a mesma coisa.

**O que conta como citação.** Um id WEB que aparece no texto da adjudicação, ou que é evidência de um `decision_claim_ids`, ou que é evidência de um predicado decidido. Este último caso entra em `k3_compare.py`; `pred_gap.py` mostra que ele acrescenta 5 unidades nos baldes de dispensa e nenhuma apoiada só em WEB.

**Números**
- 501 unidades têm decisão e amostra shared, e 303 citam fonte shared (M).
- 868 de 972 citações conferidas estão na outra amostra, 89,3% (M).
- Sobram 29 citações distintas. Nenhuma é a única evidência de um claim de decisão (M).
- Por decisão: 14 NO_SAFE_FIX, 8 FIX_SUPPORTED, 6 MULTI_FILE_FIX_PROPOSED_FOR_REVIEW e 1 MULTI_FILE_FIX_REQUIRES_POLICY (M).

**Baldes de dispensa primeiro**

*Falso positivo e "sem impacto" (6 citações no resíduo):*

| Caso | Leitura |
|---|---|
| IbisWallet v5 (TrustManagerFactory) | A afirmação está nos fatos das outras amostras. |
| FinanPlus (NIST SP 800-38D) | Está nas outras amostras, numa delas em outra URL do mesmo documento. |
| Amethyst r1 (guia JCA) | Citação incidental. As 2 irmãs sem a página decidiram igual. |
| IbisWallet v6 (standard-names) | Citação incidental. As 2 irmãs sem a página decidiram igual. |
| CometOTP produção (SecureRandom) | As 5 execuções pré-D1 sem a página também dispensaram. As 3 pós-D1 mudaram por causa do D1. |
| Herald r1 (KeyManagerFactory.crysl) | A decisão foi FIX_SUPPORTED e o "sem impacto" veio depois da geração. As 4 irmãs sem a página deram issue. A adjudicação reenviada dá NO_SAFE_FIX 4 de 4 vezes (`noise_floor.md`). |

*NO_SAFE_FIX (14 citações: 5 já estão na tabela acima e 9 foram lidas na revisão).* As 9 são:
- Melox v5 WEB-006;
- Input Leaf `AeadBlob.decrypt` WEB-004;
- Herald v4 WEB-002, v6 WEB-002 e produção `run_v2` WEB-005;
- IbisWallet v2 WEB-008 e v3 WEB-002;
- Tasks `DebugConnectionBuilder` e `AndroidOkHttpClientFactory` WEB-005.

Em todas, ou a afirmação está noutra amostra da chave, ou a página não é aquilo em que a decisão se apoia: Melox diz "does not establish", e Input Leaf é `cause_unconfirmed`. Nenhuma NO_SAFE_FIX perde o claim que a decide (M, `nofix_residual.py` com leitura).

*Outros baldes:* 14 citações estão sob decisões que geram código. A revisão adversarial lê essas 14 (§8, passo 8). Uma decisão de gerar apoiada numa página que nenhuma outra amostra tem fica listada no relatório e não reprova o portão, porque não leva a dispensa. Fora do resíduo, 5 unidades que geram código têm um predicado decidido apoiado só em WEB shared (M, `pred_gap.py`); a comparação da sombra lista essas unidades.

### Variação da decisão com a amostra de pesquisa

Fonte: `claims.py` e `noise_sets.py`.

| Condição | Pares | Decisão difere | Dispensa difere |
|---|---|---|---|
| Amostras diferentes, mesmo construtor de contexto (9 findings) | 33 | 4 (12,1%) | 4 (12,1%) |
| Reenvio com a entrada inteira da adjudicação fixa (`decision-replay`) | 176 | 19 (10,8%) | — |
| Mesma amostra, versões de código diferentes (produção) | 119 | 28 (23,5%) | 29 de 113 (25,7%) |

A amostra de pesquisa não acrescenta variação mensurável ao ruído da adjudicação.

**Limites**
- Não existe par gravado com a mesma amostra, o mesmo código e extração ao vivo. As réplicas v9 e v10 dão os primeiros 9.
- 218 das 303 unidades que citam fonte shared têm chave com uma amostra só. A sombra cobre parte disso.

### A rodar depois da implementação (pelo agente, com os números esperados)

1. `prompt_identity.py` importando as constantes de `cognalys.llm.research_corpus`: 1.929 de 1.929, ou o total do dia se pastas novas tiverem chegado.
2. `cognalys research-corpus import --dir <rascunho>/corpus_all <todas as runs de dirs.json> --json`:
   - 191 passes importáveis e 118 entradas;
   - `skipped.not_completed == 0`;
   - 0 entradas com mais de 90 dias.
3. Para cada pedido shared do `run_v6` das 9 findings: `recorded_pass_identity` mais `ResearchCorpus.get`, num corpus importado de `run` a `run_v5` com **uma** chamada de import. Esperado: 9 de 9 hits em 7 chaves.
4. `identity.py`: 503 de 503.
5. Findings movidas por balde: 0 em todos (falso positivo 0, sem impacto 0, sem correção segura 0, issue 0, PR pronto 0).

## 6. Validação paga

Roda numa sessão Opus, depois do merge do K3. A pasta é `outputs/k3_validation_<data>/` (abaixo, `$V` é o caminho absoluto em `/mnt/c`).

**Condições comuns**
- Tudo em Flex, com `no-llm-cache = true`, `llm-web-search-cache-ttl-hours = 0` e `max-cost = 10` por run.
- C: com pelo menos 12 GB livres.
- Teto total da validação: US$ 22.

### Parte 1: duas réplicas das 9 findings com corpus (`run_v9`, `run_v10`)

**Preparação**

```bash
E=outputs/agent_baseline_20261108/cognalys
cognalys research-corpus import --dir $V/corpus_bench $E/*/run/ $E/*/run_v[2-8]/   # one call
cognalys research-corpus list --dir $V/corpus_bench     # 7 entries expected for the 9 findings
```

**Execução**
- O script é o `outputs/d1_validation_20261110/run_d1_validation.sh`, com `v9 v10` para as 9 findings.
- Depois do `sed`, acrescentar `printf 'llm-research-corpus-dir = "%s"\n' "$V/corpus_bench" >> "$d/run_$v.toml"`.
- O marcador passa a ser `.done_k3_$v`.
- As réplicas usam a loja de aprendizado compartilhada, como v4 a v8.

**Conferência antes de gastar.** Depois do merge, rodar o passo 3 da §5 contra `corpus_bench` com o pedido shared refeito no commit atual: `recorded_pass_identity` do `run_v8` mais `get`.
- Se der 0 hits, o pedido shared mudou depois de `af71cfe` (Q1 e Q2Q3 não deveriam mexer nele; ver §7). Nesse caso a importação não serve: v9 vira a run de preenchimento (7 pesquisas ao vivo, 2 servidas) e v10 a réplica servida.

**Parada antecipada:** se a primeira execução não tiver trace com `delivery` `research_corpus` e a conferência tinha dado hit, parar e investigar a chave.

**Custo (E):**
- com a importação servindo: 2 × 9 × (0,1929 − 0,0566) = US$ 2,45;
- no caso de preenchimento por v9: 9 × 0,1363 + 7 × 0,0566 + 9 × 0,1363 = US$ 2,85.

**Tempo:** cerca de 2,7 h (média medida de 544 s por run).

### Parte 2: sombra com n = 36 unidades em 26 pastas, fria e com corpus lado a lado

**Seleção, cega ao conteúdo** (`k3_design/shadow_select.py`, que grava `shadow_folders.json`)
- População: pastas `outputs/rvsec_*` com execução mais recente de 2026-11-01 em diante, com `gpt-6.1-terra` e pelo menos uma unidade com pesquisa shared, decisão e desfecho.
- Ficam de fora as pastas do benchmark.
- Ordem pelo sha256 do nome, até somar 36 unidades.
- Desfechos gravados das 36:

  | Desfecho | Unidades |
  |---|---|
  | sem impacto | 15 |
  | sem correção segura | 7 |
  | issue | 6 |
  | PR pronto | 6 |
  | falso positivo | 1 |
  | reprovada antes de gerar | 1 |

- São 19 chaves, 8 delas lidas por 2 ou mais unidades.

**Pastas** (com a run-base entre parênteses quando não é `run`):

`rvsec_app_birdo_vpn_20261104`, `rvsec_in_hridayan_ashell_20261102` (run_v2), `rvsec_com_auralis_crisisconnect_20261103`, `rvsec_denaro_20261031` (run_ab61), `rvsec_misuse_20261016` (run_ab61), `rvsec_trigger_20261028` (run_ab61), `rvsec_dnstoggle_20261022` (run_v3), `rvsec_com_mixify_app_20261105`, `rvsec_moe_feng_wheelplay_20261105`, `rvsec_primftpd_20261031` (run_v3), `rvsec_com_m57_hermescontrol_20261104`, `rvsec_com_m57_hermescontrol_20261102` (run_ab61), `rvsec_com_jagr_fridamusic_20261105`, `rvsec_org_cryptomator_lite_20261104`, `rvsec_com_cruxcoach_android_20261107`, `rvsec_de_kjell_zencompanion_20261106`, `rvsec_com_ericlowry_dnstoggle_20261104`, `rvsec_com_finanplus_20261104`, `rvsec_dev_dettmer_simplenotes_20261104`, `rvsec_com_mendhak_gpslogger_20261102`, `rvsec_io_github_capitan0n_droynis_20261104`, `rvsec_dnsfilter_android_20261102`, `rvsec_org_thosp_yourlocalweather_20261101` (run_v5), `rvsec_com_ecency_app_20261107`, `rvsec_io_github_thatsfguy_meshcore_native_20261105`, `rvsec_com_rebelroot_omni_20261103`.

**Loja de aprendizado por braço.** Antes da sombra, `cp -a data/pipeline_learning $V/learning_snapshot`. Depois, para cada braço `a`: `cp -a $V/learning_snapshot $V/learning_$a`. Assim nenhum braço lê a tentativa do gêmeo.

**Comando, para cada pasta `f` com base `b` e cada braço `a` em `cold corpus`:**

```bash
mkdir -p $V/shadow/$f
sed -e "s#^run-dir = .*#run-dir = \"$V/shadow/$f/run_$a\"#" -e "s#/clones\"#/clones_k3$a\"#" \
    -e "s#/worktrees\"#/worktrees_k3$a\"#" outputs/$f/$b.toml > $V/shadow/$f/run_$a.toml
for line in 'no-llm-cache = true' 'llm-web-search-cache-ttl-hours = 0' 'llm-service-tier = "flex"'; do
  grep -q "^${line%% =*} " $V/shadow/$f/run_$a.toml || echo "$line" >> $V/shadow/$f/run_$a.toml; done
echo "learning-dir = \"$V/learning_$a\"" >> $V/shadow/$f/run_$a.toml
[ $a = corpus ] && echo "llm-research-corpus-dir = \"$V/corpus_shadow\"" >> $V/shadow/$f/run_$a.toml
TCC_LEARNING_DIR=$V/learning_$a timeout 3h cognalys run $V/shadow/$f/run_$a.toml \
  > $V/shadow/$f/run_$a.log 2>&1 < /dev/null
```

`TCC_LEARNING_DIR` é obrigatório porque a etapa de LLM só lê o ambiente (`knowledge.py:40-44`). O `learning-dir` cobre a retrospectiva.

**Ordem**
1. `cognalys research-corpus import --dir $V/corpus_shadow outputs/rvsec_*/run*/`, só com as runs de produção gravadas.
2. A primeira pasta do braço `cold` (cerca de US$ 0,3). Logo em seguida, a conferência de chave sem custo: `recorded_pass_identity` dos pedidos shared dessa pasta mais `get` em `corpus_shadow`.
   - Se não houver entrada, o pedido shared mudou: parar a sombra.
   - Nesse caso a run congelada roda sem a opção, e a sombra é refeita depois dela na variante "corpus primeiro": o braço `corpus` começa com o corpus vazio, preenche com a primeira unidade de cada chave, e só as unidades servidas entram na comparação. São cerca de 17 de 36, então é preciso estender a ordem de `shadow_select.py` até 30 unidades servidas.
3. O resto do braço `cold`.
4. A mesma conferência para todas as unidades. Exigir pelo menos 30 com entrada; se faltar, acrescentar as próximas pastas da ordem.
5. O braço `corpus`.

**Custo e tempo (E)**
- As 26 runs-base gravadas custariam US$ 5,86 em Flex, mas responderam 113 chamadas do cache (46 de pesquisa, 46 de síntese, 13 de extração, 8 de adjudicação) (M, `shadow_cost.py`).
- Pagando essas chamadas: braço frio cerca de US$ 8,0 e braço com corpus cerca de 6,0 (menos 0,0566 por unidade servida).
- Desempates por `decision-replay`: até US$ 1,5.
- Total da validação: cerca de US$ 18, com teto de US$ 22. Se o gasto passar de US$ 20, parar de acrescentar pastas assim que houver 30 unidades.
- Tempo: cerca de 13 h.

**O que se compara** (`k3_compare.py` em `$V`, reusando `collect_traces.py`, `collect_calls.py`, `units.py` e `claims.py`; `cited()` inclui a evidência dos predicados decididos)
1. Unidades servidas (trace `research_corpus`), e se os bytes da parte shared do `_external_research.json` são os da entrada.
2. Custo da etapa `external_research` por unidade em cada braço (`cognalys cost`).
3. Desfechos por balde: frio, corpus e gravado.
4. D = dispensas(corpus) − dispensas(frio), contando falso positivo mais sem impacto.
5. Para cada unidade dispensada só no braço do corpus, a checagem de claim contra a amostra do braço frio.
6. Leitura do diff de cada PR pronto do braço do corpus.
7. Pares v9 × v10 (mesma amostra, mesmo código) contra os pares de v4, v5 e v6.
8. A lista das unidades que geram código com predicado apoiado só em fonte do corpus.

**Critérios de aceite, contra o ruído**

*Réplicas.* Cada desfecho tem de cair no conjunto observado (`noise_floor.md` mais `noise_sets.py` com v6 a v8):

| Finding | Conjunto |
|---|---|
| Amethyst | {sem impacto} |
| CometOTP | {issue, PR pronto} depois do D1; falso positivo é sentinela |
| Herald, IbisWallet | {issue, sem impacto} |
| Input Leaf | {issue, PR pronto, correção gerada sem PR} |
| InterTune | {issue, PR pronto, correção não gerada} |
| Melox | {issue, sem correção segura} |
| RedReader | {issue, não gerada, gerada sem PR, sem impacto} |
| Winterhold | {issue, não gerada, PR pronto} |

*Custo.*
- A etapa `external_research` custa até 0,015 por unidade servida (hoje a parte do caso mede 0,0086).
- O número de traces `research_corpus` é igual ao de unidades servidas.
- Todo trace `research_corpus` tem 0 tokens e nenhum `web_search_calls`.

A igualdade entre o guard e o `cognalys cost` não serve de critério, porque o guard refeito é o próprio `usage()` sobre os traces. O teste da §4 cobre o guard.

*Sombra.*
- **Critério que pode reprovar de fato: zero dispensas causadas pelo corpus.**
- D ≤ +4. O desvio de D em 36 unidades é de 2,1 a 3,0 (taxa de troca de 12,1% a 25,7%), então D só sinaliza um excesso de 5 ou mais. Como a amostra gravada é um sorteio da distribuição fria, o D esperado é 0.
- Falsos positivos do braço do corpus ≤ os do frio + 1.
- |PR pronto(corpus) − PR pronto(frio)| ≤ 4.

**Dispensa causada pelo corpus** é uma unidade dispensada só no braço do corpus em que um claim de decisão, ou um predicado decidido, se apoia numa fonte do corpus sem página nem fato equivalente na amostra fria. O desempate é o `cognalys decision-replay` da adjudicação dos dois braços, 4 amostras cada: conta como causada se o pedido do corpus dispensa 3 ou mais vezes em 4 e o frio 1 ou menos.

**O que a sombra não mede:** a idade de 90 dias (as entradas terão de 5 a 8 dias), e as unidades cuja chave só tem uma amostra fora da sombra.

**Não ligar na run congelada se:**
- houver qualquer dispensa causada pelo corpus;
- D for +5 ou mais;
- o falso positivo voltar no CometOTP com claim que só o corpus tem;
- uma entrada for servida com bytes diferentes dos gravados, ou para um pedido diferente;
- um desfecho de réplica sair do conjunto e a decisão citar afirmação ausente de todas as amostras frias gravadas;
- um trace `research_corpus` tiver tokens ou buscas.

Se a sombra não terminar antes da run congelada, a run congelada roda sem a opção e o artigo informa o custo frio.

### Parte 3: preenchimento e relatório da run congelada

Esta parte só vale se a sombra passar. Não tem custo próprio.

1. **No commit congelado, sem run em curso:**
   - `cognalys research-corpus import --dir outputs/<run congelada>/research_corpus outputs/*/run*/ outputs/agent_baseline_20261108/cognalys/*/run*/ $V/shadow/*/run_cold/`, numa chamada só, que mantém o passe mais recente de cada chave;
   - depois, `cognalys research-corpus list --json > outputs/<run congelada>/research_corpus_before.json`.
2. **Contagem offline**, com o método de `k3_final/frozen_cohort2.py` mais `get`: quantas das 46 findings têm entrada.
   - Esperado (M): com o pedido shared igual ao de 11-08, 19 de 43 antes da run e 27 de 43 ao fim.
   - Se o pedido mudou: 0 antes e 20 de 43 ao fim, servidas pelas entradas que a própria run grava.
   - As 3 findings sem passe gravado ficam como desconhecidas.
3. **Regra do miss:** pesquisa ao vivo, no esforço da run, e grava. Por isso a run não usa o congelamento de pesquisa do `--experiment-manifest` (§9, pergunta 4). Com o manifesto, o digest do corpus inicial fica gravado e é comparado.
4. **Relatório da run:**
   - unidades servidas e pesquisadas, com o custo de cada grupo;
   - o `research_corpus_after.json`;
   - para as 12 findings do benchmark dos agentes, quais foram servidas e com que entrada (data e custo de produção).

   O artigo declara o corpus e o Flex sempre que comparar com os agentes (decisão 4).

## 7. Riscos, limites conhecidos e o que não fazer

**Riscos e limites**
- **Uma entrada ruim vale para todas as findings do escopo até vencer.**
  - Mitigações: só passes completos e com fonte viram entrada; as entradas são imutáveis; `list` mostra fontes e data; apagar o arquivo força nova pesquisa.
  - A sombra tem 8 chaves lidas por 2 ou mais unidades.
  - Não há filtro por conteúdo, de propósito: seria triagem.
- **Deriva da chave.**
  - Mudar política de família, campo que define a regra, construtor de perguntas ou system prompt da pesquisa gera chaves novas. A partir daí o corpus só é preenchido por runs feitas depois da mudança; importar de novo não recupera nada, porque os registros têm o pedido antigo.
  - Os desenhos de Q1 e Q2Q3 nas pastas `q1_design` e `q2q3_design` mexem em textos de extração, adjudicação e revisão, não em pesquisa. Foi conferido por busca: "research" só aparece em dados lidos (M). As especificações finais deles não estavam disponíveis (`q1_final` e `q2q3_final` vazias).
  - A conferência da §6 (Parte 1 e Parte 2, passo 2) pega isso antes de gastar.
- **`--llm-workers` maior que 1.** Duas findings do mesmo escopo que comecem juntas pesquisam as duas, e fica a **primeira** entrada gravada (`put` devolve `"kept"` para a segunda). Cada finding continua lendo a própria resposta no replano, porque o memo de dois passes vem antes do corpus. Nenhuma das 26 TOMLs da sombra define `llm-workers`.
- **Cobertura offline parcial.** O portão foi conferido em 15 chaves com mais de uma amostra. A idade acima de 21 dias não foi medida.
- **`--learning-dir` não chega à etapa de LLM.** Só `TCC_LEARNING_DIR` chega. É um defeito anterior ao K3; a sombra o contorna exportando a variável. Fica registrado para um item próprio.
- **Run congelada com `--experiment-manifest`.** Todo miss de pesquisa volta vazio e, com `no-llm-cache`, a parte do caso nunca é pesquisada. Isso vale com ou sem K3 (pergunta 4).
- **Disco.** Os clones da sombra ficam no disco do WSL, e os scripts param abaixo de 12 GB livres.

**Não fazer**
- Pôr o corpus em `cache.CACHE_KINDS`.
- Servir a parte do caso.
- Casar por especificação.
- Preencher a partir do cache de TTL ou de resposta incompleta.
- Deixar uma run trocar entrada válida.
- Escolher amostra pelo conteúdo.
- Mudar a ferramenta, o `search_context_size` ou o esforço.
- Fundir a síntese (W3).
- Comparar com os agentes sem declarar o corpus e o Flex.
- Rodar os dois braços da sombra sobre a mesma loja de aprendizado.

## 8. Passos de implementação

Fazer no worktree `tcc_v3_blockK`, sem run em curso. A ordem dos itens do plano é Q1, Q2Q3, **K3**, run congelada, A1. O K3 não depende do conteúdo de Q1 nem de Q2Q3. Os passos 10 e 11 dependem do pedido shared do commit em que rodam.

**Arquivos compartilhados com outros itens**
- `finding_adjudication.py`: Q1 e Q2Q3 mexem nos prompts. O K3 toca só `:546-559`.
- `llm_providers.py` e `cost.py`: o A1 pode tocar.
- `run_pipeline.py`, `generate_error_fixes.py`, `experiment_manifest.py`, `paper_tables.py` e `docs/GUIA_DE_USO.md`: recebem opções e seções de qualquer item.

| # | Passo | Estágio |
|---|---|---|
| 1 | `research_corpus.py`: constantes, `canonical`, identidade, `pass_completed`, `storable`, `ResearchCorpus`, `listing`, `corpus_digest`; mais `ResearchCorpusTests` | implementar (Opus, medium) |
| 2 | `cost.py` (`RESEARCH_CORPUS_DELIVERY`, `corpus_hits`, `trace_call_cost`), `retrospective.py`, `paper_tables.py`; testes em `test_llm_cost.py`, `test_learning.py` e `test_paper_tables.py` | implementar |
| 3 | `llm_providers.py`: imports, `WEB_SEARCH_TOOL`, `web_search_tool` no trace, `_LAST_TRACE` em `_finish_trace`, memo de dois passes, ramo do corpus, bypass, `_replay_two_pass`, `_research_provenance`; `ResearchCorpusProviderTests` e o teste da Anthropic | implementar |
| 4 | `finding_adjudication.py:546-559`, opções em `generate_error_fixes.py` e `run_pipeline.py`, `experiment_manifest.py`; testes de resiliência, de opções e de manifesto | implementar |
| 5 | `recorded_response_text`, `recorded_pass_identity`, `import_runs`, CLI `research-corpus`, `docs/GUIA_DE_USO.md`; `ResearchCorpusImportTests` e `test_cli_ops` | implementar |
| 6 | As 5 checagens da §5 | checagem offline (Opus, low) |
| 7 | Suíte completa: `python -m pytest -q tests` | suíte (Opus) |
| 8 | Revisão adversarial. Lentes: byte-identidade com o corpus desligado; segurança de decisão (nunca servir pedido diferente, parte do caso ou passe incompleto; replano lê a primeira resposta); generalidade. Ler as 14 citações sob decisões que geram (`residual_read.py other 30`) | revisão (Opus, high, por decisão do usuário de não usar mais o Fable) |
| 9 | Commit, merge em `melhorias-pipeline-28-09`, push | sessão Opus |
| 10 | Validação paga da §6 (réplicas, depois a sombra), linha no `noise_floor.md` e registro em "Refinamentos" do `PLANO_FABLE.md` | validação paga (sessão Opus) |
| 11 | Parte 3 da §6 no commit congelado, antes da run congelada | sessão Opus |

**Mensagem do commit** (ajustar os números com os do passo 6):

`A shared research request (the finding-independent part of external research) can now be answered from a research corpus kept across runs: --llm-research-corpus-dir DIR (TOML llm-research-corpus-dir; off by default) makes the LLM step look the request up in DIR before researching and store there the answer of a pass it researched live with both its research and synthesis answers complete. An entry is keyed by the request itself (provider, endpoint, model, reasoning effort, output limit, system prompt, the user prompt with its question scope, questions, policy and verified rule, the schema, the two pass instructions and the web search tool), so a changed prompt, specification, model, effort or tool is another entry, and it is served only while younger than --llm-research-corpus-max-age-days (90; 0 never expires). The case part is always researched, a miss runs the whole research at the run's effort, a run never replaces a valid entry, research answered by the TTL cache, a replay or an incomplete answer is not stored, a finding's replan reads the shared answer its first pass read, and --llm-cache-bypass-finding skips the corpus for that finding. The corpus is independent of --no-llm-cache and --llm-web-search-cache-ttl-hours and is not a cache kind of cognalys cache. A served request is traced with delivery research_corpus, is not sent, billed or charged to --max-cost, and cognalys cost, the retrospective and the paper's cost table count it apart; a frozen experiment's manifest records the corpus digest and compares it. cognalys research-corpus list shows the entries with their age, sources, recorded cost and digest; cognalys research-corpus import RUN... builds entries from the complete shared passes recorded runs paid for, keeping the most recent pass of each key. A research call's trace now records web_search_tool. Without the option every request, report, table and manifest is byte-identical. Offline, over every recorded run under outputs: the three pass constants rebuild 1,929 of 1,929 recorded research and synthesis requests; 503 of 503 recorded research records are rebuilt from the synthesis answers; 191 recorded shared passes, all complete, import as 118 entries; a corpus filled beforehand serves 9 of 9 benchmark findings and 2 of 41 live shared researches of the November queue; of 972 shared-source citations in decisions whose scope has another sample 868 are in that sample, and none of the 29 others is the only evidence of a decision claim; no recorded finding changes outcome. GUIA_DE_USO describes the options and the command. Full suite: N passed.`

## 9. Perguntas ao usuário

1. **Idade máxima de 90 dias com o corpus ligado?** Recomendo que sim.
   - A chave já invalida por regra, prompt, modelo e ferramenta, e o que se pesquisa (Javadoc, regras, normas) muda devagar.
   - Nada aqui mede idades acima de 21 dias, e a sombra usa entradas de 5 a 8 dias.
   - Para ser conservador, 30 dias é só trocar o padrão.
2. **Ligar o corpus na fila de produção depois que a sombra passar?** Recomendo que sim, em `outputs/research_corpus`.
   - O ganho de custo ali é quase zero (2 de 41 passes), mas deixa os escopos prontos para a run congelada e para comparações futuras.
   - Até a sombra passar, fica desligado.
3. **Painel da OpenAI (P0).** A linha de web search de 2026-11-09 (o painel usa UTC) está mais perto de US$ 0,53 (53 ações `search`) ou de US$ 0,93 (93 itens)? E a de 11-10, de 0,30 ou de 0,50?
   - Se for o segundo valor, amplio `BILLED_WEB_SEARCH_ACTIONS`. A taxa por finding sobe de 0,024 para cerca de 0,042, e o W1 economiza mais.
4. **Como a run congelada trata a pesquisa?** Recomendo rodá-la sem o congelamento de pesquisa do `--experiment-manifest`: corpus importado antes, miss pesquisado ao vivo e gravado, digest e listagem do corpus no relatório antes e depois.
   - Com o congelamento ligado, todo miss volta vazio. Isso tira a parte do caso de todas as findings e a parte shared dos escopos sem entrada (16 a 23 de 23, M).
5. **Onde guardar o corpus da run congelada?** Recomendo uma cópia da pasta junto dos resultados da run, com a saída de `cognalys research-corpus list --json`, e não no git.
   - São resumos de páginas da web, e cada trace já registra a chave e o sha da entrada servida.
   - O artigo declara número de escopos, datas e custo de produção.
6. **W3 e W4 ficam fora até depois da run congelada?** Recomendo manter assim.
   - Valem 0,0017 e cerca de zero por finding, e mudariam a pesquisa.

## Revisão: o que mudou

| # | Ponto | Decisão | Motivo |
|---|---|---|---|
| 1 | Resposta cortada gravada ou importada | Aceito | Confirmado em `llm_providers.py:2866-2875`, `:2814`, `:2841` e `:3342-3354`. Entraram `pass_completed`, o `completed` em `_LAST_TRACE` (gravado em `_finish_trace`), o motivo `not_completed` e dois testes. Os 191 passes importáveis estão completos, então nenhum número muda (M). |
| 2 | Braços da sombra dividem a loja de aprendizado | Aceito | Confirmado em `fix_generation_common.py:126`, `knowledge.py:40-44` e `run_pipeline.py:1970-1972`. Cada braço parte de uma cópia (`TCC_LEARNING_DIR` mais `learning-dir`); as réplicas mantêm a loja compartilhada. |
| 3 | Preenchimento da run congelada não especificado | Aceito | Parte 3 da §6, passo 11 e pergunta 4. Medido em `frozen_cohort2.py`: 23 escopos para 43 findings, 20 servidas sem importação e 27 com ela. O congelamento do manifesto anula misses (`:2441-2451`). |
| 4 | Estabilidade da chave depois de Q1 e Q2Q3 | Aceito | A conferência foi movida para antes de gastar (depois da primeira pasta fria), com o caso de preenchimento por v9, a variante "corpus primeiro" e a nota de que importar de novo não repara. Os desenhos de Q1 e Q2Q3 não mexem na pesquisa (M, busca); as especificações finais não foram verificadas. |
| 5 | NO_SAFE_FIX fora do portão; 16 citações sem leitura | Aceito | Reproduzido: 14 sob NO_SAFE_FIX, 0 como evidência única (M, `nofix_residual.py`, `residual_stats.py`). Resultado posto na §5. As 14 sob decisões que geram ficam para a revisão (passo 8), com regra de listar sem reprovar. |
| 6 | "Citado" ignora predicados | Aceito | Reproduzido: 32, 5 e 0 nos baldes de dispensa; 51, 21 e 5 nas outras (M, `pred_gap.py`). `cited()` passa a incluir predicados decididos. A conclusão não muda. |
| 7 | Poder da sombra e custo | Aceito | `shadow_cost.py` reproduz US$ 5,86 com 113 hits de cache. O total passa de cerca de 15 para cerca de 18, com teto de 22. O desvio de D fica entre 2,1 e 3,0, e o critério que pode reprovar é a dispensa causada pelo corpus. Os limites de idade estão declarados. |
| 8 | Nota de workers errada; replano pode ler outra resposta | Aceito | Com `replace=False` fica a primeira entrada. Entrou o memo de dois passes (`MEMO_PREFIX`, `_replay_two_pass`) consultado antes do corpus, com teste. |
| 9 | Laço de importação por finding | Aceito | Duas chaves são compartilhadas (`c64073be`, `6128fe99`). A importação virou uma chamada só. |
| 10 | Tabelas do artigo e manifesto sem o corpus | Aceito | Coluna e frase só quando há hits; campo `research_corpus` (nome distinto de `corpus`, que já é o CSV) comparado por digest; byte-idêntico sem a opção. |
| 11a | Comparação de `request` sem ida e volta por JSON | Aceito | `canonical` e um predicado `valid` único para `get` e `put`. |
| 11b | Origem dos campos de `recorded_pass_identity` não definida | Aceito | Tabela de origem na §3.1. O texto da pesquisa vem do trecho do input da síntese. |
| 11c | Comentário da ferramenta Anthropic | Aceito | `git log -S` dá `de1c4d3` (2026-10-28), e os 34 traces Anthropic estão `prepared`. |
| 11d | Critério "guard = `cognalys cost`" impossível de rodar | Aceito | Confirmado em `cost.py:600-619`. Trocado por: traces do corpus com 0 tokens e 0 buscas, contagem igual à de unidades servidas, e teste unitário do guard. |
| 11e | Bypass de cache e corpus | Aceito, como desenho e não como pergunta | O bypass pula a leitura do corpus; a entrada válida fica. |

**O que continua sem verificação**
- As especificações finais de Q1 e Q2Q3 (`q1_final` e `q2q3_final` estão vazias). A conferência de chave da §6 cobre isso antes de gastar.
- Idades acima de 21 dias.
- O comportamento da Anthropic no corpus, porque não há passe completo gravado.
- As 3 findings do benchmark sem passe shared gravado (`cometotp_unwrap`, `trilium_kmf`, `notallyx_md5`).
- A leitura das 14 citações sob decisões que geram, que fica para o passo 8.

Os scripts desta correção estão em `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\k3_final\` (`frozen_cohort.py`, `frozen_cohort2.py`, `rerun_review.sh`).