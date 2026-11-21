# Patches validados × PRs publicados (conferido em 2026-11-15)

Os seis casos são achados que viraram PR. Cada PR é o mesmo achado (APK, classe, método e spec em
`artigo_tcc_latex/amostra/inventario.json`). A comparação abaixo usa as linhas alteradas, sem espaços, do patch
validado (`patches/<caso>.diff`) e do diff público do PR (`https://github.com/<repo>/pull/<n>.diff`).

| Caso | PR | Diff validado × diff do PR |
|---|---|---|
| tokn_19 | tokn-authenticator/tokn#138 | idêntico |
| bitbanana_79 | michaelWuensch/BitBanana#184 | idêntico (355 linhas) |
| networksurvey_114 | christianrowlands/android-network-survey#154 | idêntico |
| trafficlight_38 | leekleak/traffic-light#238 | idêntico |
| untis_29198884 | SapuSeven/BetterUntis#515 | mesma chamada (`MessageDigest.getInstance("SHA-256")`); o PR também prefixa o nome do arquivo de cache com `sha256-v1-` |
| passnotes_11700 | aivanovski/keepassvault#347 | outro patch: o validado troca o trust manager que aceita tudo pelo do TrustManagerFactory da plataforma; o PR mantém o trust manager do modo de depuração e filtra os protocolos SSL/TLS 1.0/1.1 (mais um teste) |

Para o artigo: em 4 casos, a validação dinâmica cobre exatamente o diff publicado. No untis, a chamada
verificada é a mesma do PR. O resultado de passnotes vale para o patch da run, não para o PR #347.
