"""Consolidate existing run artifacts without executing or publishing findings."""
import json
import shutil
from collections import Counter
from pathlib import Path
from cognalys.reporting.finding_statistics import read_run, combine_runs, final_outcome, read_findings, main as statistics_main
from cognalys.utils.artifact_names import finding_artifact_stem

base = Path(__file__).resolve().parent
names = ['run2', 'subset_gates', 'subset_policy', 'subset_policy2',
         'subset_bitbanana', 'subset_bitbanana_readers', 'subset_bitbanana_readers2',
         'subset_bitbanana_scope', 'subset_bitbanana_scope2', 'subset_bitbanana_scope3',
         'subset_bitbanana_scope4', 'subset_bitbanana_scope5', 'subset_bitbanana_scope6']
runs = {name: read_run(base / name) for name in names}
corpus = read_findings(base.parents[1] / 'data/processed/exp_final/findings_exp_final.csv')
for name, records in runs.items():
    print(name, len(records), dict(Counter(r.get('decision', '') for r in records.values())),
          'solutions', sum(bool(r.get('solution')) for r in records.values()),
          'pr', sum(bool(r.get('pr')) for r in records.values()),
          'ready', sum(bool(r.get('pr_ready')) for r in records.values()))
combined = combine_runs(runs)
assert set(combined) == set(corpus), (len(combined), len(corpus), set(combined) - set(corpus))
output = base / 'consolidated'
output.mkdir(exist_ok=True)
rows = [{'apk': key[0], 'class': key[1], 'method': key[2], 'spec': key[3],
         **record, 'outcome': final_outcome(record)[0], 'outcome_detail': final_outcome(record)[1]}
        for key, record in sorted(combined.items())]
(output / 'findings.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
print('FINAL', len(rows), dict(Counter(r['decision'] for r in rows)),
      'outcomes', dict(Counter(r['outcome'] for r in rows)),
      'solutions', sum(bool(r.get('solution')) for r in rows),
      'pr', sum(bool(r.get('pr')) for r in rows),
      'builds', dict(Counter(r.get('build_status', '') for r in rows)))
for row in rows:
    print(row['run'], row['apk'], row['spec'], row.get('decision'), row['outcome'], row['outcome_detail'])

statistics_main(['--runs', *[str(base / name) for name in names], '--combine',
                 '--findings-csv', str(base.parents[1] / 'data/processed/exp_final/findings_exp_final.csv'),
                 '--report-dir', str(output / 'statistics')])

artifacts = []
for row in rows:
    run = base / row['run']
    kind = 'pull_requests' if row.get('pr') else 'issues' if row['outcome'].startswith('issue para') else ''
    if not kind:
        continue
    stem = finding_artifact_stem(row['apk'], row['method'], row['spec']) + ('_pr' if kind == 'pull_requests' else '')
    preview_stem = stem
    preview = run / 'previews' / kind / f'{preview_stem}.md'
    if kind == 'pull_requests' and not preview.is_file():
        preview = run / 'previews' / kind / f'{preview_stem.removesuffix("_pr")}.md'
    if kind == 'issues' and not preview.is_file():
        candidates = list((run / 'previews' / kind).glob(f'{preview_stem}_*.md'))
        assert len(candidates) == 1, (stem, candidates)
        preview = candidates[0]
    assert preview.is_file(), preview
    target = output / 'review' / kind / preview.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(preview, target)
    item = {'key': [row[k] for k in ('apk', 'class', 'method', 'spec')],
            'selected_run': row['run'], 'outcome': row['outcome'],
            'source_preview': str(preview), 'local_preview': str(target.relative_to(output))}
    if kind == 'pull_requests':
        original = run / 'pull_requests' / f'{stem}.json'
        assert original.is_file(), original
        shutil.copy2(original, target.with_suffix('.json'))
        item['source_metadata'] = str(original)
    artifacts.append(item)
(output / 'manifest.json').write_text(json.dumps({
    'base_run': 'run2', 'selection': 'Latest analysed run per unique finding; no pooling of attempts.',
    'runs_oldest_first': names,
    'excluded_older_subset': {'run': 'subset_openai', 'reason': 'Its execution predates run2.'},
    'findings': len(rows), 'review_artifacts': artifacts,
}, indent=2, ensure_ascii=False))

def count(records, field):
    return sum(bool(record.get(field)) for record in records.values())

lines = ['# Consolidação dos 34 findings', '',
         'A run2 foi combinada com as reexecuções posteriores. Cada finding aparece uma vez, com o resultado da última análise disponível. Os originais foram preservados. Esta pasta reúne relatórios e previews para revisão; não é uma nova execução da pipeline.', '',
         '| Etapa | run2 original | Consolidado atual |', '|---|---:|---:|',
         f'| Findings únicos | 34 | {len(rows)} |',
         f'| Decisão MULTI_FILE_FIX_PROPOSED_FOR_REVIEW | 17 | {sum(r.get("decision") == "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW" for r in rows)} |',
         f'| Soluções geradas | {count(runs["run2"], "solution")} | {count(combined, "solution")} |',
         f'| Previews de PR registrados | {count(runs["run2"], "pr")} | {count(combined, "pr")} |',
         f'| Previews de PR com commit e build aprovado | {count(runs["run2"], "pr_ready")} | {count(combined, "pr_ready")} |', '',
         'Há oito previews locais: sete com commit e build aprovado, mais o Metadata Remover, bloqueado por falha de build e sem commit. Não são sete PRs publicados: nenhuma destas runs registrou publicação de PR no GitHub. Os oito previews estão em review/pull_requests.', '',
         'Os 15 da run2 são soluções geradas, não 15 PRs concluídos. A decisão de propor correção também não garante geração, aplicação ou compilação. Os previews com build aprovado continuam sujeitos à revisão e aos bloqueios de publicação; nenhum envio ao GitHub foi feito nesta consolidação.', '',
         'Uma reexecução que terminou em issue ou não aplicabilidade substitui o PR antigo do mesmo finding. O consolidado representa o estado mais recente de cada achado.', '',
         '## Desfechos atuais', '', '| Desfecho | Findings |', '|---|---:|']
for outcome, number in Counter(r['outcome'] for r in rows).items():
    lines.append(f'| {outcome} | {number} |')
lines += ['', '## Previews com build aprovado', '']
for item in artifacts:
    if item['outcome'] == 'PR pronto':
        apk, _, method, spec = item['key']
        lines.append(f'- [{apk} — {method} — {spec}]({item["local_preview"]}) — origem `{item["selected_run"]}`.')
lines += ['', '## Issues propostas', '']
for item in artifacts:
    if item['outcome'].startswith('issue para'):
        apk, _, method, spec = item['key']
        lines.append(f'- [{apk} — {method} — {spec}]({item["local_preview"]}) — origem `{item["selected_run"]}`.')
lines += ['', '## Preview bloqueado por build', '']
for item in artifacts:
    if 'pull_requests/' in item['local_preview'] and item['outcome'] != 'PR pronto':
        apk, _, method, spec = item['key']
        lines.append(f'- [{apk} — {method} — {spec}]({item["local_preview"]}) — build falhou; commit ausente. Origem `{item["selected_run"]}`.')
lines += ['', '## PRs anteriores substituídos', '', '| Finding | run2 | Resultado atual | Origem |', '|---|---|---|---|']
for key, current in combined.items():
    previous = runs['run2'][key]
    if previous.get('pr_ready') and not current.get('pr_ready'):
        lines.append(f'| {key[0]} / {key[2]} / {key[3]} | PR com build aprovado | {final_outcome(current)[0]} | {current["run"]} |')
lines += ['', '## Arquivos', '',
          '- [Revisão de coerência dos 17 findings e oito previews](COERENCIA_17_FINDINGS.md).',
          '- [Auditoria das 17 propostas](AUDITORIA_17_PROPOSTAS.md).',
          '- [Histórico de reexecuções](HISTORICO_REEXECUCOES.md).',
          '- [Estatísticas completas](statistics/statistics.md).',
          '- [Tabela dos 34 findings e sua origem](statistics/findings.csv).',
          '- [Registros consolidados em JSON](findings.json).',
          '- [Manifesto dos artefatos selecionados](manifest.json).', '',
          '`subset_openai` não foi usado como sobrescrita: terminou antes da run2. As execuções usam versões diferentes da biblioteca, portanto este é um acompanhamento consolidado, não uma medição homogênea de uma única versão.', '']
(output / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
assert len(artifacts) == 24, len(artifacts)
