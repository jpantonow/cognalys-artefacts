# Julgamento cego da comparação de 2026-11-13

Kit das §3.6 e §3.7 de `outputs/design_20261110/ESPEC_COMPARACAO.md`, adaptado à rodada reduzida:
20 unidades, 4 réplicas, braços S, O, T (agentes) e C (Cognalys). Tudo em Python no WSL com o venv,
a partir dos arquivos (nada inline pelo PowerShell):

```
wsl.exe bash -lc "source ~/venv/bin/activate && cd /mnt/c/Users/Antonow/Documents/tcc_v3 && python outputs/comparison_20261113/judges/<script> <args>"
```

Nada aqui publica, e só `judge.py` sem `--dry-run` chama modelo (pelas assinaturas, Claude Code e
Codex). Rodar os juízes depende de aprovação do usuário.

## Passos

1. **Cartões** — `cards.py`. Lê as runs que já existem (S/O/T em `runs/`, C pelo `cognalys_arm/runs.json`
   quando `run_status.json` diz `complete`) e grava `cards/cards.json` (o que o juiz vê), `cards/key.json`
   (braço, réplica, build, desfecho bruto: nenhum prompt lê), `cards/lint.json` e `cards/seed.json` (sal dos
   ids e das ordens; criado uma vez, não apagar). Pode rodar de novo a qualquer momento: os ids são estáveis.
   Sai com 1 se o lint achar marcador da §3.7; a fase 2 e a prova de cegueira não rodam sem lint aprovado
   sobre os cartões atuais. `--c-incomplete-as-failure` conta como FALHA uma run C que não terminou.
2. **Fase 1 (gabarito)** — `judge.py phase1` (Opus e Sol, uma sessão por unidade e juiz, checkout só de
   leitura no commit analisado; nas unidades `fechado` e `our_pr_closed` o prompt traz a resposta do
   mantenedor de `data/pr_outcomes/`). Saída em `phase1/<juiz>/<unidade>.json`.
3. **Gabarito final** — `judge.py gabarito` (sem modelo) escreve `gabarito_proposto.md/.json` com os dois
   juízes lado a lado e `gabarito_final.template.json` (resposta do Opus). O usuário revisa, corrige, marca
   `"fixed_by_user": true` em cada unidade e salva como `gabarito_final.json`.
4. **Fase 2 (notas)** — `judge.py phase2`. Só roda numa unidade com gabarito fixado e os 16 cartões
   (4 braços x 4 réplicas; `--allow-incomplete` para forçar). Cartões FALHA não vão ao juiz (A = 0).
   Saída em `phase2/<juiz>/<unidade>.json`, com o hash de cada cartão: se um cartão mudar, a nota fica
   obsoleta e `tabulate.py` avisa.
5. **Prova de cegueira** — `judge.py blindness` (40 cartões, 10 por braço, uma sessão do Opus por cartão,
   sem ferramentas). Saída em `blindness/`.
6. **Tabela** — `tabulate.py` → `tabela.md` / `tabela.json`: A por cartão (duas leituras do C), notas por
   juiz, viés de família, perdas e ganhos candidatos, kappas.
7. **Amostra do usuário** — `tabulate.py user-sample` → `user_form.md`, `user_labels.csv` (vazio; não
   sobrescreve um já preenchido sem `--force`) e `user_sample.json`. O usuário preenche o CSV às cegas e roda
   `tabulate.py` de novo: entram o kappa usuário × nota final e as decisões nas discordâncias das unidades com
   perda candidata.

Antes de rodar de verdade, confira os prompts: `judge.py phase1 --dry-run`, `phase2 --dry-run`,
`blindness --dry-run` (`--units u1,u2` e `--judges opus,sol` limitam). Ficam em `dry_run/<fase>/<juiz>/`
(prompt, schema, comando) com `summary.json` (marcadores, base presente, rubrica literal).
`tabulate.py --self-test` confere as contas com dados sintéticos.

## Modos acrescentados em 2026-11-14

Sem variável de ambiente, os prompts e as saídas são os de antes, byte a byte (conferido com dry runs).
As variáveis se combinam; o sufixo `<S>` é `_v2` (leitura) seguido de `_<tag>` (rerun).

- **`JUDGE_READING=v2`** (leitura do autor): gabarito sem a resposta do mantenedor (`gabarito_v2.json`);
  saídas `phase1_v2/`, `phase2_v2/`, `raw/phase2_v2/`.
- **`ARMS_TAG=<tag>`** (rerun dos braços C e D): `cards.py` lê `cognalys_arm_<tag>` e `cognalys_d_arm_<tag>`
  e grava `cards_<tag>/` com o sal de `cards/` (ids e ordens iguais); `judge.py` grava `phase2<S>/`.
  `retabulate_author_rule.py` e `tabulate.py` leem esses cartões com `CARDS_DIR=cards_<tag>` e as notas com
  `PHASE2=phase2<S>`; `tabulate.py` então grava `tabela<S>.json/.md` (e lê `user_labels<S>.csv`,
  `build_overrides<S>.json`; `user-sample` só roda sem essas variáveis).
- **`JUDGE_VIEW=patch`** (visão do patch): na fase 2 só vão os cartões de PR (ISSUE e NENHUMA AÇÃO não vão:
  na leitura do autor contam 1 seja qual for o texto). Cada cartão mostra o achado, o diff contra o commit
  analisado e se o autor compilou aquele diff; título e descrição ficam retidos. Prompt
  `prompts/phase2_patch.txt` (papel, acesso ao repositório e gabarito de `phase2.txt`; só a rubrica R2,
  literal de `phase2.txt`; `fixes_what_is_reported` e `regression_scenario`; sem r4/r5). Saídas
  `phase2<S>_patch/` e `raw/phase2<S>_patch/`. A prova de cegueira nessa visão sorteia só cartões de PR,
  com semente, até 10 por braço (`--per-arm`; menos se o braço tiver menos, e o log diz o que ficou de
  fora), sem título nem descrição, em `blindness<S>_patch/`. `phase1` recusa essa visão.
- **`JUDGE_VIEW=full`**: os prompts padrão, mas a prova de cegueira grava em `blindness<S>/`. Sem ela, a
  prova grava sempre em `blindness/`, e num rerun (ids iguais aos dos primeiros cartões) pularia os cartões
  já sondados.
- **`READING_RULE=patch`** em `retabulate_author_rule.py` (com `PHASE2=phase2<S>_patch`): PR aceitável
  quando r2 >= 2 e compilou (ou a regra 4, correção já na ponta), ISSUE/NENHUMA AÇÃO = 1, FALHA = 0, final =
  os dois juízes concordam, senão o principal (opus). Grava `tabela_regra_autor<S>_patch.json/.md`.
  Salvaguardas: `PHASE2` tem de terminar em `_patch`; todo registro lido tem de ter `view: "patch"`,
  `phase: "phase2"` e o gabarito igual ao de `PATCH_GABARITO` (padrão `gabarito_v2.json`) para a unidade,
  senão aborta (as notas da visão inteira, `phase2_v2/`, não passam por notas do patch). Um cartão de PR
  sem nota final (sem registro, `.failed.json`, sha do cartão desatualizado) entra na coluna
  `unscored_prs` / "PRs sem nota", e a tabela não é gravada enquanto houver algum, salvo `ALLOW_UNSCORED=1`.
  Uma unidade com cartões de PR e nenhum registro da visão do patch fica fora de todos os braços (senão
  seus cartões ISSUE/NENHUMA AÇÃO contariam 1 sem o PR ao lado). A leitura padrão não muda.
- **`blindness_summary.py [pasta] [--cards cards_<tag>] [--json saida.json]`**: por braço, cartões
  sorteados, respondidos, atribuídos ao braço certo (fração) e à família certa (C/D contra S/O/T),
  confiança média e palpites. Sem limiar.

Rejulgamento do rerun k3 (pré-registrado em `docs/preregistro_rejulgamento_20261114.md`), da raiz do
repositório no WSL com o venv, `J=outputs/comparison_20261113/judges`:

```
ARMS_TAG=k3 python $J/cards.py                                        # cards_k3/ (lint tem de passar)
ARMS_TAG=k3 python $J/judge.py phase2                                 # protocolo -> phase2_k3/
JUDGE_READING=v2 ARMS_TAG=k3 python $J/judge.py phase2                # autor -> phase2_v2_k3/
JUDGE_VIEW=patch JUDGE_READING=v2 ARMS_TAG=k3 python $J/judge.py phase2   # patch -> phase2_v2_k3_patch/
JUDGE_VIEW=full ARMS_TAG=k3 python $J/judge.py blindness              # cegueira, cartão inteiro -> blindness_k3/
JUDGE_VIEW=patch ARMS_TAG=k3 python $J/judge.py blindness             # cegueira, patch -> blindness_k3_patch/
cd $J
CARDS_DIR=cards_k3 PHASE2=phase2_k3 python tabulate.py                # tabela_k3.md
CARDS_DIR=cards_k3 PHASE2=phase2_v2_k3 python retabulate_author_rule.py               # tabela_regra_autor_v2_k3.md
READING_RULE=patch CARDS_DIR=cards_k3 PHASE2=phase2_v2_k3_patch python retabulate_author_rule.py  # ..._v2_k3_patch.md
python blindness_summary.py blindness_k3 --cards cards_k3
python blindness_summary.py blindness_k3_patch --cards cards_k3
```

Cada `judge.py` acima aceita `--dry-run` (prompts em `dry_run/phase2/`, `dry_run/phase2<S>_patch/`,
`dry_run/blindness<S>_patch/` ...; a visão do patch confere que a R2 é literal de `phase2.txt`).

## Operação

- Retomável: tarefa com saída é pulada (`--redo` refaz). Limite de assinatura: o juiz pausa o tempo que o CLI
  deu (30 min se não deu) e o outro segue; falha de autenticação para aquele juiz; JSON inválido tem mais
  2 tentativas e depois vira `<tarefa>.failed.json`. Um arquivo `STOP` nesta pasta encerra a passada.
- Não começa enquanto `driver.py`/`run_one.py` rodam (mesmas assinaturas); `--allow-concurrent` força.
- Transcrições em `raw/<fase>/<juiz>/`; cada saída registra tokens, segundos, chamadas de ferramenta e
  caminhos fora do checkout (`access.suspicious_paths` marca leitura de `runs/`, `cards/`, `frozen_runs`...).
- Checkouts no disco do WSL: `/home/antonow/comparison_20261113/judges/{checkout,cdiff,sessions}`
  (limpeza só local; `chmod -R u+w` antes de apagar).

## Decisões e adaptações

- **Cartão do C:** PR = título e corpo do registro de PR; diff = as edições aplicadas do registro de PR
  reaplicadas no commit analisado, senão as edições da solução, senão (registrado em `key.json`,
  `diff_source`) o diff portado para a ponta. ISSUE = o preview da issue, senão o texto que o
  `issue_creator` monta. NENHUMA AÇÃO = frase do desfecho em inglês + detalhe + resumo da adjudicação.
- **Classes do C:** como a §3.6; além dela, "correção não aplicável" com solução conta como PR nas duas
  leituras (a ponta já tem a correção, como em `analyze.py`), e "depende de decisão do mantenedor" conta
  como NENHUMA AÇÃO. Rascunho retido (não publicável) é PR em "todo rascunho pronto" e FALHA em "o que
  publicaria", e o cartão é julgado.
- **Build no cartão:** para R4 poder checar "build que não aconteceu", o cartão de PR diz se o autor
  compilou aquele diff (agentes: chamada de build com o mesmo diff; C: a validação do pipeline). Para A, o
  build vem de `key.json` (ou de `build_overrides.json`, quando um harness de build existir).
- **Normalização:** além da §3.7, tira saudação isolada ("Hi!"), a autoapresentação (estudante, UnB,
  projeto de pesquisa), listas de claims "(C2, C3)" e troca "adjudication"/"behaviour review"/"evidence
  packet" por palavras comuns. Tudo contado em `lint.json`.
- **Lint:** um marcador dentro de um identificador que já existe no código do projeto no commit fixado
  (git grep) é texto do projeto e passa (listado em `allowed_project_text`). No diff só as linhas
  adicionadas são verificadas.
- **R1 entre juízes** usa o gabarito da fase 1 de cada juiz (com o gabarito final, R1 seria igual nos dois).
  O kappa do usuário compara com a nota final dos modelos (antes da decisão do usuário).
- Ainda não implementado aqui: bootstrap pareado/Holm (§3.9), custo e atribuição de etapa (§3.10),
  harness de build (§3.6).
