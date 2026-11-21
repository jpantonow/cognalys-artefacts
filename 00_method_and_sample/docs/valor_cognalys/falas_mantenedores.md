# Falas dos mantenedores sobre os PRs do Cognalys

Coleta para o artigo (artigo_tcc_latex/main.tex). Citações em inglês, copiadas literalmente; notas em português.

Fontes:
- `artigo_tcc_latex/amostra/inventario.json` (chave `prs`, 49 registros; `recorded_status`, `outcome_record`).
- `data/pr_outcomes/*.json` (arquivos indicados em `outcome_source`; comentários registrados em 2026-11-12/14).
- `outputs/prs_reports/20261113/summary_rvsec.json` (contagens, `report_date` 2026-11-13) e `pr_impact.json`.
- `outputs/prs_reports/20261113/prs/<repo>__<n>.json` (cópia da API do GitHub consultada em 2026-11-13 17:22 UTC; usada para conferir autor, associação ao repositório, datas e para recuperar textos que o inventário trunca em 2000 caracteres).

Critério: só falas de quem não é o autor do PR (jpantonow); bots ignorados (coderabbitai, Copilot, github-actions). A associação ao repositório (OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, NONE) é a que o GitHub informa; não é sinônimo de "mantenedor" e vai indicada quando não é OWNER/MEMBER. Nada foi consultado no GitHub para esta coleta.

## 1. Contagens por status

Data do retrato dos status: **2026-11-13** (`pr_status_source_dates` do inventário e `report_date` do summary_rvsec). Atenção: o campo `snapshot_date` do inventário diz 2026-10-22; ele não se refere ao status dos PRs.

| Status registrado (`recorded_status`) | PRs |
|---|---|
| accepted (incorporado) | 18 |
| open | 21 |
| closed_by_maintainer | 10 |
| **Total** | **49** |

No summary_rvsec os 18 aceitos são 17 `merged` + 1 `manual` (BombusMod#316: o GitHub registra `merged: false`; o mantenedor fechou o PR dizendo que incorporou o commit com outra mensagem).

Classe de impacto dos 18 aceitos (`incorporated_by_impact` do summary_rvsec, conferida PR a PR): security_impact 0, no_security_impact 4, impact_not_established 10, not_recorded 4. Nenhum PR aceito tem impacto de segurança registrado.

## 2. PRs aceitos (18)

Legenda da coluna "tipo de fala":
- **E** = elogia a correção ou o texto de forma específica (diz o que achou bom, que testou, que aprendeu algo);
- **G** = aprovação genérica ("thanks", "LGTM", "merged");
- **A** = pediu alterações antes de incorporar;
- **C** = contém crítica ao texto/commit ou ressalva sobre impacto;
- **—** = nenhuma fala humana registrada.

| PR | Spec | Título (registro; título final no GitHub se mudou) | Impacto | Incorporado em | Tipo de fala |
|---|---|---|---|---|---|
| gigabytedevelopers/CometOTP#114 | CipherSpec | Create a fresh Cipher for each key operation | impact_not_established | 2026-11-08 | E |
| freeotp/freeotp-android#524 | CipherSpec | Require AES keys before initializing encryption | impact_not_established | 2026-11-03 | E |
| freeotp/freeotp-android#521 | CipherSpec | Add SecretKeyFactory key round-trip coverage | not_recorded | 2026-10-27 | E |
| jimvixx/ShortMessageSecure#83 | KeyGeneratorSpec | Use HmacSHA256 for generated MAC secrets; final: "Explicitly initialize generated HmacSHA1 keys to 160 bits" | impact_not_established | 2026-10-19 | A, E, C |
| jimvixx/ShortMessageSecure#89 | MessageDigestSpec | Use SHA-256 for conversation stable IDs | impact_not_established | 2026-11-02 | A (pequeno), E, C |
| jenspfahl/ANOTHERpass#103 | KeyStoreSpec | Use KeyStore.getKey to read Android secret keys | no_security_impact | 2026-10-25 | E, C |
| ounben/AMARadio#122 | MessageDigestSpec | Use SHA-256 for cache file names | impact_not_established | 2026-10-17 | E |
| mouse233/localsend-kotlin#58 | KeyStoreSpec | Store TLS keys with a KeyStore entry | not_recorded | 2026-11-13 | E |
| bitcraze/crazyflie-android-client#103 | SSLContextSpec | Fix \<init\>; final: "Remove obsolete TLS socket factory from firmware downloads" | impact_not_established | 2026-09-25 | A, C, G |
| BombusMod/BombusMod#316 | Closeable_MeaninglessClose | Fix baosFromBase64 | impact_not_established | 2026-09-16 (manual) | C |
| lneugebauer/nextcloud-cookbook#204 | SSLContextSpec (*) | Fix configureTrustAllCertificates | not_recorded | 2026-08-09 | A |
| Crustack/NotallyX#1167 | MessageDigestSpec | Use SHA-256 for export hash verification | no_security_impact | 2026-11-07 | G |
| FliegendeWurst/TriliumDroid#177 | KeyManagerFactorySpec | Remove the unused key manager factory initialization | impact_not_established | 2026-11-06 | G |
| Waboodoo/HTTP-Shortcuts#576 | SSLContext (*) | Harden TLS protocol selection | not_recorded | 2026-08-11 | G |
| leekleak/traffic-light#238 | KeyStoreSpec | Use KeyStore.getKey to read secret keys | no_security_impact | 2026-10-21 | G |
| tokn-authenticator/tokn#138 | KeyStoreSpec | Read Android keystore keys with getKey | no_security_impact | 2026-10-21 | G |
| nextcloud/news-android#1716 | KeyStoreSpec | Fix loadAppKeyStore | impact_not_established | 2026-09-20 | — |
| paroj/DSub2000#160 | SSLContextSpec | Fix \<init\> | impact_not_established | 2026-08-31 | — (só revisão do Copilot) |

(*) Esses PRs não têm `outcome_record` no inventário (sem spec nem impacto registrados). A spec vem do corpo do PR na cópia do GitHub: nextcloud-cookbook#204 traz "Specification | `SSLContextSpec`"; HTTP-Shortcuts#576 só menciona `SSLContext.getInstance("TLS", "Conscrypt")`, então a spec é inferida.

Specs dos 18 aceitos: KeyStoreSpec 5, SSLContext(Spec) 4, CipherSpec 3, MessageDigestSpec 3, KeyGeneratorSpec 1, KeyManagerFactorySpec 1, Closeable_MeaninglessClose 1.

### Falas, PR a PR

**gigabytedevelopers/CometOTP#114** (enwokoma, MEMBER; aprovado também por princesseke, CONTRIBUTOR, sem texto) — E

> Thank you for finding this and for the clear write-up, @jpantonow. The fix is merged.
>
> Our master branch only accepts signed commits, so I rebased your commit onto master and signed it on our side. You're still the author of the commit that landed (e5e8981). Good luck with the RVSec research!

**freeotp/freeotp-android#524** (justin-stephenson, CONTRIBUTOR; fez o merge) — E

> Thank you for the PR, testing backup/restore is successful.

**freeotp/freeotp-android#521** (justin-stephenson, CONTRIBUTOR; aprovou e fez o merge) — E. A segunda frase é sugestão para o projeto, não pedido de alteração no PR.

> Thank you for the PR, test looks valid.
>
> By the way I consider renaming the EncryptedKey class to EncryptedToken to avoid confusion.

**jimvixx/ShortMessageSecure#83** (jimvixx, OWNER) — A, E, C. A proposta original (trocar o gerador para HmacSHA256) foi contestada; o PR incorporado segue a alternativa sugerida pelo próprio mantenedor (manter HmacSHA1 e inicializar 160 bits explicitamente).

Antes da alteração:

> Thank you for the contribution and for testing SMSecure with RVSec!
>
> I reviewed the proposed change. According to the Android documentation, `HmacSHA1` is a supported `KeyGenerator` algorithm, so the reported violation may be related to the CrySL rule rather than an actual Android API issue.
>
> Also, the generated key material is subsequently used with `HmacSHA1` throughout the existing MasterSecret implementation. Switching only the key generator to `HmacSHA256` does not change the MAC algorithm or provide a direct security improvement.
>
> Could you share the exact CrySL rule violation and explain why `HmacSHA1` is considered invalid in this context?
>
> I'd prefer to preserve the existing cryptographic behavior unless there is a demonstrated issue. Adding an explicit 160-bit initialization to the current `HmacSHA1` generator might be a simpler alternative.
>
> Thanks again for investigating this!

Depois da alteração:

> Thank you for the clarification and for updating the PR!
>
> The revised approach looks good to me. Explicitly initializing the key size to 160 bits preserves the existing behavior and compatibility without introducing unnecessary cryptographic changes.
>
> I appreciate your contribution and the effort you put into analyzing SMSecure with RVSec.

> Thanks again for your contribution!

**jimvixx/ShortMessageSecure#89** (jimvixx, OWNER) — A (só a mensagem de erro), E, C (diz que não há efeito na segurança criptográfica).

> Thanks for the contribution and for checking SMSecure with RVSec!
>
> The change looks good. Although SHA-1 is only used here to generate stable RecyclerView item IDs and does not affect cryptographic security, switching to SHA-256 is a reasonable improvement.
>
> One small request before merging: could you also update the `AssertionError` message from `"SHA-1 missing"` to `"SHA-256 missing"` to match the new algorithm?
>
> Thanks again for your work!

> Thanks for the quick update! Everything looks good now. I'll go ahead and merge the PR.
>
> Thanks again for your contribution to SMSecure!

**jenspfahl/ANOTHERpass#103** (jenspfahl, OWNER; aprovou) — E, C (ressalva de que não havia vulnerabilidade).

> Thanks for flagging this! I was not aware of this best practise, although the current code should not expose any vulnerability.

**ounben/AMARadio#122** (ounben, OWNER) — E. Revisão de aprovação e comentário depois do merge:

> MD5 was from old RadioDroid and wasn't changed to SHA 256.

> Thank you for your help. Ignored MD5 from RadioDroid in July and now it's finally fixed. And have fun with your study. Ben

**mouse233/localsend-kotlin#58** (mouse233, OWNER) — E. O texto não deixa claro se "Regression tests added" se refere aos testes do PR ou a testes que o mantenedor adicionou; não interpretar no artigo. O `outcome_record` do inventário ainda tem `state: open` e nenhuma fala; a fala e o merge (2026-11-13 03:27 UTC) estão em `data/pr_outcomes/mouse233__localsend-kotlin.json` e na cópia do GitHub.

> LGTM, thanks! Regression tests added and CI passed. Squashing and merging.

**bitcraze/crazyflie-android-client#103** (ataffanel, MEMBER) — A, C, G. Pediu título/descrição mais claros e outra abordagem (remover o `TLSSocketFactory` em vez de corrigi-lo); o PR foi refeito e incorporado.

> Hi, thanks for the contribution!
>
> Could you please clean up the PR title and description to make it clearer what is being fixed and why? “Fix `<init>`” does not really explain the change, and it is difficult to understand the practical impact from the description. A short explanation of the problem and how it affects firmware downloads would help.
>
> Looking at the code, I would prefer removing `TLSSocketFactory` and its usage rather than patching it. This helper was added to support TLS 1.2 on Android versions older than 5.0, but we now require Android 8.0. Its original purpose is no longer relevant, so relying on the platform HTTPS defaults seems simpler.
>
> This would not explicitly enforce TLS 1.2 as a minimum on every supported device. If there is a concrete reason we need that additional restriction, please explain it in the description. Otherwise, could you update the PR to remove the helper and verify that fetching the release list and downloading firmware still work?

> Thanks! Looks good, merged.

**BombusMod/BombusMod#316** (vitalyster, CONTRIBUTOR; fechou o PR) — C. Incorporação manual (GitHub: `merged: false`).

> Thanks for the contribution, @jpantonow — merged (with a reworded commit message/description, since `Fix baosFromBase64` alone didn't explain the change). For future PRs, please write a proper commit message describing what changed and why, rather than a generic one-liner.

**lneugebauer/nextcloud-cookbook#204** (lneugebauer, OWNER) — A. Revisão "changes requested"; depois incorporado sem outro comentário.

> Please also fix ktlint issues.

**Crustack/NotallyX#1167** (Crustack, OWNER) — G.

> Hi there, thanks for the improvement, LGTM 🙂

**FliegendeWurst/TriliumDroid#177** (FliegendeWurst, OWNER) — G.

> Thanks for the clean-up. I think it was used in a previous iteration of the logic here ...

**Waboodoo/HTTP-Shortcuts#576** (Waboodoo, OWNER; revisão de aprovação) — G.

> Looks good to me, thanks for the contribution

**leekleak/traffic-light#238** (leekleak, OWNER) — G.

> Thanks

**tokn-authenticator/tokn#138** (fthomys, COLLABORATOR; fez o merge) — G.

> thanks

**nextcloud/news-android#1716** — sem fala humana; merge por David-Development em 2026-09-20.

**paroj/DSub2000#160** — sem fala humana; só a revisão automática do Copilot (ignorada); merge por paroj em 2026-08-31.

Resumo dos 18: 8 com fala específica (E), 5 só com aprovação genérica, 1 com pedido de alteração seguido de aprovação genérica (crazyflie#103), 2 sem fala humana, 1 só com crítica ao commit (BombusMod) e 1 só com pedido de ajuste de lint (nextcloud-cookbook). Pediram alterações antes de incorporar: 4 (crazyflie#103, ShortMessageSecure#83, ShortMessageSecure#89, nextcloud-cookbook#204). Três falas de aceite dizem explicitamente que a mudança não tinha efeito de segurança ou não corrigia vulnerabilidade (ShortMessageSecure#83 antes da revisão, ShortMessageSecure#89, ANOTHERpass#103).

## 3. PRs fechados pelo mantenedor (10)

Specs: MessageDigestSpec 8 (RedReader#1373 e BetterUntis#515 sem `outcome_record`; spec tirada do corpo do PR), KeyStoreSpec 1, GCMParameterSpecSpec 1. Todos os fechamentos vieram com um comentário do dono do repositório.

### 3.1 Hash não usado para segurança: troca sem ganho (6, todos MessageDigestSpec)

**VREMSoftwareDevelopment/WiFiAnalyzer#618** — "Fix calculateChildType" (impacto not_recorded), fechado em 2026-08-27.

> This is a false positive. MD5 is used here as a checksum, not as a security control — that's exactly what it's still appropriate for. There's no password, token, or integrity check involved.
>
> Closing this one, but thanks for the effort.

**javalc6/rssreader#4** — "Write file checksums with SHA-256" (impact_not_established), fechado em 2026-10-19. O texto termina no link, tanto no inventário quanto na cópia do GitHub (742 caracteres; não truncado).

> Thanks for taking the time to run the app under RVSec, but it will not be merged. In _FileManager_, MD5 is used only as a checksum to detect accidental corruption of a backup file. It isn’t used for authentication, signatures, or password hashing, so MD5’s known weaknesses (collision attacks) don’t apply here.
>
> Since there’s no secret key, switching to SHA-256 wouldn’t stop anyone from modifying a file and recomputing a valid checksum, so it wouldn’t improve security.
>
> The _MessageDigestSpec_ warning is a generic rule that flags MD5 regardless of how it’s used. Note that _MessageDigest.getInstance("MD5")_ isn’t rejected by the Java/Android API [MessageDigest](https://developer.android.com/reference/java/security/MessageDigest)

**ItzSkyeYT/InterTune#52** — "Migrate widget artwork hashes to SHA-256" (impact_not_established), fechado em 2026-10-28.

> Thanks for the PR! This hash only names the widget’s cached cover files, so it does no security work, and switching it means migrating files for no gain. I’ll leave it as it is and close this.

**woheller69/whoBIRD#144** — "Verify downloaded models with SHA-256" (impacto registrado: **security_impact**), fechado em 2026-10-19. Único PR fechado com impacto de segurança registrado; o mantenedor discordou sem detalhar.

> MD5 is sufficient here

**QuantumBadger/RedReader#1373** — "Fix sha1", fechado em 2026-09-13. Discordância também técnica: a função mudou de algoritmo sem mudar de nome.

> Thanks for the contribution, however there are multiple issues with this PR:
>
> * The premise is wrong, as `sha1` is not actually used for cache filenames, and isn't used in any context where collisions are relevant (or where remote input is used)
> * The fix makes the existing `sha1` function return a different kind of hash, without renaming the function, which is actively misleading.
>
> Since there is no security or other issue here I'll close the PR.

**SapuSeven/BetterUntis#515** — "Fix getCacheFile", fechado em 2026-08-11. Mistura sem-ganho e política de IA (ver 3.4). Falas em ordem cronológica; lm41 é CONTRIBUTOR, não dono.

SapuSeven (OWNER):

> What is the reason for replacing MD5 here?

lm41 (CONTRIBUTOR), duas falas:

> Probably because MD5 is cryptographically broken and insecure and AI cannot detect that it is used as a non-security related cache hash generation and thus perfectly fine.

> While MD5 may not be supported on every jvm the android jvm is explicitly supporting MD5: https://developer.android.com/reference/java/security/MessageDigest

SapuSeven (OWNER), ao fechar:

> Ok this is not leading anywhere.
>
> @jpantonow I already stated that I will not be accepting AI generated PRs. I don't know if you used AI for this analysis because you didn't even mention the name of this tool, but there are strong indications of it.
>
> As @lm41 mentioned, there is no need for a cryptographically secure hashing algorithm since it's only used to generate random cache file names.
> MD5 works and is the simplest way to accomplish that.
>
> Since there doesn't seem to be a reason for this change other than a finding from some unknown analysis tool and there are no security or stability implications, I'll be closing this PR.

### 3.2 A mudança só satisfaz a regra, sem efeito em execução (1)

**christianrowlands/android-network-survey#154** — "Use KeyStore.getKey when reading the encryption key" (KeyStoreSpec, no_security_impact), fechado em 2026-10-21. Aponta também uma segunda cópia do código que o patch deixou de fora.

> Thanks for the PR. For an AndroidKeyStore AES key, getEntry and getKey return the same key, so this doesn't change how the app behaves. It only satisfies the CrySL rule. There's also a second copy of this code in NsAnalyticsCryptoManager that would be left different. I'm going to close this one rather than make a change that only satisfies the rule, but I appreciate you running the app through RVSec.

### 3.3 Falso positivo da análise e patch no código errado (1)

**vitorpamplona/amethyst#4642** — "Generate random IVs for AES-GCM encryption" (GCMParameterSpecSpec, impact_not_established), fechado em 2026-10-25. No inventário e em `data/pr_outcomes/vitorpamplona__amethyst.json` o texto está **truncado em 2000 caracteres**; o texto abaixo vem da cópia do GitHub de 2026-11-13 (completo). Trechos omitidos marcados com [...].

> Thanks for looking into this, and for saying up front how you found it. I'm going to close it, because the current code already uses a fresh random IV for every encryption. Here's why:
>
> [...]
>
> So every call gets a new random IV, and the provider refuses to reuse one even when asked to. RVSec probably flagged this because the IV is created inside the provider, where an API-level runtime checker can't trace it back to a random source. It's a false positive, not a weakness.
>
> **The analysis and the change cover different code.** The patch changes `SecretEncryption.jvm.kt`, which is the **desktop** (`jvmMain`) actual. An Android run doesn't execute that file, and `:amethyst:assembleFdroidDebug` doesn't compile it, so neither the finding nor the build check reached the changed code.
>
> [...]
>
> Please don't let this put you off. Crypto misuse in Android apps is a worthwhile thing to research, and if RVSec flags something else in Amethyst, I'm happy to look at it. For future reports, make sure to make a reproducing test against the actual run (here, something like a duplicate-IV assertion) helps a lot in telling a real finding from a tracing gap in the tool. Keeping tooling in hypotheticals, both when evaluating if bugs actually exist and checking if the proposed fix actually reaches the user interface, will only waste people's time.

As partes omitidas explicam o contrato JCE (com um teste de 100 000 IVs no JDK 21), o actual Android com AndroidKeyStore e a remoção do cache de `Cipher` por thread; ver a URL.

### 3.4 Política do repositório (2)

**idunnololz/summit#54** — "Use SHA-256 for disk cache keys" (MessageDigestSpec, no_security_impact), fechado em 2026-10-17.

> Sorry I am not currently accepting PR requests.

**SapuSeven/BetterUntis#515** — além do argumento técnico (3.1), o fechamento cita a política contra PRs gerados por IA (trecho já citado acima: "I already stated that I will not be accepting AI generated PRs.").

### 3.5 O mantenedor já tinha feito a mesma mudança (1)

**Wilm0r/giggity#543** — "Fix hashCode" (MessageDigestSpec, impact_not_established), fechado em 2026-09-20. Não é rejeição da mudança: ela foi feita pelo próprio mantenedor.

> Oh, just made this change myself independently.
>
> The hash used really doesn't matter but it's a nice cleanup anyway. Wish I remember why I even implemented that original contraption.

### Abertos com manifestação (contexto, fora das contagens acima)

Dos 21 abertos, só dois têm falas de terceiros que não são bots:

- **session-foundation/session-android#2241** (SSLContextSpec, security_impact; mpretty-cyro, COLLABORATOR): mesmo argumento de 3.2, "So this change doesn't alter runtime behaviour; it only satisfies the CrySL `SSLContextSpec` rule. We'll hold off merging for now since it doesn't change anything functionally."
- **spacecowboy/Feeder#1210** (SSLContextSpec; spacecowboy, OWNER): "now please use your human brain and summarize what your ai-pal did please. I'm not reading through that wall of ai text" e, depois, "This PR would mean some sites would stop working. And that is not acceptable." Há também uma revisão positiva longa de ibrahim-iqbal (associação NONE, não é mantenedor; truncada em 2000 caracteres no inventário, completa na cópia do GitHub) que termina em "LGTM from a read-through." Não usar como fala de mantenedor.

## 4. Citações mais fortes para o artigo

Todas vêm de PRs aceitos cuja classe de impacto é impact_not_established ou no_security_impact: **nenhum PR aceito tem impacto de segurança registrado**, e as correções elogiadas são de robustez/boas práticas (reuso de `Cipher`, tamanho de chave, `KeyStore.getKey`, algoritmo de hash), não vulnerabilidades exploráveis. Para não induzir a leitura, convém citar ao lado uma fala de fechamento (por exemplo javalc6/rssreader#4 ou vitorpamplona/amethyst#4642, seção 3).

1. "Thank you for finding this and for the clear write-up, @jpantonow. The fix is merged."
   https://github.com/gigabytedevelopers/CometOTP/pull/114#issuecomment-6722102701
   Nota: único elogio explícito ao texto do PR e à descoberta; CipherSpec, impact_not_established.

2. "Thank you for the PR, testing backup/restore is successful."
   https://github.com/freeotp/freeotp-android/pull/524#issuecomment-6607798498
   Nota: o mantenedor testou a funcionalidade afetada (backup/restore) antes do merge; mostra que a correção não quebrou o app, segundo ele.

3. "The revised approach looks good to me. Explicitly initializing the key size to 160 bits preserves the existing behavior and compatibility without introducing unnecessary cryptographic changes."
   https://github.com/jimvixx/ShortMessageSecure/pull/83#issuecomment-6263477141
   Nota: representa o ciclo contestação, revisão, aceite; deixar claro que a versão aceita é a alternativa sugerida pelo mantenedor, depois de ele recusar a proposta original (HmacSHA256).

4. "Thanks for flagging this! I was not aware of this best practise, although the current code should not expose any vulnerability."
   https://github.com/jenspfahl/ANOTHERpass/pull/103#issuecomment-6402053235
   Nota: valor como boa prática que o mantenedor desconhecia, com a ressalva dele de que não havia vulnerabilidade; representa os PRs KeyStore `getKey` (no_security_impact), dos quais um equivalente foi fechado (android-network-survey#154).

5. "Thank you for your help. Ignored MD5 from RadioDroid in July and now it's finally fixed."
   https://github.com/ounben/AMARadio/pull/122#issuecomment-6235134822
   Nota: troca de hash fraco herdado que o mantenedor já sabia e adiara; contrapeso necessário: 8 dos 10 PRs fechados também são MessageDigestSpec, a maioria por não haver ganho.

## Notas sobre os dados

- Datas: status de 2026-11-13; falas do inventário registradas em 2026-11-12; cópia do GitHub de 2026-11-13 17:22 UTC. Falas posteriores não estão aqui.
- Textos truncados em 2000 caracteres no inventário/pr_outcomes: vitorpamplona/amethyst#4642 (vitorpamplona) e spacecowboy/Feeder#1210 (ibrahim-iqbal). Ambos foram lidos completos na cópia do GitHub.
- Títulos alterados depois do registro: crazyflie-android-client#103 e ShortMessageSecure#83 (o inventário guarda o título original).
- Quatro PRs sem `outcome_record` no inventário: Waboodoo/HTTP-Shortcuts#576, lneugebauer/nextcloud-cookbook#204 (aceitos), QuantumBadger/RedReader#1373, SapuSeven/BetterUntis#515 (fechados). Status e falas vêm do summary_rvsec e da cópia do GitHub; impacto não registrado.
- Os blocos citados preservam o texto original; as quebras de linha `\r\n` viraram parágrafos. Comentários do autor (jpantonow) não foram citados.
