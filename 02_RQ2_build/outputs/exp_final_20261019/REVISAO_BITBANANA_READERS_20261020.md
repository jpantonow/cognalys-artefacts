# Revisão do bitbanana PBEKeySpec — 20/10/2026

O plano `docs/PLANO_CODEX_RUNS_RVSEC.md` foi lido. Foram executadas duas
rodadas locais, apenas para o finding PBEKeySpec do bitbanana, com cache
compartilhado, clones e worktrees no WSL. Nenhum PR ou issue foi publicado.

## Resultados

- `subset_bitbanana_readers`: compilou após reparo, mas foi reprovado na
  revisão. O modelo mudou o retorno de `appLockDataHash()` mantendo seu
  nome, atualizou a tela de senha e deixou leitores de PIN antigos.
- `subset_bitbanana_readers2`: o gate ampliado rejeitou as três tentativas.
  Não houve patch aceito nem compilação nessa rodada. O resultado é apenas
  uma prévia local de issue; ela não deve ser publicada como se a causa da
  falha estivesse no app ou dependesse de decisão do mantenedor.

O plano da segunda rodada propôs 100 mil iterações, mas omitiu
`PinEntryActivity` e `PinSetupFragment` dos arquivos autorizados para
geração. As retentativas regeneraram apenas `PinSetupActivity`. O próximo
problema do pipeline é completar o plano com todos os leitores do valor
persistido antes da geração, incluindo os leitores de emergência.

## Mudanças genéricas na biblioteca

- A extração da função que produz o valor escrito não confunde mais o
  `.commit()` seguinte com a derivação de um argumento que é uma variável.
- O gate também procura alterações na expressão de retorno de um helper
  que manteve o nome e é usado por escritores fora do patch.
- Chamadas usadas diretamente na gravação de valores novos não contam
  como verificações antigas.
- A busca evita entrar em diretórios de build e dependências.

As mudanças continuam heurísticas: não constituem prova de migração
completa e não substituem a revisão dos fluxos de PIN, senha e emergência.

## Validação

- 61 testes dos gates passaram, incluindo seis regressões adicionadas.
- Suíte completa: 2.479 testes executados, uma falha e um teste ignorado.
  A falha continua sendo `test_semgrep_rules.RuleCaseTests` para
  `SSLContextSpec`, por ausência dos exemplos vulnerável e corrigido.
- O replay dos patches anteriores barra as duas soluções defeituosas do
  bitbanana e não acusa nenhuma das 15 soluções da run2. Todos os
  repositórios consultados estavam disponíveis.
- A reavaliação do último rascunho com o código final do gate continua
  barrando `PinEntryActivity` e `PinSetupFragment`, tanto para o PIN normal
  quanto para o de emergência. O total do replay final é 18 artefatos,
  três barrados e 15 sem acusações.
- Evidências: `recheck_stored_readers.json`,
  `readers_full_tests_final.log` e
  `subset_bitbanana_readers2/retrospective/retrospective.md`.

Nenhuma alteração manual foi feita no código dos apps analisados.
