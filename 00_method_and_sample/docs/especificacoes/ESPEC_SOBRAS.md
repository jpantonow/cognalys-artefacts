> Especificação de Sobras de um rascunho descartado, escrita em 2026-11-10 para ser implementada por uma sessão Opus 5.5 seguindo docs/PLANO_FABLE.md (diagnóstico de `outputs/design_20261110/diag_polish_time/leftovers.py`, verificação e desenho no Opus 5.5). Os scripts citados ficam em `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\sobras\` (não versionado). O código lido é o do worktree `C:\Users\Antonow\Documents\tcc_v3_blockK` no HEAD 7161173; o VFIX, implementado em paralelo ali, desloca as linhas de `coordinated_generation.py` (procure pelo nome da função) e não toca `semantic_validation.py`. As perguntas da seção 9 esperam a resposta do usuário antes da implementação das partes que dependem delas.

# Especificação sobras: o teste escrito para um rascunho descartado fica no patch final (2026-11-10)

Marcação dos números:

- (M) foi medido por script, nomeado ao lado, em `outputs\design_20261110\sobras\`. Para rodar: `wsl.exe bash -lc "cd /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/design_20261110/sobras && source ~/venv/bin/activate && PYTHONPATH=/mnt/c/Users/Antonow/Documents/tcc_v3_blockK/src:. python <script>"`.
- (E) é estimativa, e a base vem dita.

A varredura `s04_scan.py` leu todo log de prompts sob `outputs/` e reconstruiu, por execução e finding, os segmentos de plano (um `patch_planning` ou um rascunho de primeira tentativa de arquivo já rascunhado abre outro) e as tentativas (rascunho sem `semantic_validation_retry` é a tentativa 1; um cujo retorno diz `attempt: a` é a tentativa a+1; `shape_retry` se dobra no rascunho que refaz). Resultado: 603 segmentos de 529 unidades, 597 tentativas de redraft (`scan_units.jsonl`). A simulação de `semantic_validation_retry_targets` do HEAD sobre os erros gravados reproduz os arquivos regenerados em 594 das 597 tentativas; as 3 diferenças são execuções anteriores às regras atuais (Session de 2026-10-15, que motivou `_BEHAVIOUR_REFUSAL_CODES`; hfut e aihub de setembro e outubro) (M, `s05_analyze.py`). Nada foi editado no repositório nem no worktree, e nenhuma chamada paga foi feita.

## 1. Resumo

| Parte | Defeito? | O que entra | Efeito medido |
|---|---|---|---|
| 1. Teste mantido num redraft que muda a produção | Sim | Quando o retorno de um redraft vem da revisão de comportamento ou das checagens sobre o rascunho revisado (`UNINTENDED_BEHAVIOR_CHANGE`, `JOINT_FINDING_EDIT_WITHOUT_EFFECT`, `DROPPED_INPUT_NO_LONGER_CONTRIBUTES`, `EXPLICIT_DEFAULT_CHANGE_TOUCHES_MORE`) e regenera um arquivo de produção, os testes do plano entram entre os alvos | 49 de 529 unidades, 88 chamadas de geração a mais em todo o histórico; 10 de 221 diffs de PR mudariam (M, `s05`, `s06`, `s07`) |
| 2. Suposições de arquivo mantido | Não, fora o que a parte 1 já cobre | Nada | Das 43 suposições de arquivo mantido em 15 registros de PR, 1 é desmentida pelo diff final (Session), e vem de um teste que a parte 1 regenera (leitura de `s06_records.out`) |

Números que decidem:

- **A conta do diagnóstico estava inflada.** Ele tomava o último rascunho registrado de cada arquivo como o que foi entregue. Em 3 dos 8 registros "com suposição de arquivo mantido" e nos 3 "com suposição só de rascunho superado", o patch entregue é um rascunho anterior, mantido depois que a regeneração falhou (`kept_after_failed_regeneration: true` no manifesto). Escolhendo a tentativa entregue pelo diff (F1 das linhas acrescentadas), os 3 "superados" são 0, e dos 8 sobram 6 com arquivo realmente mantido (M, `s03_trace.py`, `s06_records.py`).
- **"A união de todos os rascunhos" não é o que o código faz.** `assumptions` (`coordinated_generation.py:3062-3065`) é a união dos rascunhos da tentativa final, mantidos e regenerados. Os rascunhos superados não entram: o checkpoint de cada alvo é apagado antes do redraft (`:1473-1482`). O que entra de errado é a suposição de um arquivo mantido escrita contra o rascunho superado de outro.
- **Teste mantido com produção refeita:** 124 tentativas em 73 unidades (M, `s05`). Nos diffs de PR, 15 de 221 (M, `s06`). Os códigos dessas tentativas: `UNINTENDED_BEHAVIOR_CHANGE` 80, `OBSOLETE_CHECKED_EXCEPTION_CATCH` 8, `PUBLIC_API_CONTRACT_CHANGED` 7, `NAME_CONTRADICTS_BEHAVIOR` 7, outros 22, `JOINT_FINDING_EDIT_WITHOUT_EFFECT` 1 (M, `s05`).
- **Custo da parte 1:** 88 chamadas de rascunho de teste a mais em 49 unidades. Em gpt-5.6-terra Flex, US$ 0,032 por chamada, US$ 0,057 por finding afetada e US$ 0,0053 na média de todas as unidades com geração coordenada. Em gpt-6.1-terra Flex, 0,013 / 0,023 / 0,0021 (M, `s05`, `s07`, preços de `data/llm_prices.json` sobre os tokens gravados de cada teste mantido).
- **Dispensa:** 0. A parte 1 só muda quais arquivos um redraft regenera. Nenhuma pesquisa, extração, adjudicação, plano, revisão ou roteamento muda.

Precisa do sim do usuário:

1. Entrar antes da run congelada (§9, pergunta 1).
2. Não fazer a parte 2 (§9, pergunta 2).

Cortado, com motivo:

- **Regenerar os testes em todo redraft de produção.** É o que o VFIX cortou (74 unidades, 133 rodadas) e o que o A1 faria. A regra daqui fica com os códigos que pedem à produção outro comportamento: 49 unidades, 88 chamadas.
- **Só os testes que nomeiam a classe do arquivo refeito (R2).** 59 chamadas em 39 unidades, mas perde 3 dos 9 registros que a regra muda: o FreeOTP v7, o Melox e o Cryptomator `getPublicKey`, cujos testes exercitam a produção por outra classe (M, `s05`).
- **Também os códigos de produção que não preservam comportamento (R3: tudo menos compilação, renomeação, comentário e nível de API).** Acrescenta `OBSOLETE_CHECKED_EXCEPTION_CATCH` e outros sem caso medido de teste errado. Muda 7 registros, contra 9 da regra escolhida, e um deles (MTGFam) por um catch obsoleto (M, `s05`).
- **Filtrar suposições de arquivo mantido.** Tirar todas apaga 42 suposições válidas para pegar 1. Tirar as que nomeiam o arquivo refeito depois (filtro léxico) pega 7 suposições em 5 registros, e as 7 continuam verdadeiras no diff final (AppManager, Trigger, icsdroid, SMSecure v4, Bugbane) (leitura de `s06_records.out` com os diffs).

## 2. Diagnóstico medido

### 2.1 O caminho no código

- `generate_coordinated_file_drafts` (`coordinated_generation.py:1296-1496` no HEAD 7161173) roda até `MAX_SEMANTIC_VALIDATION_RETRIES + 1` tentativas. Depois de uma falha, `semantic_validation_retry_targets(semantic_errors, generation_paths)` (`:1442-1444`) escolhe os alvos. Os checkpoints dos alvos são apagados (`:1473-1482`), e os outros arquivos são reaproveitados por `reusable_draft` (`:2683-2704`).
- `semantic_validation_retry_targets` (`semantic_validation.py:6571-6620`):
  - um erro com `affected_paths` regenera só esses arquivos;
  - um erro com `path` ou `source_file_path` regenera esse arquivo e seus `retry_paths`;
  - códigos de teste vão à metade de testes, códigos de produção à metade de produção, e qualquer outro código a todos os arquivos;
  - `_SURFACE_REMOVAL_CODES` e `_BEHAVIOUR_REFUSAL_CODES` (`:6562-6569`, só portões de contrato: `PINNED_NEGOTIATED_PROTOCOL`, `STORED_DERIVED_VALUE_CHANGED` etc.) levam todos os testes junto. O comentário em `:6613-6618` já diz o motivo: "A production draft whose behaviour was refused takes the planned tests with it: they were written for the refused behaviour."
- Os erros que a revisão de comportamento e as checagens sobre o rascunho revisado devolvem ao laço não estão em nenhum desses conjuntos e todos trazem `affected_paths`:
  - `UNINTENDED_BEHAVIOR_CHANGE` (`:3163-3178`): o arquivo da regressão, ou todos os arquivos editados;
  - `DROPPED_INPUT_NO_LONGER_CONTRIBUTES` (`:3341-3354`) e `EXPLICIT_DEFAULT_CHANGE_TOUCHES_MORE` (`:3383-3396`): arquivos de produção;
  - `JOINT_FINDING_EDIT_WITHOUT_EFFECT` (`joint_no_effect_feedback`, `:889-925`): o arquivo da edição sem efeito.
  
  Por isso o redraft regenera só a produção, e o teste escrito para o rascunho recusado segue.
- `kept_files_reliance` (`:513-563`) só cuida de compilar: diz ao arquivo regenerado quais declarações o arquivo mantido chama. Não diz ao teste que a produção mudou. E, enquanto o teste é mantido, ele prende a produção às declarações que o rascunho recusado criou para ele.
- As suposições (`:3062-3065`) são a união dos `review_assumptions` dos rascunhos da tentativa atual. Com o teste mantido, entram as suposições que ele escreveu sobre a produção recusada.
- Quando a regeneração falha e havia um rascunho revisado cujo único problema era de checagem que só regenera, `kept_or` (`:1287-1294`) devolve o `finish` daquela tentativa anterior, com as suposições dela, e o manifesto ganha `kept_after_failed_regeneration` (`:3451`). Os logs, porém, terminam com os rascunhos descartados. Foi isso que enganou o diagnóstico.

### 2.2 As afirmações do diagnóstico, conferidas

| Afirmação (`diag_polish_time/leftovers.py`) | Conferido | Fonte |
|---|---|---|
| 221 diffs de PR únicos com rascunhos nos logs (de 247) | 221 (M) | `s06` |
| 96 com arquivo refeito | 79 a 81 até a tentativa entregue (M). Os 96 contavam replanos, retomadas e rascunhos posteriores ao entregue | `s05`, `s06` |
| 42 mantiveram arquivo escrito para o rascunho anterior | 54 pela definição exata (arquivo cujo último rascunho é anterior ao último redraft de outro, na tentativa entregue) (M); o diagnóstico só contava o arquivo nunca refeito | `s06` |
| 12 mantiveram um teste com a produção refeita | 15 (M). Saem o SMSecure `run_ab56` e o YourLocalWeather, entregues na tentativa 1. Entram FreeOTP v6, Cryptomator `getPublicKey`, LocalSend v3, Melox, RedReader `run_v4` e SMSecure `run_ab61` | `s06` |
| 8 registros levam suposição de arquivo mantido | 15 registros com 43 suposições pela definição exata (M). Dos 8 originais, 2 são artefato (SMSecure `run_ab56`, YourLocalWeather). Desmentida pelo diff: 1 | `s06`, leitura abaixo |
| 3 levam suposição que só existe em rascunho superado (SMSecure `run_ab56`, `run_ab61`, YourLocalWeather `run`) | 0. Os 3 têm `kept_after_failed_regeneration: true`, e as suposições são as da tentativa entregue | `s03_trace.py`, manifestos |

O rastro dos 3 "superados" (M, `s03_trace.py`):

- **SMSecure `run_ab56`:** a tentativa 1 (chamadas 02 e 03) foi revista limpa. A tentativa 2 (`ALGORITHM_ABOVE_MIN_SDK`) e a 3 (`UNINTENDED_BEHAVIOR_CHANGE`) regrediram, e a tentativa 1 foi devolvida com a nota de minSdk. As 6 suposições do registro são das chamadas 02 e 03.
- **SMSecure `run_ab61`:** o segundo plano (chamadas 10 a 16). A tentativa 2 (chamada 13) foi revista limpa, e a 3 (`ALGORITHM_ABOVE_MIN_SDK`, chamada 15) não ficou. O registro tem as suposições de 13 e do teste da 11. O teste é mantido de verdade, e a parte 1 o regenera.
- **YourLocalWeather `run`:** a tentativa 1 (chamadas 04 e 05) foi entregue. As tentativas 2 e 3 do `GeneralPreferenceFragment.java` foram descartadas.

### 2.3 Os registros com suposição de arquivo mantido, lidos

Os 7 que o diagnóstico não leu, mais o Session:

| Registro | Arquivo mantido | Por que outro foi refeito | Leitura |
|---|---|---|---|
| Session `writeEntry` (D1) | `LogFileKeyEncodingRegressionTest.java` | `JOINT_FINDING_EDIT_WITHOUT_EFFECT`: tirar o `RawAesKey` de `LogFile.java` | 1 de 3 desmentida: "Runtime-monitor acceptance of the production Key implementation" (a classe saiu). O teste mantém o `clone()` e o nome `KeyEncodingRegression` do rascunho recusado. A parte 1 regenera |
| RedReader `subset_redreader_sha1_v2` | `General.kt` | regressão em `RedditSubredditManager.java` | 2 válidas (nome do banco interno; mudanças aplicadas juntas) |
| Bugbane `run_v2` | `PlantedDetectionIntegrationTest.kt`, sem edições | `SIGNATURE_CHANGE_BREAKS_CALLERS` | 3 suposições de um rascunho sem edições, fora do diff. "Source/static analyzer" é o erro de detector que `finding_detector` já corrigiu. A parte 1 não pega (código de compilação) |
| Cryptomator `run_ab61` | `SSLSocketFactories.kt` | regressão no pinning de `WebDavCompatibleHttpClient.kt` | 2 válidas. "The coordinated caller preserves the existing certificate-pinning" era falsa para o chamador recusado e é o que o redraft corrigiu |
| SMSecure `run_ab56` | — | — | Artefato (§2.2) |
| SMSecure `run_v3` | `MasterSecretUtil.java` (produção) | `PUBLIC_API_CONTRACT_CHANGED` no teste | 3 válidas: falam da própria produção, que é a final |
| SMSecure `run_v4` | duas atividades, sem edições | `VALUE_STILL_CONFIGURES_OTHER_API`, regressão, renomeação | 4 vagas e coerentes com o `MasterSecretUtil` final ("getMasterSecret(Context, String) remains the public ... API": confere) |
| YourLocalWeather `run` | — | — | Artefato (§2.2) |

Os outros 9 registros da definição exata, lidos contra os diffs:

- AppManager `decryptData`: "The coordinated CompatUtil.java edit changes decryptData to decryptData(Context, IvParameterSpec, byte[])" confere com o diff.
- Trigger `run_v2`: "GenerateIdentityTask.kt will add the internal nested GenerationRequest" confere.
- Cryptomator `run_v6`: as suposições de `BaseCipher` sobre `ivLength` conferem.
- icsdroid, AppManager `generateRSAKeyPair`, Winterhold v5 e RedReader `run_v4` (`General.kt`): falam do próprio arquivo.
- RedReader `run_v4` (o teste) e SMSecure `run_ab61` (o teste): testes que a parte 1 regenera.

Conclusão: das 43 suposições, 1 é desmentida pelo diff final, 3 são de um rascunho sem edições, e as outras 39 valem. As 12 suposições de teste mantido nos registros que a parte 1 muda são reescritas por ela (M, `s06`).

### 2.4 Medido sobre todas as unidades

- 597 tentativas de redraft em 292 unidades. Em 124 delas, de 73 unidades, a produção foi refeita e um teste do plano ficou (M, `s05`).
- A regra escolhida (R1, §3) dispara em 88 dessas regenerações de teste, em 49 unidades: 87 por `UNINTENDED_BEHAVIOR_CHANGE` e 1 por `JOINT_FINDING_EDIT_WITHOUT_EFFECT` (Session). `DROPPED_INPUT_NO_LONGER_CONTRIBUTES` e `EXPLICIT_DEFAULT_CHANGE_TOUCHES_MORE` nunca regeneraram produção com teste mantido (M, `s05`, `s07`). No máximo 4 chamadas a mais numa unidade.
- Desfecho atual das 49: 15 com rascunho de PR, 34 sem (M, `s07`).
- Benchmark e validação do D1 (29 unidades com geração): dispara no RedReader `sha1` (`run`, `run_v3`, `run_v4`, `run_v5`) e no Session `writeEntry` (M, `s07`).
- Registros de PR que mudam (o teste mantido seria regenerado): 10 de 221. São RedReader `run_v4` (benchmark), Session `writeEntry`, FreeOTP v6 e v7, Melox `aesEcbEncrypt`, KeyPass `encrypt`, Cryptomator `getPublicKey`, LocalSend v3, SMSecure `run_ab61` e Tokn `decrypt` (M, `s06`). Os outros 5 com teste mantido foram refeitos por códigos que preservam o comportamento planejado (catch obsoleto, contrato público, renomeação, assinatura), e o Session de 2026-10-15 a regra de hoje já cobre.
- Risco do caminho novo: um teste regenerado pode atrair regressão própria. Das 343 revisões gravadas com regressão, 22 citam um arquivo de teste e 8 só testes (M, `s08_test_regressions.py`). Na última tentativa, uma regressão só em teste novo derruba o teste e mantém a produção (`regressions_only_in_new_tests`, `:1088-1098`). Antes disso, vira mais um redraft só do teste, dentro do mesmo limite de tentativas.

Nota sobre o casamento das tentativas: em 12 dos 221 registros o melhor F1 fica abaixo de 0,6 (diff portado para a ponta do branch base, ou unidade conjunta). Tokn (0,09), Melox (0,32) e LocalSend (0,40) estão entre os 10 que mudam. A tentativa escolhida pode não ser a entregue, e a contagem desses três não foi conferida à mão: o número firme é 7 de 221.

## 3. Mudanças

Um commit, só em `src/cognalys/llm/semantic_validation.py`. Nenhum prompt, schema, versão de contrato (`generator_contract_version`, `REVIEW_CONTRACT_VERSION`) ou fixture dourada muda. Os checkpoints são chaveados pelo contrato, que não muda: uma execução retomada continua de onde estava.

**`src/cognalys/llm/semantic_validation.py`**, logo depois de `_BEHAVIOUR_REFUSAL_CODES` (`:6562-6569`):

```python
# Feedback that asks a production draft to behave otherwise than the draft
# the planned tests were written against: a regression the behaviour review
# found, an edit for a joint finding with no effect, an input the new path
# dropped, an explicit default that changes more (all returned by
# coordinated_generation on the reviewed draft). Session writeEntry
# (d1_validation, 2026-11-10): JOINT_FINDING_EDIT_WITHOUT_EFFECT took the
# RawAesKey wrapper out of LogFile.java, and the kept
# LogFileKeyEncodingRegressionTest still cloned the key bytes as the removed
# code had and still assumed "the production Key implementation". Over every
# recorded run, 87 such regenerations of 49 units kept a test written for
# the refused draft. Unlike _BEHAVIOUR_REFUSAL_CODES, these take the tests
# only when a production file is regenerated: a regression the review found
# in a test regenerates that test alone.
_REDRAFTED_BEHAVIOUR_CODES = frozenset({
    "UNINTENDED_BEHAVIOR_CHANGE", "JOINT_FINDING_EDIT_WITHOUT_EFFECT",
    "DROPPED_INPUT_NO_LONGER_CONTRIBUTES", "EXPLICIT_DEFAULT_CHANGE_TOUCHES_MORE",
})
```

Em `semantic_validation_retry_targets`, entre o bloco de `_BEHAVIOUR_REFUSAL_CODES` (`:6618-6619`) e `return targets or set(generation_paths)`:

```python
    # The tests of a production file regenerated for one of these were written
    # for the draft being replaced, and so were their review_assumptions.
    # Test paths by source set (is_test_source_path), not by the word "test":
    # AndroidAttestation.kt is production.
    if (any(str(item.get("code", "")) in _REDRAFTED_BEHAVIOUR_CODES for item in errors)
            and any(not is_test_source_path(path) for path in targets)):
        targets |= {path for path in generation_paths if is_test_source_path(path)}
```

- `is_test_source_path` já está importado no módulo (`:67-73`).
- Docstring da função, ao fim do segundo parágrafo: "Feedback that asks a regenerated production file to behave otherwise (_REDRAFTED_BEHAVIOUR_CODES) takes the planned tests too."
- `test_paths` (o teste por `"test" in path.lower()`) não muda: trocá-lo mexe nos ramos existentes. A única divergência medida é `AndroidAttestation.kt` (1 de 461 caminhos, M).

Efeitos que vêm sozinhos, sem outra mudança:

- O teste regenerado recebe `semantic_validation_retry` com os erros da produção, `previous_file_contracts` com o rascunho novo da produção (os arquivos existentes vêm antes dos novos em `generation_paths`) e o retorno do histórico. As suposições dele são reescritas.
- A produção refeita deixa de receber `kept_files_rely_on` para esse teste: ele não é mais mantido, e a produção não fica presa às declarações que o rascunho recusado criou para ele.
- A linha de log "retrying coordinated generation for ..." passa a listar o teste.

## 4. Testes

**`tests/test_rename_retry_targets.py`** (estilo e constantes do arquivo), classe nova `RedraftedBehaviourTakesTestsTests`:

- `test_a_production_regression_regenerates_the_planned_tests`: `UNINTENDED_BEHAVIOR_CHANGE` com `affected_paths: [MANAGER]` sobre `[GENERAL, MANAGER, TEST]` dá `{MANAGER, TEST}`.
- `test_a_joint_edit_without_effect_regenerates_the_planned_tests`: o caso do Session, com `app/src/main/java/.../LogFile.java` e `app/src/test/java/.../LogFileKeyEncodingRegressionTest.java`.
- `test_a_dropped_input_or_a_wider_explicit_default_regenerates_the_planned_tests`: os dois códigos, um de cada vez.
- `test_a_regression_only_in_the_test_regenerates_the_test_alone`: `affected_paths: [TEST]` dá `{TEST}`.
- `test_a_compile_fix_keeps_the_tests`: `SYMBOL_NOT_DECLARED_IN_REPOSITORY` com `path: MANAGER` dá `{MANAGER}`.
- `test_a_production_file_named_like_a_test_is_production`: `platform/android/src/main/kotlin/.../AndroidAttestation.kt` com regressão, mais um teste em `src/test/`, dá os dois; sem teste no plano, dá só o `AndroidAttestation.kt`.
- `test_every_code_the_reviewed_draft_returns_is_classified`: lê `inspect.getsource(coordinated_generation)`, extrai os literais `"code": "([A-Z_]+)"` e afirma que o conjunto é igual a `_REDRAFTED_BEHAVIOUR_CODES`. Um código novo devolvido ao laço obriga a decidir se leva os testes.

**`tests/test_applied_edits_match_text.py`**, classe `KeptFilesRelianceTests`, ao lado de `test_the_regenerated_file_reads_what_the_kept_test_calls` e com o mesmo arnês (`Provider` falso, `_validated` sempre válido):

- `test_a_regression_redraft_regenerates_the_kept_test`: a primeira revisão devolve uma regressão em `src/Main.java`, e a segunda devolve `CLEAN_REVIEW`. Espera-se:
  - a ordem de alvos `["src/Main.java", "src/test/MainTest.java", "src/Main.java", "src/test/MainTest.java"]`;
  - o segundo prompt da produção sem `kept_files_rely_on`;
  - o segundo prompt do teste com `semantic_validation_retry`;
  - `result["review_assumptions"]` só com as suposições dos rascunhos da segunda tentativa.

O teste existente `test_the_regenerated_file_reads_what_the_kept_test_calls` continua passando sem mudança: o código dele é `PUBLIC_API_CONTRACT_CHANGED`, que mantém o teste.

## 5. Checagem offline de aceitação

Os scripts ficam na pasta de rascunho do implementador e rodam no WSL com o venv e `PYTHONPATH` do worktree. Os scripts `s04` a `s08` daqui são a referência; `scan_units.jsonl` (17 MB) é refeito por `s04_scan.py` em cerca de 13 min.

| Script | O que faz | Esperado |
|---|---|---|
| `acc_targets.py` | Aplica a `semantic_validation_retry_targets` implementada aos erros e caminhos das 597 tentativas de `scan_units.jsonl` e compara com a simulação R1 de `s05_analyze.py` | Os alvos mudam em 88 regenerações de teste, de 49 unidades, e são idênticos nas outras. Nenhum alvo some. Nenhum arquivo de produção entra que já não estivesse |
| `acc_records.py` | Para os 221 registros, `s06_records.py` com a função implementada no lugar de `rule_targets("R1")` | Mudam 10 registros (lista em §2.4), e as 12 suposições de teste mantido deles saem do conjunto mantido. Nenhum outro registro muda |
| `acc_codes.py` | Os literais `"code": "..."` de `coordinated_generation.py` contra `_REDRAFTED_BEHAVIOUR_CODES` | Iguais (é o teste de §4, rodado sobre o arquivo final depois do merge do VFIX) |

Findings movidas por balde, recalculando só o que é determinístico: 0 em todos os baldes. A regra não muda nenhuma decisão. O que ela muda (o rascunho de teste regenerado) só a validação paga mostra.

Já rodado agora, sem a implementação: `s03_trace.py`, `s04_scan.py`, `s05_analyze.py` (saída em `s05_analyze.out`), `s06_records.py` (`s06_records.out`, `records.json`), `s07_units.py` (`s07_units.out`) e `s08_test_regressions.py` (`s08_test_regressions.out`).

## 6. Validação paga

Roda a partir do worktree, depois do merge do VFIX e com as réplicas isoladas dele (`run_iso1`, `run_iso2`) como referência. Pré-checagens, as mesmas do VFIX:

1. `pgrep -af "cognalys run"` vazio.
2. C: com 12 GB livres.
3. `sha256sum data/pipeline_learning/verified_knowledge.json` anotado.
4. O `cognalys` importado aponta para o worktree.

O script `outputs/sobras_validation_20261110/run_sobras.sh` é cópia de `outputs/vfix_validation_20261110/run_isolated.sh` (store semeado por `seeded_store`, `learning-dir` próprio, marcador `.done_$v`, `max-cost = 10` por execução), com este laço:

```bash
for v in sob1 sob2 sob3 sob4; do one redreader_sha1 $v; done
```

Mais duas execuções da unidade do Session (`sob1` e `sob2`), em `outputs/sobras_validation_20261110/session_writeentry_<v>/`. Cada uma usa cópia do `run.toml` de `d1_validation_20261110/session_writeentry`, com `run-dir`, clones e worktrees novos e `learning-dir` semeado, como a parte B do VFIX.

- **n:** 6 execuções. O RedReader dispara a regra em 4 das suas 7 execuções gravadas, e o Session em 1 de 1 (M, `s07`). Espera-se de 2 a 3 disparos no RedReader e 1 a 2 no Session (E).
- **Custo:** 6 × 0,19 ≈ US$ 1,15, mais de 3 a 5 rascunhos de teste a US$ 0,013 a 0,032 cada; total ≈ US$ 1,2 a 1,3 (E). 0,19 é a média Flex das execuções do D1 (US$ 2,41 em 13). Teto: `max-cost = 10` por execução e o script para se a soma passar de US$ 4.

Aceitação:

1. **A regra dispara onde deve.** Em toda tentativa cujo retorno traz um dos quatro códigos e regenera produção, há no log de prompts uma chamada `coordinated_file` do teste do plano, com `semantic_validation_retry`, depois da chamada da produção da mesma tentativa. Em nenhuma outra tentativa o teste é regenerado por esta regra.
2. **O que se queria tirar saiu.** Em todo registro de PR da validação:
   - nenhuma suposição vem de um rascunho de teste anterior ao último rascunho da produção;
   - nenhum identificador que só o rascunho recusado da produção tinha (no Session: `RawAesKey`, `getEncoded`, o `clone()` da chave) aparece no teste.
3. **Desfechos.** RedReader dentro do conjunto `iso1`/`iso2` e do conjunto antigo. Session dentro do desfecho do D1 (PR pronto, não recomendado) ou de issue. Todo desfecho fora disso é lido e explicado.
4. **Custo.** As chamadas a mais são exatamente os testes regenerados pela regra, e o custo por execução sobe no máximo US$ 0,05 contra a média isolada da mesma finding.
5. **Leitura.** Todo diff que virar PR pronto é lido.

Não fazer o merge se:

- alguma finding cair em balde de dispensa (falso positivo reconhecido, violação sem impacto de segurança) fora do conjunto de referência;
- uma execução que hoje termina em PR pronto perder o rascunho porque o teste regenerado atraiu regressão e a produção não foi entregue sem ele;
- algum teste for regenerado por esta regra numa tentativa sem um dos quatro códigos, ou sem produção regenerada;
- algum registro de PR ainda levar suposição ou identificador do rascunho recusado vindo de um teste que a regra deveria ter regenerado;
- `acc_targets.py` mostrar diferença fora das 88 regenerações.

## 7. Riscos, limites conhecidos e o que não fazer

- **Teste novo com regressão própria.** 22 das 343 revisões com regressão citam um teste (M). Na última tentativa o teste sai e a produção fica. Antes, gasta um redraft só do teste, dentro do limite.
- **Replano antecipado.** `early_replan_after_regression` lê a fonte das regressões das duas últimas revisões. Uma regressão de teste regenerado, atribuída ao plano, conta. Não há caso medido: a validação lê todo replano das 6 execuções.
- **Execuções antigas.** Nenhum registro gravado é regenerado. Os 10 registros que mudariam continuam como estão até uma nova execução.
- **Teste mantido por código que preserva comportamento** (catch obsoleto, contrato público, renomeação, assinatura): 5 registros (M). Ficam de fora de propósito. `kept_files_reliance` cuida de compilar, e o comportamento planejado não mudou.
- **Suposições de rascunho sem edições** (Bugbane `run_v2`, SMSecure `run_v4`): 4 registros (M, `s05`). Não são sobra de rascunho descartado. Ficam fora desta especificação, e o VFIX item 4 já registra os arquivos do plano fora do commit.
- **Sobra num arquivo de produção mantido** (um auxiliar que o rascunho recusado criou e o mantido ainda chama): `kept_files_reliance` cobre a compilação, e nenhum registro lido mostrou sobra de comportamento.
- **A1.** Se o A1 entrar, ele regenera o teste criado sempre que a produção é regenerada, e esta regra fica contida nele. Os dois não conflitam: esta muda só `semantic_validation.py`.

Não fazer:

- filtrar suposições por arquivo mantido ou por nome de arquivo (§1, cortado);
- regenerar testes em todo redraft de produção;
- trocar `test_paths` dos ramos existentes nesta mudança;
- regenerar registros gravados.

## 8. Passos de implementação

Todos os estágios rodam com Opus 5.5. A ordem geral fica: VFIX (merge), Q1, sobras, Q2Q3, K3, run congelada, A1. Sobras não toca os arquivos do Q1 nem do Q2Q3 e pode entrar em qualquer ponto antes da run congelada.

| # | Passo | Estágio | Depende de / arquivo compartilhado |
|---|---|---|---|
| 1 | `_REDRAFTED_BEHAVIOUR_CODES` e o bloco em `semantic_validation_retry_targets` | implementar | — |
| 2 | Testes de §4 | implementar | 1; `test_applied_edits_match_text.py` também recebe testes do VFIX |
| 3 | `acc_targets.py`, `acc_records.py`, `acc_codes.py` | checagem offline | 1; `scan_units.jsonl` refeito se houver execuções novas |
| 4 | Suíte completa (`python -m pytest -q tests`, com `PYTHONPATH=src` no worktree) | suíte | 1 a 3 |
| 5 | Revisão adversarial (a regra é genérica? algum código devolvido ao laço ficou sem classe? algum caminho move finding para dispensa?) | revisão adversarial | 4 |
| 6 | Validação paga (§6) a partir do worktree | validação paga | 5; merge do VFIX e réplicas `iso1`/`iso2` |
| 7 | Leitura; merge e push só se nenhuma condição de "não fazer o merge" ocorreu | revisão adversarial | 6 |

Mensagem de commit:

`A redraft whose feedback asks a production file to behave otherwise (UNINTENDED_BEHAVIOR_CHANGE, JOINT_FINDING_EDIT_WITHOUT_EFFECT, DROPPED_INPUT_NO_LONGER_CONTRIBUTES, EXPLICIT_DEFAULT_CHANGE_TOUCHES_MORE) now regenerates the planned tests with it (semantic_validation._REDRAFTED_BEHAVIOUR_CODES, tests told apart by source set): they were written, with their review_assumptions, for the draft being replaced. Session writeEntry (2026-11-10) kept a test that cloned key bytes as the removed RawAesKey had and assumed "the production Key implementation". A regression found in a test still regenerates that test alone, and a compile, rename or API-level fix keeps the tests. Offline, over 597 recorded redraft attempts: 88 regenerations of 49 of 529 units gain the planned test (87 regressions, 1 joint edit without effect), every other target set is identical, and 10 of 221 unique PR diffs would have their kept test rewritten; no decision or routing changes. Full suite: <N> passed.`

## 9. Perguntas ao usuário

1. **Entra antes da run congelada?**
   - Muda o fluxo de 49 de 529 unidades (9%), só em redrafts pedidos pela revisão de comportamento.
   - Custa US$ 0,057 por finding afetada em gpt-5.6-terra Flex, ou 0,023 em gpt-6.1-terra Flex. Na média, 0,0053 ou 0,0021 por finding (M).
   - O VFIX cortou a versão larga (74 unidades) por ser véspera da run congelada. Esta é mais estreita e não toca decisão.
   - **Recomendo sim, depois do VFIX, com a validação de §6 (≈ US$ 1,2, E) antes do merge.** Se a run congelada já tiver começado, fica para depois dela.

2. **Parte 2 (suposições só de rascunhos posteriores à versão final dos arquivos de que dependem): fazer?**
   - Das 43 suposições de arquivo mantido em 15 registros, 1 é desmentida pelo diff final, e a parte 1 a reescreve.
   - Os 3 casos de "suposição só de rascunho superado" eram rascunhos entregues depois de uma regeneração falha.
   - Qualquer filtro determinístico medido apaga suposições válidas: todas as de arquivo mantido (42 válidas) ou as que nomeiam o arquivo refeito (7 válidas).
   - **Recomendo não fazer.**

3. **Os demais códigos de produção que não preservam comportamento** (`OBSOLETE_CHECKED_EXCEPTION_CATCH`, `STATEMENT_WITHOUT_EFFECT`, `REPORTED_VALUE_UNTOUCHED` e afins; a regra R3, com filtro por nome de classe) entram?
   - Não há caso medido de teste errado por eles. Na contagem de `s05`, R3 muda 7 registros contra 9 de R1: ganha a MTGFam (catch obsoleto) e perde 3 cujo teste não nomeia a classe refeita.
   - **Recomendo não.** Se aparecer caso, o código entra em `_REDRAFTED_BEHAVIOUR_CODES` com o exemplo no comentário.

## Apêndice: o que mudou em relação ao diagnóstico

| # | Ponto do diagnóstico | Decisão | Motivo |
|---|---|---|---|
| 1 | "As suposições finais são a união de todos os rascunhos montados, inclusive os mantidos" | Corrigido | É a união dos rascunhos da tentativa final (mantidos e regenerados); os superados são apagados antes do redraft (`:1473-1482`) |
| 2 | 3 registros com suposição só de rascunho superado | Retirado | Os 3 têm `kept_after_failed_regeneration`: o patch é o rascunho anterior, e as suposições são as dele (M, `s03_trace.py`) |
| 3 | 8 registros com suposição de arquivo mantido | Recontado | 15 pela definição exata, 2 dos 8 eram artefato, 1 suposição desmentida em 43 |
| 4 | 12 com teste mantido | Recontado | 15, com a tentativa entregue escolhida pelo diff |
| 5 | Correção 1: "quando o retorno remove ou muda uma edição" | Aceita, com gatilho por código | Os quatro códigos devolvidos ao laço pelo rascunho revisado, só com produção regenerada. "Remove ou muda" não tem predicado sem ler o rascunho; o código diz por quê a produção muda |
| 6 | Correção 1: "testes que exercitam o arquivo refeito" | Trocado por "testes do plano" | O filtro por nome perde 3 de 9 registros (M); é o mesmo critério de `_BEHAVIOUR_REFUSAL_CODES` |
| 7 | Correção 2 (suposições) | Cortada | Sem caso fora do que a parte 1 cobre; os filtros medidos só apagam suposições válidas |

O que continua sem verificação:

- O efeito da regra no desfecho (só a validação paga mostra).
- Os 12 registros com F1 abaixo de 0,6, entre eles Tokn, Melox e LocalSend, contados entre os 10 que mudam pela condição do segmento inteiro.
- `acc_targets.py`, `acc_records.py` e `acc_codes.py` dependem da implementação. Os números deles saem dos scripts de referência.
