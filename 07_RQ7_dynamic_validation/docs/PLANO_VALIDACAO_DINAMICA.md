# Plano: validação dinâmica dos patches e estabilidade das reexecuções

Criado em 2026-10-23. Para executar daqui a algumas semanas. Nada aqui foi iniciado.

São dois experimentos que respondem a lacunas declaradas no artigo (`artigo_tcc_latex/main.tex`):

1. **Reexecução do RVSec sobre o APK corrigido.** Hoje o artigo afirma (proveniência) que nada
   estabelece "a successful rerun of RVSec". Objetivo: mostrar se a violação some quando o código
   corrigido é executado.
2. **Estabilidade das reexecuções com pipeline congelado.** Threats/Internal validity diz que
   "reruns tended to reach the same decision" sem número. Objetivo: trocar por "in X of Y reruns".

---

## 1. RVSec sobre o APK corrigido

### Casos (consolidado `outputs/exp_final_20261019/consolidated/findings.json`, build com APK)

| APK | Classe / método | Spec | Run do patch |
|---|---|---|---|
| bitbanana_79 | UtilFunctions.encodePbkdf2 | PBEKeySpecSpec | subset_bitbanana_scope6 |
| networksurvey_114 | CryptoManager.getOrCreateKey | KeyStoreSpec | run2 |
| passnotes_11700 | HttpClientFactory.createHttpClient | SSLContextSpec | run2 |
| trafficlight_38 | CryptoManager.getSecretKey | KeyStoreSpec | run2 |
| untis_29198884 | DiskCache.getCacheFile | MessageDigestSpec | run2 |
| tokn_19 | KeystoreManager.getOrCreateKey | KeyStoreSpec | run2 |

Feeder (`trustAllCerts`, SSLContextSpec) compilou só o módulo (`success_app_module_only`), sem APK;
fica de fora salvo se o build completo for consertado antes.

### Procedimento por caso

1. **Base (controle):** compilar o APK da *mesma revisão sem o patch*, com a mesma toolchain do
   build corrigido. Não usar o APK do F-Droid como controle: build e assinatura diferentes
   confundiriam a comparação.
2. **Corrigido:** usar o APK gerado no run do patch (ou recompilar a partir do diff salvo).
3. Rodar `cognalys rvsec-run` nos dois, com a configuração original:
   `--tools monkey:fast,ape:sata --timeout 1200` (mesmo emulador `RVSec`, mesmo conjunto jca_android).
   Repetir **3 vezes** cada um, porque a exploração é estocástica.
4. Coletar de cada execução: violações (classe, método, spec) e cobertura do método (`RVSEC-COV`).

### Classificação

| Base | Corrigido | Resultado |
|---|---|---|
| violação reproduzida | método coberto, sem violação | **corrigido** |
| violação reproduzida | método coberto, com violação | **não corrigido** |
| violação reproduzida | método não coberto | **inconclusivo (cobertura)** |
| violação não reproduzida | qualquer | **base não reproduz**, fora da contagem |

Só conta "corrigido" com o método efetivamente executado no APK corrigido.

### Custo e tempo

- Sem custo de LLM.
- ~40 min por execução × 2 APKs × 3 repetições × 6 casos ≈ 24 h de emulador. Rodar em fila
  noturna, como as filas `/tmp/rvsec_queue*.sh` (lembrar que `/tmp` do WSL some ao reiniciar;
  guardar o script em `outputs/`).
- Verificar espaço em disco antes (C: já chegou a 0 GB).

### Riscos conhecidos

- O código corrigido fica atrás de login/configuração (gargalo de exploração já observado).
  Para KeyStore e PBKDF2 pode ser preciso um roteiro manual mínimo (abrir a tela de bloqueio,
  configurar servidor WebDAV no PassNotes). Registrar quando houver intervenção manual.
- BitBanana muda o formato do verificador guardado: instalar limpo, sem dados antigos.

### Como entra no artigo

Nova linha na RQ2 (ou parágrafo curto): "Under RVSec, the violation disappeared in A of the B
patched APKs whose method was executed; C were inconclusive because exploration did not reach
the method." Atualizar também a frase da proveniência sobre "a successful rerun of RVSec".

---

## 2. Estabilidade das reexecuções (pipeline congelado)

### Desenho

- Fixar um commit do Cognalys e o mesmo `run.toml` (modelo, spec-model, reasoning effort).
- 8 achados do consolidado, misturando desfechos: 3 PR pronto, 3 issue, 2 falso positivo /
  evidência insuficiente.
- 3 execuções independentes por achado **com o cache de LLM desligado** (senão a resposta
  repete por construção).

### Medidas

- Decisão final igual nas 3 execuções? (contagem X de Y)
- Para PR pronto: o patch toca os mesmos arquivos/métodos e usa a mesma abordagem
  (comparação manual dos diffs, classificando "mesma abordagem" / "abordagem diferente").
- Custo total.

### Custo estimado

8 achados × 3 execuções × US$ 0,75–1,45 ≈ **US$ 18–35**. Só iniciar com ordem explícita
("roda"), conferindo modelo e spec-model antes.

### Como entra no artigo

Substituir "reruns tended to reach the same decision and a patch with the same approach" em
Threats/Internal validity (Model nondeterminism) pelo número medido.
