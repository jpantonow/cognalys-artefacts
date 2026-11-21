> Especificação de Q2Q3, escrita em 2026-11-10 para ser implementada por uma sessão Opus 5.5 seguindo docs/PLANO_FABLE.md (desenho e revisão adversarial no Fable 5.2; versão final no Opus 5.5). Os scripts citados ficam em `outputs/design_20261110/scratch/` (não versionado; arquivos acima de 2 MB não foram copiados, e os caches grandes são refeitos pelos próprios scripts). As perguntas da seção 9 esperam a resposta do usuário antes da implementação das partes que dependem delas.

# Q2Q3: especificação corrigida, pronta para implementar (2026-11-10, HEAD `af71cfe`)

Convenções: (M) é medido por script sobre os registros em `outputs/`; (E) é estimativa, com a base ao lado. Nada foi editado no repositório nem no worktree, e nenhuma chamada paga foi feita.

Pastas de trabalho:
- Scripts e dados desta versão: `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\q2q3_final\` (abaixo, "pasta final").
- Scripts da primeira especificação: `...\wf5\q2q3_design\`.
- Scripts da revisão adversarial: `...\wf5\q2q3_review\`. Rodei de novo `r4_bounds.py`, `r5_counts.py` e `r9_il_chain.py`.

Os prompts novos estão gravados por inteiro, para conferir byte a byte:
- `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\q2q3_final\review_system_new.txt` (trechos A a E e G)
- `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\q2q3_final\review_system_new_with_F.txt` (com F, só se a pergunta 1 for sim)
- `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\q2q3_final\extraction_system_new.txt`
- `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\q2q3_final\prompts.json` (trechos antigos e novos, hashes)

## 1. Resumo

### Entra

| # | Mudança | Número que decide |
|---|---|---|
| E1 | `function_contract` no schema e no prompt da extração. Segue para a revisão só quando a dependência é `established`, o par é `third_party_service` ou `user_operated_peer`, e há ao menos uma citação literal, conferida por código, de uma entrada do repositório que não seja a fonte relatada nem a especificação | O pacote do Input Leaf traz SRC-004 (`InputLeapConnection.kt`) e TEST-004 (`InputLeapTestServer.kt`, servidor self-signed) em todas as 7 execuções. A extração cita SRC-004 de 1 a 3 vezes, e TEST-004 e SRC-005 nenhuma (M, `q2q3_design/il_all.py`, confirmado pela revisão) |
| E2 | O prompt da revisão passa a dizer uma coisa só sobre remover algo obsoleto ou inseguro que o código oferece a um par ou servidor ou aceita dele: o parágrafo "What the fix removes", com cinco evidências fechadas e escopo explícito. Trechos A, B, D, E e G; contrato 20 | Das 29 revisões do Input Leaf (13 gravadas e 16 reenviadas), 17 marcam a perda de descoberta e 12 não (M, `r5_counts.py` rodado de novo; o run 0014 marca: `il_run0014.py`). Das 3 segundas revisões que receberam a perda em `previous_regressions`, 2 a desculparam (M, `r9_il_chain.py`) |
| E3 | `concern_verdicts` no schema da revisão. A regra "fazer o que uma preocupação adverte é regressão" fica, com uma exceção: a preocupação sobre remover algo obsoleto ou inseguro é decidida por "What the fix removes" (trecho C). Nenhum portão em código lê o campo | 466 de 492 revisões gravadas têm preocupações (M, `concern_removal.py`). Só 2 vereditos gravados mudam por essa exceção, ambos do Grocy (TLSv1.1), e o próprio prompt já proíbe esse cenário (linhas 2007-2009) |
| E4 | Uma frase sobre a chamada proibida que o patch mantém ou move (trecho F). **Precisa do sim do usuário** (pergunta 1) | 8 de 57 `no_effect` gravados são migrações com leitura legada: RedReader 4, InterTune 4 (M, `q2q3_design/moved_call.py`, confirmado pela revisão) |
| E5 | `cognalys decision-replay --current-prompt` e `--add-section NAME=PATH`, mais o estágio `evidence_extraction` | Hoje o comando recusa qualquer pedido cujo hash difira do gravado (`decision_replay.py:325-328`), então um prompt mudado não tem como ser reenviado |

### Cortado ou adiado

- **Contrato na adjudicação: adiado** (pergunta 2). A adjudicação do Input Leaf decide o mesmo nas 7 execuções: FIX_SUPPORTED, `authorized_behavior_change.authorized = true` (M). A adjudicação lê a extração sem o campo.
- **Contrato no planejador: cortado.** O plano só recebe `validated_claims`.
- **Portão em código sobre `concern_verdicts`: cortado.** Não há registro onde medir (0 de 492, M).
- **`--llm-model` no replay: cortado.** Cada controle é reenviado no modelo em que foi gravado. Assim o prompt é a única mudança.
- **Fato determinístico de pinning ou TOFU: não fazer** (triagem léxica recusada pelo usuário).
- **Proxy léxico para contar contratos: descartado.** Marca 570 de 1.283 unidades (M, `q2q3_design/contract_proxy_stats.py`).
- **Acumular `previous_regressions` entre tentativas: não fazer agora.** O contrato vai em todo payload de revisão, então a terceira revisão também o recebe. O laço de geração pertence ao DL6r.

### Movimento esperado por balde, dispensa primeiro

- **Falso positivo ou evidência insuficiente: 0 (E).** A adjudicação recebe a extração sem o campo e o mesmo texto de instruções. O canal lateral (a frase nova mudar outras partes da extração) é medido no P1 por `violated_predicates`, `needs_more_evidence` e `requested_evidence`.
- **"Violação sem impacto": 0 esperado (E).**
  - O risco é uma revisão ganhar regressão com `current_code_is_correct_use`, e a unidade terminar nesse balde (`coordinated_generation.py:3193`, `finding_processing.py:1299-1307`).
  - No Input Leaf, 0 das 19 revisões com regressão marcam o flag (M, `q2q3_design/correct_use_check.py`).
  - No corpus todo, 60 revisões de 19 achados marcam (M, `r5_counts.py`). O P2 mede o flag em pyLoad, Cryptomator e ICSdroid, que estão entre esses 19.
  - Com F, 1 execução sai desse balde: RedReader v5.
- **PR pronto para regressão:** as 5 linhas PR pronto do Input Leaf e, talvez, weeWX. É o efeito pretendido.
- **Regressão para limpo:** 2 vereditos gravados do Grocy (TLSv1.1 assumido) (E, `concern_removal.py`). Nas opções do pyLoad e do Ursa, nada muda (E).

### Custo

- **Por finding (E):** +0,003 a +0,006 em standard e +0,0015 a +0,003 em Flex.
  - Medido: +2.126 caracteres no prompt da extração e +2.460 no da revisão (M, `build_prompts.py`); 2,0 extrações e 1,11 revisões por finding no run_v6 dos 9 achados (M, `calls_per_finding.py`).
  - Estimado: 150 a 350 tokens de saída a mais por extração e cerca de 120 por revisão, a US$ 1/5 por milhão em standard (gpt-6.1-terra).
- **Validação paga (seção 6):**
  - P1 + P2: cerca de US$ 2,38 em Flex (limite US$ 4,76 em standard).
  - P3: cerca de US$ 2,2.
  - O US$ 1,7 dos documentos estava abaixo, porque não tinha controles de regressão nem a segunda revisão.

### O que precisa do sim do usuário

- Só E4 (pergunta 1).
- E3 muda a doutrina das preocupações só para remoção de algo obsoleto ou inseguro, que o CLAUDE.md já chama de hardening. Fica como texto proposto na pergunta 3.

## 2. Diagnóstico medido

### Base

- 492 pedidos de revisão gravados e 30 amostras de reenvio (M, `r5_counts.py`).
- Vereditos gravados: 318 regressão, 116 limpo, 57 sem efeito, 1 ilegível.
- 41 gravados e as 30 amostras usam o prompt do contrato 19 byte a byte: sha256 `21da6a1daf628cfd538eb782715f9c9287e83d210d730a7b16e74c76a9272429`, 22.095 caracteres (M, `build_prompts.py`).

### Causa 1: a extração não diz quem o código atende

- A adjudicação lê só a extração (`finding_adjudication.py:790-804`). A revisão lê só os arquivos editados mais as seções do payload.
- No Input Leaf run_v6, o catálogo do pacote tem 17 entradas (M, `il_packet_shape.py`):
  - SRC-001 `target_source` `ServerScanner.kt`;
  - SPEC-001 `verified_specification`;
  - SRC-004 `related_source` `InputLeapConnection.kt` (21.395 caracteres);
  - TEST-004 `test_source` `InputLeapTestServer.kt` (7.012 caracteres).
- Toda entrada de fonte tem `data.content`. Entradas estruturadas (FLOW, SEQ, CALLEE, RUN) não têm.
- A leitura determinística `deliberate_insecurity` aparece em 1 de 492 payloads de revisão e em 0 do Input Leaf (M).

### Causa 2: o prompt da revisão licencia vereditos opostos (`coordinated_generation.py`)

| Linhas | O que diz |
|---|---|
| 1971-1973 | Regressão é aceitar ou confiar em dado diferente, "other than no longer committing the reported violation itself" |
| 1986-1991 | Remover comportamento deliberado e incondicional é regressão, mas só quando `deliberate_insecurity` existe |
| 1996-1998 | Fazer o que uma preocupação da adjudicação adverte é regressão |
| 2185-2186 | Uma preocupação que só pede revisão não é motivo para parar |
| 2014-2022 | Deixar de aceitar algo inseguro é hardening "unless the evidence shows the app depends on it", sem dizer o que conta como evidência |
| 2026-2030 | No caminho que o usuário liga, remover o que a opção habilita "beyond the reported violation itself" é regressão |
| 2222-2228 | Em `previous_regressions`, "a scenario that is only the removal of the reported violation … is not one" |

No Input Leaf run_v6 (M, `r9_il_chain.py`):
- A revisão 0010 marca regressão com origem no plano.
- A 0012 recebe a perda em `previous_regressions` e a desculpa: "a direct consequence of no longer accepting that certificate, not a separate regression".
- A 0014 recebe só o deslize da 0012 (`previous_regressions` leva apenas as regressões da última revisão, linhas 3106-3109) e sai limpa. Resultado: PR pronto.
- No v4 aconteceu o mesmo na 0013.

No payload do v6 (M, `il_v6_payload.py`):
- A preocupação 1 é "Review the effect on existing discovery servers, particularly those using untrusted or self-signed certificates", um pedido de verificação.
- Um invariante do plano autoriza o estreitamento: "certificates rejected by platform trust are no longer accepted".
- `plan_forbidden_changes` proíbe "broaden certificate … acceptance".

### Onde a regra já funciona

Quando a evidência está no arquivo revisado ou numa opção, o veredito é estável (M, `q2q3_design/tls_all_reviews.py`):
- 3x-ui `allowInsecureTls`: 6 de 6;
- Ursa `insecure=true`: 6 de 6;
- pyLoad `ssl_validate=false`: 5 de 5;
- LocalSend: 5 de 5.

### Causa 3: `patch_has_no_effect`

- As linhas 2092-2095 não dizem nada sobre a chamada mantida só para ler dado antigo.
- As revisões do RedReader concordam nos fatos e divergem no campo: "tem efeito" em 4 de 7 gravadas e 5 de 6 reenviadas; no InterTune, 21 de 25 (M, `moved_call.py`).
- Nas revisões gravadas, o RVSec segue relatando a chamada legada quando ela roda.

### PRs prontos de TLS

- 47 linhas PR pronto de família TLS, de 22 achados (M, `q2q3_design/tls_pr_ready.py`).
- 17 linhas têm revisão gravada, com 16 revisões finais distintas: 5 do Input Leaf e 11 de outros achados.
- Das 11:
  - **7 têm de continuar limpas:** Clash, Hermes, Trilium, Grocy run_v6, Cryptomator Lite run_ab61, Fread run_v2, tofshare run_v2.
  - **weeWX** é informativo.
  - **ICSx⁵, Olvid e Tasks** ficam fora: foram aprovados sob contratos antigos para patches que os contratos 5 a 10 chamam de sem efeito.
- As outras 30 linhas são de 10 a 22/10 e não têm revisão gravada.

### Achados que aceitam todo certificado

30 unidades em 13 achados (M, `q2q3_design/trust_all_units.py`). Pela leitura (E):
- **9 sob opção do usuário.** Agora recebem `activation = user_enabled_option` e `not_established`, porque a frase nova tira "an option exists" de `established`. Seguem cobertos pela regra da opção.
- **3 incondicionais para par operado pelo usuário:** Input Leaf, weeWX, LocalSend. São os candidatos a contrato estabelecido.
- **1 de serviço de terceiros:** zSMTH `getUnsafeOkHttpClient`.

## 3. Mudanças

### 3.1 `src/cognalys/llm/llm_contracts.py`, `evidence_extraction_schema` (69-230)

No nível do módulo, logo antes de `evidence_extraction_schema`:

```python
# Who the reported code serves (function_contract), read by the evidence
# extraction and carried to the behaviour review only when established
# (adjudication_rules.established_function_contract).
FUNCTION_CONTRACT_COUNTERPARTS = ("third_party_service", "user_operated_peer", "same_app_or_device", "unknown")
FUNCTION_CONTRACT_ACTIVATIONS = ("always", "user_enabled_option", "build_flag", "unknown")
FUNCTION_CONTRACT_DEPENDENCE = ("established", "not_established")
```

Dentro da função, antes do `return`:

```python
    function_contract = _object({
        "purpose": _string(),
        "counterpart": _string(list(FUNCTION_CONTRACT_COUNTERPARTS)),
        "activation": _string(list(FUNCTION_CONTRACT_ACTIVATIONS)),
        "depends_on_reported_behavior": _string(list(FUNCTION_CONTRACT_DEPENDENCE)),
        "dependence": _string(),
        "evidence_ids": _array(_string()),
        "quotes": _array(_object({"evidence_id": _string(), "quote": _string()})),
    })
```

No `_object` devolvido, acrescentar `"function_contract": function_contract` como **última** propriedade, depois de `"family_assessment"`, para todas as famílias. As definições ficam só no prompt (3.2), sem descrições no schema.

### 3.2 `src/cognalys/llm/finding_adjudication.py`

**(a) Prompt da extração.** Hoje `extraction_system` é montado inline (641-697).
- Mover a montagem para `def evidence_extraction_system_prompt(reported: bool) -> str:` no nível do módulo, sem mudar um byte.
- Chamar `extraction_system = evidence_extraction_system_prompt(bool(reported))`.
- Acrescentar a constante abaixo no nível do módulo, logo depois de `_REPORTED_BY_DETECTOR_EXTRACTION_RULE`.
- Inserir `+ _FUNCTION_CONTRACT_EXTRACTION_RULE +` entre `"the gate; it never refutes the reported violation. "` e `"Return only the requested structured output."`. Na fonte, esses dois textos estão no mesmo literal (695-696); partir o literal ali.

```python
# Who the reported code serves. Input Leaf ServerScanner.discoverySslContext
# (agent_baseline_20261108): the packet held the fingerprint-pinned session
# connection (SRC-004) and a self-signed test server (TEST-004) in all 7
# executions; the extraction cited the first one to three times and the second
# never, and the review then judged the trust narrowing without them.
_FUNCTION_CONTRACT_EXTRACTION_RULE = (
    "function_contract says who the reported code serves, from cited evidence only. purpose: in one "
    "sentence, what the reported code does for the app and which callers reach it. counterpart: who is on "
    "the other side of the reported operation -- third_party_service (servers the app's author does not run "
    "and any user reaches: a public site or API), user_operated_peer (a server, device or peer the user runs, "
    "configures or pairs with: self-hosted, on the local network, another installation of the app), "
    "same_app_or_device (the value stays in this app or on this device: stored data, a key store, a cache) "
    "or unknown. activation: when the reported code runs -- always, user_enabled_option (only after the user "
    "turns on an option, setting or preference the evidence shows), build_flag (only in a debug or flavoured "
    "build) or unknown. depends_on_reported_behavior is established only when the counterpart is "
    "third_party_service or user_operated_peer and repository evidence other than the reported source and "
    "the specification shows that this counterpart cannot meet what the rule asks the reported code to "
    "enforce instead. Look beyond the reported method at how the rest of the packet treats the same "
    "counterpart: another connection to the same peer or server that accepts it by a fingerprint, a pin, a "
    "first-use approval or a certificate the user supplies rather than the platform trust store; a test "
    "fixture for that peer that presents a self-signed certificate; a protocol or format the counterpart "
    "fixes. Say in dependence what the counterpart needs and how that evidence shows it, list the entries in "
    "evidence_ids, and copy into quotes, for each entry, the exact lines of its content that show it (at most "
    "three quotes, each at most 400 characters, verbatim). Otherwise answer not_established with an empty "
    "dependence and no quotes: permissive or outdated code does not by itself show that anyone needs it, and "
    "an option that turns the reported code on is recorded in activation, not here. The field describes the "
    "use of the code: it never confirms or refutes the reported violation, and every predicate is assessed "
    "as before. "
)
```

Hashes para `reported=False`:
- prompt novo `da61ef4f6cf37ce65adf527f77bac7483771e05d9050bb78d05d4e400afee6a6` (5.400 caracteres);
- sem a constante, `cffc286584c159fca01d4b24157f5f01090089f897f5a21c777354946099c91f` (3.274), o prompt gravado no run_v6 (M, `build_prompts.py`).

**(b) A adjudicação não lê o campo.** Nas linhas 794 e 1606, trocar `"validated_evidence_extraction": extraction` por `"validated_evidence_extraction": extraction_for_adjudication(extraction)`, importada de `adjudication_rules` como `sanitize_evidence_extraction` já é.

### 3.3 `src/cognalys/llm/adjudication_rules.py`

Importar as três tuplas de `llm_contracts`. Acrescentar no nível do módulo:

```python
# The entries that cannot show who the reported code serves: the reported code
# itself and the rule. A dependence must be shown by something else.
_CONTRACT_EXCLUDED_KINDS = frozenset({"target_source", "verified_specification"})
_CONTRACT_QUOTES = 3
_CONTRACT_QUOTE_MIN, _CONTRACT_QUOTE_MAX = 12, 400
_CONTRACT_SERVED = ("third_party_service", "user_operated_peer")
NEUTRAL_FUNCTION_CONTRACT = {
    "purpose": "", "counterpart": "unknown", "activation": "unknown",
    "depends_on_reported_behavior": "not_established", "dependence": "",
    "evidence_ids": [], "quotes": [],
}
FUNCTION_CONTRACT_MEANING = (
    "the evidence extraction's reading of who the reported code serves and why they depend on the "
    "reported behaviour, with the repository text it quotes; each quote was checked to appear in the "
    "entry it names. It is a reading of the repository, not a maintainer statement."
)
```

**`_entry_text(entry: dict) -> str`.**
- Devolve `data["content"]` quando é string.
- Senão, devolve `json.dumps(data, ensure_ascii=False, sort_keys=True)`, onde `data = entry.get("data") or {}`.
- Comparação sempre com `" ".join(text.split())` dos dois lados.

**`_sanitized_function_contract(value, packet: dict, valid_evidence: set) -> tuple[dict, list[str]]`.** Aplica, nesta ordem:
1. Valor que não é `dict` vira `deepcopy(NEUTRAL_FUNCTION_CONTRACT)`. Nota: `"function_contract: not an object; replaced by the neutral contract"`.
2. `counterpart`, `activation` e `depends_on_reported_behavior` fora das tuplas viram `"unknown"`, `"unknown"` e `"not_established"`. Nota: `"function_contract: <field> <value!r> is not a known value; set to <new>"`.
3. `purpose` e `dependence` viram `str`.
4. `evidence_ids`: mantém só ids em `valid_evidence`, sem repetição e na ordem. Nota: `"function_contract: removed invalid evidence reference(s) [...]"`.
5. `quotes`: mantém só os itens `dict` que cumprem tudo isto:
   - `evidence_id` está em `evidence_ids`;
   - a entrada de mesmo id em `packet["evidence_catalog"]` existe e seu `kind` não está em `_CONTRACT_EXCLUDED_KINDS` (ids WEB e RUN de fora do catálogo do repositório não contam);
   - `quote.strip()` tem de 12 a 400 caracteres;
   - o texto colapsado da citação está no texto colapsado da entrada.
   
   Fica com no máximo 3. Nota única quando algo cai: `"function_contract: dropped <n> quote(s) not found in a repository entry other than the reported source and the specification"`.
6. `established` cai para `not_established` quando `counterpart` não está em `_CONTRACT_SERVED`, quando `dependence.strip()` é vazio ou quando nenhuma citação ficou. Nota: `"function_contract: depends_on_reported_behavior downgraded to not_established (<reason>)"`, onde `<reason>` é `counterpart <value>`, `empty dependence` ou `no verified quote`.

**`sanitize_evidence_extraction` (212-300).** Depois do laço dos predicados, só quando a chave `function_contract` existe:

```python
    if "function_contract" in sanitized:
        sanitized["function_contract"], contract_notes = _sanitized_function_contract(
            sanitized["function_contract"], packet, valid_evidence)
        notes.extend(contract_notes)
```

Extração sem a chave fica sem ela e sem nota.

**`extraction_for_adjudication(extraction: dict) -> dict`.** Cópia rasa sem a chave `function_contract`. Não altera o argumento.

**`established_function_contract(extraction: dict, packet: dict) -> dict`.**
- Recalcula `_sanitized_function_contract` sobre `extraction.get("function_contract")`, com o catálogo do pacote (repositório mais externo). Assim um registro não sanitizado não passa.
- Devolve `{}` salvo quando o resultado é `established`.
- Nesse caso devolve, nesta ordem de chaves:

```python
{"purpose": ..., "counterpart": ..., "activation": ...,
 "depends_on_reported_behavior": "established", "dependence": ...,
 "evidence": [{"evidence_id": ..., "kind": ..., "source_file_path": ..., "quote": ...}],  # one per kept quote
 "meaning": FUNCTION_CONTRACT_MEANING}
```

`source_file_path` vem de `data.source_file_path` ou `data.path` da entrada; senão é string vazia.

### 3.4 `src/cognalys/llm/adjudication.py`, `_analysis_record` (415-422)

Depois de `"patch_plan": plan,`:

```python
            **({"function_contract": contract}
               if (contract := established_function_contract(extraction, packet)) else {}),
```

O replano (`_take_new_plan`, `coordinated_generation.py:1624-1662`) chama `adjudicate_finding` de novo. Portanto o registro novo leva o contrato da extração nova, sem código adicional.

### 3.5 `src/cognalys/llm/coordinated_generation.py`

**(a)** Mover o `system = (...)` de `review_patch_behavior` (1961-2287) para `def behavior_review_system_prompt() -> str:` no nível do módulo, com os mesmos bytes, e chamar `system = behavior_review_system_prompt()`.

**(b) Payload.** Depois do bloco de `key_provider_constraints` (1958-1960), como última chave:

```python
        # Who the reported code serves, when the evidence extraction established
        # that they depend on the reported behaviour (Input Leaf: the pinned
        # session connection and the self-signed test server). Last, so every
        # other section's bytes stay as they were.
        contract = ((error.get("evidence_analysis") or {}).get("v2") or {}).get("function_contract")
        if isinstance(contract, dict) and contract:
            payload["function_contract"] = contract
```

**(c) Trechos do prompt.** Cada trecho antigo ocorre exatamente uma vez no prompt gravado (M, `build_prompts.py`). Na fonte eles estão partidos em vários literais: editar os literais para que a concatenação dê exatamente o texto novo.

**A (1971-1974).**

De:
`accepts or trusts different data -- other than no longer committing the reported violation itself, and other than a property that follows necessarily from the replacement value the fix requires: `

Para:
`accepts or trusts different data -- other than no longer committing the reported violation itself (what that covers for a peer or server is stated under 'What the fix removes', below), and other than a property that follows necessarily from the replacement value the fix requires: `

**B (1986-1991).** Substituir:
`When deliberate_insecurity shows the behaviour deliberate and unconditional, a patch that removes it and leaves the users who rely on it no way to keep doing what they did (connecting to their own server, loading their content) is a regression for them: name that scenario. Where an option gates it, a patch that empties the option is one too. `

Por:
```
What the fix removes: no longer enabling, accepting or trusting something deprecated or insecure (an old protocol version, a weak algorithm, a certificate nothing verified) is hardening the maintainer judges in review, not a regression, unless the payload shows that the peers or servers this code talks to, or the users who reach them through it, depend on what is removed. Five things show that: function_contract, when present -- the evidence extraction's reading of who the reported code serves and why they cannot meet what the rule asks for instead, with the repository text it quotes (another connection to the same peer that accepts its certificate by a fingerprint, a pin or a first-use approval rather than the platform trust store; a test server for that peer with a self-signed certificate); deliberate_insecurity showing the behaviour deliberate and unconditional; an option, preference or setting in the code that turns the behaviour on (an 'allow insecure' or 'trust all certificates' option for self-hosted servers); code in files_before_the_patch, other than the reported code itself, that handles the same peer or server in a way the stricter behaviour would refuse; and a response of this repository's maintainers (maintainer_responses_to_this_rule_in_this_repository) saying such peers or servers are supported. When one of them shows it, a patch that removes it and leaves the users who rely on it no way to keep doing what they did (connecting to their own server, loading their content) is a regression for them: name that scenario, and say in rationale which of the five it rests on. A patch that keeps them working another way that evidence supports (the check the app already applies to the same peer, a certificate the user supplied) is not one. Where an option gates it, a patch that empties the option is one too, and so is a patch that drops, in the path the option turns on, anything the option exists to allow, old protocols included: that breaks exactly the servers the option exists for. When none of the five shows it, the removal is not a regression: a class, method or variable name, the app's purpose alone, a peer or server you would have to assume, or an adjudication concern about that compatibility is not evidence that anyone depends on it; say so in rationale. This paragraph is about what the code offers to or accepts from a peer or server; data the app stored, plan_invariants and plan_forbidden_changes keep the rules this prompt gives them. 
```

**C (1996-2000).** Substituir:
`A patch that does what plan_forbidden_changes forbids, breaks a plan_invariant, or does what one of adjudication_concerns warns against is a regression: name the scenario -- unless what it forbids is only such a necessary property of the replacement value, which you name in rationale. `

Por:
```
A patch that does what plan_forbidden_changes forbids, breaks a plan_invariant, or does what one of adjudication_concerns warns against is a regression: name the scenario -- unless what it forbids is only such a necessary property of the replacement value, which you name in rationale, and except that a concern about no longer enabling, accepting or trusting something deprecated or insecure is decided under 'What the fix removes', above. Answer each adjudication_concerns entry in concern_verdicts, by its position in the list starting at 1: does_what_it_warns_against when this patch does the specific thing the concern warns against, does_not when it does not, asks_for_verification when the concern only asks someone to verify, test or review something this patch does not decide (a device, an endpoint, the analyzer). Each does_what_it_warns_against verdict has its scenario in regressions, unless one of the two exceptions just stated applies, which you then name in rationale. 
```

**D (2014-2022).** Apagar a frase inteira:
`No longer enabling or accepting something deprecated or insecure (an old protocol version, a weak algorithm) is hardening the maintainer judges in review, not a regression, unless the evidence shows the app depends on it -- or the code is a compatibility path the user turns on deliberately (an 'allow insecure' or 'trust all certificates' option for self-hosted servers), where dropping old protocols breaks exactly the servers the option exists for: that is a regression. `

**E (2026-2030).**

De:
`unless the replaced value is itself deprecated or insecure -- never in a compatibility path the user turns on deliberately, as above, where removing what the option enables, beyond the reported violation itself, is a regression. `

Para:
`unless the replaced value is itself deprecated or insecure -- never in a compatibility path the user turns on deliberately, where 'What the fix removes', above, decides. `

**G (2226-2228).**

De:
`a scenario that is only the removal of the reported violation, or a property that follows necessarily from the replacement value as above, is not one: say so in rationale. `

Para:
`a scenario that is only the removal of the reported violation, where 'What the fix removes' finds no one who depends on it, or a property that follows necessarily from the replacement value as above, is not one: say so in rationale. `

**F (2092-2095), só com o sim da pergunta 1.** Depois de `(a GCMParameterSpec finding in decrypt answered by reordering key creation). `, acrescentar:
```
A forbidden call the patch keeps or moves is judged by what its result is still used for: when what the app produces from now on comes from an allowed value (new data is named, keyed, signed or protected with it) and the forbidden call remains only to find, read, verify or migrate what earlier versions of the app produced, the patch has an effect -- say in rationale where the forbidden call remains and when it runs, and do not set patch_has_no_effect; when the result of the kept or moved call is still what the app uses for new data (the allowed value is computed and discarded, or only stored beside it), the patch has no effect. 
```

**Hashes do prompt novo da revisão** (M, `build_prompts.py`). Ficam só no script de checagem offline (seção 5), não nos testes:
- A a E e G: `4fa8e3a5b89fe6d24a4c5710f3b7236b594c0483627e95e2c058d411e93b1ce5` (24.555 caracteres);
- com F: `1da99ed68357855076d279c682cbddf185451b5a85227f0c57431359d003276a` (25.191);
- revertendo os trechos, volta a `21da6a1d…2429`.

Se o Q1 já tiver mudado o prompt antes, os hashes mudam. O script recalcula sobre o prompt de então.

**(d) Normalizador e retorno.** No nível do módulo:

```python
CONCERN_VERDICTS = ("does_what_it_warns_against", "does_not", "asks_for_verification")


def concern_verdicts(answered, count: int) -> List[Dict]:
    """The review's verdict on each adjudication concern, by 1-based position.
    An entry naming a position outside 1..count, a position that is not an
    integer or an unknown verdict is dropped; a position answered twice keeps
    its first answer. Recorded only: no gate reads it."""
    kept, seen = [], set()
    for item in answered if isinstance(answered, list) else []:
        if not isinstance(item, dict):
            continue
        position, verdict = item.get("concern"), item.get("verdict")
        if (isinstance(position, bool) or not isinstance(position, int)
                or not 1 <= position <= count or verdict not in CONCERN_VERDICTS
                or position in seen):
            continue
        seen.add(position)
        kept.append({"concern": position, "verdict": verdict})
    return kept
```

No dicionário que `review_patch_behavior` devolve depois do parse (2335-2379), antes de `"rationale"`:
`"concern_verdicts": concern_verdicts(parsed.get("concern_verdicts"), len(payload["adjudication_concerns"])),`

Os retornos antecipados (`all_edits_on_base` e `not_run`, 1820-1850) ficam sem a chave. `decision_replay.DECISION_FIELDS["behavior_review"]` já compara `concern_verdicts` (linhas 49-51 e 210-213), sem mudança.

**(e) Versão.** `REVIEW_CONTRACT_VERSION = 20`, ou o próximo número se o Q1 já tiver subido. Comentário acima da constante (linha 247):

```python
# 20: one statement of when removing something deprecated or insecure that the
#    code offers to or accepts from a peer or server is a regression ("What the
#    fix removes": only when function_contract, deliberate_insecurity, an option
#    in the code, other reviewed code handling the same peer or this
#    repository's maintainers show who depends on it), in place of the rules
#    that licensed opposite verdicts on code that trusts everything on purpose
#    (Input Leaf ServerScanner.discoverySslContext, agent_baseline_20261108: the
#    same platform-trust patch drew the regression in 17 of 29 recorded and
#    replayed reviews and passed in 12, and a second review excused it as "a
#    direct consequence of no longer accepting that certificate");
#    function_contract in the payload when the extraction established the
#    dependence; a concern about such a removal is decided by that rule, and
#    every concern is answered in concern_verdicts, recorded only; a forbidden
#    call kept or moved only to read what earlier versions produced is a change,
#    not a patch without effect (RedReader General.sha1: 3 of 7 reviews of the
#    same migration said no effect).
```

Sem o sim da pergunta 1, tirar a última oração (de `; a forbidden call kept` até o fim, terminando em `recorded only.`).

### 3.6 `src/cognalys/llm/llm_schemas.py`, `behavior_review_schema` (248-397)

Logo depois de `"regressions": _array(regression),` (277):

```python
        # The review's answer to each adjudication concern (contract 20),
        # recorded in behavior_review.json and read by no gate.
        "concern_verdicts": _array(_strict_object({
            "concern": {"type": "integer",
                        "description": "The concern's position in adjudication_concerns, starting at 1."},
            "verdict": _string(
                "does_what_it_warns_against: this patch does the specific thing the concern warns "
                "against; does_not: it does not; asks_for_verification: the concern only asks someone "
                "to verify, test or review something this patch does not decide.",
                enum=["does_what_it_warns_against", "does_not", "asks_for_verification"]),
        }), "One entry for each adjudication_concerns entry, in order; empty when there are none."),
```

Com a pergunta 1 em sim: na descrição de `patch_has_no_effect` (380), trocar `'analyzer-facing'); false for a change for which ` por `'analyzer-facing'; a forbidden call the patch keeps or moves only to find, read or migrate what earlier versions produced, while what the app produces from now on comes from an allowed value, does count as a change); false for a change for which `.

### 3.7 `src/cognalys/llm/pipeline_common.py` (112)

`CONTEXT_BUILDER_VERSION = "2.19"`, ou o próximo. Comentário:

```python
# 2.19 adds function_contract to the evidence extraction: who the reported
# code serves and whether they depend on the reported behaviour, with quotes
# checked against the repository entries they name (Input Leaf
# ServerScanner.discoverySslContext, agent_baseline_20261108). The packet is
# unchanged; an established contract goes to the behaviour review only.
```

### 3.8 `src/cognalys/evaluation/decision_replay.py` e `src/cognalys/cli.py`

**Estágio novo.**
- `REPLAY_STAGES["evidence_extraction"] = "evidence_extraction"`.
- `DECISION_FIELDS["evidence_extraction"] = ("function_contract", "violated_predicates", "needs_more_evidence", "requested_evidence")`.
- Em `decision_fields`:
  - `function_contract`, quando é objeto, vira a string `f"{depends_on_reported_behavior}/{counterpart}/{activation}"`;
  - `violated_predicates` é derivado: o número de itens de `predicate_assessment` com `status == "violated"`, quando a lista existe;
  - `requested_evidence` é comparado pelo tamanho, como as outras listas.

**Campo novo.** `RecordedRequest.variant: Dict[str, Any] = field(default_factory=dict)`.

**Função nova `current_prompt_request(request: RecordedRequest, stage: str, add_sections: Optional[List[Tuple[str, Any]]] = None) -> RecordedRequest`.** Ela:
- recusa com `ValueError` quando:
  - `request.matches_record` é falso;
  - `stage` não é `behavior_review` nem `evidence_extraction`;
  - há `add_sections` fora de `behavior_review`;
  - o bloco de dados não termina em `}` imediatamente antes da última ocorrência de `"\n</patch_and_source>"`;
  - o nome de uma seção já é chave do JSON do bloco.
- para `behavior_review`, usa `behavior_review_system_prompt()`, `behavior_review_schema()` e o nome de schema `"behavior_review"`;
- para `evidence_extraction`:
  - usa `evidence_extraction_system_prompt(reported)`, com `reported = _REPORTED_BY_DETECTOR_EXTRACTION_RULE in request.instructions`;
  - usa `evidence_extraction_schema(family)`, com `family = request.schema_name[:-len("_evidence_extraction_v2")]`;
  - recusa quando `schema_name` não termina assim.
- emenda cada seção no fim do bloco, sem reserializar o resto: `head[:-1] + "," + prompt_json(name) + ":" + prompt_json(value) + "}"`. É o mesmo byte que o código vivo produz ao pôr a chave por último (M, `q2q3_design/splice_check.py`, 12 de 12 pedidos; confirmado pela revisão);
- devolve `dataclasses.replace(request, instructions=..., json_schema=..., schema_name=..., input=..., recorded_sha256=trace_input_sha256(instructions, input), variant=...)`, com `variant = {"of_input_sha256": request.recorded_sha256, "prompt": "current", "added_sections": [names]}`.

O modelo, o esforço e o teto de saída ficam os gravados.

**Relatório.**
- `replay` grava `report["variant"]` quando existe. Cada amostra é conferida contra a variante: `_differs_from_record` já compara com `request.recorded_sha256`.
- `report_lines` acrescenta, depois da linha do pedido: `  variant: this code's instructions and schema on the recorded input (recorded input_sha256 <12>; added sections <names or none>); the recorded answer was given under the earlier prompt`.

**CLI.** O parser fica nas linhas 1321-1344 e a função em 955-972.
- `--stage` aceita também `evidence_extraction`.
- Opções novas:
  - `--current-prompt` (`store_true`), ajuda: `send the recorded input under the instructions and JSON schema this code builds today for the stage (behavior_review, evidence_extraction) instead of the recorded ones; the report says what it is a variant of`
  - `--add-section NAME=PATH` (`action="append"`), ajuda: `with --current-prompt and --stage behavior_review: append the JSON in PATH to the recorded data block as its last section NAME`
- `--add-section` sem `--current-prompt` sai com código 2 e a mensagem `decision-replay: --add-section needs --current-prompt`.
- Em `decision_replay()`, depois de `select_request`, quando `--current-prompt`: `request = replaying.current_prompt_request(request, arguments.stage, sections)`, onde cada `NAME=PATH` vira `(NAME, json.loads(Path(PATH).read_text(encoding="utf-8")))`.

**Como o prompt mudado é reenviado.** As instruções e o schema são os que o código de hoje monta para aquele estágio. A entrada é a gravada, com a seção emendada quando pedida. O modelo é o gravado. Sem as opções novas, pedidos, relatório e saída ficam idênticos byte a byte.

### 3.9 `docs/GUIA_DE_USO.md`

- Na seção `cognalys decision-replay` (307-340): sinopse, as duas opções, o estágio novo e um parágrafo sobre a variante.
- Depois do parágrafo de `reported_by_detector` (cerca da linha 221), um parágrafo sobre:
  - `function_contract`: o que é, as citações conferidas, quando segue para a revisão, e que a adjudicação e o plano não o leem;
  - "What the fix removes";
  - `concern_verdicts`: registro, sem portão.

### 3.10 O que muda e o que fica idêntico

**Muda:**
- todo pedido de extração: instruções com a frase a mais e schema novo. A entrada só muda nas duas strings de versão `"packet_version":"2.18"` e `"context_builder_version":"2.18"` (M, `il_packet_shape.py`);
- todo pedido de revisão: instruções e schema. A entrada só muda quando há contrato estabelecido, com uma chave a mais no fim.

**Fica idêntico byte a byte, para a mesma extração:** pesquisa externa e síntese, adjudicação e sua correção (instruções e entrada, porque `adjudication_sections` não carrega versões nem o campo), planejamento, drafts, texto de PR e de issue.

**Prova:** seção 5, itens 2 e 3.

**Efeitos colaterais conhecidos:**
- o bump do contrato marca como "revisão desatualizada" todo draft não publicado revisado até o 19;
- o cache local do LLM erra uma vez em toda extração e revisão.

## 4. Testes

### `tests/test_function_contract.py` (novo, no estilo de `tests/test_deliberate_insecurity.py`)

| Teste | O que afirma |
|---|---|
| `test_every_family_schema_ends_with_the_function_contract` | Nas famílias `tls`, `message_digest`, `cipher`, `dependency`, `key_generation` e uma genérica, a última propriedade é `function_contract`, obrigatória, com os enums das três tuplas e `quotes` como lista de objetos estritos |
| `test_an_established_contract_quoting_another_repository_entry_is_kept` | Pacote mínimo com `SRC-001 target_source` e `SRC-002 related_source`, citação literal de SRC-002: nada muda e não há nota |
| `test_a_quote_not_in_its_entry_or_from_the_reported_source_is_dropped` | Citação de SRC-001, de SPEC-001 ou fora do texto de SRC-002 cai, e o contrato vira `not_established` com a nota `no verified quote` |
| `test_a_quote_matches_across_whitespace_and_in_structured_entries` | Quebras de linha e espaços colapsados casam; uma entrada sem `content` casa contra o JSON de `data` |
| `test_an_unknown_or_same_app_counterpart_or_an_empty_dependence_is_never_established` | Os três casos caem para `not_established` com nota |
| `test_a_contract_that_is_not_an_object_becomes_neutral` | O valor vira `NEUTRAL_FUNCTION_CONTRACT`, com nota |
| `test_an_extraction_without_the_field_stays_without_it` | Registro antigo não ganha chave nem nota |
| `test_only_an_established_contract_is_carried_with_its_quotes` | `established_function_contract` devolve `{}` ou o objeto com `evidence` (id, kind, path, quote) e `meaning`; recalcula a sanitização sobre um registro não sanitizado |
| `test_the_adjudication_reads_the_extraction_without_the_field` | `extraction_for_adjudication` tira só a chave e não altera o argumento; o código de `finding_adjudication` chama a função nos dois lugares (`source.count("extraction_for_adjudication(extraction)") == 2`) |
| `test_the_analysis_record_carries_it_only_when_established` | `v2` de `_analysis_record` tem a chave só nesse caso |
| `test_the_extraction_prompt_states_the_field_once_after_the_intent_rule` | `function_contract says who the reported code serves` aparece uma vez, depois de `An INTENT deliberate_insecurity entry` e antes de `Return only the requested structured output.`; com `reported=True`, a regra do detector vem antes; sem a constante, o texto é o anterior (comparar removendo a constante) |
| `test_the_review_payload_carries_an_established_contract_last` | Com `_coordinated_run(..., error_extra={"evidence_analysis": {..., "v2": {"function_contract": CONTRACT}}})`, a última chave do JSON é `function_contract`; sem contrato, a string não aparece no prompt |
| `test_the_review_prompt_says_one_thing_about_what_the_fix_removes` | `What the fix removes:` aparece uma vez; contém `Five things show that`, `other than the reported code itself`, `something deprecated or insecure (an old protocol version, a weak algorithm, a certificate nothing verified)` e `data the app stored, plan_invariants and plan_forbidden_changes keep the rules this prompt gives them`; não contém `unless the evidence shows the app depends on it` nem `beyond the reported violation itself`; contém `where 'What the fix removes' finds no one who depends on it` |
| `test_a_concern_about_removing_something_insecure_is_decided_by_that_rule` | O prompt contém `except that a concern about no longer enabling, accepting or trusting something deprecated or insecure is decided under 'What the fix removes', above` e mantém `does what one of adjudication_concerns warns against is a regression` |
| `test_the_review_schema_asks_a_verdict_per_concern` | Tipo inteiro, enum e objeto estrito, logo depois de `regressions` |
| `test_verdicts_are_recorded_by_position_and_unknown_ones_dropped` | Posição fora do intervalo, `True` como posição, veredito desconhecido e posição repetida |
| `test_a_does_what_it_warns_against_verdict_alone_is_not_a_regression` | Revisão com esse veredito e `regressions` vazio termina `complete`; `behavior_review.json` guarda o veredito |
| `test_a_forbidden_call_kept_only_for_earlier_data_is_a_change` | Só com a pergunta 1: a frase F está no prompt e a oração está na descrição do schema |
| `test_versions_record_the_change` | `REVIEW_CONTRACT_VERSION >= 20` e `CONTEXT_BUILDER_VERSION >= "2.19"` (comparar como tupla de inteiros) |

### `tests/test_applied_edits_match_text.py`

Em `test_the_insecure_value_carve_out_never_covers_an_option_the_user_turns_on` (1354-1368):
- trocar o trecho afirmado em 1359-1363 por `"what that choice gave; after: what it gives now), unless the replaced value is itself deprecated or insecure -- never in a compatibility path the user turns on deliberately, where 'What the fix removes', above, decides."`;
- acrescentar `self.assertNotIn("beyond the reported violation itself", system)`.

As outras afirmações continuam valendo, inclusive a de 1350 (`not, by that alone, deprecated or insecure`) e a de 1367.

`test_the_review_payload_and_prompt_carry_it` em `tests/test_deliberate_insecurity.py:364-376` continua passando: B mantém `a patch that removes it and leaves the users who rely on it no way to keep doing what they did`.

### `tests/test_decision_replay.py` (acrescentar)

- `test_the_current_prompt_replays_the_recorded_input_under_this_codes_instructions_and_schema`
- `test_an_extraction_replay_takes_the_family_and_the_detector_rule_from_the_record`
- `test_an_added_section_is_spliced_last_into_the_recorded_data_block`
- `test_a_section_already_in_the_payload_a_stage_without_a_builder_or_a_record_that_does_not_rebuild_is_refused`
- `test_a_variant_is_checked_against_its_own_digest_and_reports_what_it_is_a_variant_of`
- `test_an_extraction_replay_compares_the_contract_and_the_violated_predicates`
- `test_add_section_without_current_prompt_exits_2`
- `test_without_the_new_options_the_request_and_report_are_unchanged`

## 5. Checagem offline de aceitação

### Já rodado agora (pasta final, salvo indicação)

| Script | Resultado (M) |
|---|---|
| `build_prompts.py` | Os 7 trechos antigos ocorrem uma vez cada no prompt do contrato 19; reverter devolve o gravado byte a byte; extração idem; hashes e deltas da seção 3 |
| `q2q3_review/r5_counts.py` (de novo) | 492 e 30; 318, 116, 57 e 1; Input Leaf 17 de 29 marcam (5 de 13 gravados, 12 de 16 reenviados); 60 revisões de 19 achados com `current_code_is_correct_use` e regressão |
| `il_run0014.py` | A revisão 0014 do `run` marca a perda ("Scanning a TLS-only Input Leap server with a self-signed certificate") |
| `q2q3_review/r9_il_chain.py` (de novo) | 3 segundas revisões com a perda em `previous_regressions`, 2 a desculpam; as terceiras (v4 0013 e v6 0014) não a recebem |
| `concern_removal.py` | 7 regressões apoiadas só em preocupação sobre remover aceitação: IL `run` 1, pyLoad 1, Grocy TLSv1.1 2, Cryptomator v2 1, Ursa 2. Pela leitura, só as 2 do Grocy perdem o apoio; pyLoad e Ursa têm opção; a do Cryptomator v2 é sobre `init(null)` mantido; a do IL depende do contrato |
| `paid_plan.py` | Os 35 pedidos do plano pago reconstroem o hash gravado; custos e tetos da seção 6 |
| `q2q3_review/r4_bounds.py` (de novo) | Confirma o defeito de teto: as extrações do Grocy run_v6 e do Cryptomator ab61 são `cache_hit` com limite de US$ 0,22 e 0,25 |
| `cache_origin.py` | As chamadas enviadas de verdade com a mesma entrada: Grocy `run` `…_0036` e Cryptomator `run_v9` `…_0006` |
| `request_catalog.py` | Cada pedido de extração gravado traz o catálogo com `kind` e `data.content` nas entradas de fonte. O script de cola lê dali |
| `calls_per_finding.py` | 2,0 extrações e 1,11 revisões por finding no run_v6 dos 9 achados |

### Movimento simulado por leitura, dispensa primeiro (E)

| Movimento | Quantidade |
|---|---|
| Para falso positivo ou evidência insuficiente | 0 |
| Para sem impacto | 0 esperado; o P2 confere o flag |
| De sem impacto para fora | 1 execução (RedReader v5), só com E4 |
| De `no_effect` para "tem efeito" | 8 revisões de 2 achados, só com E4 |
| De regressão para limpo | 2 vereditos gravados do Grocy TLSv1.1 |
| De limpo para regressão | as revisões finais do Input Leaf, desde que a extração estabeleça o contrato; weeWX incerto |
| Nos achados com opção ou evidência no arquivo (3x-ui, Ursa, pyLoad, LocalSend) | nada muda |

**Limite.** Sem contrato estabelecido, o texto novo deixa o Input Leaf limpo de forma estável, porque o nome, o propósito e a preocupação deixam de contar. Por isso o P1 vem primeiro e trava o resto.

### A rodar depois de implementar (scripts em `...\wf5\q2q3_impl\`)

1. **`check_prompts.py`.** Importa da árvore do worktree `behavior_review_system_prompt()` e `evidence_extraction_system_prompt(False)`. Compara com `review_system_new.txt` (ou `_with_F`) e `extraction_system_new.txt`. Esperado: iguais. Se o Q1 já mudou o prompt, aplicar os trechos ao prompt do Q1 e conferir que reverter devolve o do Q1.
2. **Harness do D1** em `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\d1_accept\` (`prompts.py`, `compare_prompts.py`, `run_prompts.sh`), com as duas árvores, e o provedor de captura respondendo a extração com um contrato estabelecido que cita SRC-002. Esperado:
   - adjudicação idêntica em instruções e entrada, sem a substring `function_contract`;
   - extração com instruções diferindo só pela frase, e entrada diferindo só nas duas strings de versão;
   - revisão com entrada idêntica salvo a chave final `function_contract`.
3. **`cognalys decision-replay … --dry-run --current-prompt`** nos 35 pedidos da seção 6, e com `--add-section` nos 18 pedidos de revisão que a recebem: todos aceitos.
4. **Sobre todo `*_evidence_extraction.json` em `outputs/`**, aplicar `sanitize_evidence_extraction` e `established_function_contract` com o pacote da execução. Esperado: 0 contratos e 0 notas novas, porque os registros antigos não têm o campo.
5. **`cognalys results`** e estatísticas de 5 execuções gravadas (Input Leaf run_v6, RedReader run_v5, weeWX, 3x-ui, Cryptomator run_ab61): saída byte-idêntica à de antes.

## 6. Validação paga

Tudo roda do worktree, antes do merge, em Flex e no modelo gravado de cada pedido (sem troca de modelo). Preparação:

```
cd /mnt/c/Users/Antonow/Documents/tcc_v3
source ~/venv/bin/activate
set -a; . ./.env.openai >/dev/null 2>&1; set +a
export COGNALYS_WORKSPACE=/mnt/c/Users/Antonow/Documents/tcc_v3
export PYTHONPATH=/mnt/c/Users/Antonow/Documents/tcc_v3_blockK/src
python -c "import cognalys,sys; p=cognalys.__file__; print(p); sys.exit(0 if 'tcc_v3_blockK' in p else 1)" || exit 3
B=outputs
O=outputs/q2q3_validation_$(date +%Y%m%d)
```

### P1, extração: 20 amostras, cerca de US$ 0,90 (E)

Base: estimativa do próprio replay sobre o uso gravado, com a entrada inteira sem cache, mais o delta do prompt e 300 tokens de saída (`paid_plan.py`). Forma do comando:

```
python -m cognalys.cli decision-replay $B/<execução> --finding <parte do id> --stage evidence_extraction \
  --call <call id> --current-prompt --samples <n> --max-cost <teto> --llm-service-tier flex \
  -o $O/x_<rótulo> --json
```

| Rótulo | Execução | `--finding` | `--call` | n | teto |
|---|---|---|---|---|---|
| il_v4 | `agent_baseline_20261108/cognalys/inputleaf_discovery/run_v4` | `discoverySslContext` | `20261109T185608_509004_444320_0005_evidence_extraction` | 3 | 0.20 |
| il_v5 | `…/inputleaf_discovery/run_v5` | `discoverySslContext` | `20261109T195450_536518_446803_0005_evidence_extraction` | 3 | 0.20 |
| il_v6 | `…/inputleaf_discovery/run_v6` | `discoverySslContext` | `20261110T063547_090648_555518_0005_evidence_extraction` | 3 | 0.20 |
| clash | `rvsec_alpha_20261028/run` | `serverSocketFactory:::SSLContextSpec` | `20261028T134247_171903_145699_0020_evidence_extraction` | 1 | 0.20 |
| hermes | `rvsec_com_m57_hermescontrol_20261102/run` | `<init>:::SSLContextSpec` | `20261102T142442_364783_126857_0006_evidence_extraction` | 1 | 0.10 |
| trilium | `rvsec_eu_fliegendewurst_triliumdroid_20261105/run` | `invokeSuspend:::KeyManagerFactorySpec` | `20261105T230707_294725_70221_0005_evidence_extraction` | 1 | 0.10 |
| grocy | `rvsec_grocy_20261022/run` | `TLSSocketFactory:::<init>:::SSLContextSpec` | `20261023T180356_387411_46787_0036_evidence_extraction` | 1 | 0.20 |
| cryptomator | `rvsec_lite_20261024/run_v9` | `from:::SSLContextSpec` | `20261103T162344_583587_4969_0006_evidence_extraction` | 1 | 0.15 |
| fread | `rvsec_tls_20261015/rerun_20261015/run_v2` | `buildSSLFactory:::SSLContextSpec` | `20261029T042228_684536_405637_0005_evidence_extraction` | 1 | 0.15 |
| tofshare | `rvsec_tofshare_20261031/run_v2` | `onClick:::SSLContextSpec` | `20261031T210039_058558_82532_0006_evidence_extraction` | 1 | 0.15 |
| weewx | `rvsec_com_odiousapps_weewxweather_20261104/run` | `NetworkClient` | `20261104T085108_953638_29862_0006_evidence_extraction` | 1 | 0.10 |
| zsmth | `rvsec_sslctx_20261017/run` | `getUnsafeOkHttpClient` | `20261017T212539_698534_14808_0008_evidence_extraction` | 3 | 0.40 |

O Grocy e o Cryptomator usam as chamadas enviadas de verdade (`cache_origin.py`). Elas têm a mesma entrada das revisadas no run_v6 e no run_ab61.

**Script de cola `$O/contracts_from_replays.py`.** Escrito no passo 10. Para cada pasta `$O/x_<rótulo>`, ele:
1. lê `decision_replay.json` e, por `report["request"]`, o pedido gravado;
2. tira do bloco `<repository_and_web_data>` o `finding_evidence_packet.evidence_catalog` e o `external_evidence_catalog`;
3. lê cada `replay_prompt_logs/*_response.json`;
4. aplica `sanitize_evidence_extraction(answer, {"evidence_catalog": …, "external_evidence_catalog": …})` e `established_function_contract`;
5. grava `$O/contracts/<rótulo>.json` com o contrato da primeira amostra que o estabelece. Só quando alguma estabelece;
6. grava `$O/contracts/summary.json`, com uma linha por amostra:
   - `counterpart`, `activation`, `depends` bruto e sanitizado;
   - ids citados e citações mantidas e descartadas;
   - `violated_predicates`, `needs_more_evidence`, `len(requested_evidence)` e `output_tokens` do metadata;
   - os mesmos quatro do registro gravado.

### P2, revisão: 79 amostras, cerca de US$ 1,48 (E, mesma base com 120 tokens de saída)

Mesma forma, com `--stage behavior_review --current-prompt` e, quando `$O/contracts/<rótulo>.json` existe, `--add-section function_contract=$O/contracts/<rótulo>.json`.

**Input Leaf** (rótulo do contrato: il_v4, il_v5 ou il_v6 conforme a execução):

| Execução | `--call` | n | teto |
|---|---|---|---|
| run_v4, 1ª revisão | `20261109T185801_793963_444320_0009_code_generation.behavior_review` | 8 | 0.25 |
| run_v4, 2ª revisão | `20261109T185913_269181_444320_0011_code_generation.behavior_review` | 3 | 0.10 |
| run_v4, 3ª revisão | `20261109T190000_875208_444320_0013_code_generation.behavior_review` | 3 | 0.10 |
| run_v5, 1ª revisão | `20261109T195648_362849_446803_0009_code_generation.behavior_review` | 8 | 0.25 |
| run_v6, 2ª revisão | `20261110T063855_155444_555518_0012_code_generation.behavior_review` | 3 | 0.10 |
| run_v6, 3ª revisão | `20261110T064011_800994_555518_0014_code_generation.behavior_review` | 3 | 0.10 |

**Controles limpos de TLS** (contrato do rótulo P1 de mesmo nome, se existir):

| Rótulo | Execução | `--call` | n | teto |
|---|---|---|---|---|
| clash | `rvsec_alpha_20261028/run` | `20261028T134844_334858_145699_0026_code_generation.behavior_review` | 3 | 0.15 |
| hermes | `rvsec_com_m57_hermescontrol_20261102/run` | `20261102T143039_889073_126857_0010_code_generation.behavior_review` | 3 | 0.10 |
| trilium | `rvsec_eu_fliegendewurst_triliumdroid_20261105/run` | `20261105T231047_520005_70221_0009_code_generation.behavior_review` | 3 | 0.10 |
| grocy | `rvsec_grocy_20261022/run_v6` | `20261024T004921_478840_163826_0039_code_generation.behavior_review` | 3 | 0.15 |
| cryptomator | `rvsec_lite_20261024/run_ab61` | `20261103T220248_452740_1576_0013_code_generation.behavior_review` | 3 | 0.20 |
| fread | `rvsec_tls_20261015/rerun_20261015/run_v2` | `20261029T043101_273155_405637_0011_code_generation.behavior_review` | 3 | 0.15 |
| tofshare | `rvsec_tofshare_20261031/run_v2` | `20261031T211216_259369_82532_0010_code_generation.behavior_review` | 3 | 0.25 |
| weewx | `rvsec_com_odiousapps_weewxweather_20261104/run` | `20261104T085409_620415_29862_0010_code_generation.behavior_review` | 3 | 0.10 |

**Controles de regressão gravada** (sem contrato; testam só o prompt):

| Rótulo | Execução | `--finding` | `--call` | n | teto |
|---|---|---|---|---|---|
| 3x-ui | `rvsec_xui_20261101/run` | `socketFactory:::SSLContextSpec` | `20261101T134733_753322_59271_0026_code_generation.behavior_review` | 3 | 0.20 |
| ursa | `rvsec_ursa_20261025/run_v3` | `sessionPinnedClient` | `20261101T111819_049730_21046_0026_code_generation.behavior_review` | 3 | 0.20 |
| pyload | `rvsec_client_20261101/run` | `checkAuth` | `20261101T190435_629090_64402_0028_code_generation.behavior_review` | 3 | 0.20 |
| localsend | `rvsec_localsendkotlin_20261029/run_v2` | `createSslContext` | `20261030T012446_009412_418278_0026_code_generation.behavior_review` | 3 | 0.20 |
| cryptomator_reg | `rvsec_lite_20261024/run_ab61` | `from:::SSLContextSpec` | `20261103T220002_547783_1576_0011_code_generation.behavior_review` | 3 | 0.20 |
| icsdroid | `rvsec_icsdroid_20261022/run_v4` | `AppHttpClient` | `20261101T105639_506913_11267_0031_code_generation.behavior_review` | 3 | 0.20 |
| dnstoggle | `rvsec_com_ericlowry_dnstoggle_20261104/run` | `encrypt:::CipherSpec` | `20261104T170409_591856_89309_0025_code_generation.behavior_review` | 3 | 0.20 |
| bwt | `rvsec_bwt_20261024/run_v5` | `encryptDES` | `20261101T103209_811406_1121_0015_code_generation.behavior_review` | 3 | 0.20 |
| trigger | `rvsec_trigger_20261028/run_ab61` | `createKeyPair` | `20261103T222307_025105_2784_0015_code_generation.behavior_review` | 3 | 0.20 |

**Total P1 + P2:** 99 amostras, cerca de US$ 2,38 em Flex. O limite é US$ 4,76 se tudo cair para o standard (M para o limite, E para o gasto; `paid_plan.py`).

### P3, ponta a ponta: cerca de US$ 2,2 (E)

Base: uma réplica dos 9 achados custou US$ 1,75 na validação do D1 (M, `driver.log`), mais duas execuções do Input Leaf a US$ 0,13 a 0,25 cada (E).

Copiar `outputs/d1_validation_20261110/run_d1_validation.sh` para `$O/run_q2q3_validation.sh` com estas trocas:
- as linhas de preparação acima (as duas variáveis exportadas e a checagem de `cognalys.__file__`) logo depois de `set +a`, sourcing também `.env.github` como no D1;
- `timeout 3h python -m cognalys.cli run` no lugar de `timeout 3h cognalys run`;
- o marcador `.done_d1_` trocado por `.done_q2q3_`;
- os laços: `inputleaf_discovery` em `vN`, `vN+1` e `vN+2`, e os outros 8 achados uma vez em `vN`. N é o primeiro número livre nas 9 pastas (hoje 9; o Q1 pode ocupar 7 e 8);
- sem o bloco `session_writeentry`.

Depois da primeira execução, conferir que `coordinated_drafts/*/*/behavior_review.json` grava `review_contract_version` 20 (prova de que o código do worktree rodou). Depois: `cognalys cost` de cada execução e leitura de todo diff que virar PR pronto.

### Critérios, contra os conjuntos de ruído de `noise_floor.md` e os desfechos do run_v6

| Fase | Passa quando | Não fazer o merge quando |
|---|---|---|
| P1 | Input Leaf: 9 de 9 `established`, `user_operated_peer`, com citação mantida de SRC-004 ou TEST-004. zSMTH: 3 de 3 `not_established`. Fread: `not_established`. Em nenhuma das 20 amostras `violated_predicates` cai a 0 com o registro gravado tendo 1 ou mais | Input Leaf abaixo de 9 de 9: cabe uma revisão da frase da extração e um reenvio só dessas 9 (cerca de US$ 0,25). zSMTH estabelecido. Qualquer queda de `violated_predicates` a 0 que a leitura não explique |
| P2 Input Leaf | As 28 amostras com contrato marcam a perda de descoberta de servidor self-signed. Pela regra de três, o limite inferior de 95% fica perto de 0,89 por revisão. As da 2ª revisão marcam de novo a perda que receberam | Alguma das 28 sai limpa ou a desculpa: cabe uma revisão de texto e um reenvio só do Input Leaf (cerca de US$ 0,35) |
| P2 limpos de TLS | Clash, Hermes, Trilium, Grocy, Cryptomator, Fread e tofshare: no máximo 1 de 3 com regressão por "What the fix removes", e nenhuma regressão apoiada nessa regra sem uma das cinco evidências (ler o `rationale`). weeWX é só informativo | Algum dos 7 com 2 ou 3 de 3 em regressão pela regra nova |
| P2 regressões | Cada um dos 9 com ao menos 2 de 3 em regressão | Algum com 0 ou 1 de 3 cuja mudança cite B, C ou G |
| P2, todos | Nenhuma amostra marca `current_code_is_correct_use` com regressões onde o registro gravado não marcava. `concern_verdicts` responde cada preocupação em todas | Qualquer amostra que marque o flag novo, porque é o caminho para "sem impacto" |
| P3 | Input Leaf: 3 de 3 em {issue, correção gerada sem PR, correção não gerada}, ou PR pronto cujo diff mantém a descoberta de self-signed (ler). Os outros 8 dentro dos seus conjuntos | Input Leaf em PR pronto com patch só de confiança da plataforma, ou em "sem impacto" ou "falso positivo". Algum dos 8 em balde de dispensa fora do seu conjunto. CometOTP de volta a "falso positivo" |

Conjuntos dos outros 8: Amethyst {sem impacto}; CometOTP {issue, PR pronto}; Herald e IbisWallet {sem impacto, issue}; InterTune {issue, PR pronto, não gerada}; Melox {issue, sem correção segura}; RedReader {issue, não gerada, gerada sem PR, sem impacto}; Winterhold {issue, não gerada, PR pronto}.

**Poder do P3 (E).** As três execuções do Input Leaf compartilham `previous_attempts_of_this_finding` (`fix_generation_common.py:129`), a mesma dependência que `noise_floor.md` registra para o CometOTP. "3 de 3" é fraco sozinho: se a mudança só baixar o PR pronto para 30%, passa 34% das vezes. A prova principal é o P2, por revisão; o P3 confirma a cadeia inteira. O relatório diz as duas coisas.

**Ação em falha.** Todo o plano roda antes do merge. Falha em qualquer fase significa:
- não fazer o merge;
- registrar a falha numa entrada datada em "Refinamentos" do `PLANO_FABLE.md`;
- no máximo uma revisão de texto, com reenvio só do que falhou.

## 7. Riscos, limites e o que não fazer

### Riscos

- **A meta depende da extração.** Sem contrato estabelecido, o Input Leaf passa como "limpo" de forma estável. O P1 trava tudo.
- **Contrato estabelecido por engano** bloquearia um PR bom para servidor público. Freios:
  - o par tem de ser `third_party_service` ou `user_operated_peer`;
  - `dependence` não pode estar vazia;
  - a citação literal é conferida em código e tem de vir de entrada que não seja a fonte relatada nem a especificação;
  - vale a cláusula "keeps them working another way".
  
  O P1 mede o zSMTH. O P2 mede Cryptomator, Trilium e Grocy, servidores do usuário com PR limpo.
- **A exclusão da fonte relatada perde dependências mostradas no próprio arquivo** (outro método do mesmo arquivo). Para esses casos ficam o `deliberate_insecurity`, a opção e o próprio arquivo revisado.
- **Custo do Input Leaf pode subir.** Com duas regressões seguidas do plano, o V1 replaneja; na réplica 1 isso custou US$ 0,47 em standard. Na média das 9, cerca de +0,02 (E).
- **Com E4, RedReader e InterTune chegam mais vezes a rascunho revisado.** As referências são `pr_closed`; a memória (`rejected_outcome_for`) segura a publicação. Relatar separado.
- **Os exemplos do prompt são quase todos de TLS.** A regra é sobre par ou servidor e protocolo, e o escopo é dito no próprio parágrafo. Fora de TLS, o contrato só chega à revisão.

### Limites

- **Controles gravados em `gpt-5.6-terra`** (Clash, Grocy, Fread, tofshare, zSMTH, 3x-ui, Ursa, pyLoad, LocalSend, ICSdroid, BWT) testam o prompt nesse modelo. O P3 testa a cadeia em `gpt-6.1-terra`.
- **A comparação de cada controle é com uma resposta gravada só** (n=1). Os critérios usam a maioria de 3 por isso.
- **O reenvio com `--current-prompt` usa o payload gravado**, que em execuções antigas não tem as chaves mais novas.
- **Os controles de regressão não recebem contrato.** A extração deles não é reenviada.
- **As 30 linhas PR pronto de TLS de 10 a 22/10 não têm revisão gravada.** O braço de servidor público do P3 fica sem medida; ele está coberto só no P1 (zSMTH, Fread).
- **`previous_regressions` continua levando só a última revisão.**

### O que não fazer

- Portão em código sobre `concern_verdicts`.
- Fato léxico de pinning ou TOFU.
- Mudar `case_policy.strategy`.
- Mudar `DELIBERATE_INSECURITY_GUIDANCE` (é compartilhada por 4 prompts).
- Pôr o contrato no planejador ou na adjudicação sem a pergunta 2.
- Trocar o modelo no replay.
- Baixar o esforço, cortar pesquisa ou juntar etapas.

## 8. Passos de implementação

| # | Estágio | Passo | Depende de |
|---|---|---|---|
| 1 | implementar | Mover os dois prompts para funções de módulo sem mudar um byte. Teste: `behavior_review_system_prompt()` igual ao texto do contrato 19, `evidence_extraction_system_prompt(False)` igual ao gravado (conferir pelo script, não pelo teste) | — |
| 2 | implementar | `decision_replay.py` e `cli.py` (3.8), os testes de `test_decision_replay.py` e o guia. Commit 1 | 1 |
| 3 | suíte | `python -m pytest -q tests` | 2 |
| 4 | implementar | Extração: 3.1, 3.2, 3.3, 3.4 e 3.7, e os testes correspondentes | 1 |
| 5 | implementar | Revisão: 3.5 (a) a (e) com A, B, C, D, E e G; 3.6 (`concern_verdicts`); ajuste do teste existente | 4 |
| 6 | implementar | Só com o sim da pergunta 1: trecho F, oração do schema e seu teste | 5 |
| 7 | suíte | Suíte completa. Nenhuma fixture dourada deve mudar; se mudar, regenerar por código | 6 |
| 8 | checagem offline | Os cinco itens "a rodar depois" da seção 5 | 7 |
| 9 | revisão adversarial | Três lentes: correção (emenda, sanitização, citações, ordem das chaves); segurança de decisão (algum caminho leva a balde de dispensa, `current_code_is_correct_use`?); generalidade (nada preso a especificação ou APK) | 8 |
| 10 | validação paga | Escrever `contracts_from_replays.py`; P1; P2 (só se o P1 passar). Se falhar: uma revisão de texto e reenvio | 9 |
| 11 | validação paga | P3, do worktree | 10 |
| 12 | merge | Commit 2, merge em `melhorias-pipeline-28-09` (com o git do WSL, como na memória `worktree-git-mechanics`), push; linhas no `noise_floor.md`; entrada datada em "Refinamentos" do `PLANO_FABLE.md` | 11 |

### Arquivos compartilhados com outros itens

- **Q1 (vem antes).**
  - Edita o prompt da adjudicação em `finding_adjudication.py` e pode subir `REVIEW_CONTRACT_VERSION` e `CONTEXT_BUILDER_VERSION`; usar os próximos números.
  - Os testes afirmam trechos, não o hash. O `check_prompts.py` recalcula sobre o prompt do Q1.
  - Os passos 1 a 3 podem ir antes do Q1. Se ele precisar reenviar adjudicação com prompt mudado, basta registrar um construtor `adjudication` em `current_prompt_request`.
  - O DL6r parte 2 mexe no laço de geração de `coordinated_generation.py`, não em `review_patch_behavior`.
- **K3.** Os pedidos de pesquisa não mudam.
- **Run congelada.** Só depois do passo 12.
- **G2.** Ao reordenar o payload da revisão, manter `function_contract` por último.
- **A1.** A revisão final do laço usa a mesma `review_patch_behavior` e herda tudo.

### Mensagens de commit (preencher `<N>` e `<M>` com a suíte)

Commit 1:
```
cognalys decision-replay gains --current-prompt, --add-section NAME=PATH and the stage evidence_extraction. With --current-prompt the recorded input of a behaviour-review or evidence-extraction request is sent, on its recorded model, effort and output ceiling, under the instructions and JSON schema this code builds today (coordinated_generation.behavior_review_system_prompt and finding_adjudication.evidence_extraction_system_prompt, now module-level functions that return the same bytes as before); --add-section appends a JSON section last to the review's recorded data block, the bytes review_patch_behavior produces when it adds a conditional section. The record must first rebuild to its own input_sha256, each sample's trace is checked against the variant that is sent, and the report says what it is a variant of (recorded digest, added sections) and that the recorded answer was given under the earlier prompt. An extraction replay compares function_contract (dependence/counterpart/activation), the number of violated predicates, needs_more_evidence and requested_evidence. Without the new options the command's requests, report and output are byte-identical. Offline, the 35 recorded requests the Q2Q3 validation replays rebuild to their recorded digests, and the 492 recorded review payloads under outputs are each one JSON data block the splice applies to. GUIA_DE_USO describes the options. Full suite: <N> passed, <M> skipped.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

Commit 2 (sem o sim da pergunta 1, tirar a frase que começa em "A forbidden call"):
```
The evidence extraction now states who the reported code serves (function_contract: purpose, counterpart, activation, whether the counterpart depends on the reported behaviour, the evidence and verbatim quotes), and the behaviour review says one thing about removing something deprecated or insecure that the code offers to or accepts from a peer or server. Input Leaf ServerScanner.discoverySslContext (agent_baseline_20261108): the packet held the fingerprint-pinned session connection and a self-signed test server in all 7 recorded executions, the extraction cited the first one to three times and the second never, and the review of the same platform-trust patch reported the lost discovery of self-signed servers in 17 of 29 recorded and replayed answers and did not in 12, one second review calling it "a direct consequence of no longer accepting that certificate". A contract reaches the review payload, as its last section, only when the extraction established the dependence for a third-party service or a user-operated peer, with a quote that the sanitizer finds verbatim in a repository entry other than the reported source and the specification; the adjudication reads the extraction without the field and the planner does not receive it, so their requests are byte-identical apart from the extraction's own content. The review prompt (contract 20) replaces the rules that licensed opposite verdicts with "What the fix removes": such a removal is hardening unless function_contract, deliberate_insecurity, an option in the code, other reviewed code handling the same peer or this repository's maintainers show who depends on it; stored data, plan invariants and forbidden changes keep their rules; a concern about such a removal is decided by that rule, and every concern is answered in concern_verdicts, recorded in behavior_review.json and read by no gate. A forbidden call kept or moved only to read what earlier versions produced is a change, not a patch without effect (8 of 57 recorded no-effect verdicts, RedReader and InterTune). Offline, over the 492 recorded reviews of 166 runs: reverting the passages gives the contract-19 prompt byte for byte, no recorded verdict is expected to move toward a dismissal outcome, 2 recorded Grocy verdicts that rested only on a concern about an assumed TLSv1.1 server are expected to clear, and reviews whose evidence is an option or the reviewed file (3x-ui, Ursa, pyLoad, LocalSend) keep theirs. CONTEXT_BUILDER_VERSION 2.19. Paid validation before the merge (decision-replay of 20 extractions and 79 reviews, three Input Leaf executions and the other eight benchmark findings once): <results>. GUIA_DE_USO describes the field. Full suite: <N> passed, <M> skipped.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

## 9. Perguntas ao usuário

**1. A chamada proibida mantida só para ler dado antigo conta como "sem efeito"?**
- O CLAUDE.md fala em "same calls, same values" e não decide o caso. Hoje a revisão responde das duas formas para o mesmo patch.
- Recomendo **não é sem efeito**: o app passa a produzir o dado novo com o valor permitido, e a revisão registra onde a chamada antiga ficou.
- Consequências:
  - 8 vereditos gravados de 2 achados deixam `no_effect`, e 1 execução deixa "sem impacto" (RedReader v5);
  - o RVSec continua relatando a chamada legada sempre que a migração roda, como as revisões gravadas do RedReader dizem; o texto do PR tem de dizer isso;
  - RedReader e InterTune, ambos com referência `pr_closed`, chegam mais vezes a rascunho revisado.
- Se a resposta for não, o passo 6 é pulado e o resto não muda.

**2. Levar o `function_contract` também à adjudicação?**
- Recomendo **agora não**. A adjudicação do Input Leaf já é estável (7 de 7) e já levanta a preocupação. Uma frase nova ali poderia mover decisões de FIX para NO_SAFE_FIX sem medida offline, e mexe no mesmo prompt do Q1.
- Reabrir depois do P3 se o Input Leaf só chegar a issue depois de replano, a mais de US$ 0,30 por execução.

**3. Atualizar o CLAUDE.md depois que a validação passar?** Texto proposto para o item da revisão de comportamento:
- "Removing something deprecated or insecure that the code offers to or accepts from a peer or server is a regression only when the payload shows who depends on it (the extraction's function_contract with quoted repository text, deliberate_insecurity, an option in the code, other reviewed code handling the same peer, or this repository's maintainers); an adjudication concern about that compatibility is answered in concern_verdicts and decided by that rule. A forbidden call kept only to read earlier data is a change, not a patch without effect."
- Recomendo sim, com a última oração só se a pergunta 1 for sim.

## Apêndice: Revisão, o que mudou

| # | Ponto da revisão | Decisão | Motivo |
|---|---|---|---|
| 1 | Lista fechada de B contradiz outras regras | Aceito | B ganhou escopo (par ou servidor) e diz que dado armazenado, invariantes e mudanças proibidas mantêm suas regras. As respostas de mantenedor entram como a 5ª evidência, restrita a este repositório: as de outros projetos não mostram dependência destes usuários |
| 2 | "what the rule forbids" alarga "deprecated or insecure" | Aceito | Volta "something deprecated or insecure", com "a certificate nothing verified" como terceiro exemplo. A frase 2031-2033 e seu teste ficam coerentes |
| 3 | O 4º item é satisfeito pelo próprio código; a frase de `previous_regressions` desculpa | Aceito | "other than the reported code itself" e trecho G. Confirmado por `r9_il_chain.py`: 2 de 3 segundas revisões desculparam, e as terceiras não receberam a perda. As 2ª revisões do v4 e do v6 entram no P2. Não acumulei `previous_regressions`, porque o contrato vai a todo payload |
| 4 | O caminho para "sem impacto" não foi medido | Aceito | Confirmado: `finding_processing.py:1299-1307`; 60 revisões de 19 achados (M). Critério novo no P2 (flag novo bloqueia) e conjunto permitido no P3 |
| 5 | Faltam controles de regressão | Aceito | 9 controles com n=3 |
| 6 | O braço de servidor público tem n=0 | Aceito em parte | zSMTH entra no P1 com n=3. Read You fica de fora: pela leitura, roda sob opção do usuário e não serve de servidor público. O P3 segue sem esse braço, declarado como limite |
| 7 | Dois comandos do P1 não enviam nada | Aceito | Confirmado (`cache_hit`, limite 0,22 e 0,25). Troquei pelas chamadas enviadas com a mesma entrada (`cache_origin.py`), com teto 0,20 e 0,15 |
| 8 | Modelo e prompt mudam juntos | Aceito o defeito, rejeitada a correção | Removi `--llm-model`: cada controle roda no modelo gravado, então o prompt é a única mudança, sem braço extra |
| 9 | A revisão não confere o contrato; o sanitizador é fraco | Aceito | Par restrito a dois valores; citação literal conferida contra entrada que não seja a fonte relatada nem a especificação; as citações seguem para a revisão |
| 10 | Canal lateral para a adjudicação | Aceito | O replay de extração compara `violated_predicates`, `needs_more_evidence` e `requested_evidence`; critério de queda a 0 no P1 |
| 11 | O P3 não tem ação de falha nem poder | Aceito | O P3 passa a rodar antes do merge, do worktree (`PYTHONPATH` e `COGNALYS_WORKSPACE`, com checagem de `cognalys.__file__`). Dependência e poder declarados; a prova principal é o P2 |
| 12 | O trecho C muda doutrina sem contar | Aceito, com outra correção | A regra das preocupações fica. Só a preocupação sobre remover algo obsoleto ou inseguro vai para B, como o CLAUDE.md já diz. Movimento medido: 2 vereditos do Grocy. Texto na pergunta 3 |
| 13 | A entrada da extração muda | Aceito | Confirmado: duas strings de versão. A entrada da adjudicação não tem versões e fica idêntica |
| 14 | Os testes fixam o sha256 inteiro | Aceito | Os testes afirmam trechos; o hash fica em `check_prompts.py` |
| 15 | Contagem 16/13 | Aceito | São 17/12: o run 0014 marca (`il_run0014.py`) |
| 16 | Script de cola não especificado | Aceito | `contracts_from_replays.py` com entrada, regra do catálogo e saída definidas; critérios para os controles do P1 |
| 17 | "or that an option exists" e definições duplicadas | Aceito | A opção vai para `activation`; as definições ficam só no prompt, sem descrições no schema |
| 18 | Falta uma consequência na pergunta 1 | Aceito | O RVSec segue relatando a chamada legada quando ela roda |

### O que continua sem verificação

- Se a extração vai estabelecer o contrato com citações literais no Input Leaf (P1).
- Se `concern_verdicts` torna a revisão mais consistente.
- O movimento de regressão para limpo além dos 2 vereditos do Grocy, que é leitura e não medida.
- O comportamento em `gpt-5.6-terra` diante do texto novo.
- Se os passos que o `cognalys run` dispara como subprocesso herdam o `PYTHONPATH` do worktree. A checagem do `review_contract_version` 20 na primeira execução do P3 confirma isso.
- O efeito de F no texto do PR do RedReader e do InterTune.