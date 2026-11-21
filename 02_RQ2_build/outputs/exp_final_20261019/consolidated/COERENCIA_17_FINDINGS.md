# Coerência dos 17 findings e das correções consolidadas

## Parecer

**O conjunto é parcialmente coerente.** A origem dos artefatos e os diffs
estão bem preservados, mas a classificação, a validação e alguns textos
não sustentam tratar todas as propostas como correções concluídas.

Foram revisados os 17 findings classificados como correção proposta, os
oito previews de PR associados, os seis encaminhamentos a issue desse
grupo, os planos/rascunhos relevantes e os históricos de aplicação/build.
As outras dez issues dos 34 findings não integram este parecer dos 17.

Nos sete casos com commit, o diff registrado na metadata coincide
**exatamente** com `git diff base_commit commit_hash` do repositório local.
O oitavo é Metadata Remover, sem commit. São seis casos que produziram APKs,
um que apenas compilou os fontes do módulo do app (Feeder) e um build falho.
Nenhum PR foi publicado por estas runs.

Conforme solicitado, não executei o app nem acrescentei exigência de teste
de comportamento. Os pareceres abaixo são estáticos e usam os builds já
registrados. A falta de testes de aparelho não foi usada como razão para
reprovar um patch nesta revisão.

## Achados prioritários

### 1. P1 — O replay TLS julga uma propriedade diferente da reportada

PassNotes e Feeder têm `patch_correctness.verdict = incorrect`, mas constam
entre os sete com commit e compilação. O achado original fala da origem do
`TrustManager[]`: era um trust manager definido pela aplicação. Os patches
passam a usar um `TrustManagerFactory`, atendendo à correção planejada.

O replay, porém, usa `sslcontext-disallowed-protocol-or-default`. Essa regra
Semgrep acusa `SSLContext.getInstance("TLS")`, pois aceita só `TLSv1.2` e
`TLSv1.3`. Os próprios planos exigem manter `TLS`. Portanto, `unchanged`
nessa regra não demonstra que a correção de proveniência do trust manager
falhou. Também não pode ser apresentado como comprovação de eliminação do
achado original.

O registro coerente seria: correção de proveniência revisada no fonte,
compilação aprovada, replay disponível não cobre a mesma causa. A
divergência entre regra gerada, especificação e política precisa ser
explicitada, sem mudar o seletor apenas para satisfazer esse replay.

Evidências: [PassNotes](review_packets/05.json),
[Feeder](review_packets/07.json) e
[regra utilizada](../../../data/detector_rules/semgrep/SSLContextSpec.yml).

### 2. P1 — Untis muda a compatibilidade do cache sem explicitar isso no PR

O patch troca MD5 por SHA-256 e elimina o alerta da regra reexecutada.
Mas `getCacheFile` usa o digest inteiro como nome do arquivo. Entradas
existentes passam a ter outro nome esperado; `get` retorna `null` se o novo
arquivo não existir. Os dados antigos não são encontrados por essa nova
derivação, e não há fallback, migração, versionamento ou limpeza no patch.

Isso pode ser uma invalidação de cache aceitável, mas o plano exige uma
decisão explícita e a adjudicação registra `authorized = false` para a
mudança de nomes persistidos. O corpo do PR apenas diz que a mudança se
limita ao algoritmo e omite o efeito. Há também um invariante impossível
no plano: manter a quantidade de bytes do digest enquanto se usa o SHA-256
inteiro, cujo resultado tem tamanho diferente do MD5.

Antes de chamar a correção de coerente, definir a política do cache e
descrevê-la no PR. A ausência de testes de aparelho não é o bloqueio aqui:
o efeito está diretamente no código.

Evidências: [fonte do commit](review_packets/08_source_00.txt),
[texto do PR](review_packets/08_pr_body.md) e [plano](review_packets/08.json).

### 3. P1 — O rascunho MTG Familiar não fornece credenciais de produção

O gate `CHECKED_EXCEPTION_NOT_PROPAGATED` acusa `IOException` no fragmento
substituído, mas a fonte completa preserva um `catch (IOException)` externo.
Esse bloqueio específico ignora o contexto envolvente.

Independentemente disso, o novo `AndroidKeyStoreCredentialProvider` lança
`CredentialUnavailableException` em `getClientId`, `getClientSecret` e
`getUserAgent`, sem fornecer registros migrados. A chamada de produção usa
esse provedor. Se aceito como está, o caminho novo de obtenção de token não
teria as credenciais necessárias. O plano já depende de provisionamento
externo ainda ausente.

A issue é um encaminhamento razoável para discutir essa migração, mas o
rascunho não é uma correção completa. Corrigir apenas o falso bloqueio do
gate não resolveria isso.

Evidências: [rascunho e getters](../subset_policy/error_solutions/coordinated_drafts/com.gelakinetic.mtgfam_99.apk/3_fetch/01_MarketPriceFetcher.java.json),
[fonte com o catch externo](../subset_policy/error_methods/com.gelakinetic.mtgfam_99.apk/3_fetch.json).

### 4. P1 — DSub tem conflito entre plano, port e classificação

O plano autoriza restaurar a validação e proíbe reter um hostname verifier
que aceite tudo. A tentativa registrada no port substitui o trust manager,
mas preserva `selfSignedHostnameVerifier` com `return true`. O gate barra
mudanças que neutralizam a opção de conexão insegura; a etapa depois
responde `ABSTAIN`.

Há uma divergência real entre autorização e gate, mas esse candidato
também não carrega integralmente a correção prevista. Autorizar o gate
isoladamente não tornaria o port completo. É preciso resolver o destino
da opção e preservar a correção aprovada ao adaptar para a base atual.

O consolidado nativo ainda agrupa esse caso em “código mudou upstream”.
A causa final é uma recusa semântica no port, e a violação não foi
declarada corrigida upstream. A categoria precisa refletir isso.

Evidências: [port e contexto](review_packets/10.json),
[plano original](../subset_gates/error_solutions/v2_artifacts/github.paroj.dsub2000_217.apk/3__init__patch_plan.json).

### 5. P2 — Textos e vínculo entre findings precisam acompanhar o resultado

- **BitBanana/SecretKeyFactory:** a issue diz que não se encontrou uma
  migração que preserve dados e compile. O PR PBEKeySpec posterior já
  implementa essa migração. Vincular a issue ao mesmo trabalho e atualizar
  sua descrição. O SHA1 legado continua intencionalmente no código; isso
  não equivale a provar desaparecimento de todo alerta SecretKeyFactory.
- **PassNotes:** o patch conserva `hostnameVerifier { _, _ -> true }` no
  ramo de debug. O texto informa isso, mas a frase “This is safe because”
  apoiada apenas em assinatura, condição do ramo e seletor iguais não
  justifica segurança. Descrever a mudança específica de confiança e seu
  limite, sem alegar validação TLS completa.
- **Feeder:** `trustAllCerts()` agora valida certificados normalmente, mas
  a API e o parâmetro `trustAllCerts` de `JsonFeedParser` mantêm os nomes.
  A mudança de semântica deve ser explicada e o contrato dos chamadores
  revisto. O corpo informa corretamente que só os fontes do módulo do app
  foram compilados; o resumo do lote deve manter essa distinção.
- **TOTP, RedReader e Droid SCEP:** os previews de issue expõem códigos
  internos como `PROTOCOL_MANDATED_ALGORITHM`,
  `STORED_DERIVED_VALUE_CHANGED` e `CALLER_SUPPLIED_VALUE_OVERRIDDEN` sem
  explicar suficientemente a decisão concreta ao mantenedor. Traduzir
  para incompatibilidade com contas/servidores, localização dos bancos e
  escolha RSA 8192, respectivamente.

## Parecer por finding

| # | Finding | Parecer estático | Encaminhamento coerente |
|---|---|---|---|
| 1 | BitBanana — PBEKeySpec | Coerente no escopo revisado. Seis telas usam a API de verificação; escritores criam o formato novo; legado só é substituído após acerto. Custo corrente 100 mil, salt aleatório, comparação constante e limpeza dos PBEKeySpec. Diff confere com o commit compilado. | Manter preview para revisão. A validação registrada não reproduz o alerta original no baseline; não alegar prova dinâmica de eliminação. |
| 2 | BitBanana — SecretKeyFactory | Compartilha a migração do #1. Rascunho individual antigo usa métodos inexistentes e a issue ficou desatualizada. | Vincular ao PR do #1, descrevendo explicitamente o SHA1 mantido apenas para legado. |
| 3 | Network Survey — KeyStore | Coerente. `getEntry(...).secretKey` vira `getKey(...) as? SecretKey`, mantendo alias, carga do keystore e criação quando não há chave utilizável. | Preview tecnicamente coerente com a sequência reportada; não alegar ganho de segurança não demonstrado. |
| 4 | MTG Familiar — SecretKeySpec | Rascunho incompleto e falso bloqueio de exceção. Credenciais migradas indisponíveis. | Issue de migração/provisionamento; não aceitar o rascunho atual como PR completo. |
| 5 | PassNotes — SSLContext | A proveniência do trust manager foi corrigida conforme plano; hostname continua sem validação no debug. Replay julga outro predicado. | Ajustar texto e classificação da validação; tratar como correção limitada. |
| 6 | Traffic Light — KeyStore | Coerente. Mantém alias e cache, retornando a SecretKey recuperada ou o mesmo fallback de criação. | Manter preview. A propriedade revisada é a sequência reportada. |
| 7 | Feeder — SSLContext | Factory e mesmo manager usados no contexto e no OkHttp; hostname permissivo removido. API de nome trust-all passa a validar; replay não cobre a causa original. | Ajustar contrato/nome ou explicação aos chamadores e a validação. Registrar compilação apenas do módulo, sem APK. |
| 8 | Untis — MessageDigest | Alerta MD5 eliminado, mas compatibilidade do cache mudou sem política definida no PR. | Definir invalidação/migração/versionamento e explicitar o efeito antes de considerar a correção completa. |
| 9 | Nextcloud Cookbook — SSLContext | Não criar patch duplicado é coerente para o alerta observado: o monitor original aceita TLS e a base já substituiu SSL por TLS. A especificação curada e o Semgrep, mais restritos, divergem desse monitor. | Registrar que a alteração pretendida já está upstream. Não dizer que toda configuração TLS foi corrigida; trust-all permanece fora deste escopo. |
| 10 | DSub — SSLContext | Plano, gate e port divergem; o port também conserva hostname permissivo que o plano mandava remover. | Corrigir contrato de adaptação e categoria do resultado; continuar bloqueado enquanto essa inconsistência existir. |
| 11 | Tokn — KeyStore | Coerente. Recupera a mesma SecretKey pelo alias após a mesma inicialização/criação, sem mudar os dados cifrados. | Manter preview; patch limitado ao caminho de leitura reportado. |
| 12 | Giggity — MessageDigest | Coerente sem novo PR: a base já usa `getUrl().hashCode()` e não cria o MD5 do método reportado. | Registrar atendido upstream. |
| 13 | mOTP — MessageDigest | Gate correto ao impedir trocar algoritmo obrigatório do protocolo. A proposta inicial não podia prometer preservar compatibilidade com SHA-256. | Issue que explique a dependência do protocolo, ou classificação correspondente antes de gerar. |
| 14 | TOTP — Mac | Gate correto ao impedir troca arbitrária do algoritmo de contas cadastradas. | Preservar algoritmo do cadastro; explicar o contrato na issue, sem apenas expor código interno do gate. |
| 15 | RedReader — MessageDigest | Bloqueio coerente: hash define nomes de bancos persistidos. Plano exige aprovação de descarte e proíbe fallback, logo depende de uma decisão não tomada. | Definir política de dados ou discutir com mantenedor; não apresentar como correção autônoma. |
| 16 | Metadata Remover — SecureRandom | Mudança local coerente: remove seed literal e conserva a instância compartilhada. Não há commit nem build aprovado; Hilt falha também na base sem patch. | Manter preview bloqueado pelo build; não atribuir essa falha à alteração do SecureRandom. |
| 17 | Droid SCEP — KeyPairGenerator | Gate correto: o rascunho converte 8192 em 4096 sem autorização, embora o próprio plano proíba escolher essa política. | Tratar decisão de tamanhos aceitos antes de gerar. Violação da allowlist não prova que RSA 8192 seja inseguro. |

Entre os oito previews, quatro não apresentaram incoerência técnica no
escopo estático revisado: #1, #3, #6 e #11. Isso não é autorização de
publicação. #5 e #7 exigem alinhamento de contrato/texto/validação; #8
precisa resolver a política do cache; #16 continua bloqueado pelo build.

## Consistência do consolidado

- As 17 propostas se distribuem em oito previews, seis issues e três
  resultados de não aplicação. A categoria nativa de não aplicação inclui
  indevidamente a recusa semântica do DSub junto a dois atendimentos upstream.
- Os oito previews são sete com commit/compilação e um sem commit/build.
  A compilação do Feeder é limitada ao módulo. Esses números não representam
  PRs publicados nem pareceres de correção funcional.
- A seleção da última execução por finding é coerente para acompanhar o
  estado atual e preserva os originais. Porém, falta relacionar findings
  diferentes que compartilham o mesmo patch, como os dois do BitBanana.
- `pr_ready` é calculado por commit e build, sem exigir que a validação
  concorde com a causa original. Não deve ser traduzido como “correção
  comprovada” ou “segura para publicar”.

## Conferência complementar solicitada: BombusMod / baosFromBase64

Esse PR é de outro lote (`run_20260915_202333`), fora dos 17. O CSV original
registra `ares`, repetição 2, timeout 180 e alerta aos 33 segundos. A
documentação local identifica esse corpus como de monitor dinâmico, com
ARES como explorador. A frase pública “static analysis of Android apps”
não representa corretamente a origem do finding.

Para o patch há registro de compilação, sem evidência localizada de uma
reexecução dinâmica após a alteração. A implementação atual de
`finding_source.UI_EXPLORERS` não inclui `ares`, o que também merece ajuste
de classificação de origem. Não houve envio de comentário ou alteração
nesse PR durante esta revisão.

## Rastreabilidade

- [Verificação dos sete diffs contra commits locais](pr_snapshot_verification.json).
- [Pacotes de evidência por finding](review_packets.log).
- [Histórico por tentativa](HISTORICO_REEXECUCOES.md).
- [Detalhes dos dez que não têm preview com build aprovado](AUDITORIA_17_PROPOSTAS.md).
- Os fontes dos commits e os diffs analisados estão em `review_packets/`;
  os arquivos originais e os previews consolidados não foram reescritos
  nesta revisão.
