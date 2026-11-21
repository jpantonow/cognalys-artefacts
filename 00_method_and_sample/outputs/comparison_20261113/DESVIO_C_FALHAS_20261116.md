# Desvio declarado: as 4 runs FALHA do braço C são substituídas pela rodada pós-correção (2026-11-16)

**Decisão (usuário, 16/11/2026).** As 4 runs FALHA do braço C k3 (fd90351) vieram de defeitos de
implementação do pipeline, não do método, e são substituídas pela mesma unidade e réplica
rodada no commit 3dee64a, que traz as três correções e seus testes:
- `pr_fff072cb8953` r1 e r3 (NotallyX): o diagnóstico do teste gerado era perdido quando a falha
  ocorria dentro do frame do Robolectric.
- `co_f637c4b8467e` r3 (winterhold): um rótulo só de revisão era barrado pela checagem de escopo.
- `co_0a32f516feb2` r2 (org.cry.otp): `reported_value_untouched` não via o achado conjunto.

As outras 76 runs do C k3 ficam como estão: a estatística só muda nas runs que falharam.

**Fonte das runs substitutas:** `frozen_runs_20261113/c_arm_k3_fixes/<unidade>/r<k>`
(`cognalys_arm_k3_fixes/`). Configurações, achados e controles de vazamento são os do C k3.

**Ameaças, declaradas:**
- Só as falhas ganharam uma segunda execução. Uma run que não falhou, mas teve um resultado ruim
  por acaso, não teve essa chance, o que dá ao C um pequeno viés a favor.
- As correções foram motivadas pelas falhas observadas (post hoc).
- As outras 8 runs da rodada pós-correção mostram a variação entre réplicas e são reportadas à
  parte, sem entrar na tabela.
- O braço D (demonstração parcial, 17/40) rodou em fd90351.

## Kit dos juízes (desvio do pré-registro de 14/11, registrado antes de qualquer nota, 16/11 08:39)

- `cards.py`: SP2 entra no lugar de SP (DESVIO_SP_20261115.md); as 4 runs FALHA do C vêm de `c_arm_k3_fixes` (C_REPLACED); e "ChatGPT" do template de PR atual do NotallyX conta como texto do projeto no lint (conferido no GitHub).
- `tabulate.py` e `retabulate_author_rule.py`: SP2 no lugar de SP.
- `judge.py phase2 --allow-incomplete`, porque o braço D é parcial (17/40). Todos os outros braços estão completos.
- O lint passou; os testes a seco das 3 leituras e das 2 provas de cegueira estão sem marcadores. Cartões C: PR 31 / NENHUMA AÇÃO 39 / ISSUE 10 / FALHA 0.

sha256 após a mudança:

    88202d39b5cd8b1ce79279a29f6e7e6dc654b19d73ab5ab07bdd7cac9a9ffbf0 *cards.py
    ec5a71d823d4cbaa6f831fba9dc956e8b02b803dbb9ca995f42c05e2f8c5bd64 *tabulate.py
    ab6dd38c99f9f2201e4bba3ffcd3a266d19b0bc86fc034e28a81f9198176ede4 *retabulate_author_rule.py
    89ec66c3b4346b203ad875eab16c974c6ce5c3d59094f013ead0509c5a0cf204 *judge.py
