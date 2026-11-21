> Especificação de Q1 partes 2 a 4 e DL6r parte 2, escrita em 2026-11-10 para ser implementada por uma sessão Opus 5.5 seguindo docs/PLANO_FABLE.md (desenho e revisão adversarial no Fable 5.2; versão final no Opus 5.5). Os scripts citados ficam em `outputs/design_20261110/scratch/` (não versionado; arquivos acima de 2 MB não foram copiados, e os caches grandes são refeitos pelos próprios scripts). As perguntas da seção 9 esperam a resposta do usuário antes da implementação das partes que dependem delas.

# Especificação: Q1 partes 2 a 4 e DL6r parte 2 (versão final, 2026-11-10, HEAD af71cfe)

Pastas de rascunho usadas abaixo, todas sob `C:\Users\Antonow\Documents\tcc_v3\outputs\design_20261110\scratch\wf5\`:
- `Q1D` = `q1_design\`, com os scripts `s..` da primeira especificação;
- `Q1R` = `q1_review\`, com os scripts `r..` da revisão;
- `Q1F` = `q1_final\`, com os scripts `f..` desta versão. Para rodá-los: `wsl.exe bash /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/design_20261110/scratch/wf5/q1_final/run.sh q1_final/<script>`.

Nada foi editado no repositório nem no worktree, e nenhuma chamada paga foi feita. Os únicos `decision-replay` rodados foram `--dry-run`.

## 1. Resumo

(M) é medido por script, com o nome dele. (E) é estimado, com a base ao lado.

**O que entra.** Nenhum prompt, schema ou versão muda.

| Regra | O que faz | Efeito nas 297 de produção |
|---|---|---|
| R1 | Uma violação confirmada que para antes de qualquer rascunho deixa de virar issue quando o impacto julgado não é `confirmed`. Vale para NO_SAFE_FIX adjudicado (inclui o planejador que abstém, com ou sem promoção) e para contrato externo. Ela vira registro em `confirmed_without_safe_fix`, com `kind`. | 17 issues saem: 10 para "contrato externo impede a correção", 7 para "violação confirmada sem correção segura" (M, `Q1F\f04_simulate.py`) |
| R2 (forma estrita) | A resposta de uso correto só fecha como "violação sem impacto de segurança" quando o impacto é `non_security`, que é o valor que o próprio prompt prescreve (`finding_adjudication.py:1219-1223`). Impacto `confirmed` vira issue; outro impacto segue para R1. | 4 saem de "sem impacto" para "confirmada sem correção segura" (M, `f04`) |
| R3 | A abstenção do planejador depois de promoção, com impacto `confirmed`, volta a ser issue. | 0 (M, `f04`). No benchmark, 1 execução (MeloX `run_v5`) |
| T1 | A checagem do texto do PR deixa de ler pedido ao revisor e nota "For the maintainer to decide:" como efeito para o usuário. | 5 frases em 5 de 272 rascunhos; 2 retenções somem (M, `Q1D\s12_effects_sim.py` e `Q1R\r03_t1.py`) |

**Baldes nas 297 com as respostas recomendadas** (variante "Bs": `unknown` conta como não confirmado, R2 estrita, R3) (M, `f04`):

| Balde | HEAD (já com DL6r parte 1) | Proposto |
|---|---|---|
| falso positivo reconhecido | 17 | 17 |
| violação sem impacto de segurança | 78 | 74 |
| evidência insuficiente | 13 | 13 |
| issue | 47 | 30 |
| violação confirmada sem correção segura | 45 | 56 |
| contrato externo impede a correção | 0 | 10 |

- Nenhum registro entra em balde de descarte; o de "sem impacto" só diminui.
- Continuam issue as 10 issues de impacto confirmado que HEAD mantém: 9 com análise própria mais `getKey` do DNSToggle, que é conjunta.
- A 11ª, BWT `decryptDES`, já é "correção não gerada" em HEAD pelo DL6r parte 1.

**Precisa do sim do usuário** (seção 9):
- R1 inteiro.
- Se `unknown` entra em R1. Muda 0 das 297 e 39 registros a mais fora do benchmark: 133 contra 94 (M, `f04`).
- R2 estrita, que muda a regra do CLAUDE.md sobre uso correto.
- R3.

Os números de cada resposta estão na seção 5.

**Antes da decisão do usuário, uma medida paga.** R1 transforma `runtime_security_impact` em campo decisivo, e a estabilidade dele nas findings de produção que R1 move não foi medida.
- Proposta: `decision-replay` de 6 findings × 8 amostras, cerca de US$ 2,00 (E, soma das estimativas por amostra do `--dry-run`, `Q1F\f03_dry_read.py`).
- Isso corrige o plano antigo ("19 NO_SAFE_FIX mais Herald e IbisWallet, cerca de US$ 1,6"), que estava errado em três pontos:
  - são 24 issues `no_safe_fix`;
  - Herald e IbisWallet já têm 12 e 16 amostras em `replay_q2`;
  - reenviar a adjudicação não testa o roteamento deste item, que é determinístico e foi checado offline.
- Não há execução paga do benchmark neste item. A réplica das 9 (US$ 1,75 na v6, M) vai com o Q2Q3, com os conjuntos de ruído da seção 6.

**O que é cortado ou adiado:**

| Parte | Decisão | Motivo |
|---|---|---|
| Campo `maintainer_action` e divisão de `current_code_is_correct_use` (parte 3 original) | Cortado | Com R1 e R2 estrita, o booleano não decide mais entre issue e não issue. IbisWallet fica com um só desfecho em 15 de 16 amostras de replay (M, `Q1R\r05_correct_use_impact.py`). O campo mudaria todo pedido de adjudicação. |
| DL6r parte 2 (turno de reparo antes do redraft) | Vai para o A1 | Não há ganho medível offline. Base: 190 redrafts depois de falha só de `DRAFT_QUALITY_CODES`; 56 completam na tentativa seguinte e 69 falham de novo, 52 repetindo um código (M, `Q1D\s16_selfcheck_redraft.py`). A run congelada fica sem ele (pergunta 7). |
| Bloco de correção do plano | Fica como está | É o S2 da §6 de PLANO_SUPERAR_SONNET, que não se reabre. |
| Regra para o `cause_status` do Winterhold | Cortado | A forma aparece em 1 finding (M, `Q1D\s18_unattributed.py`). Com `unknown` em R1, as duas leituras do mesmo pacote (NO_SAFE_FIX `unknown` e NO_SAFE_FIX `not_established`, com causa confirmada) caem no mesmo balde. |
| Mesma checagem de impacto no fechamento por uso correto da rota de geração (`coordinated_generation.py:1427-1436`) | Não feito; limite declarado | Os 5 fechamentos gravados por essa rota têm impacto `non_security` (M, `Q1F\f08_generation_correct_use.py`), então a mudança alteraria 0 registros e tocaria um arquivo do A1. |
| Reescrever a frase de `DELIBERATE_INSECURITY_GUIDANCE` | Vai para o Q2Q3, com o texto dado na seção 8 | O Q2Q3 já muda e valida esses prompts. |
| Pular a `issue_presentation` das findings de R1 | Cortado | Teto de US$ 0,0004 por finding analisada (M, `Q1D\s20_issue_calls_cost.py`). |
| Reinterpretar execuções antigas na estatística | Não fazer | Só o escritor muda; execuções gravadas mantêm o desfecho. |

**CometOTP v6 (issue) contra v7 e v8 (PR pronto):** este item não cobre.
- **Etapa:** portão `STORED_DERIVED_VALUE_CHANGED` na validação semântica (`contract_gates.py:285-347`). As 3 tentativas do único plano falharam nele.
- **Motivo:** o plano trocava `SecretKeySpec(raw, "AES")` por `KeyGenerator`, e o portão conta a construção removida como derivação de valor armazenado.
- **v7 e v8 não são réplicas independentes:** os pedidos de `patch_planning` deles trazem `previous_attempts_of_this_finding` com `stored_derived_value_changed`, e o da v6 não (M, conferido por `Q1D` e `Q1R`).

## 2. Diagnóstico medido

**População** (M, `Q1D\s01_index.py`, `s02_scan.py`, `s07_population.py`; refeito em `Q1F\f04_simulate.py`):
- 627 diretórios de execução; 1.432 registros analisados (1.370 fora do benchmark e da validação D1, 60 no benchmark, 2 na D1).
- As 297 findings de produção têm 56 issues como gravadas e 47 sob o roteamento de HEAD. HEAD já reaplica o DL6r parte 1; a conjunta `generateNewKeyPair` do bluegate `run_v11` acompanha a primária.

**As 56 issues de produção** (M, `Q1D\s08_issues_dump.py`, `s21_edge_counts.py`):

| Tipo | n | Impacto confirmado |
|---|---|---|
| `generation_blocked` | 28 | 6 |
| `no_safe_fix` (13 `EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX`, 11 `NO_SAFE_FIX`) | 24 | 4 |
| achados conjuntos, sem arquivo próprio | 4 | 1 |

**Causa no código:**
- `finding_processing.py:1108-1206`: a cadeia de quem para na adjudicação termina em `else: persist_blocked_issue_candidate` (1202-1206). Um NO_SAFE_FIX só escapa da issue por `documented_use_without_security_impact` (195-239), que depende do booleano `current_code_is_correct_use` (237).
- `llm_contracts.py:390-402`: o booleano junta "uso documentado" com "toda mudança altera o que o código confia". `not_established` e `unknown` são respostas do modelo, no mesmo enum (`llm_contracts.py:361-363`). O prompt reserva `unknown` para "a hazard the evidence does not address" (`finding_adjudication.py:1292-1294`).
- `finding_adjudication.py:1553-1557`: quando extração ou adjudicação falham nas checagens, o próprio pipeline escreve `unknown`, mas zera junto violação e causa (`unknown`). Esse `unknown` nunca passa nas outras condições de R1.
- `finding_adjudication.py:2132-2147`: a abstenção do planejador só é registrada quando há promoção. Com ou sem promoção, a decisão é reescrita para NO_SAFE_FIX (2147). Sem promoção, cai no `else`. Com bloqueio `invalid_patch_plan` não se pede `issue_presentation` (2176-2181): a issue sai com a pergunta genérica de reserva.
- `finding_statistics.py:799-842`: o registro `confirmed_without_safe_fix` ganha da issue (799 antes de 810). O teste `issue` vem antes da decisão `EXTERNAL_` (834), por isso nenhuma issue de contrato externo aparece como "contrato externo".

**Instabilidade das amostras de replay** (M, `Q1R\r05_correct_use_impact.py`, `r06_unknown.py`):
- IbisWallet, pacote v5: uso correto verdadeiro 5 e falso 3, impacto `not_established` em 8 de 8. Pacote v4: uso correto falso 8 de 8, impacto `not_established` 7 e `confirmed` 1.
- Herald: 12 de 12 NO_SAFE_FIX com `not_established`.
- MeloX `aesEcbEncrypt`: impacto confirmado em 6 de 16.
- Winterhold v5: 6 amostras.
  - NO_SAFE_FIX com causa confirmada: 1 com `unknown`, 1 com `not_established`.
  - NO_SAFE_FIX com causa `unknown`: 3.
  - FIX_SUPPORTED: 1.

**"Sem impacto" por uso correto** (M, `r05`):

| Impacto | Todas as execuções | Nas 297 |
|---|---|---|
| `non_security` | 34 | 26 |
| `not_established` | 7 | 4 |
| `confirmed` | 1 | 0 |

Os 7 casos com `not_established` contrariam o prompt.

**Issues publicadas** (M, `Q1D\s09_published_issues.py`, levantamento de 2026-11-08): 8 de relato.
- Só DNSToggle#73 levou a mudança, e tinha impacto confirmado.
- WeeWX#51 e OpenBible2#190 receberam "por design".
- criticalmaps#380 foi publicada pelo usuário a partir de `run_20260819_105403`, segue sem resposta e está entre as 17 de R1.

**As 17 de R1 nas 297, lidas** (M, `f01_list_r1_pop.py`; leitura em `Q1D\s08_issues.txt`):

| Finding | Destino | Leitura |
|---|---|---|
| password.monitor ×2 (HIBP) | contrato externo | API de terceiro fixa SHA-1 |
| MeloX `md5Hex` ×4 (NetEase) | contrato externo | API de terceiro |
| Navidrome `md5Hash` | contrato externo | protocolo Subsonic |
| criticalmaps `SHA1` | contrato externo | backend do próprio projeto; issue #380 publicada, sem resposta |
| droynis `sha1` | contrato externo | pedido nomeável (fingerprint SHA-256) |
| J2ME `isSignatureValid` | contrato externo | pedido nomeável |
| DSub2000 `md5Hex` | confirmada sem correção | nomes de cache e token Subsonic |
| MeloX `NeteaseAuthenticatedEapi.md5Hex` | confirmada sem correção | API de terceiro |
| GPSLogger `getSocketFactory` | confirmada sem correção | planejador sem edição; pergunta genérica |
| Herald `tlsContext` | confirmada sem correção | KeyManager próprio para mTLS |
| aShell `saveAdbKeyPair` | confirmada sem correção | ordem de chamadas, `non_security` |
| nightblast `createKeyIfNeeded` | confirmada sem correção | lacuna da regra |
| PCAPdroid `getVerifiedBuild` | confirmada sem correção | pedido nomeável |

Em 13 o mantenedor não tem o que fazer. Em 3 há um pedido nomeável, sem risco estabelecido. Em 1 (criticalmaps) o usuário já publicou a issue.

**Evidência INTENT** (M, `Q1F\f06_intent_issues.py`):
- Nenhuma das 17 tem INTENT de insegurança deliberada.
- R1 move 3 execuções com INTENT `unconditional`: IbisWallet `run_v2` a `run_v4`, todas do benchmark.
- As issues delas não propõem opção nem pinning, como `DELIBERATE_INSECURITY_GUIDANCE` (`deliberate_insecurity.py:603-607`) promete. Perguntam se foi intencional e pedem para fechar se sim.

**T1:**
- `pr_text.py:1247-1266` conta como efeito qualquer frase com `_EFFECT_VERB` e `_EFFECT_SUBJECT` (1215-1224).
- `pr_generator.py:880-881` alimenta a função com `review_assumptions` e `publication_considerations`.
- Input Leaf v5 foi retido por "Verify on supported Android devices that … untrusted certificates are rejected…".
- RedReader v4 foi retido por "For the maintainer to decide: the new code no longer uses user (…)", em que "user" é um parâmetro. O prefixo é `coordinated_generation.MAINTAINER_DECISION_PREFIX` (`coordinated_generation.py:251`).

**DL6r parte 2.**
- Já existe turno de reparo para falha de forma, com o rascunho anterior (`coordinated_generation.py:2983-3023`).
- O redraft semântico regenera só os arquivos atingidos, sem o rascunho anterior (1445-1458).

## 3. Mudanças

Trabalhar no worktree `C:\Users\Antonow\Documents\tcc_v3_blockK`. Antes, conferir que nenhuma run está em curso: `wsl.exe bash -lc "pgrep -af 'cognalys run' || true"`. O código abaixo assume as respostas recomendadas; o que muda com cada "não" está dito no lugar.

**3.1 `src/cognalys/utils/disposition.py`**

Função nova, logo depois de `POLICY_DECISIONS` (linha 102 e seguintes):

```python
def external_contract_decision(decision) -> bool:
    """Whether ``decision`` says a counterpart outside the repository fixes
    what the finding is about (EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX
    today): the decisions reporting.finding_statistics has always labelled
    "contrato externo impede a correção"."""
    text = str(decision or "").upper()
    return text.startswith("EXTERNAL_") or "CONTRACT" in text
```

**3.2 `src/cognalys/llm/finding_processing.py`**

(a) Linha 39: acrescentar `external_contract_decision,` ao bloco `from cognalys.utils.disposition import (`.

(b) `documented_use_without_security_impact` (195-239):
- Trocar as linhas 225-227 por:

```python
    if field("detector_violation") not in {"confirmed", "source_established"}:
        return ""
    impact = field("runtime_security_impact")
    if impact and impact != "non_security":
        # The adjudication prescribes non_security for correct use. A
        # confirmed impact reaches the maintainer as an issue (IbisWallet
        # run, 2026-11-08), and an impact left open is a confirmed violation
        # without a safe correction (blocked_without_confirmed_impact). A
        # record without the field reads as before.
        return ""
```

- No fim da docstring, acrescentar: `Only an impact of non_security (or a record without the field) is read as without security impact: 7 recorded closures answered correct use with not_established and one with confirmed (2026-11-10).`
- Se o usuário recusar a forma estrita (pergunta 3), a condição vira `if impact == "confirmed":`, com o comentário reduzido à frase do IbisWallet.
- A disjunção `or field("runtime_security_impact") == "confirmed"` da linha 226 sai nos dois casos. Depois do teste de impacto ela só deixaria passar registros que esse teste recusa.

(c) `planner_abstained_after_promotion` (357-389): depois do teste de violação e causa (383-385), inserir:

```python
    impact = str(evidence_analysis.get(
        "runtime_security_impact", (status if isinstance(status, dict) else {}).get(
            "runtime_security_impact", ""))).strip()
    if impact == "confirmed":
        # A confirmed impact reaches the maintainer, with or without a
        # promotion (MeloX run_v5, 2026-11-09).
        return ""
```

Se o usuário recusar R3 (pergunta 4), omitir 3.2(c) e o teste 3c.

(d) Depois de `planner_abstained_after_promotion` (linha 389), acrescentar:

```python
# The values with which the adjudication answered runtime_security_impact
# without confirming an impact. "unknown" is its answer for a hazard the
# evidence does not address; the pipeline's own "unknown" (written when the
# extraction or the adjudication failed its checks) also sets the violation
# and the cause to unknown and never passes the tests below. A record
# without the field keeps its issue.
IMPACT_NOT_CONFIRMED = frozenset({"not_established", "non_security", "unknown"})


def blocked_without_confirmed_impact(evidence_analysis) -> Dict:
    """{"kind", "reason"} of a confirmed violation that ends before any draft
    with an impact the adjudication did not confirm, or {}.

    Measured 2026-11-10 over the 297 production findings: 17 of 47 issues were
    an adjudicated NO_SAFE_FIX or external contract whose impact the
    adjudication did not confirm, and in 13 the maintainer had nothing to act
    on (a third-party API fixing MD5, a rule gap). Herald and IbisWallet moved
    between an issue and no issue on the correct-use answer alone. The
    judgement is the adjudication's; this only checks it applies: its own
    decision (one a pipeline gate turned into NO_SAFE_FIX keeps its issue), a
    confirmed violation and cause, no planned edit, and an impact in
    IMPACT_NOT_CONFIRMED. kind is "external_contract" for a decision
    utils.disposition.external_contract_decision names, else "no_safe_fix";
    reason is what blocked the change, in the planner's words when the plan
    was the blocker.
    """
    if not isinstance(evidence_analysis, dict):
        return {}
    v2 = evidence_analysis.get("v2") or {}
    adjudication = v2.get("adjudication") if isinstance(v2, dict) else {}
    if not isinstance(adjudication, dict):
        adjudication = {}
    evidence_status = evidence_analysis.get("evidence_status") or {}
    if not isinstance(evidence_status, dict):
        evidence_status = {}

    def field(name: str) -> str:
        return str(evidence_analysis.get(name, evidence_status.get(name, ""))).strip()

    decision = str(evidence_analysis.get("decision", "")).upper()
    external = external_contract_decision(decision)
    if not (decision == "NO_SAFE_FIX" or external):
        return {}
    if str(adjudication.get("decision") or decision).upper() != decision:
        return {}
    if field("detector_violation") not in {"confirmed", "source_established"}:
        return {}
    if field("runtime_security_impact") not in IMPACT_NOT_CONFIRMED:
        return {}
    cause = str(evidence_analysis.get("confirmed_cause", "")).strip()
    if str(evidence_analysis.get("cause_status", "")) != "confirmed" or not cause:
        return {}
    plan = evidence_analysis.get("coordinated_fix_plan") or {}
    if isinstance(plan, dict) and plan.get("required_edits"):
        return {}
    blockers = [item for item in adjudication.get("blocking_for_generation") or []
                if isinstance(item, dict)]
    invalid_plan = next((item for item in blockers
                         if item.get("kind") == "invalid_patch_plan"), None)
    if invalid_plan:
        validator = {part.strip() for part in
                     str(invalid_plan.get("description", "")).split(";")}
        planned = v2.get("patch_plan") if isinstance(v2, dict) else {}
        listed = planned.get("generation_blockers") or [] if isinstance(planned, dict) else []
        texts = [str(item.get("description", "") if isinstance(item, dict) else item).strip()
                 for item in listed]
        texts = [text for text in texts if text not in validator]
    else:
        texts = [str(item.get("description", "")).strip() for item in blockers]
    reason = "; ".join(dict.fromkeys(text for text in texts if text)) or cause
    return {"kind": "external_contract" if external else "no_safe_fix", "reason": reason}
```

Se o usuário recusar `unknown` (pergunta 2):
- o conjunto vira `frozenset({"not_established", "non_security"})`;
- a frase sobre `"unknown"` no comentário sai;
- entra no lugar: `"unknown" keeps the issue: the evidence did not address the hazard.`

(e) Na cadeia, entre o fim do ramo `generation_shortfall` (linha 1201) e o `else:` (1202), inserir:

```python
                elif ((settled := blocked_without_confirmed_impact(evidence_analysis))
                      and not code_gone_from_base(error.get("contribution_base") or {})):
                    # A confirmed violation with no safe fix, or one a
                    # counterpart outside the repository fixes, whose impact
                    # the adjudication did not confirm: nothing the
                    # maintainer is asked. Another requirement the source
                    # breaks still reaches them.
                    self.record_confirmed_without_safe_fix(
                        error, apk_name, method_name, settled["reason"],
                        kind=settled["kind"])
                    self.persist_blocked_issue_candidate(
                        error, evidence_analysis, evidence_file, apk_name,
                        method_name, final_scope_issues, collateral_only=True,
                    )
```

Não se passa `settled_texts`. Esse parâmetro filtra violações colaterais que repetem um valor julgado uso correto (`source_facts.py:424-434`), e o motivo de R1 é um bloqueio, não esse julgamento. O ramo `generation_shortfall` também não passa.

O caminho de geração que falha (1293-1339) não muda; isso é a parte 4. Uma geração barrada continua desfecho de geração.

**3.3 `src/cognalys/llm/issue_candidates.py`**, `record_confirmed_without_safe_fix` (201-225):
- Assinatura: `def record_confirmed_without_safe_fix(self, error: Dict, apk_name: str, method_name: str, reason: str, kind: str = "") -> Path:`.
- No dicionário gravado, depois de `"reason": reason,`, acrescentar `**({"kind": kind} if kind else {}),`. Sem `kind`, o registro fica byte a byte como hoje.
- Log: com `kind == "external_contract"`, `self.logger.info("%s: a counterpart outside the repository fixes the value: %s", method_name, reason[:300])`. Senão, o texto atual.
- Docstring, trocar a primeira frase por `A violation the adjudication confirmed that ends before any draft with nothing to ask the maintainer: a planner abstention after a review promotion (finding_processing.planner_abstained_after_promotion) or a blocked violation whose impact the adjudication did not confirm (finding_processing.blocked_without_confirmed_impact).`
- Docstring, acrescentar: `kind names why: "external_contract" (read as "contrato externo impede a correção") or "no_safe_fix"; absent for a planner abstention after a promotion.`

**3.4 `src/cognalys/reporting/finding_statistics.py`**
- Importar `from cognalys.utils.disposition import external_contract_decision`. Se já houver import desse módulo, juntar.
- Linhas 414-419: calcular `reason` como hoje. Se `str(data.get("kind", "")) == "external_contract"`, gravar em `records.setdefault(key, {})["external_contract"]`; senão em `["confirmed_without_safe_fix"]`. Comentário: `# kind external_contract: finding_processing.blocked_without_confirmed_impact.`
- `_CONCLUDED` (524-525): acrescentar `"external_contract"`.
- `_final_outcome`, imediatamente antes da linha 799:

```python
    if record.get("external_contract"):
        # A counterpart outside the repository fixes the value and the
        # adjudication did not confirm an impact
        # (finding_processing.blocked_without_confirmed_impact).
        return "contrato externo impede a correção", record["external_contract"]
```

- Linha 834: trocar a condição por `if external_contract_decision(decision):`. O comportamento é idêntico.

**3.5 `src/cognalys/utils/pr_text.py`** (commit separado). Depois de `_EFFECT_SUBJECT` (1221-1224):

```python
# A sentence asking the reviewer to check something ("Verify on supported
# devices that untrusted certificates are rejected") names what to test, and
# a note handed to the maintainer to decide (coordinated_generation.
# MAINTAINER_DECISION_PREFIX) is about the code: neither says what a user
# loses. Input Leaf run_v5 and RedReader run_v4 (2026-11-09) were held for
# not stating one of each as an effect.
_REVIEWER_REQUEST = re.compile(
    r"(?i)^\s*(?:(?:on|in|with|for|before|after|during)\b[^,;:.]{0,80},\s*)?"
    r"(?:please\s+)?(?:verify|review|confirm|check|test|validate|ensure|run|re-?run|"
    r"establish|determine|decide|make\s+sure)\b")
_MAINTAINER_NOTE = re.compile(r"(?i)^\s*for\s+the\s+maintainer\s+to\s+decide\s*:")
```

Em `user_visible_effects` (1247-1266):
- primeira linha do laço `for text in texts or []:`: `if _MAINTAINER_NOTE.match(str(text or "")): continue`;
- a condição da linha 1262 vira `if _UNVERIFIABLE.search(sentence) or _REVIEWER_REQUEST.match(sentence):`;
- docstring, acrescentar: `A request to the reviewer (_REVIEWER_REQUEST) and a note for the maintainer to decide (_MAINTAINER_NOTE) record none.`

`"pr-maintainer-summary-v9"` não muda, porque as instruções ficam idênticas.

**3.6 Documentação**
- `docs/GUIA_DE_USO.md` §8, linha de `error_solutions/` (linha 825): acrescentar `; confirmed_without_safe_fix/ para violações confirmadas que param antes de qualquer rascunho sem impacto confirmado (kind external_contract quando um contrato externo fixa o valor, no_safe_fix nos demais; sem kind na abstenção do planejador após promoção).`
- Na mesma seção, onde o guia descreve "violação sem impacto de segurança" por uso correto: acrescentar `A resposta de uso correto só fecha assim com impacto non_security; impacto confirmado vira issue.` (sem R2 estrita: `…não fecha assim com impacto confirmado, que vira issue.`)
- `docs/PLANO_FABLE.md`, seção "Refinamentos": entrada `2026-11-10: item 2 (Q1 partes 2-4)`, com as seções 1 e 5 daqui.
- `outputs/agent_baseline_20261108/noise_floor.md`: seção `## Conjuntos depois do Q1`, com a tabela da seção 6.

**3.7 Pedidos gravados**

Ficam idênticos byte a byte os pedidos de pesquisa, extração, adjudicação, plano, rascunho, revisão e texto de issue. As versões não mudam: `ADJUDICATION_PROMPT_VERSION` 6, `PLAN_PROMPT_VERSION` 11, `REVIEW_CONTRACT_VERSION` 19, `CONTEXT_BUILDER_VERSION` 2.18 e `generator_contract_version` 19.

Prova: `git diff --stat af71cfe` no worktree lista só `utils/disposition.py`, `llm/finding_processing.py`, `llm/issue_candidates.py`, `reporting/finding_statistics.py`, `utils/pr_text.py`, os três arquivos de teste e os documentos. Não lista `finding_adjudication.py`, `llm_contracts.py`, `llm_schemas.py`, `coordinated_generation.py`, `pipeline_common.py`, `deliberate_insecurity.py` nem nada em `data/`.

Muda só a lista `user_visible_effects` do pedido de resumo do PR, nos 5 rascunhos de 272 cujas notas têm um pedido ao revisor ou uma nota ao mantenedor contados como efeito (M, `s12`, `r03`).

## 4. Testes

**`tests/test_finding_outcomes.py`, classe `DecisionPathTests`.** Partir de `self._documented_use_analysis(correct_use=False)` e acrescentar `"runtime_security_impact"` ao dicionário de topo.

1. `test_a_blocked_violation_without_a_confirmed_impact_is_no_maintainer_issue`, com subTests:
   - `not_established`, `non_security` e `unknown`: um arquivo em `out/confirmed_without_safe_fix`, com `kind == "no_safe_fix"` e `reason` igual ao `confirmed_cause`; `persisted` chamado uma vez com `collateral_only=True`. Sem `unknown` no conjunto, esse subTest passa para a lista seguinte.
   - `confirmed` e campo ausente: nenhum registro; `collateral_only` falso.
   - `detector_violation="not_confirmed"`: nenhum registro.
   - `v2.adjudication.decision = "SAFE_INTERNAL_DIGEST_MIGRATION"` com decisão NO_SAFE_FIX: nenhum registro.
   - `coordinated_fix_plan.required_edits` com uma edição: nenhum registro.
2. `test_an_external_contract_without_a_confirmed_impact_is_recorded_as_one`:
   - Montagem: decisão e `v2.adjudication.decision` `EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX`, `error["spec"] = "MessageDigestSpec"`, `digest_contract_assessment` presente, como o teste do Xtra monta.
   - Com `not_established`: registro com `kind == "external_contract"`.
   - Com `confirmed`: issue, `collateral_only` falso.
3. `test_a_planner_abstention_ends_the_same_with_or_without_a_promotion`:
   - (a) Sem promoção:
     - `blocking_for_generation=[{"kind": "invalid_patch_plan", "description": "supported adjudication requires at least one planned edit"}]`;
     - `v2.patch_plan.generation_blockers=["no edit keeps ED25519 working", "supported adjudication requires at least one planned edit"]`;
     - impacto `not_established`.
     - Espera-se registro com `reason == "no edit keeps ED25519 working"`.
   - (b) Com promoção e impacto `not_established`: registro, como hoje.
   - (c) Com promoção e impacto `confirmed`: nenhum registro; `collateral_only` falso.
4. `test_correct_use_needs_a_non_security_impact`:
   - `correct_use=True`, impacto `non_security`: `out/no_security_impact` existe.
   - `confirmed`: `out/no_security_impact` não existe e a issue não é colateral.
   - `not_established`: `out/no_security_impact` não existe e `out/confirmed_without_safe_fix` tem um registro.
   - Campo ausente: `out/no_security_impact` existe.
5. `test_documented_use_comes_before_the_blocked_violation`: `correct_use=True`, impacto `non_security`, decisão NO_SAFE_FIX. Espera-se só `out/no_security_impact`, sem `confirmed_without_safe_fix`; a ordem da cadeia está fixada.

Os testes existentes `test_documented_use_is_a_violation_without_security_impact`, `test_without_the_judgement_the_finding_stays_an_issue` e `test_a_planner_abstaining_after_a_promotion_is_no_maintainer_issue` passam sem alteração: os fixtures não têm o campo de impacto.

**`tests/test_finding_statistics.py`**, no estilo de `test_a_planner_abstention_after_a_promotion_stays_a_confirmed_violation` (184-203):

6. `test_an_external_contract_record_is_its_own_outcome`:
   - Registro com `"kind": "external_contract"` dá `("contrato externo impede a correção", reason)` para `detector_violation` `confirmed`, `not_confirmed` e `unknown`.
   - Sem `kind`, ou com `"no_safe_fix"`, dá `"violação confirmada sem correção segura"`.
   - Um `issue_candidates/a.apk/0_x_issue.json` colateral ao lado não muda o desfecho.
7. `test_the_external_contract_reading_is_the_dispositions`: `disposition.external_contract_decision` é verdadeiro para `EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX` e falso para `NO_SAFE_FIX`, `FIX_SUPPORTED`, `MULTI_FILE_FIX_REQUIRES_POLICY` e `""`.

**`tests/test_pr_text_quality.py`, classe `WhyTheChangeMattersTests`** (878):

8. `test_a_request_to_the_reviewer_records_no_effect`:
   - `user_visible_effects` devolve `[]` para cada um destes textos:
     - a frase gravada do Input Leaf v5;
     - `WEEWX_NOTES[1]`;
     - `"On supported Android devices, verify trusted-peer discovery, rejection of untrusted certificates, and discovery failure and plaintext-fallback behavior."`;
     - `"For the maintainer to decide: the new code no longer uses user (the account name) when connecting."`;
   - Devolve `[WEEWX_EFFECT]` para `WEEWX_NOTES` inteiro.
   - Para `["Verify X on devices. Peers with self-signed certificates will no longer connect."]`, devolve só a segunda frase.
   - `review_text_problems` sobre `WEEWX_BODY.format(effect=WEEWX_EFFECT)` com essas notas não muda: compara-se o resultado com e sem a nota de pedido.
9. `test_the_maintainer_note_is_the_reviews_prefix`: `pr_text._MAINTAINER_NOTE.match(coordinated_generation.MAINTAINER_DECISION_PREFIX + " x")` não é `None`.

## 5. Checagem offline de aceitação

**Rodado agora**, sobre os campos gravados, com as funções de HEAD para a cadeia antiga e a cadeia nova reimplementada (M, `Q1F\f04_simulate.py`, saída em `f04_out.txt`). As conjuntas acompanham a primária. Variantes:
- A: sem `unknown`, R2 só para `confirmed`;
- B: com `unknown`;
- As e Bs: as mesmas com R2 estrita.

| Conjunto | Issues HEAD | A | B | As | Bs (recomendada) |
|---|---|---|---|---|---|
| 297 de produção | 47 | 30 | 30 | 30 | 30 |
| Fora do benchmark (1.370) | 329 | 235 | 196 | 235 | 196 |
| Benchmark (60) | 25 | 16 | 16 | 16 | 16 |
| Validação D1 (2) | 0 | 0 | 0 | 0 | 0 |

Baldes de descarte primeiro: falso positivo / sem impacto / evidência insuficiente.

| Conjunto | HEAD | A e B | As e Bs |
|---|---|---|---|
| 297 | 17 / 78 / 13 | 17 / 78 / 13 | 17 / 74 / 13 |
| Fora do benchmark | 140 / 121 / 223 | 140 / 121 / 223 | 140 / 115 / 223 |
| Benchmark | 6 / 12 / 0 | 6 / 11 / 0 | 6 / 9 / 0 |

Nenhum registro entra num balde de descarte em nenhuma variante. As saídas de "sem impacto" vão para "violação confirmada sem correção segura" (R2 estrita) ou para issue (IbisWallet `run`, impacto confirmado).

**Movimentos com Bs:**
- 297:
  - 17 issues saem (10 para contrato externo, 7 para confirmada sem correção);
  - 4 saem de "sem impacto" para confirmada sem correção: mixify `sign`, tasks `newClient$suspendImpl` e `openConnection`, syncthinglite `generateKeystore`.
- Fora do benchmark:
  - 105 issues vão para confirmada sem correção (inclui Fread `pipeline_v2 buildSSLFactory`, que hoje é issue colateral);
  - 21 vão para contrato externo;
  - 7 conjuntas acompanham a primária;
  - 6 saem de "sem impacto" (5 e 1 conjunta);
  - 44 registros já "confirmada sem correção" e 2 já "contrato externo" pela decisão só ganham o registro.
- Com A, fora do benchmark: 72 + 18 + 4 conjuntas = 94 issues saem; 15 + 2 só ganham o registro.
- Benchmark:
  - 11 issues vão para confirmada sem correção: Herald v2 a v6, IbisWallet v2 a v4, MeloX v3, v4 e v6;
  - IbisWallet `run` (R2) e MeloX `run_v5` (R3) voltam a issue;
  - com R2 estrita, IbisWallet v5 e v6 vão de "sem impacto" para confirmada sem correção.
- Issues de impacto confirmado mantidas: 10 de 10 nas 297 (9 próprias mais a conjunta do DNSToggle, M, `Q1F\f05_confirmed_issues.py`), 40 de 40 fora do benchmark, 2 de 2 no benchmark.

**Arnês de `process_finding` em HEAD** (M, `Q1F\f07_harness_replay.py`). Cada análise gravada movida passa por `FixGenerator.process_finding`, com `adjudicate_finding` trocado pela análise gravada, pelo mesmo arnês dos testes; nada é enviado. Em HEAD:
- os 98 registros de R1 para confirmada sem correção e os 20 para contrato externo terminam em issue completa;
- os 7 de R2 estrita terminam em `no_security_impact` com issue colateral;
- o de R3 termina em `confirmed_without_safe_fix` com issue colateral;
- o de R2 termina em `no_security_impact` com issue colateral.

Isso confirma que a cadeia de HEAD leva cada caso ao ramo que a simulação supõe.

**T1** (M, `Q1D\s12_effects_sim.py` e `Q1R\r03_t1.py`, dois scripts independentes):
- 5 frases de 5 rascunhos deixam de contar; as retenções do Input Leaf v5 e do RedReader v4 somem;
- o efeito do primftpd continua exigido;
- `review_text_problems` não muda nos 272 corpos gravados;
- nos 1.523 arquivos de evidência e revisão, o filtro de pedido descarta só 2 frases "Decide whether…" (M, `Q1R\r08_t1_wider.py`).

**Depois de implementar** (estágio offline check, antes do commit 1):
1. Escrever `Q1F\f09_check_impl.py`, cópia de `f04_simulate.py` com três mudanças:
   - `sys.path.insert(0, "/mnt/c/Users/Antonow/Documents/tcc_v3_blockK/src")` no lugar do `src` principal;
   - na cadeia nova, `fp.documented_use_without_security_impact`, `fp.planner_abstained_after_promotion` e `fp.blocked_without_confirmed_impact` do worktree no lugar de `documented()`, `abstained()` e `r1()`;
   - na cadeia antiga, os valores de `f04_rows.json`.
   Esperado: 0 diferenças contra a coluna da variante escolhida em `f04_rows.json`.
2. Rodar `f07_harness_replay.py /mnt/c/Users/Antonow/Documents/tcc_v3_blockK/src Bs` (ou a variante escolhida). Esperado:
   - os 98 em `confirmed_without_safe_fix[no_safe_fix] + issue(collateral_only)` e os 20 em `confirmed_without_safe_fix[external_contract] + issue(collateral_only)`;
   - os 7 de R2 estrita em `confirmed_without_safe_fix[no_safe_fix] + issue(collateral_only)`;
   - R3 e R2 em `issue`;
   - nenhum `crash`.
3. Rodar `s12_effects_sim.py` e `r03_t1.py`, trocando a função proposta pela `pr_text.user_visible_effects` do worktree: 5 frases, 2 retenções a menos, primftpd ainda sinalizado, 0 mudanças em `review_text_problems`.
4. Prova da 3.7 (`git diff --stat`).

Qualquer número diferente bloqueia o commit até ser explicado.

## 6. Validação paga

**Antes da implementação, para a decisão do usuário sobre R1:** `decision-replay` da adjudicação nas findings de produção que R1 move e que foram gravadas com o modelo atual (`gpt-6.1-terra`).
- Os 17 pedidos foram reconstruídos byte a byte no `--dry-run` (M, `Q1F\f02_replay_dry.sh`, `f03_dry_read.py`).
- As 5 findings MeloX `md5Hex` dividem o contrato NetEase e custam US$ 0,092 por amostra cada; entram 2 delas.

Gravar em `outputs/q1_replay_20261110/run_q1_replay.sh`:

```bash
#!/bin/bash
# Q1: how often the adjudication's runtime_security_impact flips on findings R1 would move.
cd /mnt/c/Users/Antonow/Documents/tcc_v3 || exit 1
source ~/venv/bin/activate
set -a; . ./.env.openai >/dev/null 2>&1; set +a
R=outputs/q1_replay_20261110
mkdir -p "$R"
while IFS='|' read -r name run fid cap; do
  [ -f "$R/$name/decision_replay.json" ] && { echo "$name done already"; continue; }
  cognalys decision-replay "outputs/$run" --finding "$fid" --stage adjudication --samples 8 \
      --max-cost "$cap" --llm-service-tier flex -o "$R/$name" --json > "$R/$name.json" 2> "$R/$name.err" < /dev/null
  echo "$name exit $?"
done <<'EOF'
gpslogger|rvsec_com_mendhak_gpslogger_20261102/run|Networks:::getSocketFactory|0.30
ashell|rvsec_in_hridayan_ashell_20261102/run_v2|AdbCrypto:::saveAdbKeyPair|0.30
herald_v2|rvsec_dev_herald_android_20261105/run_v2|ClientCerts_androidKt:::tlsContext|0.30
droynis|rvsec_io_github_capitan0n_droynis_20261104/run|AndroidPackageDetails:::sha1|0.25
melox_eapi|rvsec_com_lladlam_melox_android_20261105/run|NeteaseAuthenticatedEapi:::md5Hex|1.50
melox_library|rvsec_com_lladlam_melox_android_20261105/run|NeteaseLibraryClient:::md5Hex|1.50
EOF
```

Rodar com `wsl.exe bash -lc "cp /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/q1_replay_20261110/run_q1_replay.sh /tmp/ && bash /tmp/run_q1_replay.sh"`, sem outra run em curso.

- **n:** 6 findings × 8 amostras = 48 chamadas.
- **Custo:** US$ 2,00 (E, soma de `estimated_cost_per_sample_usd` do dry-run: 0,0179 + 0,0180 + 0,0172 + 0,0136 + 0,0919 + 0,0918, vezes 8). O teto somado dos `--max-cost` é US$ 4,15; o limite ao preço padrão é US$ 4,01. A estimativa da revisão (US$ 0,75 para 9 findings) não vale: ela não contou o pacote do MeloX.

**Aceitação**, contra os conjuntos de `noise_floor.md` e as séries de `replay_q2`:
- Herald teve 0 de 12 amostras com impacto confirmado.
- IbisWallet teve 1 de 16.
- MeloX `aesEcbEncrypt` teve 6 de 16. É o único caso conhecido em que R1 troca uma moeda por outra: o conjunto continua {issue, confirmada sem correção segura}.
- **R1 segue para implementação** se nenhuma das 6 findings tiver impacto `confirmed` em 3 ou mais de 8 amostras e o total for no máximo 5 de 48.
- **Não implementar R1 como está**, e levar ao usuário, se alguma finding tiver 3 ou mais amostras `confirmed`. Nesse caso R1 tornaria a issue dessa finding uma moeda, como o booleano fazia no IbisWallet.
- Registrar à parte, sem efeito sobre a decisão, as amostras com `detector_violation` `not_confirmed`. É ruído da adjudicação que este item não muda.

**Depois da implementação:** nenhuma execução paga neste item. Os pedidos são idênticos byte a byte, e o roteamento é conferido offline (seção 5) antes do merge.

A réplica das 9 do benchmark (US$ 1,75 na v6, M) roda com o Q2Q3, com estes conjuntos de ruído:

| Finding | Antes | Depois do Q1 (Bs) |
|---|---|---|
| Herald | {sem impacto, issue} | {sem impacto, confirmada sem correção segura}; issue só com impacto confirmado |
| IbisWallet | {sem impacto, issue} | {issue, confirmada sem correção segura}; issue só com impacto confirmado; "sem impacto" só com `non_security` |
| MeloX | {issue, confirmada sem correção segura} | igual; issue quando a geração falha ou o impacto é confirmado |
| Winterhold | {issue, correção não gerada, PR pronto} | acrescenta confirmada sem correção segura (NO_SAFE_FIX com causa confirmada) |
| Input Leaf | {issue, PR pronto, correção gerada sem PR} | igual; a retenção do tipo v5 passa a PR pronto |
| RedReader | {issue, correção não gerada, correção gerada sem PR, sem impacto} | acrescenta PR pronto (a retenção do tipo v4 some) |
| Amethyst, CometOTP, InterTune | sem mudança | sem mudança |

**Não fazer merge se:**
- a checagem 1 ou 2 da seção 5 der diferença não explicada;
- algum arquivo proibido aparecer no `git diff --stat`;
- a suíte falhar.

## 7. Riscos, limites conhecidos e o que não fazer

**Riscos:**
- **Um risco real ainda não provado perde a issue.** Com Bs, o valor de impacto que R1 lê é `not_established` na maior parte dos registros, `non_security` e `unknown` no resto. A finding continua nos resultados com o motivo, e o rótulo "confirmada sem correção segura" não é descarte. Um registro sem o campo mantém a issue.
- **O impacto troca entre amostras.** MeloX `aesEcbEncrypt` deu 6 de 16 confirmado. R1 passa a depender dessa troca em vez da do booleano. A medida da seção 6 diz se isso vale para as findings de produção.
- **droynis e J2ME** vão para "contrato externo" embora o valor esperado esteja no repositório; o rótulo segue a decisão do adjudicador. PCAPdroid `getVerifiedBuild`, droynis e J2ME tinham um pedido nomeável. criticalmaps#380, publicada pelo usuário, não seria gerada.
- **`DELIBERATE_INSECURITY_GUIDANCE`** (`deliberate_insecurity.py:603-607`) diz que NO_SAFE_FIX leva uma issue ao mantenedor. Com impacto não confirmado isso deixa de valer em 3 execuções gravadas (IbisWallet v2 a v4), e as issues delas não propunham a opção que a frase promete. O Q2Q3 aplica o texto da seção 8.
- **R3 devolve a issue de pergunta genérica** ("Is there a constraint on this code I may be missing…"). Com bloqueio `invalid_patch_plan` não se pede apresentação (`finding_adjudication.py:2176-2181`). Afeta 1 execução gravada (MeloX `run_v5`).
- **O filtro T1 olha só o começo da frase.** Um efeito real que comece por "Test" ou "Run" como substantivo seria descartado; há 0 casos nos 272 rascunhos. Uma terceira forma de falso efeito continua: "Plaintext fallback and client-certificate rejection handling remain unchanged." (Input Leaf `run_v3`), que hoje não retém nada.
- **A run congelada terá menos issues** que as anteriores; o artigo tem de dizer que a regra mudou.

**Limites conhecidos:**
- **Fechamento por uso correto na rota de geração** (`coordinated_generation.py:1427-1436` e depois `finding_processing.py:1299-1307`): sem checagem de impacto. Os 5 casos gravados são `non_security`.

**Não fazer:**
- refatorar a cadeia de decisão (adiado para 2027);
- mexer em `finding_is_reportable`;
- estender R1 a gerações barradas;
- editar `current_code_is_correct_use` no prompt ou no schema;
- replano para códigos de recusa (§6);
- corrigir o portão `STORED_DERIVED_VALUE_CHANGED` por um caso;
- editar o CLAUDE.md sem o sim do usuário;
- tocar em `coordinated_generation.py`.

## 8. Passos de implementação

Todos os estágios usam Opus 5.5.

| # | Passo | Estágio |
|---|---|---|
| 1 | Seção 6, replay pago (antes de qualquer código) e relatório dos números por finding | paid validation |
| 2 | Perguntas 1 a 5 ao usuário, com os números do passo 1 | decisão |
| 3 | 3.1, 3.3 e 3.4, com os testes 6 e 7 | implement (medium) |
| 4 | 3.2 conforme as respostas, com os testes 1 a 5 | implement (medium) |
| 5 | `python -m pytest -q tests` no WSL, com `PYTHONPATH=src`, no worktree | suite |
| 6 | Seção 5, itens 1, 2 e 4 | offline check |
| 7 | Revisão adversarial do diff dos passos 3 e 4: predicado, ordem da cadeia, nenhum movimento para descarte, generalidade | adversarial review (high) |
| 8 | Linhas da 3.6 no GUIA; commit 1 | implement |
| 9 | 3.5 com os testes 8 e 9; suíte; seção 5 item 3; commit 2 | implement / suite / offline |
| 10 | PLANO_FABLE e `noise_floor.md` (3.6); commit 3; merge com o git do WSL e push pelo helper de token, como na memória `worktree-git-mechanics` | implement |

**Dependências.** A ordem dos itens é Q1, Q2Q3, K3, run congelada, A1.
- **Q2Q3** usa `record_confirmed_without_safe_fix(..., kind="external_contract")` no caminho de geração (`finding_processing.py:1308-1329`) e depende dos passos 3 e 4. Divide `finding_processing.py` com este item.
- **Q2Q3** também troca, em `utils/deliberate_insecurity.py`, o trecho `"NO_SAFE_FIX, so the maintainer receives an issue proposing an opt-in setting or certificate pinning, or a change that keeps the old behaviour behind an explicit option, only when such an option already exists in the code; do not invent a new setting. "` por `"NO_SAFE_FIX, or a change that keeps the old behaviour behind an explicit option, only when such an option already exists in the code; do not invent a new setting. "`. Isso muda os prompts de extração, adjudicação, plano e revisão das findings com essa evidência, e o Q2Q3 valida junto.
- **Q2Q3** roda a réplica das 9 com os conjuntos da seção 6.
- **A1** herda o DL6r parte 2: o estrato "PR pronto por redraft de auto-checagem" e os 52 redrafts que repetem um código.
- **K3** não toca nestes arquivos.
- `pr_text.py`: conferir antes do passo 9 se o Q2Q3 também o altera e, se sim, fazer o T1 primeiro.

**Commit 1** (com as respostas recomendadas):

> A violation the adjudication confirmed and stopped before any draft now reaches the maintainer as an issue only when the adjudication confirmed its security impact. An adjudicated NO_SAFE_FIX or external-contract decision on a confirmed violation with a confirmed cause, no planned edit and a runtime_security_impact of not_established, non_security or unknown (finding_processing.blocked_without_confirmed_impact; a record without the field keeps its issue) is recorded in error_solutions/confirmed_without_safe_fix with a kind (external_contract for a decision utils.disposition.external_contract_decision names, no_safe_fix otherwise) and what blocked the change as its reason, in the planner's words when the plan was the blocker; only a collateral violation may still be written as an issue, and the statistics read the kind as "contrato externo impede a correção" or "violação confirmada sem correção segura". A repair decision a pipeline gate turned into NO_SAFE_FIX, a policy decision and every failed generation keep their issue. The same impact now decides the neighbouring routes: the adjudication's correct-use answer closes a finding as without security impact only with an impact of non_security, the value its prompt prescribes, a confirmed impact staying an issue and any other going to the record above; and a planner that abstains after a review promotion on a confirmed impact is an issue again, so a planner abstention ends the same way with or without a promotion. No prompt, schema or version changes. Offline, over every recorded execution under outputs: of the 297 production findings 56 are issues as recorded and 47 under the routing already in place; 17 more leave the issue bucket (10 to contrato externo, 7 to violação confirmada sem correção segura) and 4 leave violação sem impacto de segurança for violação confirmada sem correção segura; none moves to a false positive, to no security impact or to insufficient evidence, and the 10 issues with a confirmed impact stay. Outside the benchmark 133 issue records move (7 of them joined findings) and 6 records leave violação sem impacto de segurança; in the benchmark 11 issues move, 2 executions leave violação sem impacto de segurança, and one execution each returns to an issue by the correct-use rule (IbisWallet run) and the promoted-abstention rule (MeloX run_v5). GUIA_DE_USO describes the record. Full suite: N passed.

Trocas conforme as respostas:
- **Sem `unknown`:**
  - "of not_established, non_security or unknown" vira "of not_established or non_security";
  - "Outside the benchmark 133 issue records move (7 of them joined findings)" vira "Outside the benchmark 94 issue records move (4 of them joined findings)".
- **Sem R2 estrita:**
  - "only with an impact of non_security, the value its prompt prescribes, a confirmed impact staying an issue and any other going to the record above" vira "except when the impact is confirmed, which stays an issue";
  - removem-se as cláusulas "and 4 leave violação sem impacto de segurança…", "and 6 records leave…" e "2 executions leave…".
- **Sem R3:** remover "and a planner that abstains … without a promotion" e "and the promoted-abstention rule (MeloX run_v5)".

**Commit 2:**

> The pull-request text check no longer reads a request to the reviewer or a note for the maintainer to decide as an effect users will notice: pr_text.user_visible_effects skips a sentence that opens by asking for a check (verify, review, confirm, test, ..., optionally after a leading "On ...,") and a note under the review's "For the maintainer to decide:" prefix. Input Leaf run_v5 was held for not stating "Verify on supported Android devices that ... untrusted certificates are rejected" although its body stated the real effect, and RedReader run_v4 for a note that the new code no longer uses a parameter named user. Offline, over the 272 recorded pull-request drafts: 5 sentences of 5 drafts stop counting as effects, each a request or a maintainer note, those two holds lift, primftpd's recorded effect on ARCFOUR-only peers is still required, and review_text_problems changes on none. Only the user_visible_effects list of those drafts' summary requests changes. Full suite: N passed.

**Commit 3:** `docs: Q1 parts 2-4 measured (PLANO_FABLE refinements, noise sets after Q1)`.

## 9. Perguntas ao usuário

1. **R1 entra?**
   - Nas 297: 17 das 47 issues passam a "contrato externo" (10) ou "confirmada sem correção segura" (7); nenhuma vai para falso positivo, sem impacto ou evidência insuficiente; as 10 de impacto confirmado ficam.
   - Das 17, em 13 o mantenedor não tem o que fazer; 3 tinham um pedido nomeável (droynis, J2ME, PCAPdroid `getVerifiedBuild`); criticalmaps#380 foi publicada por você e não teria sido gerada.
   - Nas execuções de benchmark, 3 movidas têm evidência de insegurança deliberada (IbisWallet v2 a v4). O prompt promete a essas uma issue com proposta de opção, e as issues gravadas não traziam proposta.
   - Recomendação: sim, se o replay da seção 6 passar. Se não passar, R1 fica fora e o item se reduz a T1.
2. **`unknown` conta como impacto não confirmado?**
   - Muda 0 das 297; fora do benchmark, 39 registros a mais saem da issue (133 contra 94), quase todos digests `okio` de biblioteca.
   - Sem isso, a mesma entrada do Winterhold sai NO_SAFE_FIX `unknown` (issue) ou `not_established` (sem issue).
   - Recomendação: sim. O prompt usa `unknown` para "o perigo que a evidência não aborda", o que também não dá ao mantenedor o que fazer.
3. **R2 estrita?** A resposta de uso correto só fecha como "sem impacto" com impacto `non_security`; com `confirmed` vira issue; com outro valor, "confirmada sem correção segura".
   - Muda a regra do CLAUDE.md "When every fix changes what the code trusts or protects and the code already uses the API as documented, the outcome is 'violação sem impacto de segurança'".
   - Efeito: 4 das 297 e 6 fora do benchmark saem de "sem impacto" (para longe do descarte); IbisWallet fica com um só desfecho em 15 de 16 amostras.
   - Recomendação: sim. Se não, fica só a parte do impacto confirmado (1 execução gravada, IbisWallet `run`), e a meta de desfecho único para o IbisWallet cai.
4. **R3 entra?**
   - Reverte, só para impacto confirmado, a decisão da auditoria de 31/10 sobre abstenção promovida. É 1 execução gravada (MeloX `run_v5`), e a issue sai com a pergunta genérica de reserva.
   - Recomendação: sim, para a abstenção terminar igual com ou sem promoção.
5. **Linha nova no CLAUDE.md?** Só com o seu sim. Texto sugerido, a ajustar às respostas 2 a 4:
   > A confirmed violation stopped before any draft (an adjudicated NO_SAFE_FIX, a planner that found no edit, or an external contract) becomes an issue only when the adjudication confirmed its security impact; otherwise it ends as 'violação confirmada sem correção segura' or 'contrato externo impede a correção'. The correct-use answer closes a finding as 'violação sem impacto de segurança' only with a non_security impact. A failed generation keeps its issue.

   A linha atual sobre uso correto ganharia " (with a non_security impact)".
6. **BWT `decryptDES`** (impacto confirmado) já é "correção não gerada" em HEAD pelo DL6r parte 1. Recomendação: manter; o remédio é a rerun.
7. **DL6r parte 2 dentro do A1?**
   - O A1 roda depois da run congelada, então a run e o artigo não terão o turno de reparo.
   - Base: 69 de 190 redrafts de auto-checagem falham de novo, 52 repetindo um código.
   - Recomendação: sim, no A1. Se a resposta for não, o turno precisa de uma rodada de desenho própria antes da run congelada; esta especificação não traz um desenho implementável dele.

## Revisão: o que mudou

| # | Ponto | Decisão | Razão |
|---|---|---|---|
| 1 | `unknown` contra `not_established` | Aceito | `unknown` é resposta do modelo (`llm_contracts.py:361-363`, `finding_adjudication.py:1292-1294`); a opção vai ao usuário (pergunta 2) com os números (0 nas 297, +39 fora, M `f04`), e o comentário foi corrigido. Ler o campo cru em `v2.adjudication`, rejeitado: o `unknown` do próprio pipeline (`finding_adjudication.py:1553-1557`) zera violação e causa e não passa em R1, e o campo ausente já lê como `""`. |
| 2 | Meta de variância não atingida | Aceito | R2 estrita recomendada: 4 nas 297, 6 fora do benchmark, IbisWallet 15 de 16 (M, `f04`, `r05`). |
| 3 | R2 muda o CLAUDE.md sem perguntar | Aceito | Virou a pergunta 3, e a disjunção morta sai. Aplicar a regra na rota de geração foi rejeitado com medida: 5 fechamentos gravados, todos `non_security` (M, `f08`); fica como limite declarado. |
| 4 | Validação incapaz de reprovar | Aceito | Replay pago antes da implementação e arnês de `process_finding` offline antes do merge (já roda em HEAD, M `f07`); as 3 execuções do benchmark saem. O custo da revisão estava errado: o MeloX custa US$ 0,092 por amostra (dry-run), daí 6 × 8 = US$ 2,00. |
| 5 | Contagens que não se reproduzem | Aceito | Refeitas com o predicado real e as conjuntas: 47 e 30 nas 297; 94 (A) ou 133 (B) fora do benchmark; tabela completa sem filtro de issue (M, `f04`). |
| 6 | Frase do prompt fica falsa | Aceito | 3 execuções do benchmark, 0 em produção, e as issues delas não propunham opção (M, `f06`); a troca vai ao Q2Q3 com o texto exato. |
| 7 | Contagem "14 sem o que fazer" | Aceito | Agora são 13, com criticalmaps#380 dita na pergunta 1. |
| 8 | R3 devolve a issue genérica | Aceito | Dito na pergunta 4 e nos riscos (`finding_adjudication.py:2176-2181`). |
| 9 | Adiar o DL6r parte 2 não é neutro | Aceito | Dito na pergunta 7, com a consequência para a run congelada. |
| 10 | `settled_texts` com o motivo errado | Aceito | O argumento é omitido (`source_facts.py:424-434`). |
| 11 | Lacunas de teste e aceitação | Aceito | Teste 5 (uso documentado antes de R1); `review_text_problems` na aceitação; a forma "remain unchanged" ficou como limite. |

**Ainda não verificado:**
- A estabilidade de `runtime_security_impact` nas findings de produção que R1 move. É o replay pago da seção 6.
- Os números de T1. Vêm de dois scripts independentes (`s12`, `r03`), mas não foram rodados de novo nesta rodada.
- O arnês de identidade de 389 pedidos não foi rodado de novo, porque nenhum arquivo que monta prompt muda.
- O comportamento do código implementado. O arnês `f07` só foi rodado em HEAD; com o worktree, roda no passo 6.