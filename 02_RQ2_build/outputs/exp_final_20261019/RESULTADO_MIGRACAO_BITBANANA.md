# Migração dos verificadores persistidos — entrega de 20/10/2026

A biblioteca foi alterada e gerou um preview local completo para o achado
PBEKeySpec do bitbanana (`53c35a191a23`). O patch final compilou com
`assembleDebug`, retorno 0 e um APK produzido. Nenhum PR ou issue foi publicado.

- [Preview final do PR](subset_bitbanana_scope6/previews/pull_requests/app.michaelwuensch.bitbanana_79.apk_encodePbkdf2_PBEKeySpecSpec.md)
- [Diff final aplicado e compilado](subset_bitbanana_scope6/bitbanana_migration.patch)
- [Relatório do build final](subset_bitbanana_scope6/application_results/build_validation/53c35a191a23/production/build_report.json)
- [Auditoria dos leitores sobre o diff final](subset_bitbanana_scope6/applied_stored_value_audit.json)
- [Reparo das duas exceções de compilação](subset_bitbanana_scope6/application_results/validation_fixes/53c35a191a23_validation_fix.json)
- [Conclusão da pipeline](subset_bitbanana_scope6_resume.log)

## Resultado no app

O patch altera `UtilFunctions` e as seis telas: `PasswordEntryActivity`,
`PasswordSetupActivity`, `PasswordSetupFragment`, `PinEntryActivity`,
`PinSetupActivity` e `PinSetupFragment`. A auditoria do diff aplicado não
registrou `STORED_VALUE_READERS_NOT_UPDATED`.

Os novos verificadores usam PBKDF2-HMAC-SHA256, 100.000 iterações, salt
aleatório de 16 bytes e formato marcado que carrega os parâmetros. O caminho
SHA1/5.000 permanece para verificar o legado, com substituição condicionada
ao acerto da credencial. O patch inclui comparação com `MessageDigest.isEqual`,
`clearPassword()` em `finally` e a quebra de linha após `LOG_TAG`.

O custo de 100.000 é o alvo curado adotado na política desta execução;
não representa uma afirmação de conformidade com a recomendação OWASP citada
na solicitação. A base atual declara `minSdkVersion 26`.

## Alterações genéricas na biblioteca

- Coleta de identidade do armazenamento/chave, escritores e leitores;
  fontes completas obrigatórias e escopo parcial explícito.
- Plano que cobre os consumidores e retentativa que trata todos os leitores
  apontados, com replanejamento limitado a arquivos comprovadamente relacionados.
- Gate para leitores antigos, inclusive quando a escrita usa variável ou o
  helper mantém o nome; política numérica explícita para o custo corrente.
- Contrato preservado ao carregar a solução e recomputado na base atual.
  O portador pode atualizar um leitor confirmado que ganhou verificação de
  credenciais no upstream, mesmo sem edição na revisão analisada.
- Renomeações reavaliadas com as referências da base atual. Reparos conferem
  a política numérica no código resultante, incluindo constantes não alteradas.
- Normalizador preserva a quebra entre uma âncora sem terminador e uma nova linha.

Não foram feitas alterações manuais nos fontes do app: o patch e o reparo
de compilação foram produzidos e aplicados pela pipeline.

## Validação

- Suíte completa final: 2.513 testes, 2.511 passando, um pulado e uma
  falha preexistente em `test_every_rule_has_a_vulnerable_and_a_repaired_case`
  para `SSLContextSpec` (cobertura `alert_present`/`alert_absent`).
  [Log integral](stored_scope_full_tests_delivery.log).
- 33 testes específicos de migração e 5 testes de formatação passaram.
- 61 testes dos gates existentes, 23 regressões anteriores e 5 testes de
  integração com worktree passaram durante a implementação.
- Replay: 15 soluções da run2 sem acusações novas; três patches defeituosos
  do bitbanana continuam barrados.
- Retrato determinístico offline: decisões dos 268 achados anteriores
  inalteradas. Esse retrato não reexecuta coletores online de evidências.
- `git diff --check` passou.

Por orientação explícita do usuário, não foram exercitados os fluxos do app
nem medida a latência de desbloqueio. O resultado comprova compilação e os
critérios estáticos descritos, sem comprovação de comportamento no aparelho.
O preview continua `REVIEW_ONLY_NOT_PUBLISHABLE`; publicar exige uma decisão
separada para esse PR concreto.

O preview anterior de `subset_bitbanana_scope2`, embora compilasse, foi
reprovado pela auditoria posterior por deixar um leitor de senha na base
atual sem migração. Para revisão, usar os artefatos de `subset_bitbanana_scope6`.
