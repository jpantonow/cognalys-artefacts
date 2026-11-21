# Por que 17 propostas não resultaram em 17 PRs

Foram auditados os 17 findings com decisão registrada como
`MULTI_FILE_FIX_PROPOSED_FOR_REVIEW`, usando a origem selecionada no
consolidado. Sete têm preview de PR com commit e build aprovado; os outros
dez estão detalhados abaixo. Proposta, geração e build são etapas distintas.
Nada foi reexecutado no app ou publicado nesta auditoria.

## Rechecagem das runs posteriores

O inventário de todos os diretórios executados confirmou que as reexecuções
estão no consolidado. Há 62 registros finding/run para essas 17 propostas.
O [histórico completo](HISTORICO_REEXECUCOES.md) registra os horários locais
e os resultados por tentativa; os [dados em JSON](history_17_findings.json)
permitem conferir a seleção.

- **BitBanana:** corrigido na `subset_bitbanana_scope6`, incluindo o reparo
  de compilação retomado em `validation_fix`; build final aprovado. O finding
  PBEKeySpec já entra entre os sete PRs. A solução compartilha a migração
  necessária ao finding SecretKeyFactory, cujo registro individual mais
  recente permanece como issue da `subset_bitbanana`.
- **MTG Familiar:** PR com build na `run2`; depois, `subset_policy` terminou
  como issue por gate. Não há execução posterior desse finding com sucesso.
- **DSub2000:** PR com build na `run2`; depois, o port da `subset_gates`
  foi recusado. Não há execução posterior desse finding com sucesso.
- **Droid SCEP:** PR com build na `run2`; depois, `subset_gates` terminou
  como issue por alterar a escolha do chamador. Não há execução posterior
  desse finding com sucesso.
- **mOTP/MessageDigest:** houve solução gerada na `subset_policy`, mas a
  validação não aprovou a eliminação do finding. A `subset_policy2` posterior
  terminou como issue por contrato de protocolo.
- **TOTP/Mac, RedReader, Metadata Remover, Nextcloud e Giggity:** não há
  nova execução desses findings após a `run2` neste lote.

As melhorias posteriores na biblioteca não constituem, por si só, um novo
resultado de aplicação/build para findings que não foram reexecutados.
O resultado comprovado continua sendo sete previews com build aprovado;
isso não determina o resultado de uma execução futura com a biblioteca atual.
Existem `run3.toml` e `run3.sh`, mas não `run3/` nem `run3.log`: a configuração
não comprova uma terceira execução completa dos 34 findings.

## Os dez que não chegaram a PR com build aprovado

| Finding | Onde parou | Causa constatada | Encaminhamento |
|---|---|---|---|
| BitBanana / SecretKeyFactory | Geração | O modelo chamou `AppLockUtil.verifyAppLockVerifier` e `createCurrentAppLockVerifier`, que não existem, e também alterou a derivação persistida sem compatibilidade. | A migração foi implementada no PR PBEKeySpec posterior, que compila. Vincular os dois findings à mesma correção. O caminho SHA1 legado permanece intencionalmente; não foi demonstrado desaparecimento de todo alerta SecretKeyFactory em execução. |
| MTG Familiar / SecretKeySpec | Gate de geração | `CHECKED_EXCEPTION_NOT_PROPAGATED: IOException`. O gate analisa o fragmento; a fonte completa tem um `catch (IOException)` externo que o cobre. Há também um defeito independente: o novo provedor lança `CredentialUnavailableException` em todos os três getters de credenciais. | Corrigir o contexto do gate, mas também rever o plano: faltam credenciais/ciphertexts migrados e provisionamento real. O patch não está pronto apenas por remover o bloqueio. |
| Nextcloud Cookbook / SSLContext | Aplicação | `ALREADY_FIXED_UPSTREAM`: a base já usa o seletor `TLS`, eliminando o `SSL` reportado. | Não precisa desse PR. |
| DSub2000 / SSLContext | Port para a base atual | A violação continua. O port foi barrado por `USER_OPTION_NEUTRALIZED`: restaurar confiança normal muda o efeito de “permitir conexões inseguras”. O modelo depois absteve. | Há uma inconsistência entre plano e gate: o adjudicador autoriza explicitamente essa mudança de confiança, enquanto o gate não considera essa autorização. Rever o tratamento da opção e a propagação do contrato ao port. |
| Giggity / MessageDigest | Aplicação | `ALREADY_FIXED_UPSTREAM`: `Schedule.Item.hashCode()` já usa `getUrl().hashCode()`, sem criar o MD5 reportado. | Não precisa desse PR. |
| mOTP / MessageDigest | Gate de geração | `PROTOCOL_MANDATED_ALGORITHM`: trocar MD5 por SHA-256 muda os códigos exigidos pelo protocolo mOTP. | Não há substituição unilateral compatível com o servidor. A análise deveria reconhecer esse contrato antes de propor o patch. |
| TOTP / Mac | Gate de geração | `PROTOCOL_MANDATED_ALGORITHM`: trocar HmacSHA1 quebra contas cadastradas com esse algoritmo. | Preservar o algoritmo do cadastro; tratar a regra/protocolo antes de declarar uma correção suportada. |
| RedReader / MessageDigest | Gate de geração | `STORED_DERIVED_VALUE_CHANGED`: SHA1 compõe nomes de bancos `*_subreddits.db`; trocar o digest muda o nome e perde a localização dos dados existentes. O plano proíbe fallback SHA1 e depende de aprovação de descarte. | O plano é condicionado a uma decisão de dados ainda ausente. Rever a política de migração ou obter uma decisão explícita sobre descarte; a proposta atual não é autônoma. |
| Metadata Remover / SecureRandom | Build | O patch foi aplicado, mas o Hilt falha com `NoClassDefFoundError` para `JavacBasicAnnotationProcessor`. O mesmo erro ocorre no build da base sem o patch. | Resolver o build/dependências de anotação do projeto ou do ambiente. A falha não foi atribuída ao patch. Há metadata de PR, porém sem build aprovado. |
| Droid SCEP / KeyPairGenerator | Gate de geração | `CALLER_SUPPLIED_VALUE_OVERRIDDEN`: o modelo transforma a escolha RSA 8192 em 4096. O plano proíbe escolher um novo tamanho sem autorização, mas exige apenas 3072/4096. | Resolver primeiro a política para valores fornecidos pelo usuário/chamador. O patch gerado tomou uma decisão que o próprio plano vedava. |

## Falhas da pipeline encontradas

1. **A contagem de propostas é otimista.** Há planos que exigem decisões
   ou dados externos ainda ausentes e mesmo assim seguem para geração.
   RedReader, Droid SCEP e MTG Familiar têm essas condições escritas nos
   próprios planos. mOTP/TOTP já têm contratos de protocolo identificáveis.
   Essas propostas não representam 17 correções autônomas prontas.
2. **MTG: análise de exceções sem o bloco envolvente.** O gate em
   `semantic_validation.py` calcula as exceções apenas a partir do
   `corrected_code` do fragmento. O `catch (IOException)` preservado fora
   da âncora não entra nessa decisão. A recusa específica é um falso bloqueio,
   embora o rascunho tenha outro defeito funcional independente.
3. **DSub: contrato de comportamento divergente entre etapas.** O arquivo
   de adjudicação tem `authorized_behavior_change.authorized = true` para
   substituir os permissivos trust manager/hostname verifier. O gate
   `neutralized_option_branches` não consulta essa autorização, e o port
   não transporta todo o contexto da adjudicação para a checagem. Uma correção
   precisa tornar explícito o destino da opção insegura e validar uma política
   coerente em todas as etapas.
4. **DSub: classificação de desfecho enganosa.** O relatório nativo o
   agrupa em “código mudou upstream” por `ORIGINAL_CODE_ABSENT_FROM_BASE_BRANCH`.
   O port documenta `ABSTAIN` após uma recusa semântica, não
   `ALREADY_FIXED_UPSTREAM`. Há dois casos realmente corrigidos upstream,
   mais um caso barrado no port, dentro daquela categoria de três.
5. **Findings relacionados não são deduplicados como trabalho de correção.**
   Os dois achados do BitBanana chegam ao mesmo helper e à mesma migração.
   A pipeline pode acompanhar os dois findings e vinculá-los a um único PR.

## Ordem de trabalho sugerida

1. Corrigir os falsos bloqueios/contexto no MTG e no DSub, a classificação
   de port recusado e o vínculo entre os findings do BitBanana.
2. Impedir que planos dependentes de escolhas/dados ausentes sejam
   apresentados como correções autônomas. Isso inclui o contrato mOTP/TOTP,
   o descarte/migração de bancos do RedReader, o tamanho RSA no Droid SCEP
   e o provisionamento das credenciais do MTG.
3. Investigar o classpath Hilt/Dagger do Metadata Remover e repetir só seu
   build após resolver a causa preexistente.

O critério de 17 PRs não corresponde a 17 correções independentes necessárias:
dois findings já estão corrigidos upstream, e os dois do BitBanana compartilham
uma correção. Os demais ainda precisam dos encaminhamentos acima. Não há
evidência para prometer 17 PRs seguros apenas repetindo a geração.

## Evidências

- [Todos os dez casos em JSON, incluindo erros dos gates e respostas do port](missing_prs_evidence.json).
- [Gate e símbolos inexistentes no BitBanana](../subset_bitbanana/error_solutions/coordinated_drafts/app.michaelwuensch.bitbanana_79.apk/1_encodePbkdf2/semantic_validation.json).
- [Último rascunho do MTG, incluindo os getters indisponíveis](../subset_policy/error_solutions/coordinated_drafts/com.gelakinetic.mtgfam_99.apk/3_fetch/01_MarketPriceFetcher.java.json).
- [Plano condicionado do MTG](../subset_policy/error_solutions/v2_artifacts/com.gelakinetic.mtgfam_99.apk/3_fetch_patch_plan.json).
- [Fonte analisada do MTG com o catch externo](../subset_policy/error_methods/com.gelakinetic.mtgfam_99.apk/3_fetch.json).
- [Adjudicação autorizando a mudança de confiança do DSub](../subset_gates/error_solutions/v2_artifacts/github.paroj.dsub2000_217.apk/3__init__adjudication.json).
- [Aplicação e port do DSub](../subset_gates/application_results/execution_report.json).
- [Gate mOTP](../subset_policy2/error_solutions/coordinated_drafts/org.cry.otp_31.apk/2__init_/semantic_validation.json).
- [Gate TOTP](../run2/error_solutions/coordinated_drafts/org.cry.otp_31.apk/17_hmac_sha/semantic_validation.json).
- [Plano RedReader](../run2/error_solutions/v2_artifacts/org.quantumbadger.redreader_117.apk/21_sha1_patch_plan.json).
- [Plano Droid SCEP](../subset_gates/error_solutions/v2_artifacts/systems.sieber.droid_scep_7.apk/2_CertReq_patch_plan.json).
- [Atribuição do build Metadata Remover](../run2/application_results/build_validation/40d39da41dec/validation_feedback.json).
