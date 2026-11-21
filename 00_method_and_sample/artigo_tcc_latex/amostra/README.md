# Amostra delimitada do artigo

Inventário montado em 22/10/2026 com os 34 findings consolidados e os casos de PRs do relatório, acrescidos dos quatro registros de publicação de 20/10. Não usa a planilha histórica de erros.

Os estados dos PRs são retratos locais datados de 20–21/10, não uma consulta atual ao GitHub. A API pública não permitiu concluir a atualização online (HTTP 403).

| Medida | n |
|---|---:|
| Findings consolidados | 34 |
| Identificadores de APK no consolidado | 23 |
| PRs elegíveis | 41 |
| PRs com identidade completa de finding | 37 |
| Findings presentes nos dois conjuntos | 13 |
| Findings adicionais vinculados a PRs | 24 |
| União de findings com identidade completa | 58 |
| Identificadores de APK na união | 47 |
| APKs de entrada disponíveis e com manifesto verificado | 14 |
| Identificadores sem binário local verificado | 33 |
| PRs sem vínculo completo com APK/finding | 4 |

A chave é APK, classe, método e especificação: 34 + 37 − 13 = 58. Não se agrupam erros diferentes só porque pertencem ao mesmo projeto. Versões diferentes de APK permanecem distintas.

O estado de incorporação/publicação é separado da decisão e do build. Os dez incorporados incluem BombusMod #316, incorporado manualmente: não são dez merges do GitHub. Gallery #1145 foi excluído por fechamento automático. Os quatro PRs publicados depois do consolidado já estavam entre seus 34 findings.

Os quatro PRs abaixo permanecem no inventário de PRs, fora dos denominadores de findings e APKs, pois os registros consultados não fornecem o vínculo local completo:

- [HTTP Shortcuts #576](https://github.com/Waboodoo/HTTP-Shortcuts/pull/576)
- [rssreader #4](https://github.com/javalc6/rssreader/pull/4)
- [aiHub #279](https://github.com/SilentCoderHere/aihub/pull/279)
- [Session #2241](https://github.com/session-foundation/session-android/pull/2241)

`inventario.json` guarda proveniência, base do cruzamento, feedback, estados registrados e inspeção dos APKs. `findings.csv` contém apenas identidades totalmente vinculadas.

Para reproduzir na raiz: `python3 artigo_tcc_latex/amostra/consolidar.py`. A leitura de pacote/versionCode usa `aapt2` no caminho indicado no script; sem ele, somente a verificação de contêiner é reproduzida. O script lê apenas o pequeno CSV dos 34, seus resultados, metadados de PR e os APKs de aquisição.

Em AMARadio, ShortMessageSecure, whoBIRD, ReadYou e zSMTH, o sufixo `.debug` do arquivo não faz parte do pacote real. O JSON preserva rótulo e identidade do manifesto separadamente. Verificar contêiner, pacote e versão não comprova equivalência binário/fonte nem eliminação do relato original. APKs de entrada e APKs gerados por builds são conjuntos distintos.

O consolidado auditado registra seis builds com APK e uma compilação apenas do módulo, não sete assemblies completos. Seus estados locais precedem os quatro registros posteriores de publicação. O histórico não foi sobrescrito para aparentar uma única execução atual.
