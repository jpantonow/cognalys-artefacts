# Dados de replicação: campanha estudo02, estudo de caso e filas RVSec

CSVs em UTF-8, separados por vírgula, com cabeçalho. Todos são reconstruídos por `montar_dados.py`, que só lê as fontes.

| arquivo | conteúdo | fontes |
|---|---|---|
| `campanha_estudo02_109_usos.csv` | Os 109 usos da campanha estudo02-20260916 (52 apps), com as colunas-chave da planilha, a triagem (veredito, motivo, nota), `entrou_no_estudo_de_caso` e, para os 34, o desfecho de outubro | `data/raw/exp_novo/20260925_misuses-selected.csv`, `data/processed/exp_final/triagem_109.csv`, `findings_nativos*.csv`, `findings_exp_final.csv`, `outputs/exp_final_20261019/consolidated/findings.json` |
| `estudo_de_caso_34_achados.csv` | Os 34 achados do estudo de caso: APK, classe, método, spec, mensagem, desfecho consolidado de outubro, PRs e issues publicados e estado em 18/11 | as acima, mais `outputs/prs_reports/20261118_2258/` e `artigo_tcc_latex/amostra/inventario.json` |
| `prs_publicados_49.csv` | Os 49 PRs elegíveis do inventário do artigo, um por linha, com `origem` (`estudo_de_caso_34` / `filas` / `sem_vinculo`), identidade do achado, mensagem, pasta `outputs/rvsec_*` de origem e estado em 18/11 | `outputs/prs_reports/20261118_2258/summary_rvsec.json` e `collection.json`, `amostra/inventario.json`, `data/pr_outcomes/*.json`, metadados `*_pr.json` das runs |
| `filas_rvsec_apps.csv` | Uma linha por app (pasta × APK) de cada pasta `outputs/rvsec_*` (e subpastas de lote): APK construído, exploração RVSec, achados totais e do app, runs do cognalys, desfechos dos achados do app e PRs | `sources.csv`, `github.repo`, `apks/`, `build.log`, `build.failed`, `rvsec_run.out`, `rvsec.unexplorable`, `findings_all.jsonl`, `findings.jsonl` de cada pasta; `filas_rvsec_cognalys_results.json` |
| `filas_rvsec_cognalys_results.json` | Retrato de `cognalys results <run> --json` de cada diretório de run sob `outputs/rvsec_*` (coletado em 18/11/2026) | `cognalys results` |

## De 109 para 34

A passagem é a triagem de `triagem_109.csv`: dos 109 usos, 72 são `biblioteca` (classe ausente do repositório no commit fixado), 3 são `biblioteca-vendorizada` (MyExpenses `AESObfuscator`, cópia da Play Licensing Library) e 34 são `nativo`. Os 34 nativos são 31 estabelecidos (`primary` 23, `severe-constant` 5, `severe-trust-all` 3; `findings_nativos.csv`) e 3 `undecided` (`findings_nativos_undecided.csv`). O script confere que esse conjunto é idêntico, pela chave APK + classe + método + spec, a `findings_exp_final.csv` e ao consolidado de outubro.

## Contagens (18/11/2026)

- 34 do estudo de caso: 16 issue para o mantenedor, 7 PR pronto, 3 falso positivo, 3 código mudou upstream, 3 evidência insuficiente, 1 violação sem correção segura, 1 correção gerada sem PR. 15 deles têm PR publicado (16 PRs elegíveis; FreeOTP tem dois) e 7 têm issue vinculada.
- 49 PRs: 16 `estudo_de_caso_34`, 26 `filas`, 7 `sem_vinculo` (6 de runs de agosto e setembro em `outputs/run_2026*`, com achados que não estão nem nas filas nem nos 34, e HTTP-Shortcuts #576, sem identidade no inventário).
- `filas_rvsec_apps.csv` é um retrato: a fila de 18/11 ainda estava rodando quando ele foi gerado.

## Regras de vínculo

- PR de um achado: mesmo repositório + classe + método + spec (inventário, `data/pr_outcomes`, `*_pr_created.json` das runs) ou o vínculo por APK do inventário, que cobre repositórios renomeados.
- `origem` de um PR: `filas` quando os metadados do PR estão numa pasta `outputs/rvsec_*` ou o achado só existe nas filas; `estudo_de_caso_34` quando o achado é um dos 34 e o PR não veio de uma fila. As colunas `achado_tambem_*` mostram quando o mesmo achado está nos dois conjuntos.
- Issue de um achado: o registro `*_issue_created.json` da run (pelo `finding_id` ou pela posição no `findings_index.json`); sem registro, o título da issue (repositório + método ou classe), que não distingue a spec.
- Desfecho de um achado das filas: a run principal mais recente (por data de início) que o contém; runs `run_ab*`, `run_research_*` e `run_valB*` são experimentais e só entram quando não há outra.
- Estados de PR e issue: relatório `outputs/prs_reports/20261118_2258` (18/11/2026, 22h58).

## Reconstruir

Na raiz do repositório, no WSL com `~/venv` ativo:

```
python3 artigo_tcc_latex/dados/montar_dados.py            # usa o retrato JSON
python3 artigo_tcc_latex/dados/montar_dados.py --coletar  # refaz antes os `cognalys results` que faltam
```
