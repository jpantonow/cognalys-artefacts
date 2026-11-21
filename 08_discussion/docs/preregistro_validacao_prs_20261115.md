# Pré-registro: validação dinâmica de todos os PRs publicados (2026-11-15)

Este pré-registro foi escrito antes de compilar ou executar qualquer caso novo. Ele amplia a validação
dinâmica de `docs/PLANO_VALIDACAO_DINAMICA.md` (seção 1), cujos seis casos estão em
`outputs/dynamic_validation_20261114/results.md`, para os PRs publicados. A ampliação foi decidida depois de
conhecido o resultado dos seis casos (5 corrigidos, 0 não corrigidos, 1 inconclusivo) e é declarada como tal.

## População e inclusão

- População: os 49 PRs de `outputs/prs_reports/20261113/summary_rvsec.json`. A tabela de casos é
  `outputs/dynamic_validation_20261114/pr_cases.json`, gerada por `tools/pr_cases.py` sem nenhuma execução.
- Incluídos: as entradas sem `problems` (26). Cinco delas já foram validadas (tokn_19, bitbanana_79,
  untis_29198884, networksurvey_114 e trafficlight_38); restam 21 casos novos.
- Também incluídos, com o valor que falta fixado por regra antes de qualquer resultado:
  - pacotes do app ausentes (freeotp_48_pr521, freeotp_48_pr524, droidscep_7, redreader_117): o pacote do
    manifesto lido do APK base compilado (aapt2 badging);
  - método renomeado ou removido pelo patch (notallyx_71134, orgzlyrevived_292, amaradio_159): os sites
    corrigidos são os métodos que contêm a chamada governada depois do patch, lidos do diff, como em
    bitbanana_79. Se o patch remove a chamada governada, o site corrigido é o método que a continha.
- Excluídos, com o motivo registrado em `pr_cases.json`:
  - spec fora do conjunto jca_android, que o RVSec não verifica: bombusmod, wwwjdic, ntfy;
  - sem identidade do achado ou sem metadados do pipeline: HTTP-Shortcuts #576, nextcloud-cookbook;
  - mensagem em formato antigo sem linha do detector correspondente, ou sem linha do detector no site;
  - sem build que tenha produzido APK, ou com vários APKs sem registro de qual o RVSec analisou;
  - passnotes_11700 / keepassvault #347: o achado do PR é outro (mensagem antiga de protocolo TLS), não o
    validado em 14/11; esse resultado vale para o patch da run, não para o PR.

## Procedimento

- O commit compilado e o patch seguem a regra de `pr_cases.json`:
  - quando o diff do PR é igual ao do pipeline (sem espaços), base do pipeline + diff do pipeline;
  - nos outros casos, merge-base do PR + o `.diff` público do PR.
- Build e RVSec iguais aos seis casos:
  - build: mesma tarefa Gradle nas duas variantes, minificação desligada, helper de build da biblioteca;
  - RVSec: `cognalys rvsec-run`, `monkey:fast,ape:sata`, 600 s, jca_android, instrumentação dexlib2,
    emulador limpo a cada execução.
- Uma repetição (base e corrigido) por caso, nesta ordem: aceitos, abertos, fechados pelo mantenedor; dentro
  de cada grupo, a ordem de `pr_cases.json`.
- Segunda repetição só para os casos inconclusivos da primeira, se houver tempo, na mesma ordem e depois que
  todos os casos tiverem a primeira. Inconclusivo quer dizer:
  - base sem violação no site, ou
  - corrigido sem violação e sem cobertura do site.
  A segunda repetição pode levar um caso a qualquer resultado, inclusive "não corrigido".
- Um build que falha é tentado uma vez com a mesma receita. Se falhar de novo, o caso é registrado como "não
  compilou", sem ajuste à mão no código do app. Uma exclusão de dependência que impeça a instrumentação, como a
  de net.bytebuddy no networksurvey, é aplicada igual nas duas variantes e declarada no caso.
- Corte: o que não tiver terminado até 2026-11-18 23:59 é relatado como "não executado".

## Análise

- Regra de `classify.py`, sem mudança:
  - pendente;
  - base não reproduz;
  - não corrigido: violação da spec no site do corrigido, em qualquer repetição;
  - corrigido: sem violação e site coberto;
  - inconclusivo (cobertura).
- Todos os casos são relatados, com o status do PR, incluindo "não corrigido", "não compilou" e "não
  executado".
- Os quatro PRs publicados já validados (3 repetições cada) entram com o resultado que já têm, identificados à
  parte.
- O texto do artigo diz "a violação reportada deixou de aparecer no método corrigido", nunca "o RVSec deixou de
  reportar o app".

## Esclarecimentos (2026-11-15, 15h)

Escritos depois dos dois primeiros builds e antes de qualquer execução do RVSec nos casos novos.

1. "Os quatro PRs publicados já validados" quer dizer os cinco PRs publicados com resultado anterior: tokn,
   untis, networksurvey e trafficlight (corrigidos) e bitbanana (inconclusivo). O passnotes fica excluído.
2. JDK: o helper de build escolhe o JDK como nos builds do pipeline, sem fixar o JDK 21 dos seis casos. Os
   dois patched já compilados são idênticos byte a byte aos APKs de produção do pipeline.
3. Escolha do APK: usa o nome registrado; se ele não existir, o nome com os dígitos como curinga; se ainda
   não houver, o único APK do build. A regra usada fica registrada por caso.
4. Rótulos: valem os de `classify.py`. "Base não reproduz" também habilita a segunda repetição.
5. Um patch que não aplica é relatado como "não compilou (patch não aplica)".

## Desvios declarados (2026-11-17), depois de resultados

1. **Chamada removida pelo patch** (decisão do usuário). Quando o APK corrigido nunca executa o site e o diff
   do PR remove do arquivo do site o uso da API da especificação (o nome da especificação sem "Spec") sem
   acrescentar outro, o caso é relatado como "corrigido (chamada removida pelo patch)": não sobra o que
   executar. A regra pré-registrada daria "inconclusivo (cobertura)"; as duas leituras ficam em
   `results_prs.json` (`status` e `rule_result`). O predicado está em `classify_prs.py`
   (`call_removed_by_patch`). Caso afetado até agora: prim-ftpd #453 (o patch tira o RC4 da lista de cifras
   e troca o corpo de `BaseRC4Cipher.init`, onde estava `new SecretKeySpec(key, "ARCFOUR")`, por uma exceção).
2. **Ferramentas do relato original.** A validação usou `monkey:fast,ape:sata` em todos os casos. O achado do
   FreeOTP (`EncryptedKey.encrypt`, CipherSpec, PRs #524 e #521) veio da campanha `estudo02-20260916` do grupo
   do RVSec (`data/raw/exp_novo`), em que só droidbot dfs_greedy (5/9 execuções), bfs_greedy (4/9), humanoid
   (2/9) e ares (1/9) chegaram ao método; monkey e ape chegaram 0/9 vezes cada. Os dois casos são repetidos
   com as ferramentas do relato (droidbot dfs_greedy e bfs_greedy, 300 s, 3 repetições;
   `queue_prs_origin_tools.sh`, pasta `runs_origin_tools/`); humanoid e ares precisam de imagens que não
   estão nesta máquina. Os resultados com monkey/ape continuam relatados. Os outros casos não alcançados
   (prim-ftpd, your-local-weather, ReadYou, InterTune) vieram da nossa fila com ape:sata (ReadYou também
   monkey:fast) a 600 s, as mesmas ferramentas da validação.
3. **Interrupções.** A segunda repetição com monkey/ape do FreeOTP #524 (base) terminou; a do APK corrigido e a
   do #521 foram interrompidas para dar lugar às ferramentas do relato. O PC reiniciou às ~06:01 de 17/11; a
   execução interrompida (your-local-weather, base, rep 2) recomeçou do zero e seus arquivos parciais foram
   apagados.

## Desvios declarados (2026-11-17, segunda parte)

4. **Mensagem reportada que sumiu** (decisão do usuário, tomada depois de conhecido o resultado). A regra pré-registrada contava como "não corrigido" qualquer violação da especificação no site do corrigido. Passa a valer o rótulo "corrigido (mensagem reportada sumiu; restam outras já presentes no original)" quando todas estas condições valem:
   - a mensagem reportada (código do detector ou texto) apareceu no site do original;
   - ela não aparece em nenhuma repetição do corrigido;
   - o site do corrigido foi coberto;
   - toda mensagem que resta no site já aparecia no original.

   Nesses casos o PR tirou o que foi reportado, e o que sobra no site é código que o PR não mexeu. Implementado em `classify_prs.py` (`reported_message_gone`). A leitura pela regra original fica em `results_prs.json` (`rule_result`).
   - Casos afetados: Fread #168 e zSMTH #66, em que "SSL" virou "TLS" e ficaram KeyManager/SecureRandom nulos, e grocy #1012, em que sumiu a mensagem de ordem e ficou o KeyManager/TrustManager não observado.
   - Contagem: corrigidos passam de 22 para 25 e não corrigidos de 7 para 4.
5. **Os sete PRs excluídos por mensagem em formato antigo** (decisão do usuário). São eles: nextcloud news #1716, crazyflie #103, DSub2000 #160, Feeder #1210, etesync #301, giggity #543 e WiFiAnalyzer #618.
   - Esses PRs vieram de execuções do RVSec anteriores ao formato atual das mensagens. Por isso não havia linha do detector atual para comparar.
   - Passam a ser validados com o RVSec atual (`jca_android`) e o critério da especificação no site: o APK original precisa mostrar uma violação da especificação do PR no método do PR, e então o corrigido é rodado.
   - Mesma configuração dos outros casos: `monkey:fast,ape:sata`, 600 s, segunda repetição para os inconclusivos.
   - Os que não compilarem, ou cujo original não mostrar a violação, são relatados como tais.
   - Resultados em `results_prs.json`, com a marca deste desvio.

## Desvio declarado (2026-11-18), depois de resultados

6. **Casos sem resposta revisitados** (decisão do usuário, tomada depois de conhecidos os resultados de 14/11 a 18/11). Escrito antes de qualquer build ou execução do RVSec destes casos. Casos e regras em `outputs/dynamic_validation_20261114/pr_cases_desvio6.json` (gerado por `tools/resolve_desvio6_cases.py`, sem nenhuma execução); cadeia em `chain_desvio6_20261118.sh`.

   **A. Seis PRs excluídos passam a ser validados.** Para cada um, o valor que faltava é fixado por uma fonte registrada, nunca por escolha à vista do resultado:
   - session-android #2241 (`messenger_4545`, SSLContextSpec): faltava a tarefa de build. Vale a do build da fila do RVSec que produziu o APK analisado (`outputs/rvsec_tls_20261015/run.log`: `:app:assembleWebsiteDebug`, `apk_count=1`). O PR foi incorporado em 2026-11-17 e entra como incorporado.
   - MetadataRemover #264 (`metadataremover_20020`, SecureRandomSpec) e nextcloud-cookbook #204 (`nextcloudcookbook_62`, SSLContextSpec): faltavam a tarefa e o APK. Valem `head_build_task` e `head_build_variant` da campanha do RVSec (`data/raw/exp_novo/dataset.csv`), a mesma fonte do desvio 5. A identidade do achado do nextcloud-cookbook vem do inventário (achado `4c1c74bcbc8f`, `SSLCONTEXT-PROTO-00`).
   - summit #54 (`summit_355`, MessageDigestSpec): faltava o nome do APK. O build da fila (`outputs/rvsec_misuse5_20261017/build.log`, `:app:assembleFdroidDebug`) produziu um único APK. O patch renomeia `md5` para `sha256`, e o site corrigido é `SimpleDiskCache.sha256`, o método que contém a chamada governada depois do patch (regra pré-registrada).
   - HTTP-Shortcuts #576 (`httpshortcuts_1104060001`, SSLContextSpec): faltava a identidade do achado. Ela vem da linha do RVSec em `data/processed/errors_amostragem.csv` (`HttpClientFactory.configureTLS`, SSLContextSpec, mensagem antiga "expecting one of {TLSv1.2, TLSv1.3} but found TLS.") e do nome do ramo do PR (`fix/configuretls-sslcontextspec-...`). A tarefa e o APK vêm da campanha, como acima. O PR muda `TLSEnabledSSLSocketFactory.withTLS`, a fábrica de sockets que `configureTLS` instala, e não o arquivo do site. O site, no original e no corrigido, é o do achado.
   - keepassvault #347 (`passnotes_11700_pr347`, SSLContextSpec): é validado contra o achado que o PR trata (mensagem antiga de protocolo TLS em `HttpClientFactory.createHttpClient`), não contra o validado em 14/11. É um caso novo, com pasta própria. Os dois APKs são compilados de novo pelo `tools/build_prs.sh`: commit `77b70f8` e mesma tarefa de 14/11; o corrigido recebe o diff do PR #347. As execuções de 14/11 continuam valendo só para o patch daquela run.
   - Build igual ao dos outros casos:
     - `tools/build_prs.sh` sem mudança de receita;
     - commit do pré-registro: base do pipeline quando o diff do PR é igual ao do pipeline, senão o merge-base do PR;
     - corrigido = `.diff` público do PR aplicado sem alteração;
     - APK escolhido pela regra do esclarecimento 3;
     - pacotes ausentes = pacote do manifesto do APK base.
   - Rótulos de `classify_prs.py` sem mudança, inclusive os desvios 1 e 4. HTTP-Shortcuts #576 e keepassvault #347 têm mensagem em formato antigo, sem linha do detector atual. Por isso valem no nível da especificação, como no desvio 5: qualquer violação da especificação do PR no site, com células `V*`. Os outros quatro comparam a mensagem como os demais casos.
   - Execução como nos outros casos: `queue_prs.sh` sem mudança de configuração (`monkey:fast,ape:sata`, 600 s, jca_android). Primeiro uma repetição. Depois a segunda, só para os que ficarem inconclusivos ou com base que não reproduz. Um caso que não compilar, cujo patch não aplicar ou cujo original não mostrar a violação é relatado como tal.

   **B. Uma repetição a mais** (base e corrigido, mesma configuração e mesmos APKs) para os casos inconclusivos (cobertura) ou com base que não reproduz:
   - rep 3 para ReadYou #1334 (`reader_47`), InterTune #52 (`intertune_96`), your-local-weather #209 (`yourlocalweather_213`) e news-android #1716 (`owncloudnewsreader_196`), que já têm duas;
   - rep 4 para BitBanana #184 (`bitbanana_79`), que tem três de 14/11. Usa os APKs de 14/11 (`apks/bitbanana_79`) e a configuração de `classify.py` (`CASES["bitbanana_79"]`) sem mudança.
   - Combinação, fixada antes de ver qualquer resultado: o caso é rotulado com todas as suas repetições juntas, como `classify.classify` já faz. A base reproduz se alguma repetição da base mostrar a violação. O corrigido tem a violação se alguma repetição mostrar e está coberto se alguma repetição executar o site.
   - Uma repetição nova pode levar o caso a qualquer rótulo, inclusive "não corrigido". Não há repetição além destas.
   - O BitBanana deixa de aparecer como "já validado" e passa a ter o rótulo das quatro repetições. O rótulo de 14/11 (três repetições) fica registrado ao lado.

   **C.** BombusMod #316, wwwjdic #115 e ntfy #184 continuam excluídos: a especificação deles está fora do conjunto jca_android que o RVSec verifica.

   **Ordem e corte.**
   - Ordem: builds de A; depois rep 1 de A; depois as repetições de B; por fim a rep 2 dos casos de A que a regra indicar.
   - Uma faixa só: nenhum build ao lado do emulador, e o emulador só começa quando nenhuma outra fila do RVSec roda.
   - O que não terminar até 2026-11-19 18:00 é relatado como "não executado".
   - Os resultados ficam em `results_prs.json` com a marca `2026-11-18 desvio 6`.
