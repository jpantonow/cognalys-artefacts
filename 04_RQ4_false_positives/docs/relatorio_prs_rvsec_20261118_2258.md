# Levantamento de pull requests do RVSec — Cognalys / TCC

Atualizado em **18/11/2026**. Consulta autenticada e paginada à API do GitHub para PRs de **@jpantonow criados em 2026**, com conferência individual de estado, merge, eventos de fechamento, comentários e revisões formais. As 97 consultas individuais foram concluídas sem falhas.

Entram somente os PRs cujos achados vieram do RVSec: o app executado sob o RVSec, com ape, monkey, humanoid, DroidBot ou ARES conduzindo a interface. A origem é a coluna `tool` do achado de cada PR, lida no registro de publicação e no relatório de execução da run. PRs de candidatos manuais, de regras Semgrep e de pesquisa web ficam fora; os que foram incorporados aparecem só como observação.

## Resumo

| Situação atual | PRs do RVSec |
|---|---:|
| Merge pelo GitHub | 20 (obs.: 5 PRs não RVSec incorporados) |
| Incorporado manualmente, sem merge do PR | 1 |
| Fechado pelo mantenedor, sem incorporação identificada | 10 |
| Aberto | 18 |
| **Total** | **49** |

São **21 mudanças incorporadas** de achados do RVSec: 20 merges e 1 incorporação manual. Os 49 PRs são uma contagem de contribuições, não de findings independentes.

Observação: 5 PRs de outras origens foram incorporados e não entram nas contagens: [SilentCoderHere/aihub#279](https://github.com/SilentCoderHere/aihub/pull/279) (Semgrep); [Domi04151309/HomeApp#114](https://github.com/Domi04151309/HomeApp/pull/114) (candidato manual); [Valent-in/Auto-Shine#12](https://github.com/Valent-in/Auto-Shine/pull/12) (candidato manual); [mretallack/SlimBook#3](https://github.com/mretallack/SlimBook/pull/3) (candidato manual); [hanjoongcho/aaf-easydiary#253](https://github.com/hanjoongcho/aaf-easydiary/pull/253) (candidato manual).

PRs fechados pelo próprio autor (tentativas retiradas ou substituídas) e fechados por bot não são decisões dos mantenedores e ficam fora. Em PRs fechados sem merge, o ator foi lido no último evento de fechamento. As datas das tabelas são as datas UTC registradas pelo GitHub.

## Incorporados por classe de impacto

| Classe de impacto (adjudicação) | PRs incorporados |
|---|---:|
| com impacto de segurança | 1 |
| sem impacto de segurança (correção da regra) | 4 |
| impacto não estabelecido | 12 |
| não registrada | 4 |

A classe é o campo `runtime_security_impact` da adjudicação do achado, gravado no registro do PR ou, nos PRs publicados antes desse registro, lido na adjudicação da run que publicou o PR. Sem adjudicação encontrada, ou quando a adjudicação não avaliou o impacto (`unknown`), fica "não registrada".

## Mudanças desde o levantamento anterior (2026-11-18_2000)

- [thatSFguy/meshcore-mobile-app#10](https://github.com/thatSFguy/meshcore-mobile-app/pull/10): aberto → mergeado.

## Mergeados no GitHub (20)

| PR | Mudança | Responsável / data | Observação |
|---|---|---|---|
| [gigabytedevelopers/CometOTP#114](https://github.com/gigabytedevelopers/CometOTP/pull/114) | Create a fresh Cipher for each key operation | @enwokoma · 2026-11-08 | O mantenedor agradeceu o achado e a descrição; como o master só aceita commits assinados, rebaseou e assinou o commit do lado deles, mantendo a autoria. |
| [thatSFguy/meshcore-mobile-app#10](https://github.com/thatSFguy/meshcore-mobile-app/pull/10) | Use a separate key generator for each Keystore tier | @thatSFguy · 2026-11-18 | Ver eventos e conversa do PR. |
| [FliegendeWurst/TriliumDroid#177](https://github.com/FliegendeWurst/TriliumDroid/pull/177) | Remove the unused key manager factory initialization | @FliegendeWurst · 2026-11-06 | O mantenedor agradeceu a limpeza; o código removido vinha de uma versão anterior da lógica. |
| [thuryn/your-local-weather#209](https://github.com/thuryn/your-local-weather/pull/209) | Select key derivation by Android API level | @thuryn · 2026-11-18 | Ver eventos e conversa do PR. |
| [mouse233/localsend-kotlin#58](https://github.com/mouse233/localsend-kotlin/pull/58) | Store TLS keys with a KeyStore entry | @mouse233 · 2026-11-13 | Ver eventos e conversa do PR. |
| [Crustack/NotallyX#1167](https://github.com/Crustack/NotallyX/pull/1167) | Use SHA-256 for export hash verification | @Crustack · 2026-11-07 | O mantenedor aprovou ("thanks for the improvement, LGTM") e fez o merge; o CodeRabbit comentou, sem revisão humana além da aprovação. |
| [jimvixx/ShortMessageSecure#89](https://github.com/jimvixx/ShortMessageSecure/pull/89) | Use SHA-256 for conversation stable IDs | @jimvixx · 2026-11-02 | O mantenedor observou que o SHA-1 só gera IDs estáveis do RecyclerView, sem papel criptográfico, e considerou a troca por SHA-256 uma melhoria razoável; pediu ajustar a mensagem do AssertionError, a revisão fez o ajuste e ele mergeou. |
| [freeotp/freeotp-android#524](https://github.com/freeotp/freeotp-android/pull/524) | Exige chave AES antes de inicializar a cifração | @justin-stephenson · 2026-11-03 | Correção no código de produção (chave AES exigida, conversão PBKDF2 para SecretKeySpec AES) com testes de compatibilidade; o mantenedor testou backup/restore antes do merge. |
| [freeotp/freeotp-android#521](https://github.com/freeotp/freeotp-android/pull/521) | Teste de ida e volta da chave derivada por SecretKeyFactory | @justin-stephenson · 2026-10-27 | Merge de um patch só de teste: o mantenedor considerou o teste válido. O código de produção do finding não mudou, então não conta como correção do uso. |
| [jenspfahl/ANOTHERpass#103](https://github.com/jenspfahl/ANOTHERpass/pull/103) | Usa getKey para recuperar chaves secretas do Android | @jenspfahl · 2026-10-25 | O mantenedor agradeceu, disse não conhecer a prática e que o código atual não expõe vulnerabilidade; aprovou e fez o merge. |
| [leekleak/traffic-light#238](https://github.com/leekleak/traffic-light/pull/238) | Lê chaves secretas do AndroidKeyStore com getKey | @leekleak · 2026-10-21 | Merge confirmado; o mantenedor agradeceu. |
| [tokn-authenticator/tokn#138](https://github.com/tokn-authenticator/tokn/pull/138) | Lê chaves do AndroidKeyStore com getKey | @fthomys · 2026-10-21 | Merge confirmado; o colaborador responsável agradeceu. |
| [ounben/AMARadio#122](https://github.com/ounben/AMARadio/pull/122) | SHA-256 nos nomes de arquivos de cache | @ounben · 2026-10-17 | O mantenedor aceitou a troca do digest usado nos nomes dos arquivos de cache. |
| [jimvixx/ShortMessageSecure#83](https://github.com/jimvixx/ShortMessageSecure/pull/83) | Inicializa explicitamente a chave HmacSHA1 com 160 bits | @jimvixx · 2026-10-19 | A revisão preservou HmacSHA1 e passou a explicitar o tamanho de 160 bits, em vez de trocar unilateralmente o algoritmo. |
| [session-foundation/session-android#2241](https://github.com/session-foundation/session-android/pull/2241) | TLS no cliente HTTP dos nós | @mpretty-cyro · 2026-11-17 | Ver eventos e conversa do PR. |
| [nextcloud/news-android#1716](https://github.com/nextcloud/news-android/pull/1716) | Carregamento do KeyStore em `loadAppKeyStore` | @David-Development · 2026-09-20 | Merge confirmado no GitHub. |
| [bitcraze/crazyflie-android-client#103](https://github.com/bitcraze/crazyflie-android-client/pull/103) | Remove a fábrica de sockets TLS obsoleta usada no download de firmware | @ataffanel · 2026-09-25 | A revisão pediu remover o helper TLS obsoleto; a versão revista foi mergeada. |
| [paroj/DSub2000#160](https://github.com/paroj/DSub2000/pull/160) | Filtra protocolos TLS legados | @paroj · 2026-08-31 | Merge confirmado; sugestões automáticas não são revisão humana de segurança. |
| [Waboodoo/HTTP-Shortcuts#576](https://github.com/Waboodoo/HTTP-Shortcuts/pull/576) | Seleção do protocolo TLS mais restrita | @Waboodoo · 2026-08-11 | Merge confirmado no GitHub. |
| [lneugebauer/nextcloud-cookbook#204](https://github.com/lneugebauer/nextcloud-cookbook/pull/204) | `configureTrustAllCertificates` | @lneugebauer · 2026-08-09 | Merge confirmado no GitHub. |

## Incorporado manualmente (1)

| PR | Mudança | Responsável / data | Observação |
|---|---|---|---|
| [BombusMod/BombusMod#316](https://github.com/BombusMod/BombusMod/pull/316) | Fechamento de stream em `baosFromBase64` | @vitalyster · 2026-09-16 | Incorporação manual confirmada em comentário; a mensagem de commit foi reescrita. O PR está fechado e merged=false na API. |

## Fechados pelos mantenedores sem merge (10)

| PR | Mudança | Responsável / data | Observação |
|---|---|---|---|
| [ItzSkyeYT/InterTune#52](https://github.com/ItzSkyeYT/InterTune/pull/52) | SHA-256 nos hashes das capas do widget | @ItzSkyeYT · 2026-10-28 | O hash só nomeia os arquivos de capa em cache, sem papel de segurança; trocar exigiria migrar arquivos sem ganho. |
| [vitorpamplona/amethyst#4642](https://github.com/vitorpamplona/amethyst/pull/4642) | IVs aleatórios na cifração AES-GCM | @vitorpamplona · 2026-10-25 | O provedor já gera um IV aleatório quando init recebe só a chave; o mantenedor demonstrou isso no JDK 21. O patch também alterava a implementação desktop. |
| [christianrowlands/android-network-survey#154](https://github.com/christianrowlands/android-network-survey/pull/154) | Usa getKey ao recuperar a chave de criptografia | @christianrowlands · 2026-10-21 | O mantenedor considera getEntry e getKey equivalentes para essa chave; a mudança só satisfaz a regra CrySL e deixaria outra cópia do código diferente. |
| [woheller69/whoBIRD#144](https://github.com/woheller69/whoBIRD/pull/144) | SHA-256 na verificação de modelos baixados | @woheller69 · 2026-10-19 | O mantenedor considera MD5 suficiente para a verificação dos modelos. |
| [idunnololz/summit#54](https://github.com/idunnololz/summit/pull/54) | SHA-256 nas chaves do cache em disco | @idunnololz · 2026-10-17 | O projeto não está aceitando PRs; o fechamento não é um parecer técnico sobre o patch. |
| [javalc6/rssreader#4](https://github.com/javalc6/rssreader/pull/4) | MD5 → SHA-256 no checksum de backup | @javalc6 · 2026-10-19 | O checksum detecta corrupção acidental; o mantenedor não considera a troca por SHA-256 uma melhoria de segurança nesse contexto. |
| [Wilm0r/giggity#543](https://github.com/Wilm0r/giggity/pull/543) | Remove o MD5 de `hashCode` | @Wilm0r · 2026-09-20 | O mantenedor já havia feito mudança equivalente; considerou a limpeza útil. |
| [VREMSoftwareDevelopment/WiFiAnalyzer#618](https://github.com/VREMSoftwareDevelopment/WiFiAnalyzer/pull/618) | MD5 → SHA-256 | @VREMSoftwareDevelopment · 2026-08-27 | O mantenedor considera o MD5 um checksum sem papel de segurança neste uso. |
| [QuantumBadger/RedReader#1373](https://github.com/QuantumBadger/RedReader/pull/1373) | SHA-1 → SHA-256 em `sha1` | @QuantumBadger · 2026-09-13 | O mantenedor questiona a premissa de segurança do uso do SHA-1 e a mudança de algoritmo mantendo o nome sha1. |
| [SapuSeven/BetterUntis#515](https://github.com/SapuSeven/BetterUntis/pull/515) | MD5 → SHA-256 em nomes de cache | @SapuSeven · 2026-08-11 | MD5 gera nomes de cache; o mantenedor também rejeita contribuições geradas por IA. |

## Abertos (18)

| PR | Mudança | Aberto em | Revisão / observação |
|---|---|---|---|
| [wolpi/prim-ftpd#453](https://github.com/wolpi/prim-ftpd/pull/453) | Retire RC4 cipher support in SSH | 2026-11-03 | Há interação humana registrada; ver conversa e revisões do PR. |
| [PrivacyDragon/Bookwyrm_Android#29](https://github.com/PrivacyDragon/Bookwyrm_Android/pull/29) | Use SecureRandom for stored AES-GCM IVs | 2026-11-01 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [Vauth/salty-app#6](https://github.com/Vauth/salty-app/pull/6) | Generate PBKDF2 salts for new encrypted values | 2026-10-29 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [0xZhangKe/Fread#168](https://github.com/0xZhangKe/Fread/pull/168) | Use TLS for the HTTP client's SSL context | 2026-10-29 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [orgzly-revived/orgzly-android-revived#1121](https://github.com/orgzly-revived/orgzly-android-revived/pull/1121) | Replace SHA-1 note hashes with SHA-256 | 2026-10-29 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [yogeshpaliyal/KeyPass#1252](https://github.com/yogeshpaliyal/KeyPass/pull/1252) | Usa o AndroidKeyStore explicitamente na geração da chave AES | 2026-10-25 | Só comentários automáticos (CodeRabbit e boas-vindas); sem revisão humana registrada. |
| [patzly/grocy-android#1013](https://github.com/patzly/grocy-android/pull/1013) | Inicializa keystores ausentes antes de gravar entradas de chave | 2026-10-24 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [patzly/grocy-android#1012](https://github.com/patzly/grocy-android/pull/1012) | Inicializa o SSLContext TLS uma única vez | 2026-10-24 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [schorschii/Droid_SCEP#2](https://github.com/schorschii/Droid_SCEP/pull/2) | Valida tamanhos de chave RSA antes do enrollment | 2026-10-22 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [michaelWuensch/BitBanana#184](https://github.com/michaelWuensch/BitBanana/pull/184) | Migra verificadores do bloqueio do app para PBKDF2-HMAC-SHA256 | 2026-10-20 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [ReadYouApp/ReadYou#1334](https://github.com/ReadYouApp/ReadYou/pull/1334) | Validação de certificados nos clientes HTTP em cache | 2026-10-18 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [zfdang/zSMTH-Android#66](https://github.com/zfdang/zSMTH-Android/pull/66) | TLS e validação de certificados HTTPS | 2026-10-18 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [Crazy-Marvin/MetadataRemover#264](https://github.com/Crazy-Marvin/MetadataRemover/pull/264) | `SecureRandom` sem semente fixa | 2026-10-12 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [nelenkov/wwwjdic#115](https://github.com/nelenkov/wwwjdic/pull/115) | Fechamento de stream | 2026-09-16 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [aivanovski/keepassvault#347](https://github.com/aivanovski/keepassvault/pull/347) | Protocolo TLS em `createHttpClient` | 2026-09-15 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [spacecowboy/Feeder#1210](https://github.com/spacecowboy/Feeder/pull/1210) | Restringe os protocolos TLS em `trustAllCerts` | 2026-09-02 | O mantenedor se opõe ao impacto na compatibilidade com sites. Um revisor externo apoia a proposta; isso não equivale à aprovação do mantenedor. |
| [etesync/android#301](https://github.com/etesync/android/pull/301) | Filtra protocolos TLS legados | 2026-08-30 | Sem comentário humano ou revisão formal de terceiros registrados. |
| [binwiederhier/ntfy-android#184](https://github.com/binwiederhier/ntfy-android/pull/184) | Sincronização de mapa em `scrub` | 2026-08-11 | Sem comentário humano ou revisão formal de terceiros registrados. |

## Leitura dos resultados

- Aceitação pelo mantenedor e merge não comprovam, sozinhos, correção de segurança ou eliminação do relato original do detector.
- Fechamento humano reúne motivos diferentes: contexto sem risco demonstrado, comportamento intencional, mudança já feita upstream, compatibilidade e política de contribuição.
- Os casos de Android Network Survey #154 e Tokn/Traffic Light/ANOTHERpass mostram respostas distintas a mudanças relacionadas ao AndroidKeyStore; não devem ser tratados como uma regra geral de aceitação ou rejeição.
- FreeOTP #521 foi mergeado, mas só acrescenta um teste; não deve ser contado como correção do uso reportado.
- PRs abertos têm resultado de adoção pendente. Revisão externa e comentário automático ficam separados de aprovação do mantenedor.

## Fontes e reprodução

Cada link das tabelas aponta para o PR consultado. O estado `merged` foi conferido no recurso do PR; o ator de fechamento veio dos eventos da issue. BombusMod foi separado com base na confirmação explícita de incorporação manual do mantenedor.

- [Busca paginada e contagem de origem](../outputs/prs_reports/20261118_2258/search.json)
- [Estados, eventos, comentários e revisões coletados](../outputs/prs_reports/20261118_2258/collection.json)
- Registros individuais: `outputs/prs_reports/20261118_2258/prs/`.
- Script: `scripts/levantar_prs.py --date 20261118_2258`, etapas `search`, `collect` e `report`. A coleta usa somente leituras do GitHub e não publica comentários nem altera PRs.
