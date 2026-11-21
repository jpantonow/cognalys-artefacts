# Plano de revisão do `main.tex`: artigo Cognalys (17/11/2026)

**Origem:** síntese dos achados revisados e verificados dos blocos resumo/introdução, fundamentação, pipeline, Study Settings, Results RQ1–RQ5, Results RQ6, Discussão/fim, coerência e referências. Os achados duplicados foram fundidos. Quando a verificação deu uma `corrected_proposal`, usei a versão corrigida. Nenhum achado foi refutado.

---

## 0. Convenções deste plano (decida antes de aplicar)

1. **Numeração das RQs.** Os achados propunham três arranjos diferentes. Escolhi o que menos quebra referências:
   - **RQ1–RQ5 continuam como estão**: estudo de caso datado e levantamento.
   - **RQ6 = comparação com agentes (opção A)**, já incluindo a variação entre execuções. O `\label{subsec:rq6}` atual é mantido.
   - **RQ7 = validação dinâmica.** Ela substitui a RQ7 atual, que não tem resultado nenhum. O método fica em `\label{subsec:dynval}` (Study Settings) e o resultado em `\label{subsec:rq7}`.

   Todos os textos abaixo já foram adaptados a essa numeração. Uma alternativa era a validação dentro da RQ2, mas misturaria populações diferentes (o snapshot de 34 achados e os 49 PRs). A outra era RQ6 = validação e RQ7 = comparação, mas obrigaria a trocar todas as referências a `subsec:rq6`.
2. **Placeholders visíveis.** Acrescente ao preâmbulo, depois da linha 20:
   ```latex
   \newcommand{\pending}[1]{\textbf{[TODO: #1]}}
   ```
   Antes da entrega, `grep -n '\\pending\|TODO-\|<commit>' main.tex` tem de voltar vazio. Nenhum número da opção A nem da validação dinâmica entra no texto antes de ser medido.
3. **Levantamento de PRs = 17/11/2026.** O estado dos 49 PRs é o mesmo de 13/11 (18/10/21). O que mudou foi o número de PRs com resposta: **31** (era 30). O motivo é o comentário do mantenedor no prim-ftpd #453 em 15/11. Com isso, 18 abertos ficam sem resposta, a proporção vai a 63% e a coluna Plain de `tab:pr-format` sobe de 11 para 12. O `amostra/inventario.json` ainda lê `outputs/prs_reports/20261113`. Regenere com `consolidar.py` apontando para `20261117`, ou deixe o texto dizer que o inventário vem de 13/11 com os mesmos estados.
4. **Referências.** Só entram no texto as referências conferidas no bloco de referências (lista em C4). As que não foram conferidas (CDRep, Egele, Qi, Agentless, Repairnator, SapFix, Dependabot, Wessel 2021 etc.) ficam como opcionais, só depois de conferidas (C17).
5. **Regra da edição do paper (CLAUDE.md).** Este plano é o texto que você aprova. As edições são feitas por script no WSL, e depois é preciso recarregar a aba do editor (File: Revert File).

---

## 1. Fatos errados e contradições com as decisões da sessão

### F1. Resumo inteiro · linhas 35–63 · alta
As linhas 58–61 ainda afirmam o resultado do desenho removido: agentes melhores e mais baratos que o Cognalys. Isso contradiz as decisões 1 e 2, e no k3 o C tem 0,96 contra 0,85 do TP. A frase "each published pull request" também está errada, porque 16 dos 49 foram excluídos da validação. O resumo tem 330 palavras, não data o levantamento, não fala do porte para a ponta do branch e apresenta a issue como saída geral. A versão abaixo tem cerca de 270 palavras. Ela não usa "blind", porque a prova de cegueira identificou o C (ver S8), e não fala de custo.
```latex
\begin{abstract}
RVSec reports cryptographic API misuses that an Android application executed,
but not which source change is safe: a fix can depend on stored data, protocol
peers, callers, and application policy. \system, a Python library and
command-line tool, treats each RVSec finding as a claim to check before
repairing it. With repository evidence from the analyzed revision, large
language model stages under deterministic checks decide whether the violation
is real and whether a fix keeps the application's behavior. A confirmed
violation with such a fix, even a rule-only one, becomes a patch applied in an
isolated worktree at the tip of the target branch and built; other findings
end with their reason recorded, or with an issue when their security impact
was confirmed. Nothing is published without explicit human approval.
We report a dated case study of an earlier pipeline version and two
pre-registered evaluations with declared deviations; all results are
descriptive. As of November 17, 2026, of 49 pull requests submitted for RVSec
findings, 18 were incorporated (one only adds a test), 10 were closed, and 21
were open; six of the ten closures questioned the security relevance of the
reported use. When RVSec was rerun on the original and the patched APK, the
reported violation no longer appeared in the patched method for \pending{N} of
the \pending{M} pull requests whose finding it reproduced.
% TODO: casos inconclusivos (método não alcançado) não contam como corrigidos.
On 20 findings, two LLM judges, not told which side produced each delivery,
found \pending{C}\% of \system{}'s runs acceptable, against
\pending{TP, SP2, OP} for three coding-agent configurations that follow the
same process, counting issue and no-action outcomes as acceptable.
Neither the build nor the rerun shows that a patch preserves the application's
behavior.
\end{abstract}
```

### F2. Contagens e data do levantamento propagadas · linhas 807, 815, 818, 882, 1439, 1457, 1608 · alta
Com o levantamento de 17/11, as contagens passam a ser 31, 18 e 63%. Os demais pontos onde a data e o número aparecem são resolvidos dentro de F1, S1, S5, F20, S14, F25 e F26. Aqui ficam as trocas pontuais.
- `tab:pr-state` (ela vai para a RQ3, ver F20). Legenda: `State of the 49 RVSec pull requests on November 17, 2026.` Linhas: `Open (18 without a reply) & 21 \\` e `Received a maintainer response & 31 \\`.
- Linha 882: `For \researchq{3}, we queried the GitHub API on November 17, 2026 for the 49 eligible pull requests (Table~\ref{tab:pr-state}) and for the 33 issues ...` (o resto da frase não muda).
- `tab:pr-format`. Legenda: `Format of the 49 RVSec pull requests and their state on November 17, 2026. Words and headings are medians of the current body, rounded.` Linha: `Received a response & 11 & 8 & 12 \\`. O prim-ftpd #453 foi aberto em 3/11, então entra no formato plain.
- Linha 1608: "asked maintainers before writing any patch" é falso para BombusMod #317 e wwwjdic #116, abertos depois do PR no mesmo dia. Troque por `asked maintainers whether they would welcome a patch, in most cases before writing one`.

### F3. Pergunta sobre agentes na introdução · linhas 107–110 · alta
A pergunta atual é a do desenho antigo, em que os agentes não recebiam o processo. A frase que repetiria o item de contribuição foi omitida de propósito.
```latex
wrong both about whether a violation matters and about its own patch. A
natural question is whether a general-purpose coding agent is enough when it
receives the finding, the repository, and the same process written as
instructions (Section~\ref{subsec:rq6}).
\pending{one sentence with the result of the comparison; if the data support
it, that the difference comes from executing the process in separate stages,
with a decision stage apart from the edit, rather than from the instructions}
```

### F4. Citação errada na introdução · linhas 101–106 · média
A citação `pearce2022asleep` sustenta a afirmação "a passing build does not show that a patch is correct", mas Pearce et al. tratam de código vulnerável gerado pelo Copilot. A citação correta é o overfitting de patch (Smith et al., conferido). A frase também exagerava o que o SWE-bench mostra.
```latex
Large language models make these steps possible to attempt. They repair code
and sometimes outperform earlier program repair
systems~\cite{xia2023apr,zhang2023apimisuse}, and coding agents are now
evaluated on resolving real repository issues~\cite{jimenez2024swebench}. They
also make the steps riskier: generated code can contain security
weaknesses~\cite{pearce2022asleep}, a patch can satisfy its oracle, here the
build, without being correct~\cite{smith2015overfitting}, and a model can be
wrong both about whether a violation matters and about its own patch.
```

### F5. Regra da issue na introdução · linhas 116–121 · média
Hoje a frase diz que toda abstenção pode virar issue. Pela regra do pipeline, só uma violação confirmada sem correção e com impacto de segurança confirmado vira issue.
```latex
keeps the application's behavior. A confirmed violation with such a fix
becomes a patch, whether or not it has security impact. Otherwise the finding
ends with its reason recorded: a false positive, code that already uses the API
as documented (so that any fix would change what it trusts or protects), or a
fix that needs a decision outside the repository. A confirmed violation left
without a fix becomes an issue for the maintainers only when its security
impact was confirmed. These abstentions count as valid outcomes rather than
failures.
```

### F6. Contribuições · linhas 131–147 · média
Os problemas são quatro. O item 4 diz "each PR" e esconde os 16 PRs excluídos. O item 5 fala de dois agentes, quando são três configurações, e de "current branch" em vez da ponta. Os TODOs aparecem só como comentário. O item 3 não tem data. O número 33 já pode entrar, porque as exclusões foram fixadas antes de qualquer execução.
```latex
This work makes five contributions:
\begin{itemize}
  \item a Python library and CLI that complement RVSec by connecting each
  runtime finding on an APK to the source revision it came from, to a decision
  on whether it is real, and to a reviewable patch or issue;
  \item a staged design with separate contracts for evidence, adjudication,
  authorized edits, isolated validation, and human review, in which abstention
  and every intermediate decision remain inspectable;
  \item a study of the 49 pull requests and 33 issues submitted to maintainers
  for RVSec findings up to November 17, 2026 (18 pull requests incorporated, 10
  closed, and 21 open), including the reasons maintainers gave for their
  decisions;
  \item a dynamic validation, pre-registered before any new case was built, in
  which RVSec reruns the original and the patched APK of the 33 published pull
  requests that met the inclusion criteria: \pending{result}; and
  \item a pre-registered comparison on 20 findings with three coding-agent
  configurations (Codex with two models and Claude Code) that are prompted
  with the same process and whose patches are also ported to the tip of the
  target branch: \pending{result}.
\end{itemize}
```

### F7. Figura do pipeline e subseção de aplicação e build · linhas 288–352, 541–569 e 127 · alta
A figura e o texto colocam o porte depois do build e sempre feito por modelo. No código, o worktree já é criado na ponta do branch. A sequência é: aplicação literal, depois merge de três vias, e só em caso de conflito o porte por LLM. Há um único build, na ponta. A junção de achados vem depois da extração. Faltam na figura o import do RVSec, o template de PR e as checagens de publicação.

**Figura** (substitui as linhas 288–352):
```latex
\begin{figure*}[t]
\centering
\resizebox{\textwidth}{!}{%
\begin{tikzpicture}[
  node distance=5mm and 5mm,
  box/.style={draw,rounded corners,align=center,minimum height=10mm,
              text width=2.5cm,font=\footnotesize},
  aux/.style={draw,dashed,rounded corners,align=center,minimum height=8mm,
              text width=2.5cm,font=\footnotesize},
  gate/.style={draw,diamond,aspect=2,align=center,font=\footnotesize,
               inner sep=1pt,text width=1.7cm},
  flow/.style={-{Latex[length=2mm]},thick},
  feed/.style={-{Latex[length=2mm]},dashed},
  lbl/.style={font=\scriptsize}
]
\node[box] (csv) {RVSec reports: labels, echoes folded, app or library code};
\node[box,right=of csv] (extract) {Clone, bytecode name to source, extraction};
\node[box,right=of extract] (packet) {Joined analysis units; deterministic evidence packet};
\node[box,right=of packet] (claims) {Claim extraction and function contract};
\node[gate,right=of claims] (adj) {Adjudication};
\node[box,right=8mm of adj,text width=3.1cm] (abstain) {No patch: false positive, insufficient evidence, no security impact, no safe fix, external contract, or maintainer decision; an issue only with confirmed impact};
\draw[flow] (csv)--(extract);
\draw[flow] (extract)--(packet);
\draw[flow] (packet)--(claims);
\draw[flow] (claims)--(adj);
\draw[flow] (adj)--node[above,lbl]{no fix}(abstain);
\node[aux,above=6mm of packet] (corpus) {External research; shared part from the research corpus};
\draw[feed] (corpus)--(packet);
\node[box,below=17mm of adj] (plan) {Authorized patch plan};
\node[box,left=of plan] (gen) {Per-file drafts and semantic gates};
\node[gate,left=of gen] (review) {Behavior review};
\node[box,left=8mm of review] (apply) {Worktree at the branch tip: apply, three-way merge, or model port};
\node[box,left=of apply] (repair) {Gradle build; compilation repair, reviewed again};
\draw[flow] (adj)--node[right,lbl]{fix}(plan);
\draw[flow] (plan)--(gen);
\draw[flow] (gen)--(review);
\draw[flow] (review)--node[above,lbl]{clean}(apply);
\draw[flow] (apply)--(repair);
\draw[flow] (review.north) to[out=60,in=120] node[above,lbl]{redraft (same plan) or replan (once)}(gen.north);
\node[aux] (memory) at (abstain |- plan) {Maintainer responses and earlier patches};
\draw[feed] (memory)--(plan);
\draw[feed] (memory.north west)--(adj.south east);
\draw[feed] (memory.south) -- ++(0,-5mm) -| (review.south);
\node[box,below=14mm of repair] (text) {PR text in the repository's template, or issue text; checked against the diff};
\node[box,right=of text] (checks) {Checks on the live repository: duplicate, upstream change, policy};
\node[gate,right=of checks] (publish) {Human approval};
\node[box,right=10mm of publish] (public) {Explicit remote publication};
\draw[flow] (repair)--(text);
\draw[flow] (text)--(checks);
\draw[flow] (checks)--(publish);
\draw[flow] (publish)--node[above,lbl]{approved}(public);
\end{tikzpicture}
}
\caption{Workflow exposed by the Python library and CLI. Findings in one piece
of code form one unit. A unit whose earlier pull request is still open, or was
merged after the analyzed commit, or whose file is no longer on the target
branch, ends before any model call. A finding can end without a patch at
adjudication, at planning, or when the behavior review finds the patch has no
effect; it becomes an issue only when its security impact was confirmed. A
draft whose behavior review reports a regression is redrafted on the same plan
or, once per unit, replanned. The patch is applied in a worktree at the tip of
the target branch, merged three-way when that code has moved, and ported by a
model only when neither applies; a claim that the tip already holds the fix is
accepted only when a deterministic check confirms it. Dashed inputs inform
decisions but cannot authorize an edit: only repository evidence and explicit
policy can.}
\label{fig:pipeline}
\end{figure*}
```

**Subseção** (substitui as linhas 541–569):
```latex
\subsection{Application on the Branch Tip and Build}

The analysis reads the revision the analyzed APK was built from, but a pull
request targets the current tip of the project's base branch, which may have
moved on. The base clone is never edited: each accepted solution revision is
applied atomically to a dedicated Git worktree created at that tip, on a
stable branch, and a new attempt receives a distinct path instead of
overwriting an earlier one. The execution report records the base commit,
changed files, exact edits, finding identifier, revision, and failure reason.

Application tries the most literal step first. The reviewed edits are applied
as written, following files the base renamed; when the anchors of a patch made
of separate edits have moved, Git's three-way merge carries the edits from the
analyzed revision onto the tip. Only when this fails does a narrow model stage
port the patch. It receives the patch as reviewed, what upstream changed in
each touched file, and those files as they are on the tip. It then re-anchors
the same correction, states that the tip no longer contains the violation, or
declines. A port must pass the semantic gates and the applier's anchor checks.
A port that writes lines the reviewed patch did not write is reviewed for
behavior again, and a rejected port goes back to the model with the reason at
most once more. When the semantic gates refuse a port and the violation is
still on the tip, the finding becomes an issue candidate. The statement that
the tip no longer contains the violation is accepted only when a deterministic
check confirms it: either the reported statement is in no file of the tip, or
every corrected block is already there and the code it replaces is not.
Otherwise the statement counts as a declined port. A finding is recorded as
\emph{not applicable} in three cases: the tip already holds the correction,
its file is no longer on the branch, or no port applies and the code the patch
replaces exists only at the analyzed revision. The last case says that nothing
is left to patch as reviewed, not that the violation is gone. No pull request
is opened for a finding that is not applicable. After a port, the pull-request
summary is rewritten for the edits actually applied.

Build validation then runs the repository's selected Gradle task, normally a
non-minified debug assembly, on a JDK chosen from the Gradle wrapper and the
declared Java version. It records commands, return code, duration, APK count,
logs, and compiler diagnostics. Environment failures involving SDKs, JDKs,
Gradle, network access, submodules, or unavailable repositories are labeled
infrastructure failures. A failure that the unmodified tip also shows is
attributed to the project rather than to the patch, but it is never counted
as a pass. Some passes are narrower than a full build: only the patched module
compiled, only production code built, or the patch compiled on the analyzed
revision because the tip itself does not build. Each is recorded under its own
status and never counted as a full build. A patch whose production files lie
in a source set the APK does not compile is not published. If new compiler
diagnostics are attributable to changed files, a separate, narrow model stage
may propose exact compilation repairs, followed by revalidation. It cannot
reopen the security decision, and a repair that edits files outside the plan
is reviewed for behavior again.
```
A frase da linha 127 da introdução é corrigida no parágrafo C12.

### F8. Frase de roteamento para o mantenedor · linhas 488–495 · alta
A frase atual contradiz a regra das linhas 519–527 e ainda usa "automatic patch". Ela deixa de fora vários gates: valor enviado para fora do app, opção do usuário, ciphertext embarcado e a nota no PR.
```latex
Before any code is applied, semantic gates reject patches with known
failure shapes: changing a value that is stored, sent out of the
application, or compared with a constant without keeping the old form for
existing values, leaving a reader of a changed stored value unchanged,
removing the behavior behind a user setting, removing ciphertext the
application ships, replacing an algorithm a protocol requires, or leaving the
reported value untouched. Each rejection returns to the generator with the
reason; when no change inside the application can satisfy it, as with
ciphertext encrypted under a key the application ships or a weaker behavior
the user opted into, the finding goes to the maintainers by the routing
described below. A rename must update every reference in the repository in
the same patch; when the rename check fails, the draft is regenerated with
the failure explained. A question only the maintainers can answer, such as
whether to authorize a rename, becomes a note in the pull request rather than
a block.
```

### F9. Proposta e publicação · linhas 576–588 · alta
A frase "findings without a safe fix produce an issue" está errada. Também faltam o template de PR, o preview, a aprovação por identificador, as checagens no repositório vivo e a definição de "ready pull request", termo que a linha 1002 usa.
```latex
\subsection{Proposal and Publication}

For a patch that applied and built, \system{} writes a short pull-request
description for the maintainers that names RVSec as the analyzer. When the
repository has a pull-request template, the description fills that template
instead, keeping its headings and checklists; a box is ticked only from a fact
the pipeline verified, such as a build that passed, and a box claiming a test
on a device is never ticked, because RVSec ran the original APK, not the
patched one. A deterministic check compares the text with the final diff and
with a list of internal terms, and a text that fails is rewritten. A finding
without a patch produces an issue only under the routing rule above, and its
text passes the same checks. In what follows, a \emph{ready pull request} is a
committed draft whose build passed: a candidate for human review, not a
publication, whose diff still has to be read.

Nothing is published automatically. A pull request or issue is opened only
for a finding the operator approves by its identifier, after reading a preview
of what would appear on GitHub. Before opening it, \system{} checks the
finding's earlier pull requests and the repository as it is then. By default
it refuses a pull request for a finding whose earlier one may still be open or
was closed without merging, one whose change another of its own pull requests
already makes or overlaps, a diff the default branch already contains or no
longer matches, a diff that changes only tests, a draft whose behavior review
predates the current review contract, and a repository that states it accepts
no contributions; each refusal names the explicit override that would lift it.
A policy on AI-assisted contributions is reported to the operator, who then
writes the text; it does not block. An issue is refused when the code it
reports is no longer on the default branch. Commits carry their author's
sign-off, and \system{} never adds a sign-off to a commit someone else wrote.
Later review comments can be answered by revising the existing pull request,
again only after approval. Pull-request creation currently targets GitHub; for
repositories hosted elsewhere, \system{} still produces the analysis and the
validated patch. Prompt traces record requests, responses, versions, token
usage, and latency, but no credentials.
```

### F10. Patch sem efeito · linhas 511–517 · baixa
A rejeição de patch sem efeito também é feita por checagens determinísticas, não só pela revisão. Além disso, o achado só fecha quando esse motivo é o único que resta na última tentativa.
```latex
The review, and deterministic checks of the finished patch, also reject a
patch that the runtime monitor would not notice: one in which the governed
call receives the same material as before, as with a renamed constant, an
alias that delegates to the same call, a factory around the same object, or
a key rebuilt from its own bytes or encrypted and decrypted again under the
same key. Each rejection goes back to the generator with its reason; when
only such rejections remain after the last attempt, the finding closes as a
violation without security impact. A change that alters the value, such as
replacing \texttt{SSL} with \texttt{TLS}, still becomes a proposal even when
it only satisfies the rule.
```

### F11. Unidades juntadas e encerramento antes do modelo · linhas 379–383 · média
Faltam a junção entre classes diferentes, quando um método grava um valor que outro lê, e o encerramento sem chamada de modelo, que a linha 938 usa sem explicar.
```latex
Findings reported in one piece of code are analyzed as one unit and receive
one patch: they share a class and file, and either one method calls the
other, the same method violates two rules, or the same rule and message are
reported at several steps of one object's lifecycle. Findings of the same
specification in different classes of one module also join when one method
stores a value that the other reads back for the same governed object, such
as an IV written at encryption and read at decryption; a finding related to
any member of a unit joins that unit. Separate drafts for such findings
rewrote the same code in incompatible ways. A unit ends before any model
call when each of its findings already has a pull request of ours that is
open or was merged after the analyzed revision, or when the reported file is
not on the target branch.
```

### F12. Contratos e decisões · linhas 411–453 · média
A classe `FindingEvidencePacket` não existe com esse nome. Os "six contracts" deixam de fora estágios estreitos, inclusive o porte. A lista de contrapartes está incompleta. O contrato externo é uma classificação, não uma migração.
- Linha 413: `Python deterministically constructs an evidence packet for each analysis unit.`
- Linha 423: `The core analysis has six model contracts:`
- Item 1: troque `(a third-party service or a user-operated peer)` por `(a third-party service, a peer the user operates, or the same app or device)`.
- Depois de `\end{enumerate}` (linha 446):
```latex
When the extraction names evidence it lacks, or the adjudication abstains
because evidence is missing, Python fetches what was named from the checkout
and the two contracts run once more on the enlarged packet; the new answer is
kept only when it passes the same validators. Narrower model stages surround
these contracts: the external research above, compilation repair, and the
port to the branch tip. Each returns a schema-checked answer.
```
- Linhas 448–453:
```latex
The adjudication chooses among fifteen decisions in five groups: repairs that
are applied and built without further review (a single- or multi-file fix, an
internal digest migration, or a dependency fix or replacement); repairs
proposed for review, whose patch a person must read before it is applied (a
multi-file fix, or a backend or multi-repository migration); decisions that
wait for an explicit policy; classifications of a confirmed violation that
call for no change here (a non-security digest use, a violation without
security impact, or an external digest contract that no client-side change
can meet); and \texttt{NO\_SAFE\_FIX}. A review-only decision may permit local
application for validation while keeping publication blocked. Version fields
and hashes cover the prompt, schema, policy, specification, context builder,
and semantic resolver, enabling comparisons across runs.
```

### F13. Entradas e execução · linhas 590–612 · média
O texto apresenta o Flex como se valesse para todos os provedores, mas só vale para a OpenAI. A regra atual de nova tentativa no Flex, o teto de custo, a vigilância de travamento e os comandos de revisão e publicação não aparecem.
```latex
\subsection{Inputs and Execution}

\system{} does not depend on a particular detector: a finding only needs to
identify the project, the class and method, and the violated specification,
with the message, repository, and commit as optional context. Findings can be
supplied as a CSV or JSON file, imported from SARIF or Semgrep reports, or
produced by \texttt{cognalys scan}, which runs the library's own Semgrep rules
on a local checkout. The RVSec integration is optional: \texttt{cognalys
rvsec-run} builds the F-Droid APKs, runs RVSec in its container with the chosen
explorers, and imports the reports as findings, while earlier reports can be
imported without running RVSec again. RVSec remains responsible for
instrumentation and the monitored specifications; \system{} adds the analysis,
the repair proposals, and their validation. A run is described by a small
configuration file (findings, model provider, validation options) and started
with \texttt{cognalys run}. Other commands list a run's steps without executing
them, summarize each finding's outcome across runs, report the model cost per
step, show a run's patches or apply them to a local checkout, preview and
publish the proposals a person approved, and refresh maintainer responses. The
same operations are available from Python through \texttt{cognalys.api}.

The model provider is configurable: OpenAI and Anthropic models through their
APIs, and locally served models through Ollama, without web research. With
OpenAI models, calls ask for the Flex processing tier, billed at half the
standard price. A call Flex reports it has no capacity for is sent on Flex
again after a wait of one and then two minutes; it goes on the standard tier
when Flex still has no capacity, answers with a rate limit, or leaves the call
queued for 15 minutes, and each trace records the tier that served it. A run
stops making model calls once its recorded cost reaches a configured ceiling,
and the model step is stopped and resumed once from its cache when it records
no model call and no log line for at least 30 minutes.
```

### F14. Tabela de trabalhos relacionados · linhas 236–266 · alta
A tabela atribui ao Cognalys "security-specific validation" e "detector acceptance". O pipeline nunca reexecuta o RVSec; isso só acontece na validação dinâmica, que é um estudo externo. Faltam os agentes de código e as ferramentas que abrem PRs. A versão abaixo usa só referências conferidas, e a linha "CryLogger and RVSec" (aplicada hoje) fica como está.
```latex
\begin{table*}[t]
\centering
\caption{Relationship to representative prior work.}
\label{tab:related}
\footnotesize
\begin{tabular}{p{2.9cm}p{3.1cm}p{3.4cm}p{6.0cm}}
\toprule
Work & Primary input & Main outcome & Relation to \system \\
\midrule
CrySL and CogniCrypt~\cite{krueger2017cognicrypt,krueger2021crysl} &
Java programs and declarative crypto rules & Static misuse reports; code
templates for common tasks & CrySL rules are the origin of RVSec's
specifications; \system{} takes misuse reports as input and decides whether
and how to change the code. \\
CryptoGuard~\cite{rahaman2019cryptoguard} & Java and Android programs &
High-precision static warnings & Prunes false positives by program analysis
but proposes no repair. \\
CryLogger and RVSec~\cite{piccolboni2021crylogger,
torres2023runtime} & Executions and runtime specifications & Concrete misuse
observations & Provide dynamic evidence but not a conservative source-repair
workflow. \\
LLM-based APR~\cite{xia2023apr,zhang2023apimisuse} &
Faulty code and its context & Candidate patches on benchmarks & Measures patch
generation; \system{} adds abstention, edits authorized per file and symbol, a
build in the real project, and human approval before publication. \\
SWE-bench and SWE-agent~\cite{jimenez2024swebench,yang2024sweagent} &
Repository and issue text & Patch accepted by the issue's tests & Same
repository-level setting; \system{} starts from analyzer evidence and checks a
patch with a build, a static rule when one exists, and a model review of its
behavior; our evaluation also reruns RVSec on the patched app. \\
Codex CLI, Claude Code~\cite{openai2026codex,anthropic2026claudecode}
& Repository and a task in natural language & Edits made in an agent loop &
Baselines of our comparison, given \system{}'s process as instructions. \\
Pull requests from bots and tools~\cite{mirhosseini2017autopr,wessel2018bots,
wyrich2021botprs} & Outdated dependencies, project events & Pull requests for
maintainers & Their changes are checked by an existing oracle, such as a
release; \system{}'s changes alter security-relevant code, and each is
approved by a person before it is sent. \\
\bottomrule
\end{tabular}
\end{table*}
% Opcional (C17): linha CDRep só depois de conferir a referência.
```

### F15. Definições da RQ2 e da RQ3 · linhas 655–658 e 663–675 · alta
As duas definições afirmam que as razões dos mantenedores são o único julgamento externo dos patches, o que deixou de ser verdade. A RQ3 cita "changes requested" e "every pull request opened during the study".
- Última frase da RQ2: `These results establish that a patch can be applied and built; they do not establish behavioral or security correctness. Maintainers' decisions (\researchq{3}) and the RVSec rerun of the published patches (\researchq{7}) address parts of that question.`
- Corpo da RQ3:
```latex
  For the 49 pull requests and 33 issues submitted for RVSec findings, we
  record the state on November 17, 2026 (incorporated, closed by a
  maintainer, or open) and whether anyone other than the author or a bot
  replied. When maintainers explained a decision, we extracted the reasons
  from their comments and grouped them, for example into disputes about
  security relevance, compatibility concerns, behavior the maintainer
  considers intentional, code that had already changed upstream, and project
  contribution policies. These reasons are the judgment of the people who own
  the code. They complement the build-level evidence of \researchq{2}, the
  rerun of RVSec on the published patches (\researchq{7}), and the model
  judges of \researchq{6}, who score a separate set of findings, and they show
  which kinds of rejection the pipeline's pre-submission checks did or did not
  anticipate.
```

### F16. "The study is exploratory" · linhas 713–733 · alta
A frase atual vale para o estudo inteiro e contradiz as duas avaliações pré-registradas. Junto com esta troca, é obrigatório apagar o teste de Fisher e o bootstrap com Holm, o que é feito em S8.
```latex
The finding identity is APK identifier, class, method, and specification; the
PR identity is repository and PR number. Multiple attempts at one finding do
not create independent cases. Different specifications in the same method
remain distinct findings, although a single coordinated patch can address
both. PR status and build status remain separate dimensions.

The case study and the survey of pull requests are exploratory: no hypothesis
was fixed before their data were collected, and the pipeline changed while
they were collected (each change is recorded beside the measurement that
motivated it). The dynamic validation and the comparison follow procedures
and analysis rules written in the repository before the runs they govern;
the comparison's registrations also record the hashes of its prompts, scripts,
and answer keys. Each registration states which runs preceded it and were
carried over, and every rule changed after results were seen is declared as a
deviation where it applies. No part of the study applies a statistical test:
results are descriptive counts and case explanations.
Table~\ref{tab:rq-data} lists the data behind each question.
```
Linha 740: `Each result reports the number of findings, patches, pull requests, or runs to which it refers.`

### F17. Regra de fechamento pelo autor · linhas 796–798 · baixa
A frase descreve uma regra que nunca se aplica e que contradiz o script de levantamento, que exclui fechamentos feitos pelo autor ou por bot. Troque por:
`Pull requests closed by the author or by a bot are not maintainer decisions and are not among the eligible records.`
Na mesma edição, depois de `... driving the interface.` (linha 796), acrescente:
```latex
Forty-six of the 49 pull requests concern cryptographic specifications; three
(BombusMod, wwwjdic, and ntfy) concern resource-handling and synchronization
specifications from an earlier RVSec campaign that our own queues do not
enable.
```

### F18. Análise: o build não equivale à aceitação pelo detector · linhas 916–921 · média
O texto trata como não medido algo que a RQ7 agora mede.
```latex
Counts are descriptive. A build-valid proposal is not automatically
semantically correct, secure at runtime, or accepted by maintainers, and it
does not by itself show that the originating detector no longer reports the
violation; for the published pull requests we check this last point by
rerunning RVSec on the original and the patched APK (\researchq{7}), which
still covers only the paths the explorers reach. Likewise, a closed PR is not
necessarily an incorrect patch: intentional behavior, contribution policy, an
already-applied change, or manual incorporation can explain closure. We
therefore avoid a pooled success rate across generated patches, compilation,
and PR outcomes.
```

### F19. RQ1 reescrita como estudo datado · linhas 1018–1065 · alta
Problemas:
- Os três casos "não aplicáveis" tiveram causas diferentes. No DSub2000, a violação continua na base.
- A legenda diz "Latest".
- Faltam os eventos posteriores: 15 dos 34 achados têm PR; FreeOTP CipherSpec acabou mergeado; Droid SCEP virou o PR #2.
- O roteamento de issue do snapshot é diferente do atual.
- O label `subsec:recent-runs` não é usado em lugar nenhum.
```latex
\subsection{\researchq{1}: Finding Analysis and Repairability}
\label{subsec:rq1}

In the October 2026 snapshot, the adjudication of the 34 findings recorded 17
decisions to propose a reviewable fix
(\texttt{MULTI\_FILE\_FIX\_PROPOSED\_FOR\_REVIEW}), 13 decisions that no
safe fix existed (\texttt{NO\_SAFE\_FIX}), and four decisions that an external
digest contract prevented a client-only fix. These are decisions, not
completed fixes. Table~\ref{tab:adjudication} shows where each finding ended
in that snapshot. The entries are local records: an issue here is a proposal
kept for review, and publication was a later, separate step.

\begin{table}[H]
\centering
\caption{Outcome of each of the 34 findings in the October 2026 snapshot
(local records).}
\label{tab:adjudication}
\footnotesize
\begin{tabular}{p{6.1cm}r}
\toprule
Recorded outcome & $n$ \\
\midrule
Committed draft that built (pull-request preview) & 7 \\
Local issue proposal for the maintainers & 16 \\
Recognized false positive & 3 \\
Not applicable to the current base branch & 3 \\
Insufficient evidence & 3 \\
Confirmed violation without a safe fix & 1 \\
Generated fix whose build failed (also on the base) & 1 \\
\midrule
Total & 34 \\
\bottomrule
\end{tabular}
\end{table}

The three findings marked as not applicable had different causes. In
Nextcloud Cookbook, the current base branch already held the change of an
earlier pull request from this work (\#204, merged on August 9, 2026). In
Giggity, the maintainer had made an equivalent change independently, and the
earlier pull request from this work (\#543) was closed without merging. In
DSub2000 the reported code had changed upstream, but the base branch still
initialized its TLS context with an accept-all trust manager. A check rejected
the adaptation to that base because it removed what the user's
insecure-connection option turned on, and the port step then abstained.
DSub2000 is therefore an unresolved confirmed violation rather than an
upstream fix. None of the three produced a new pull request in this
consolidation.

Six of the 17 proposal decisions ended as issue proposals because generation
was blocked: the drafts needed production credentials the repository does not
hold (MTG Familiar), changed an algorithm a protocol mandates (two in mOTP),
changed persisted database names (RedReader), overrode a caller-chosen RSA key
size (Droid SCEP), or called verifier methods that do not exist (BitBanana,
whose migration reached a pull request through its sibling finding). A positive
planning decision is therefore weaker than a validated repair. The other ten
issue proposals came from no-safe-fix or external-contract decisions and
stopped before any draft. The current library opens an issue for such a
finding only when the adjudication confirmed a security impact
(Section~\ref{sec:pipeline}); by the impact recorded in the snapshot, nine of
these ten would not qualify for one.

Later events changed several of these outcomes. Fifteen of the 34 findings
also have a published pull request (Table~\ref{tab:sample}); on November 17,
2026, five of them had been incorporated, five closed by maintainers, and five
were open. The FreeOTP \texttt{CipherSpec} finding, recorded as a confirmed
violation without a safe fix, was analyzed again in seven runs between October
22 and 28, and FreeOTP merged a test for it (\#521) and then its production
fix (\#524). The Droid SCEP finding, a local issue proposal in the snapshot,
became pull request \#2. Four of the 16 local issue proposals correspond to
published issues: two opened before the snapshot (Critical Maps \#379 and
OpenBible \#190) and two after it (MTG Familiar \#669 and RedReader \#1394).
For WiFiAnalyzer, the snapshot declined a fix and recorded no security impact,
which is the reason the maintainer gave when closing the earlier pull request
\#618; the snapshot still proposed an issue, which the current routing would
not. For RedReader's \texttt{sha1}, the snapshot still proposed a fix whose
premise the maintainer had rejected when closing \#1373.

\textbf{Answer to \researchq{1}.} In the October snapshot, the analysis of
34 findings ended in seven committed, building drafts, 16 local issue
proposals, three recognized false positives, three findings with insufficient
evidence, three not applicable to the current base, one confirmed violation
without a safe fix, and one generated fix whose build failed for a reason the
base shares. These are the classifications of one earlier version of the
library; later runs and maintainer responses changed several of them.
```

### F20. RQ3 · linhas 1099–1134 · alta
O texto precisa dos números de 17/11. Hoje a `tab:pr-state` fica em Study Settings e nunca é citada; ela passa para esta seção. Correções de fato:
- "asked before submitted" é falso para BombusMod e wwwjdic;
- o FreeOTP #521 só acrescenta um teste;
- "findings from other sources" não tem apoio no estudo.

Faltam o InterTune, os merges de digest que servem de contraponto e uma tabela de motivos.

Primeiro parágrafo e em seguida `tab:pr-state` (com F2):
```latex
On November 17, 2026, 18 of the 49 pull requests had been
incorporated---17 merged on GitHub and BombusMod \#316, which the maintainer
applied by hand and closed---10 had been closed after review without
incorporation, and 21 remained open, 18 of them without any reply
(Table~\ref{tab:pr-state}). Thirty-one of the 49 (63\%) received a response
from someone other than the author. One merge, FreeOTP \#521, only adds a
test; the production change for the same finding is FreeOTP \#524, also
merged. By the adjudication recorded for each pull request, none of the 18
incorporated changes had a confirmed security impact: four had none, ten had
an impact that was not established, and four have no recorded class.

The 33 issues opened for RVSec findings had two roles. Twenty-five asked
maintainers whether they would welcome a pull request; 11 received a reply,
and all are now closed, four of them by maintainers. The other eight reported
a finding for which the pipeline proposed no safe fix and asked how the
maintainers wanted it handled; four received a response and two remain open.

\begin{table}[H]
\centering
\caption{Reasons maintainers gave for the 10 pull requests closed without
incorporation.}
\label{tab:closures}
\footnotesize
\begin{tabular}{p{5.8cm}r}
\toprule
Stated reason & PRs \\
\midrule
Digest used outside a security role (checksum, cache or file name,
identifier) & 6 \\
Detector premise does not hold (provider already generates a random IV) & 1 \\
Change only satisfies the rule; existing code is equivalent & 1 \\
Equivalent change already made upstream & 1 \\
Project not accepting pull requests & 1 \\
\midrule
Total & 10 \\
\bottomrule
\end{tabular}
\end{table}

Maintainer responses add application knowledge that a generic rule lacks
(Table~\ref{tab:closures}). The six digest closures (WiFiAnalyzer, rssreader,
whoBIRD, BetterUntis, RedReader, and InterTune) objected to replacing MD5 or
SHA-1 used as a checksum, a cache file name, or an identifier. The same kind
of change was merged elsewhere: AMARadio accepted SHA-256 in cache file names
(\#122), ShortMessageSecure in stable list identifiers, which its maintainer
described as having no cryptographic role but as a reasonable improvement
(\#89), and NotallyX in export hash verification (\#1167). Whether such a
change is welcome is the maintainer's decision, not a property of the rule,
which is why {\system} still proposes a correct change for a violation
without security impact and keeps the response.

Other responses changed the patch itself. In Crazyflie, review redirected a
TLS helper patch toward removing the obsolete helper; in ShortMessageSecure
\#83, the accepted revision kept \texttt{HmacSHA1} and made the key size
explicit instead of replacing the algorithm. Among open pull requests, the
Session reviewer noted that on Android ``SSL'' and ``TLS'' select the same
implementation, the Feeder maintainer objected that restricting protocols in
an option users turn on would break some sites, and the prim-ftpd maintainer
agreed to remove RC4 after testing. Responses reach later analyses of the same
specification (Section~\ref{sec:pipeline}), and two of them became checks of
the library: a provider-generated IV is treated as random (Amethyst \#4642),
and dropping protocols from a compatibility option the user enables counts as
a regression (Feeder \#1210).

Three of the 49 pull requests address specifications outside the
cryptographic set (stream closing in BombusMod and wwwjdic, a synchronized
map in ntfy). They come from earlier RVSec runs whose specification set still
included generic API rules. The same workflow carried them to a pull request,
which does not establish coverage of arbitrary API families.

\textbf{Answer to \researchq{3}.} On November 17, 2026, 18 of 49 pull
requests had been incorporated, 10 closed, and 21 remained open; 31 received a
response. Six of the ten closures disputed the security relevance of a
digest used outside a security role, while comparable digest changes were
merged in three other projects; the other closures cited a detector premise,
a rule-only change, an upstream change, and a contribution policy. Issues that
reported a finding without a patch drew a response in four of eight cases.
Incorporation and closure must be read with these reasons, and responses that
changed a patch's premise became checks of the library.
```
Em Study Settings (linhas 754–760) fica apenas o critério de inclusão (S5).

### F21. RQ4: "nine" está errado · linhas 1136–1156 (e conclusão 1798) · alta
Das 9 entradas com impacto `non_security`, uma é o falso positivo do Aegis. Ficam 8 violações reais. Os 4 casos de contrato externo não são `non_security`. "how a key store is loaded" descreve mal o que o PR mudou. Falta a tabela por especificação.
```latex
\subsection{\researchq{4}: False Positives and Violations Without Security Impact}

In the October snapshot the analysis recognized three of the 34 findings as
false positives, each in a different specification: Aegis
(\texttt{GCMParameterSpecSpec}), OTP (\texttt{SecretKeySpecSpec}), and FreeOTP
(\texttt{AlgorithmParametersSpec}). This is the analysis's own judgment, not a
measure of detector precision. Eight findings were judged real rule
violations without security impact, six of them in two specifications: all
three \texttt{KeyStoreSpec} findings (Network Survey, Traffic Light, and
Tokn), which concern the sequence of calls that reads a key from the key
store rather than how the key is protected, and three of the 12
\texttt{MessageDigestSpec} findings (two in WiFiAnalyzer, one in Giggity),
which use a weak digest as an identifier. The other two are a Nextcloud
Cookbook \texttt{SSLContextSpec} finding and the Droid SCEP
\texttt{KeyPairGeneratorSpec} finding. Four other \texttt{MessageDigestSpec}
findings, outside this group, are bound to an external contract the
application cannot change on its own (two in Password Monitor, one each in
OpenBible and Critical Maps); their impact was not established in three and
confirmed in one.

Seven of the eight violations without security impact have a pull request
among the 49: Traffic Light \#238, Tokn \#138, and Nextcloud Cookbook \#204
were incorporated; Network Survey \#154, WiFiAnalyzer \#618, and Giggity
\#543 were closed; Droid SCEP \#2 is open. The pull requests as a whole show
the same concentration (Table~\ref{tab:pr-spec}): eight of the ten closed
after review were \texttt{MessageDigestSpec} changes, against three
incorporated and one open (Table~\ref{tab:closures}). Network Survey \#154 was
closed because \texttt{getEntry} and \texttt{getKey} are equivalent for that
key. In Amethyst \#4642 the maintainer showed that the provider already
generates a random IV when \texttt{init} receives only the key, a value RVSec
cannot trace; the patch also changed the desktop implementation.

\begin{table}[H]
\centering
\caption{State of the 49 RVSec pull requests on November 17, 2026, by
specification of the finding.}
\label{tab:pr-spec}
\footnotesize
\begin{tabular}{lrrrr}
\toprule
Specification & Incorp. & Closed & Open & Total \\
\midrule
\texttt{MessageDigestSpec} & 3 & 8 & 1 & 12 \\
\texttt{SSLContextSpec}    & 3 & 0 & 8 & 11 \\
\texttt{KeyStoreSpec}      & 5 & 1 & 1 & 7 \\
\texttt{CipherSpec}        & 3 & 0 & 3 & 6 \\
Other ten, or unrecorded\textsuperscript{a} & 4 & 1 & 8 & 13 \\
\midrule
Total & 18 & 10 & 21 & 49 \\
\bottomrule
\end{tabular}
\\[2pt]\raggedright\scriptsize\textsuperscript{a}HTTP Shortcuts \#576 has no
recorded specification.
\end{table}

\textbf{Answer to \researchq{4}.} In the snapshot the analysis recognized
three false positives, in three specifications, and eight violations without
security impact, six of them in \texttt{KeyStoreSpec} and
\texttt{MessageDigestSpec}. Among the pull requests,
\texttt{MessageDigestSpec} accounts for eight of the ten closures. For
digests the deciding question is what the value is used for, which a rule
over the algorithm name cannot answer, and maintainers answered it
differently in different projects.
```

### F22. RQ5: números não reproduzíveis · linhas 1158–1196 · alta
Nenhum arquivo do repositório reproduz 640 chamadas, US$ 39,00 ou a mediana de 0,75. O recálculo com o módulo de custo atual dá outros valores, e a janela de execuções precisa ser fixada. O texto chama de "issues for the maintainers" propostas que são locais. "About one dollar" generaliza o snapshot para o sistema, e o "lighter screening stage" não foi medido. O parágrafo "Model comparison" vai para S7.
```latex
\subsection{\researchq{5}: Model Usage and Cost}

The prompt traces of the runs that produced the October snapshot (October 19
and 20, 2026; \pending{N} runs with calls for these findings) record
\pending{CALLS} model calls for its 34 findings, which cost
US\$\pending{TOTAL} at list prices, a median of US\$\pending{MED} per
finding. These runs predate the Flex tier: every OpenAI call was served on the
standard tier, and cached input of \texttt{gpt-5.6-terra} is priced at the
full input rate, so the values are an upper bound. The seven findings that
ended as committed drafts with a successful recorded build cost
US\$\pending{PR} in total (\pending{PR\%}), and the 16 that ended as local
issue proposals US\$\pending{ISS} (\pending{ISS\%}); recognized false
positives and findings with insufficient evidence were among the cheapest,
with means of US\$\pending{FP} and US\$\pending{IE}. Cost concentrates in
findings that were rerun: the two BitBanana findings, whose stored-verifier
migration was revised repeatedly, account for US\$10.43. The snapshot did not
end the spending: \pending{K} later runs up to November 3 analyzed some of
the same findings again and bring the total to US\$\pending{ALL}. Most of
that increase went to the FreeOTP \texttt{CipherSpec} finding (US\$7.60 in
all), recorded in the snapshot as a confirmed violation without a safe fix
and later fixed by a merged pull request (FreeOTP \#524, \researchq{3}).
These costs belong to the pipeline under development; the cost per run of the
frozen configuration, on \texttt{gpt-6.1-terra} and the Flex tier, is reported
with the comparison (\researchq{6}).
% Candidatos (cognalys.llm.cost.usage_by_finding, 17/11): snapshot = runs até
% subset_bitbanana_scope6 (20/10 14:01): N=11, CALLS=569, TOTAL=35.71, MED=0.77,
% PR=11.00 (31%), ISS=17.98 (50%), FP=0.82, IE=0.42; até 3/11: K=15, ALL=51.83.
% Gerar por script versionado e citar o caminho; ajustar l.890-894 ("over all runs").

\textbf{Answer to \researchq{5}.} In the October snapshot, with
\texttt{gpt-5.6-terra} at list prices (an upper bound), analyzing a finding
cost a median of US\$\pending{MED}, and most of the spending went to findings
that ended without a pull request, mainly local issue proposals. Under the
frozen configuration used in the comparison (\texttt{gpt-6.1-terra}, Flex
tier), one run of an analysis unit cost US\$0.10 on average, or US\$0.19 at
list price.
```

### F23. Discussão: custo e "Pipeline or agent?" · linhas 1534–1569 · alta
O trecho tira conclusões do desenho antigo: "three to six times less", `tab:agents`, "quality favored the agents". O GitHub Actions é descrito como ambiente isolado, o que exagera. A frase "without credentials" está imprecisa, porque a CLI usava o próprio login.

Linhas 1534–1544:
```latex
Whether the pipeline is worth its cost depends on what a practitioner already
has. In the frozen configuration, \system{} spent US\$0.10 per run on the Flex
tier (US\$0.19 for the same tokens at list price) over 80 runs. Each run covers
web research, adjudication, planning, drafting and the behavior review, and the
same run also builds the patch and ports it to the branch tip.
\pending{cost per run of the agent arms at list price, phase 1 + port; Codex
with GPT-6.1 Sol could not be priced}
These amounts are small next to the time a maintainer spends reviewing a pull
request, so this paper reports cost only as a reference: the choice between a
pipeline and an agent turns on the quality of what is sent
(Section~\ref{subsec:rq6}).
```
Linhas 1546–1569:
```latex
\paragraph{Pipeline or agent?} \pending{one sentence with the option A result}
The agents in the comparison received the pipeline's process as instructions:
the same stages, in the same order, carried out in one conversation, followed
by a separate call that ported the patch to the branch tip. \system{} runs
each stage as a separate model call, checks its output in code before the next
stage starts, and decides whether to act before any edit is drafted.
% TODO (opção A): manter a frase seguinte só se os números mostrarem a diferença.
Since the agents had the instructions but not this execution, the difference
we observed is consistent with the staged execution and its separate decision
stage, rather than with the prompt, being what matters.
Around that judgment, \system{} maps a runtime report to the source that
produced it, joins findings in the same code, keeps the maintainers' earlier
responses, can hold out what a repository's own earlier runs and responses
taught it during an experiment, and submits every patch to deterministic
checks, a build, the port to the branch tip, and a behavior review before a
person decides to publish it. Its model never executes commands: it returns
edits as data, which let the pipeline run over dozens of third-party
repositories on one workstation. In the comparison, the agents ran on the same
workstation with restricted tools---a fixed list of tools and commands for
Claude Code and Codex's workspace sandbox---with no GitHub token or other
secret in their environment besides the CLI's own sign-in. Running an agent
with a free shell over hundreds of unknown repositories calls for stronger
isolation: a disposable container per run that sees only the repository,
reaches only the package registries and the model's API, and holds no secrets.
The two approaches are therefore complementary: \system{} can prepare a
finding and its context and decide what reaches a maintainer, while an agent
explores the code and writes the patch inside such an environment, as outlined
under Future Work. \system{} also supports locally served models through
Ollama, without web research; their quality was not evaluated here.
```

### F24. Lição sobre os gates · linhas 1588–1593 · alta
"About a dozen" não foi medido. "None ... found to be wrong" é falso: a auditoria registrou um falso bloqueio no MTG Familiar e um gate que não leu a autorização no DSub2000.
```latex
\textbf{Deterministic gates are cheap insurance, if they see the same context.}
In the consolidated October snapshot, semantic gates refused six of the 17
proposals, so none of them ended as a pull request. Two would have replaced an
algorithm a protocol mandates (MD5 for mOTP codes and HmacSHA1 for TOTP
accounts in the same one-time-password app). One would have changed a stored
derived value (RedReader's database names), and one overrode a key size the
caller chose (Droid\_SCEP). One neutralized a user option that allows insecure
connections (DSub2000, refused when the patch was carried to the branch tip).
The last reported a checked exception as not propagated (MTG Familiar). The
audit of these cases found the first four blocks correct. The exception check
had analyzed only the edited fragment and missed an enclosing \texttt{catch},
so it was a false block, although that draft had an independent defect and
would not have become a pull request either. The option gate had not read a
change the adjudication had authorized. Both were recorded as pipeline
defects. Encoding such invariants outside the model removes a class of
plausible but harmful patches at no model cost, provided each gate reads the
same context as the stage it checks.
```

### F25. Conclusão · linhas 1787–1805 · alta
A conclusão ainda traz números do estudo de 34 achados ("nine", "about one dollar") e não fala das duas avaliações. O número de respostas passa para 31.
```latex
\section{Conclusions}
\label{sec:conclusion}

This paper presented \system{}, a Python library that takes RVSec findings from
a runtime report to a reviewable contribution. For each finding it locates the
source that produced the report, decides whether the violation is real and
whether a fix keeps the application's behavior, builds the patch and carries it
to the current branch, and leaves the decision to publish with a person.

Maintainers incorporated 18 of the 49 pull requests submitted for RVSec
findings and closed 10 without incorporating them; 31 received a response.
Their reasons---behavior they consider intentional, the compatibility of
stored values, whether the rule matters in their context, a change already
made upstream, and contribution policy---are mostly application and project
knowledge that a rule cannot encode, and the pipeline now reads these
responses when it analyzes later findings of the same specification. When
RVSec reran the original and the patched APK, the reported violation no longer
appeared in the patched method for \pending{N} of the \pending{M} pull
requests it could check. In the comparison with coding agents that followed
the same process, \pending{result}.

The evidence is descriptive and drawn from small, purposive samples. It
supports \system{} as a review-oriented complement to RVSec. It does not
establish detector precision or the semantic and security correctness of the
patches: a violation that no longer appears in a rerun shows that the monitored
use changed, not that the application's behavior was preserved or that it
became safer.
```

### F26. Artifact Availability · linhas 1807–1833 · média
O texto cita `relatorio_prs_rvsec_20261108.md`, cujos números (16 merges, 22 abertos) contradizem o artigo. Também cita o diretório de 13/11. A frase sobre os pacotes `.debug` está errada. Faltam os pré-registros, a validação dinâmica, o kit da comparação e a URL.
```latex
\section*{Artifact Availability}

The replication repository (\url{\pending{URL, or "available from the author on request"}})
contains the \texttt{cognalys} package, its command-line interface and public
API, the versioned specifications and policies, the test suite, and the LaTeX
source of this paper. Timestamped execution directories hold the evidence
packets, adjudications, patch plans, prompt traces, execution reports, build
diagnostics, and local pull-request and issue previews; credentials are
excluded.

The October snapshot of 34 findings is documented in
\texttt{consolidated/README.md}, \texttt{AUDITORIA\_17\_PROPOSTAS.md}, and
\texttt{COERENCIA\_17\_FINDINGS.md} under
\texttt{outputs/exp\_final\_20261019/}, together with the BitBanana migration
report. The case inventory is in \texttt{artigo\_tcc\_latex/amostra/}:
\texttt{inventario.json} lists the findings, the pull requests, and the
manifest checks of the available APKs (read with \texttt{aapt2 dump badging},
keeping the run label and the manifest identity separately);
\texttt{findings.csv} lists the fully linked identities; and
\texttt{consolidar.py} rebuilds them. The pull-request and issue states and
maintainer responses collected on November 17, 2026 are in
\texttt{outputs/prs\_reports/20261117/} and summarized in
\texttt{docs/relatorio\_prs\_rvsec\_20261117.md}; an earlier collection of
November 13 with the same states is in \texttt{outputs/prs\_reports/20261113/}.
The responses the pipeline reads are in \texttt{data/pr\_outcomes/}.

The pre-registrations of the two evaluations and their declared deviations are
in \texttt{docs/}: \texttt{preregistro\_validacao\_prs\_20261115.md} for the
dynamic validation, and \texttt{preregistro\_bracos\_mais\_20261114.md},
\texttt{preregistro\_opcao\_a\_20261116.md}, and
\texttt{preregistro\_gabarito\_v3\_20261116.md} for the comparison, which also
record the hashes of its prompts, scripts, and answer keys. The dynamic
validation's builds, RVSec runs, and per-case results are in
\texttt{outputs/dynamic\_validation\_20261114/}. The comparison's units, agent
runs, port phase, cards, judge outputs, and tables are in
\texttt{outputs/comparison\_20261113/}, which also keeps the runs of the
earlier design. The RVSec group's campaign behind some findings is described
in \texttt{data/raw/exp\_novo/README.md}.
```

### F27. Amann et al. · linhas 193–195 · baixa
A frase sobre Amann et al. generaliza para detectores dinâmicos um resultado obtido com detectores estáticos. Troque por: `An evaluation of static API-misuse detectors also found low precision and recall~\cite{amann2019evaluation}.`

### F28. Diacríticos e DOI no `referencias.bib` · baixa
- `Kruger, Stefan` vira `Kr{\"u}ger, Stefan` nas entradas nadi2016hoops, krueger2017cognicrypt e krueger2021crysl.
- Em krueger2017cognicrypt: `G{\"o}pfert, Florian` e `G{\"u}nther, Felix`.
- Em krueger2021crysl: `Sp{\"a}th, Johannes`.
- Em torres2023runtime: `Bonif{\'a}cio, Rodrigo`.
- Em piccolboni2021crylogger, acrescente `doi = {10.1109/SP40001.2021.00010}`.
- Em zhang2023apimisuse: `title = {... Repairing {API} Misuses}` e `note = {arXiv:2310.16390 [cs.SE], preprint}`. O IEEEtran não imprime o campo `eprint`.

---

## 2. Mudanças estruturais

### S1. Abertura de Study Settings, com as quatro partes · linhas 618–627 · alta
```latex
The study has four parts with separate data and dates. (i)~A retrospective
case study of the 34 distinct findings in the consolidated snapshot of the
October 19, 2026 experiment, assembled on October 22 while the pipeline was
still changing (\researchq{1}, \researchq{2}, \researchq{4}, \researchq{5}).
(ii)~A survey of the 49 pull requests and 33 issues submitted for RVSec
findings, collected from GitHub on November 17, 2026 (\researchq{3};
Section~\ref{subsec:selection}). (iii)~A pre-registered, blind-judged
comparison of one recorded configuration of {\system} with three coding-agent
configurations instructed to follow the same process, on 20 analysis units
(\researchq{6}; Sections~\ref{subsec:frozen} and~\ref{subsec:protocol}).
(iv)~A pre-registered dynamic validation that reruns RVSec on the original and
the patched APK of the published pull requests (\researchq{7};
Section~\ref{subsec:dynval}). Cases and runs that preceded a registration, and
every deviation, are declared as such. Runs, audits, publication records, and
maintainer feedback keep separate timestamps; later events do not rewrite an
earlier build or adjudication result.
```

### S2. Objetivos · linhas 632–636 · média
```latex
The implementation objective is to complement runtime detection with
source-level classification, abstention, and reviewable repair through a
reusable library. The empirical objective has three parts: to describe what
the library decided, built, and spent on a dated set of findings, and how
maintainers answered the pull requests and issues it prepared; to check with
RVSec whether the published patches remove the reported violation; and to
compare the library with general-purpose coding agents asked to follow the
same process.
```

### S3. Definições das RQs · linhas 639–709 · alta
**RQ1** (corpo, linhas 641–646):
```latex
  It examines, in the dated case study, how repository evidence led to a
  contextual classification and a decision about whether a repair could be
  proposed. Outcomes include recognized false positives, insufficient
  evidence, compatibility constraints, and review-oriented repair or issue
  proposals. A proposal decision does not imply that a patch was generated or
  validated; those outcomes are examined in \researchq{2}.
```
**RQ4** (linhas 681–684):
```latex
  A report can be a correct rule violation and still not matter for
  security, or not hold in the source at all. We group the adjudicated
  outcomes of the case study by specification and compare them with the
  reasons maintainers gave in \researchq{3}, to identify which specifications
  most often produce reports that do not call for a security repair.
```
**RQ5** (linhas 688–690):
```latex
  We sum the model calls, tokens, and cost recorded for each finding of the
  case study, and compare them across outcomes, to show how much is spent on
  findings that end without a pull request. The cost of the comparison runs is
  reported in \researchq{6} as a reference only.
```
**RQ6** (substitui as linhas 692–700; o custo é só referência, e o porte é feito primeiro por merge de três vias):
```latex
\item[\researchq{6}:] \emph{How do the deliveries of {\system} under a fixed
  configuration compare with those of general-purpose coding agents that are
  instructed to follow the same process, and how much do the deliveries of
  {\system} vary between runs?}
  A coding agent can also read a repository, edit it, build it, and write a
  pull request or an issue. We give two coding agents, in three model
  configurations, the same findings and inputs as {\system}, a prompt that
  describes every stage of the pipeline, web search, and the build command
  {\system} uses. Each pull request an agent proposes is then carried to the
  tip of the target branch as {\system} carries its own: a three-way merge
  first and, only on a conflict, the same agent ports the patch, reports that
  the tip already fixes it, or abstains. Every arm runs four times per
  finding, and the judges are not told which arm produced each delivery. We
  report the share of acceptable deliveries, how many pull requests each arm
  proposed and how many of them were judged acceptable and built at the tip,
  how replicas of the same finding differ, and, as a reference only, the cost.
  One agent arm uses the same model as {\system}, so a difference between
  the two cannot be attributed to the model.
```
**RQ7** (substitui as linhas 702–709):
```latex
\item[\researchq{7}:] \emph{When RVSec runs again on the patched APK of a
  published pull request, does the reported violation still appear in the
  patched method?}
  A build shows that a patch compiles, and a merge shows that a maintainer
  accepted it; neither shows that the misuse RVSec observed is gone. For each
  published pull request whose finding can be traced to a method and to a
  specification RVSec checks, we build the original and the patched APK from
  the same commit, run RVSec on both, and record whether the violation is still
  reported in the patched method, whether that method was reached, and
  whether the patch removed the governed call altogether
  (Section~\ref{subsec:dynval}).
```
Corrija também a indentação da linha 692.

### S4. Tabela de dados por RQ · nova, depois da linha 733 · média
```latex
\begin{table}[H]
\centering
\caption{Data behind each research question.}
\label{tab:rq-data}
\footnotesize
\begin{tabular}{p{0.9cm}p{3.7cm}p{2.6cm}}
\toprule
RQ & Data & Units and date \\
\midrule
RQ1, RQ4 & Case study: consolidated snapshot of the October 19 experiment & 34 findings, Oct.~22 \\
RQ2 & Case study: generated solutions, previews, and builds & 11 solutions, 8 previews \\
RQ3 & GitHub survey of pull requests and issues for RVSec findings & 49 PRs, 33 issues, Nov.~17 \\
RQ5 & Prompt traces of the case study's runs & 34 findings, \pending{N} runs \\
RQ6 & Comparison with coding agents (registered in advance) & 20 units, 4 runs per arm \\
RQ7 & RVSec on original and patched APKs (registered in advance) & 33 of 49 PRs, Nov.~14--\pending{end} \\
\bottomrule
\end{tabular}
\end{table}
```

### S5. Seleção de casos · linhas 742–759 · média
```latex
\subsection{Case Selection}
\label{subsec:selection}

\subsubsection{Dated case study and pull-request survey}

The case study is the consolidated snapshot of the experiment of October 19,
2026, assembled on October 22: 34 distinct findings from 23 APKs, each
recorded with the repository and the source commit of its APK. The snapshot
joins a base run and later targeted reruns of the same findings and keeps the
latest analyzed outcome of each finding, so it holds 34 findings rather than
34 independent executions. It was produced by the pipeline as it stood in
October 2026 and is reported as a dated record of that version:
\researchq{1}, \researchq{2}, \researchq{4}, and \researchq{5} describe what
that version decided, built, and spent, not what the current version would
decide. \researchq{6} measures the frozen configuration described in
Section~\ref{subsec:frozen}.

The second source is the survey of pull requests submitted for RVSec
findings. Their states and maintainer responses were collected from the
GitHub API on November 13, 2026, and again on November 17, 2026, when no pull
request had changed state and one more had received a maintainer response;
this paper reports the November 17 collection. Inclusion requires an open PR,
an incorporated change, or closure by a maintainer. Superseded author-closed
attempts are excluded. BombusMod \#316 is distinguished as manual
incorporation: GitHub closure without a PR merge must not be counted as a
GitHub merge. Table~\ref{tab:sample} shows the resulting inventory and its
overlap with the case study.
```

### S6. Coleta de dados para RQ6 e RQ7 · linhas 896–903 · alta
O trecho atual parte da "first comparison of November 8", que foi removida. Substitua por:
```latex
\researchq{6} uses the frozen configuration and the comparison protocol of
Sections~\ref{subsec:frozen} and~\ref{subsec:protocol}. Each agent run keeps
its delivery file (pull request, issue, or no action), its diff, the build
log at the analyzed commit, the agent's event log, and its token counts. When
the delivery is a pull request, the run also keeps the second phase: the
patch ported to the branch tip, the delivery (ported, already fixed, or
abstention), the build log at the tip, and the tokens of the agent when the
patch did not apply cleanly. For {\system}, each run keeps the artifacts of
Table~\ref{tab:artifacts}. The judges' prompts and answers are kept, and the
key that maps cards to arms is kept in a separate file that the judges never
see. For \researchq{7}, each case keeps the original and the patched APK, the
build record of the patched APK, and the RVSec reports of every repetition on
both APKs (Section~\ref{subsec:dynval}).
```

### S7. Configuração congelada e unidades · linhas 923–943 · alta
Hoje há marcadores literais (`commit <commit>`, `<, with or without the draft edit loop>`). A coorte descrita é a de 45 achados, não as 20 unidades. A linha 938 contradiz a comparação. A troca das 4 FALHA não está declarada. O parágrafo da escolha do modelo vem da RQ5, já corrigido: são 8 unidades e 10 achados, e a base de preço é declarada.
```latex
\subsection{Fixed Configuration and Units of the Comparison}
\label{subsec:frozen}

The comparison runs {\system} at one fixed configuration: library commit
\texttt{fd90351}, \texttt{gpt-6.1-terra} for every stage at high reasoning
effort with a 32,768-token output limit, on the Flex tier, with every patch
built and a ceiling of US\$2 per run. Each run records an experiment manifest
(model, prompts, output schemas, policies, specifications, learning store,
the research corpus it starts from, and the code's commit), and a run counts
as this configuration only when its manifest matches. Three leak controls
apply to every run. The unit's own repository is held out: neither its
maintainers' responses to our pull requests nor what earlier runs learned in
it reach any prompt, while responses from other repositories to the same rule
remain, and the agents receive the same ones. The learning store holds
verified imports but no earlier attempt. No cached model response is reused,
except the shared, rule-level part of the web research, which is served from
the research corpus the manifest records; the part about the unit's own case
is always researched again.

The model was chosen by a descriptive check. Eight analysis units (ten
findings from six applications) were rerun with \texttt{gpt-5.6-terra}, used
until November 2, 2026, and with \texttt{gpt-6.1-terra}, released at the end
of October, under the same pipeline revision, reasoning effort, output limit,
and cost ceiling. Both made 83 calls. At list prices of US\$2 and US\$12
versus US\$1 and US\$5 per million input and output tokens, with cache writes
billed at the input price and no web-search fee, they cost US\$6.31 and
US\$2.43 (US\$2.71 for \texttt{gpt-6.1-terra} with its cache-write price and
search fee). The two models reached the same outcome in four units (five
findings), including two units that became pull requests. Of the four units
that differed, the outcome of \texttt{gpt-6.1-terra} matched our earlier
manual judgment in two: a key manager built over an empty keystore, closed as
having no security impact, and a provider-generated GCM IV, recognized as a
false positive where \texttt{gpt-5.6-terra} produced a pull request. Its one
additional pull request, which replaced certificate pinning with a trust
anchor, was not one we would publish. The sample is too small to compare
decision quality; the lower cost is what supported the change.

The 20 analysis units were drawn on November 13, 2026 with a recorded seed,
from two pools. Ten come from the published pull requests whose finding and
analyzed commit are recorded and whose original run ended with a committed,
building draft, one unit per finding, stratified by state: three
incorporated, five open, and two closed by the maintainer. The other ten come
from the 45 findings of the case study and of an earlier exploratory
comparison with coding agents, among those the pipeline analyzes again (no
pull request of ours, or one a maintainer closed), excluding any method that
also appears among the published pull requests: nine without a pull request
and one whose pull request was closed. An analysis unit is one finding, or
several findings in one method that the pipeline joins into a single patch.

Four of the 80 runs of {\system} failed because of three implementation
defects: a lost test diagnostic, a review-only label rejected by a scope
check, and a check that did not see a joined finding. The defects were fixed
at commit \texttt{3dee64a}, and those four runs, and only those, were
replaced by the same unit and replica run at that commit. Since only failed
runs received a second chance, the replacement favors {\system}; it is
declared as a deviation.
% Dados da escolha do modelo: outputs/rvsec_{AppManager_20261101,com_m57_hermescontrol_20261102,
% denaro_20261031,lite_20261024,misuse_20261016,trigger_20261028}/run_ab56 e run_ab61.
```

### S8. Protocolo da comparação na opção A · linhas 945–1013 · alta
O texto atual descreve o desenho antigo: quatro braços sem o processo, nenhum porte, o "memory stratum" (que é falso com o holdout), teste de Fisher, bootstrap com Holm e a amostra de 40 cartões com kappa ≥ 0,6. Também faltam as leituras v3, a regra 4 e o aviso de que a abstenção vale 1. A versão abaixo inclui as famílias dos juízes, a cegueira e a declaração das leituras v1/v2.
```latex
\subsection{Comparison with Coding Agents}
\label{subsec:protocol}

\emph{Arms.} Three agent arms are compared with {\system} (arm C): the Codex
CLI with \texttt{gpt-6.1-terra} (TP), the model {\system} uses; Claude Code
with Claude Sonnet 5.5 (SP2); and the Codex CLI with \texttt{gpt-6.1-sol}
(OP). All run at high reasoning effort, four times per unit. TP and C share
the model, so their difference reflects where the process is carried out (in
the instructions to one agent session, or in separate stages with
deterministic checks between them) rather than the model. A partial run of
{\system} with Claude Sonnet 5.5 (17 of 40 planned runs, commit
\texttt{fd90351}) is kept in the artifact and is not compared.

\emph{Phase 1: analysis and patch.} Each agent run starts from a fresh
checkout of the analyzed commit, without a remote or credentials, and from a
fresh configuration without memory, with a 60-minute limit. Its prompt gives
the RVSec report lines, the published rule behind each one, and the responses
of other repositories' maintainers to the same rule, the inputs {\system}
reads; nothing comes from the unit's own repository. The prompt then
describes, in the pipeline's order, each stage of {\system}: evidence
collection, research, the decision on whether the violation is real and
matters, planning, drafting, the checks, the behavior review, redrafting and
replanning, the build, and the text. It asks for exactly one delivery (a pull
request with its description, an issue, or a note that nothing should be
sent), provides web search and the build command {\system} uses, and states
the text rules {\system} applies to its own proposals. The SP2 and OP prompts
omit one request of the TP prompt, to keep stage-by-stage working notes in a
file, because the model's safeguards in Claude Code stopped most runs that
received it. A prompt cannot reproduce separate model contexts per stage,
validators and retries enforced by code, or the research corpus; the
stage-by-stage mapping is part of the replication package.

\emph{Phase 2: port to the branch tip.} {\system} carries each pull request
to the tip of the target branch before it can be published
(Section~\ref{sec:pipeline}); the agent arms do the same in a second phase.
Each unit has one tip, taken from the {\system} runs, to which every agent
arm ports. The phase-1 patch is first applied with a three-way merge, as
{\system} does, and a clean result is built at the tip without the agent. On
a conflict, the same agent, model, and effort receive a port prompt and
report the patch as ported, already fixed at the tip, or abandoned.
``Already fixed'' stands only when a deterministic check finds the added
lines present at the tip and the removed lines absent; otherwise it counts as
abandoned, as in {\system}. A ported patch is built at the tip unless the
agent already built that diff. The tokens of both phases count toward the
arm's cost.

\emph{Cards and judges.} Each delivery becomes a card with its kind, title,
text, and diff against the analyzed commit, and whether its author built that
diff; signatures, internal identifiers, tool names, and pipeline vocabulary
are removed, and a lint rejects any card that still carries them. Two model
judges, Claude Opus 5.5 through Claude Code and \texttt{gpt-6.1-sol} through
Codex, at high effort, without web access, and with a read-only checkout of
the analyzed commit, score every card of a unit in random order, since the
order of candidates sways model judges~\cite{wang2024fair}: the patch from 0
(should be rejected) to 3 (mergeable as is), the accuracy of the text from 0
to 2, and the reason given for an issue or an abstention from 0 to 2. A
card's final score is the one both judges give, or the principal judge's
(Opus) when they differ. Model judges tend to favor their own
generations~\cite{panickssery2024selfpref}, and here GPT-6.1 Sol is the model
of OP and comes from the provider of the model TP and {\system} use, while
Claude Opus 5.5 comes from the provider of SP2; the scores of each judge are
therefore kept and reported beside the final ones. The judges score against a
written reference per unit: what an acceptable patch must change and must not
break. It is the third version of a reference the judges drafted from the
code and the author fixed. That version was revised after earlier readings
had been seen, to remove verdicts that contradicted the delivery policy and
constraints that required one mechanism over an equivalent one, and it
contains no maintainer response. For C, TP, and the first two SP2 runs, the
cards were also scored under the original and the second version; those
readings, the reason for each change, and every version are kept with the
data, and OP and the later SP2 runs were scored only under the third.

\emph{Acceptability.} A run is acceptable when it delivers an issue or an
abstention, or a pull request whose patch scores at least 2 and text at least
1, and that was built at the tip after the port; a build failure that the
unmodified code shares is not charged to the patch. A pull request whose port
found the fix already at the tip, or was abandoned, is never sent, so it is
judged on its patch alone (at least 2); this rule was set after the results of
the third reference had been seen and is declared as a deviation. A failed run
counts 0. The measure of an arm is the share of acceptable runs per unit,
averaged over units. In a second, patch-only reading, the judges see each pull
request's diff and whether it was built, without its title and description; a
pull request is then acceptable with a patch score of at least 2 and a build,
and issues and abstentions count as acceptable without being judged. For
{\system}, a pull request is any committed, building draft, including one the
pipeline would hold back. Because an issue or an abstention always counts as
acceptable, the measure rewards abstention: an arm that never proposes a
change scores 1. We therefore report beside it how many pull requests each arm
proposed and had judged acceptable, and in how many units it produced at least
one such pull request.

\emph{Blinding.} A separate session of the principal judge, given one card at
a time, tries to name the arm behind it, on full cards and on cards showing
only the patch, and reports how often it was right and how often each guess
was right. The probe was run on the earlier design's cards and, before any
score was read, on 10 cards per arm of TP, SP2, OP and {\system} in each view
(declared on November 17, 2026).

\emph{Cost and analysis.} Cost is a reference, not a criterion: the agents
ran on subscriptions and are priced at list prices, including the web search
fee, where the model has a published price (\texttt{gpt-6.1-sol} had none, so
OP is reported without a cost), and {\system} at what was billed on the Flex
tier and at list price for the same tokens. All comparisons are descriptive:
with 20 units and four replicas, no statistical test is applied; we report
per-judge scores, the judges' agreement (Cohen's kappa), and differences as
observed. RVSec does not enter the scores, since only {\system}'s published
patches were rerun (\researchq{7}).
```

### S9. Subseção de método da validação dinâmica · nova, depois de S8, antes de `\section{Results}` · alta
```latex
\subsection{Dynamic Validation of Published Pull Requests}
\label{subsec:dynval}

\researchq{7} reruns RVSec on the patches that reached maintainers. Its
procedure was registered on November 15, 2026, before any new case was built
or run. It extends a pilot on six findings, and the extension was decided
after the pilot's results were known. The population is the 49 pull requests
of Table~\ref{tab:pr-state}. Sixteen are excluded, each with its recorded
reason: the specification is outside the cryptographic and TLS set RVSec
checks (three), the finding has no recorded identity or pipeline metadata,
the recorded message has no matching detector line at the reported method, no
build produced the APK RVSec analyzed, or the pull request addresses a
different finding from the one validated. The remaining 33 are 28 new cases
and five pull requests validated in the pilot with the same RVSec settings and
three repetitions (the pilot used a fixed JDK; the new cases choose it as the
pipeline's builds do).

For each case, the original and the patched APK are built from the same
commit with the same Gradle task and without minification. The patch is the
pull request's public diff applied at its merge base, or the pipeline's diff
at its base commit when the two are identical. RVSec runs on each APK in a
clean emulator for 600 seconds, with Monkey and APE driving the interface and
its cryptographic and TLS specifications active. Every case gets one
repetition; a case that is inconclusive or whose original APK does not
reproduce the finding gets a second one, time permitting, after all cases
have their first. A build that fails is retried once with the same recipe and
then reported as not built, and a patch that does not apply is reported the
same way, without editing the application's code. Each case receives one label
by a rule fixed before the runs. When the original APK shows no violation of
the specification at the method in any repetition, the case is \emph{base does
not reproduce} and is left out of the count. Otherwise it is \emph{not
corrected} when a violation of the specification is reported in the patched
method in any repetition, \emph{corrected} when the patched method executes
and no such violation is reported, and \emph{inconclusive} when the patched
method never executes. Results are stated as ``the reported violation no
longer appears in the patched method'', not as ``RVSec no longer reports the
application''. Cases not finished by November 18, 2026 are reported as not
executed.

Two deviations were declared after results were seen. When the patched method
never executes because the patch removed the governed API call from its file
without adding another, the case is labeled \emph{corrected (call removed by
the patch)}; the original rule would give \emph{inconclusive}, and both labels
are kept. The FreeOTP finding came from a campaign of the RVSec group in which
only DroidBot and other explorers reached the reported method, so its two pull
requests are also run with DroidBot, the explorer of the original report, for
300 seconds and three repetitions; the Monkey and APE results are reported as
well. A run interrupted by a restart of the machine was repeated from the
start.
```

### S10. Guia de leitura no início de Results · linha 1015 em diante · alta
```latex
\section{Results}
\label{sec:results}

The results draw on four bodies of evidence with different dates, and each
subsection states which one it uses. \researchq{1}, \researchq{2}, and
\researchq{4} report the October 2026 snapshot: the 34 findings of the
experiment of October 19, 2026, each with the outcome of its latest run up to
October 20, assembled into the case inventory on October 22. Those outcomes
come from runs of several revisions of the library with \texttt{gpt-5.6-terra}
on the standard tier, so the snapshot predates the frozen configuration of
Section~\ref{subsec:frozen} and several routing rules of
Section~\ref{sec:pipeline}. It is a dated case study of an earlier version,
not a measurement of the current one. \researchq{5} sums the model usage of
the same findings over the runs recorded for them. \researchq{3} reports the
pull requests and issues as they stood on November 17, 2026; \researchq{6}
reports the comparison of Section~\ref{subsec:protocol}; and \researchq{7}
reports the RVSec reruns of the published pull requests, made from November 14
to \pending{end date}, 2026.
```

### S11. Resultados da RQ2 só com o snapshot · linhas 1067–1097 · alta
A frase do "TLS replay" (linhas 1088–1089) sai: não é definida em lugar nenhum e testa outra propriedade. A reexecução do RVSec vai para a RQ7.
```latex
\subsection{\researchq{2}: Application and Build}
\label{subsec:rq2}

The 17 proposal decisions of the snapshot yielded 11 generated solutions.
Eight became local pull-request previews and three were not applicable to the
current base (\researchq{1}). Seven previews have a commit and a successful
build, but only six of these builds produced an APK; for Feeder, only the
application module's sources were compiled. The eighth, Metadata Remover, has
no successful build: Hilt's annotation processing fails with the same error on
the unmodified commit, so the failure is not attributed to the patch.

The audit of the previews found concerns that no build reveals. The
BetterUntis patch changes the names of persisted cache files without stating
that compatibility decision; the PassNotes patch keeps an accept-all hostname
verifier in its debug branch; and Feeder's \texttt{trustAllCerts} validates
certificates after the patch while keeping its name. In BitBanana an early
candidate compiled while it omitted credential readers affected by the new
stored verifier; the delivery of October 20, which spans a helper and six
credential screens, keeps legacy verification and replaces a stored verifier
only after a successful unlock, and also built an APK. No flow of the
application was tested. Whether the reported violation disappears at runtime
is examined for six of these previews and for the published pull requests in
\researchq{7}.

\textbf{Answer to \researchq{2}.} In the October snapshot, seven of the eight
pull-request previews applied and built, six of them to an APK, and the eighth
fails on the unmodified commit as well. The build did not reveal the
compatibility, debug-branch, and naming concerns the audit found in three
previews.
```

### S12. Resultados da RQ6 refeitos para a opção A · substitui as linhas 1198–1391 · alta
Sai tudo o que vem do desenho antigo: `tab:agents`, `tab:replicas` e `tab:blind` (commit 8be9f78, braços S/O/T, braço D), a linguagem de teste ("candidate losses", "confirmed by the test"), as causas pela leitura do protocolo k1, os custos antigos, as perdas NotallyX/Droid_SCEP do k1 e a "Answer to RQ6" antiga. Também é preciso tirar os `\ref{tab:agents}` e `\ref{tab:blind}` que sobrarem (linhas 1537, 1642 e 1763 são cobertas por F23, S15 e S16). Os valores do C são medidos; os dos agentes ficam em `\pending`.
```latex
\subsection{\researchq{6}: Comparison with Coding Agents and Run-to-Run Variation}
\label{subsec:rq6}

The comparison of Section~\ref{subsec:protocol} ran on 20 analysis units:
nine findings without a pull request of ours, three whose pull request a
maintainer had closed, and eight whose pull request is open or was
incorporated. Until its decision and its draft, no arm saw the unit's
repository after the analyzed commit, or its pull requests, issues, or
maintainer responses; only the port of phase 2 works on the branch tip, as
{\system} itself does when it merges a patch there. Every arm received the
published rule behind each report and the maintainer responses to earlier
proposals for the same rule in other repositories.
Table~\ref{tab:optiona} gives each arm's deliveries at the analyzed commit,
what became of each pull request at the branch tip, and the two readings of
the judges. {\system} ran at library commit \texttt{fd90351}; four of its 80
runs failed on implementation defects of the pipeline, since fixed, and were
replaced by the same unit and replica run at commit \texttt{3dee64a}.

\begin{table}[H]
\centering
\caption{\system{} and three coding agents that follow the same process, on
20 RVSec findings, four runs per unit. Acceptable runs are averaged over
units; an issue or a note that nothing should be sent counts as acceptable.
Cost is a reference, not a criterion: list price for every arm (the agents ran
on subscriptions); \system{} was billed US\$0.10 per run on the Flex tier.}
\label{tab:optiona}
\footnotesize
\setlength{\tabcolsep}{2pt}
\begin{tabular}{p{2.9cm}rrrr}
\toprule
 & \system{}
 & \begin{tabular}[b]{@{}r@{}}Codex\\Terra\end{tabular}
 & \begin{tabular}[b]{@{}r@{}}Claude\\Sonnet\end{tabular}
 & \begin{tabular}[b]{@{}r@{}}Codex\\Sol\end{tabular} \\
\midrule
Runs & 80 & 80 & 80 & 80 \\
PR / issue / nothing & 31/10/39 & \pending{} & \pending{} & \pending{} \\
Units with a PR (of 20) & 9 & \pending{} & \pending{} & \pending{} \\
Median lines per PR & 8 & \pending{} & \pending{} & \pending{} \\
\multicolumn{5}{l}{\emph{Pull requests at the branch tip}} \\
\quad Ported and built & 20 & \pending{} & \pending{} & \pending{} \\
\quad Already fixed or code gone & 11 & \pending{} & \pending{} & \pending{} \\
\quad Abstained or failed & 0 & \pending{} & \pending{} & \pending{} \\
\multicolumn{5}{l}{\emph{Judges}} \\
\quad Acceptable, author's reading & 0.96 & \pending{} & \pending{} & \pending{} \\
\quad Acceptable, patch reading & 0.93 & \pending{} & \pending{} & \pending{} \\
\quad PRs acceptable, author's / patch & 28 / 25 & \pending{} & \pending{} & \pending{} \\
Cost per run, list (US\$) & 0.19 & \pending{} & \pending{} & \pending{} \\
\bottomrule
\end{tabular}
\end{table}

Of the 31 pull requests of \system{}, 20 were ported to the branch tip and
built there. In 11 the tip already held the fix or no longer had the code
(AMARadio, ANOTHERpass, and NotallyX), and these are judged on their patch
alone. Counting only the 29 that \system{} would publish, the readings are
0.95 and 0.91. Of the agents' pull requests, \pending{n} applied cleanly at
the tip, \pending{n} were ported by the agent after a conflict, and
\pending{n} ended as already fixed or abstained.

The share of acceptable runs rewards abstention: an issue or a note that
nothing should be sent is acceptable whatever its reason, so an arm that never
proposed a change would score 1.0 on every unit where it did not fail. We
therefore read it beside the number of pull requests each arm proposed and how
many the judges accepted. \pending{one sentence with the measured difference
in accepted and refused pull requests between \system{} and each agent arm}
\system{} and the Codex Terra arm used the same model, the same inputs, and the
same rules; they differ in how the process is executed. \system{} runs each
stage as a separate model call over its own input. It settles whether the
violation is real and whether a safe fix exists before any draft is written,
keeps the extraction hidden from adjudication and the plan's invariants from
the drafter, and enforces its checks and retries in code. The agent follows
the same stages in one conversation and checks its own work.
% TODO opção A: manter a frase seguinte só se os números a sustentarem.
The difference therefore comes from staged execution with a separate decision
stage, not from the instructions, which both arms had.
At list price, \system{} cost US\$0.19 per run, including its web searches and
its port to the branch tip, and \pending{TP}, \pending{SP2}, and \pending{OP}
for the three agent arms, each including its port; cost is reported as a
reference and was not a criterion.

\paragraph{Run-to-run variation.} Arm C ran each of the 20 units four times
without the response cache. The class of delivery (pull request, issue, or no
action) was the same in all four replicas for 16 of the 20 units, and the
recorded outcome for 11. In four units the class changed: mOTP's HMAC and
Salty's key derivation moved between a pull request and no action, and
Winterhold's key derivation and ReadYou's TLS setup between a pull request and
an issue. In five units, only the reason for the outcome changed, for example
between no safe fix, an external contract, and no security impact, and
NotallyX moved between a fix the tip already held and a building pull request.
The post-fix round ran all four replicas of the three units that had failed;
its eight runs that did not replace a failure repeated the mOTP and Winterhold
splits, and NotallyX ended as already fixed in all four. The billed cost of a
unit varied up to 3.8 times across its replicas (ReadYou, US\$0.074 to
US\$0.28; the most expensive replica ended as an issue).
\pending{the same counts for the three agent arms} A single run is therefore a
sample of a decision, not the decision, and differences of one or two units
between arms are within this variation.

\paragraph{Judges and blinding.} The judges agreed on acceptability with
$\kappa = $\pending{} and on the patch score with a quadratic-weighted
$\kappa = $\pending{} under the author's reading. In the probe of the
earlier design, on full cards the principal judge named \system{} for all 8
of its cards but also for 17 of the 32 cards of other arms, at a mean
confidence between 0.28 and 0.35, so naming \system{} was a default guess
rather than an identification; on cards showing only the patch it named
\system{} for 1 of 10. \pending{same probe on the cards of TP, SP2, OP and
\system{}: per arm, correct arm, share of guesses for each arm, precision of
each guess (pre-registered 2026-11-17)}

\textbf{Answer to \researchq{6}.}
\pending{given the same findings, inputs, and rules, and, for Codex, the same
model as \system{} (\texttt{gpt-6.1-terra}), the coding agents proposed X pull
requests and had Y judged acceptable, against 28 of 31 for \system{} (27 of the
29 it would publish)}
Because the author's reading counts abstention as acceptable, the comparison
rests on accepted and refused pull requests rather than on the share of
acceptable runs. \pending{if confirmed: the difference comes from executing the
process in separate stages, with the decision to act taken before any draft,
rather than from the instructions.} Across its four runs per finding,
\system{} delivered the same kind of result in 16 of 20 units.
```

### S13. Resultados da RQ7, subseção nova depois da RQ6 · alta
Os números parciais ficam só no comentário. As exclusões por estado (6/3/7) já estão medidas.
```latex
\subsection{\researchq{7}: RVSec on the Patched APKs}
\label{subsec:rq7}

For six of the snapshot's previews, with three repetitions each, the reported
violation no longer appeared in the patched method in five: Tokn, Traffic
Light, Network Survey, BetterUntis, and PassNotes, whose original APK showed it
in one of three repetitions. BitBanana was inconclusive: its method runs only
when an app lock is set up, which the explorers reached in one of three runs
of the original APK and in none of the patched one. Table~\ref{tab:dynval}
reports the 33 published pull requests included under the procedure of
Section~\ref{subsec:dynval}, by state.

\begin{table}[H]
\centering
\caption{RVSec rerun on the original and the patched APK of the 49 published
pull requests, by state on November 17, 2026. ``Corrected'': the reported
violation no longer appeared in the patched method (including patches that
removed the governed call).}
\label{tab:dynval}
\footnotesize
\setlength{\tabcolsep}{3pt}
\begin{tabular}{lrrrrrr}
\toprule
 & Corr. & Not corr. & Incon. & No repro. & Not checked & Total \\
\midrule
Incorporated & \pending{} & \pending{} & \pending{} & \pending{} & 6 & 18 \\
Closed       & \pending{} & \pending{} & \pending{} & \pending{} & 3 & 10 \\
Open         & \pending{} & \pending{} & \pending{} & \pending{} & 7 & 21 \\
\midrule
Total        & \pending{} & \pending{} & \pending{} & \pending{} & 16 & 49 \\
\bottomrule
\end{tabular}
\end{table}
% Parcial 17/11 (results_prs.md + 14/11): Incorp 8/2/0/2/6; Fechado 5/1/1/0/3;
% Aberto 8 (1 = chamada removida, prim-ftpd)/3/2/1/7; Total 21/6/3/3/16.
% Pendentes: ReadYou, InterTune (rep 2), FreeOTP x2 (DroidBot), your-local-weather (rep 2).
% Se houver "não compilou" ou "não executado" no corte, acrescentar coluna.

Incorporation and the runtime re-check diverge in both directions. Some
incorporated pull requests did not remove the reported violation:
ShortMessageSecure \#83 kept \texttt{HmacSHA1} at the maintainer's request,
so the key-generator rule is still violated, and in ANOTHERpass \#103 the
KeyStore call sequence is still reported. In Fread \#168 and zSMTH \#66 the
reported protocol message disappeared while other messages of the same
specification remained at the same call, and in Grocy \#1012 messages of the
same specification remained; the pre-registered rule counts all three as not
corrected. Conversely, the reported violation disappeared in pull requests
that maintainers closed, such as whoBIRD, rssreader, RedReader, BetterUntis,
and Network Survey: satisfying the monitor did not make these changes ones the
maintainers wanted. In Amethyst \#4642, closed because the provider already
generates a random IV and because the patch also changed the desktop
implementation, the message remained after the patch.
\pending{FreeOTP \#521/\#524 rerun with DroidBot: result}

\textbf{Answer to \researchq{7}.} Rerunning RVSec on the published pull
requests, the reported violation no longer appeared in the patched method in
\pending{X} of the 33 that could be checked and still appeared in
\pending{Y}; \pending{Z} were inconclusive or did not reproduce on the
original APK. A build, the disappearance of the reported violation, and a
maintainer's acceptance are separate properties, and the cases show each
without the others.
```

### S14. Principal Findings · linhas 1396–1420 · alta
O texto atual é genérico e repete as Lessons. Não menciona nenhuma das duas avaliações.
```latex
\subsection{Principal Findings}

Four observations summarize the study. First, maintainers judged the findings
in context. Of the 49 pull requests submitted for RVSec findings, 18 were
incorporated, 10 were closed after review, and 31 drew a response
(Section~\ref{sec:results}). Their reasons for declining were behavior they
consider intentional, the compatibility of stored values, and whether the rule
matters in their context. Neither the rule nor the reported call contains that
knowledge, and in this study a reviewable change drew it out more often than
an early question did (Section~\ref{sec:lessons}); the groups differ in time
and content, so this comparison is descriptive.

Second, a violation can be real and still not call for a security repair. In
the case study of the 34 findings consolidated in October, reports without
security impact concentrated in message digests and key stores, where the
deciding question is what the value is used for (\researchq{4}). Six of the
ten pull requests closed by maintainers replaced MD5 or SHA-1 that, in the
maintainer's view, served as a checksum, a cache name, or another value
without a security role.

Third, a patch that builds is not yet a patch that removes the violation. When
RVSec reran the original and the patched APK of the published pull requests,
no violation of the reported specification appeared at the patched method in
\pending{N\_CORR} of the \pending{N\_VAL} cases it could check, including
\pending{N\_REM} where the patch removed the call; a violation of the same
specification still appeared in \pending{N\_NAO}; in \pending{N\_INC} the
exploration did not reach the method, and in \pending{N\_BASE} the original APK
did not reproduce the report (Section~\ref{subsec:rq7}). Among the cases where
a violation remained, \pending{N\_OUTRA} showed a different constraint of the
same specification after the reported one disappeared, and \pending{N\_MANT}
kept a choice the maintainer had asked to preserve.

Fourth, \pending{option A result in one sentence}; when taking no action is
acceptable, an arm that abstains more scores higher, so the share of
acceptable runs is read together with the number of pull requests each arm
proposed and how many of them the judges accepted.
% TODO (opção A): manter a frase seguinte só se os números confirmarem.
Since the agents received the same process as instructions, the difference
points to how the process is executed (separate stages, each checked by code,
with a decision before any edit) rather than to the instructions themselves.
```

### S15. Ameaças à validade · linhas 1630–1735 · alta
Substitua os parágrafos indicados.

**Evolving implementation** (linhas 1630–1637):
```latex
\emph{Evolving implementation.} The pipeline, prompts, output schemas, and
semantic gates changed during the study as problems were found. The October
snapshot of 34 findings is therefore a dated case study: its outcomes were
produced by several versions of \system{}, only the latest result per finding
is kept, which avoids counting retries as independent cases, and its outcome
distribution should not be read as the performance of one configuration. The
published pull requests also came from different versions, and some were
revised during review. Only the comparison with coding agents used a single
recorded configuration (Section~\ref{subsec:frozen}).
```
**Model nondeterminism** (linhas 1639–1650):
```latex
\emph{Model nondeterminism.} Adjudication and patch generation depend on
language models whose answers vary between calls and between model versions.
In the comparison configuration, each of the 20 units was analyzed four times
without the response cache (76 runs at one commit; the four runs that had
failed on pipeline defects were rerun after those defects were fixed).
\system{} reached the same outcome class (a pull request, including a patch
whose fix the target branch already held, an issue, or no action) in all four
runs for 16 units. In the other four it alternated between a pull request and
an issue in two units and between a pull request and no action in two, and
each of these four units varied even among the runs made at the same commit
(Section~\ref{subsec:rq6}). The decision changes recorded across the reruns of
the October case study may therefore reflect model variation as well as
changes to the pipeline or its policies. Cached responses and versioned
evidence packets make each recorded decision auditable, and the semantic gates
and the build step reject unsafe or non-compiling proposals regardless of what
the model answers, but they do not make the decision itself reproducible.
```
**Researcher judgment** (linhas 1652–1657):
```latex
\emph{Researcher judgment.} The same researcher operated the pipeline, chose
which findings to rerun and which proposals to publish, fixed the comparison's
references, and classified outcomes such as false positive or lack of
security impact. Reruns were concentrated on findings that seemed close to a
pull request, which may favor proposal outcomes. Some analysis rules were set
after results were seen and are declared as deviations from the
pre-registrations: the second and third answer keys of the comparison and its
rule for fixes already on the branch tip, and the dynamic-validation label for
a patch that removes the governed call. Each is reported beside the
pre-registered reading. Maintainer responses offer an independent check only
for the published cases.
```
**Comparison** (substitui "Agent comparison" e "Blind comparison", linhas 1659–1674). Este é o único lugar em que o desenho anterior é mencionado, conforme a decisão 1.
```latex
\emph{Comparison with coding agents.} An earlier design of this comparison, in
which the agents either did not receive the pipeline's process or stopped at
the analyzed commit without carrying the patch to the branch tip, gave them a
smaller task than \system{} and was replaced before the results reported here;
its runs and scores remain in \texttt{outputs/comparison\_20261113/}, beside the
pre-registration of the current design. In the current design, the 20 units
were drawn with a recorded seed from this study's own pull requests and from
the findings of the case study, so they represent this study's findings, not
Android cryptographic misuses in general. The judges are language models; the
principal judge, Claude Opus 5.5, comes from the provider of the Claude Code
arm and decides when the judges disagree, and GPT-6.1 Sol is the model of one
Codex arm and comes from the provider of the model that \system{} and the
other Codex arm use, so a judge may favor its own family; no person scored the
cards. The reference was revised twice after earlier results were seen, and
the scoring of pull requests whose fix the branch tip already holds was
changed after the third version's results. Because an issue or no action is
acceptable in every unit, the measure rewards abstention. Blinding was
partial: the probe named \system{} for all 8 of its full cards, but also for
13 of the 24 cards of the earlier design's agent arms, and for 1 of 10 of its
cards when it saw only the patch. Four failed runs of \system{} were replaced
by runs made after their defects were fixed, a second chance the other runs
did not have. The agents' prompt was written in this project from the
pipeline; it reproduces the pipeline's stages but not its separate contexts,
the checks and retries its code enforces, or its research corpus. Claude Code
with Claude Sonnet 5.5 refused the version that asked for a written record of
each stage, so two arms used a prompt without that request. The agents ran on
subscriptions, on the same workstation as \system{}, with a 60-minute limit per
run; Codex's usage limit paused one arm several times, and no list price was
available for GPT-6.1 Sol, so that arm's cost is not reported.
```
**Construct validity** (linhas 1685–1699):
```latex
The initial observations are rule violations, not confirmed exploitable
vulnerabilities. We use \emph{finding} or \emph{misuse} and separate a
detector violation from runtime security impact. Build success measures
compilation under a recorded task; it does not measure functional correctness,
security, or maintainer acceptance. No person labeled every decision and
patch, and the comparison's judges are language models, so the results
establish feasibility and reveal failure modes rather than precision or recall.

The specification registry is another source of construct risk. Rules can be
incomplete or mismatched with Android providers, and RVSec's monitors were
translated by hand from CrySL rules, so a wrong rule or interpretation can
still shape the evidence requirements. The dynamic validation measures
something narrower than a fix: whether the reported violation stopped
appearing in the patched method. A patch that only moved the call out of the
monitor's sight would pass it, and a correct patch can still be counted as not
fixed when the specification has other constraints the code does not meet at
the same method. Only the published pull requests were rerun; the patches of
the case study and of the comparison were not, so the judges' scores leave
RVSec out rather than apply it to one side only. The patched APK applies the
pull request's diff to the commit the pipeline built on, or to the pull
request's merge base, not to the code the maintainers merged.

The validation also depends on exploration. Each new case ran once with
Monkey and APE, each for 600 seconds, with a second run only for cases that
were inconclusive or did not reproduce the violation; the five cases validated
earlier ran three times. A case counts as fixed only when the original APK
shows the violation and the patched APK runs the reported method, unless the
patch removed the governed call. The FreeOTP finding came from a campaign of
the RVSec group in which only DroidBot, Humanoid, and ARES reached the method;
both FreeOTP cases were rerun with the two DroidBot strategies of that report,
while Humanoid and ARES could not be run in our environment.
\pending{FreeOTP with DroidBot: result}
Finally, the label for a patch that removes the governed call, leaving nothing
to execute, was added after results were seen; the pre-registered label is
reported beside it.
```
**External validity.** Depois de `...counts as a detector comparison.` (linha 1712), acrescente:
```latex
The dynamic validation covers 33 of the 49 pull requests: 16 were excluded
before any run, because their specification is outside the cryptographic set
RVSec checks, their finding lacks a complete identity or a detector line at
the reported site, or no build recorded which APK RVSec had analyzed. The
comparison uses 20 analysis units drawn from this study, not a sample of RVSec
findings.
```
E substitua as linhas 1718–1722, que estão em jargão interno, por:
```latex
Results may not generalize to proprietary applications, other languages, or
other API families; three of the 49 pull requests concern resource-handling or
synchronization rules rather than cryptography. \system{} accepts findings
from other detectors in supported formats, but no such input is part of the
counts reported here.
```
**Conclusion validity** (linhas 1724–1735). A versão de um dos achados dizia "fixed before reading any delivery", o que é falso; está corrigido abaixo.
```latex
\subsubsection{Conclusion validity}

The observations support descriptive counts and case explanations, not
inferential effectiveness claims, and no statistical test is applied. The 34
findings of the October case study, the published pull requests, their dynamic
validation, and the comparison with coding agents have different denominators
and partially overlap. Incorporation does not establish security correctness,
and maintainer closure does not establish patch incorrectness. In the dynamic
validation, the absence of a report on the patched APK counts only where the
original APK reproduces the finding under the same exploration, mostly in a
single repetition; cases that do not reproduce or remain inconclusive are
reported as such. The comparison covers 20 units with four runs per arm; two
language models score each delivery against references the author fixed and
later revised after earlier results, and the scoring gives an issue or an
abstention a fixed credit, which favors abstaining arms. Differences between
arms are reported as observed, without a claim that they would generalize.
```

### S16. Trabalhos futuros · linhas 1739–1785 · média
Três itens dados como futuros já foram feitos: a configuração congelada, as réplicas e a reexecução do detector. O argumento de "cached rate" vem do desenho antigo. Além disso, as respostas dos mantenedores já são lidas pelo pipeline.

Linhas 1739–1754:
```latex
A confirmatory study should fix its hypotheses before collecting data and draw
findings at random from RVSec campaigns rather than from the cases this study
analyzed. The frozen configuration, repeated runs, and detector rerun used here
can be reused. What this study lacks are two independent human security
reviews per case, labeling detector correctness, cause, required scope,
decision, patch semantics, and publication readiness, so that the precision of
supported-fix decisions, the unsafe-fix rate, and the appropriateness of
abstention can be measured. The detector rerun should use several repetitions
per APK and the explorers of the original report.

On the detection side, the main obstacle we observed is reachability:
automated exploration rarely reaches cryptographic code behind login,
configuration, or user-created data, so many monitored misuses are never
reported, and a patch whose method is not reached cannot be checked. Guided
exploration that prioritizes screens related to the monitored specifications
could raise the number of findings in application code and the number of
patches the rerun can confirm.
```
Linhas 1756–1775:
```latex
The pipeline could also be offered to coding agents. Its checks---the semantic
validation of a draft, the build, the port to the branch tip, and the cost
ceiling---could be exposed as tools, packaged as an agent skill or a tool
server, so that a general-purpose agent explores the repository and proposes
the change while the tools decide whether a delivery may proceed. Such a
design should keep the stage that judges a finding separate from the one that
fixes it and keep publication human-authorized.
% TODO (opção A): se confirmado, "In the comparison of Section~\ref{subsec:rq6},
% agents given the process as instructions did not reproduce that separation."
Whether it preserves decision quality, and at what cost, is an empirical
question for the comparison protocol of Section~\ref{subsec:protocol}. The same
core could also run inside a project's own continuous integration, as a GitHub
Action that analyzes the RVSec findings of each new build and attaches the
proposed change, or the reason for not proposing one, to the pull request or
commit that introduced the misuse, so that the maintainers review it where
they already work. Running RVSec there requires an Android emulator in the CI
environment, which makes exploration the main cost of that setting. The
library would remain the core: an agent skill, a tool server, and an action
are layers over it.
```
Linhas 1777–1785:
```latex
Further technical work includes classpath-aware semantic resolution for Java
and Kotlin, stronger Gradle variant discovery, containerized Android build
environments, and targeted runtime tests. For TLS cases, the rerun showed that
a patch can remove the reported protocol name while RVSec still reports the
null key managers, random sources, or custom trust managers passed to
\texttt{SSLContext.init}; the pipeline should plan against every constraint of
the specification at the reported call and say in the pull request which ones
it leaves unchanged and why. For digest and key-derivation changes, it should
model stored formats and coordinate migrations across server and client
repositories. Maintainer responses already reach adjudication, planning, and
the behavior review for findings of the same specification; measuring how
much they change later decisions, with a repository's own responses held out,
is left for future work.
```

---

## 3. Conteúdo a acrescentar (fundamentação só com referências conferidas)

### C1. Substituir "From Detection to Automated Repair" · linhas 204–223 · alta
A subseção atual repete a introdução e não fala de nada que o trabalho usa. A nova versão cobre o overfitting ligado à validação dinâmica, os agentes, as contribuições de ferramentas e os LLMs como juízes. Usa só referências conferidas.
```latex
\subsection{Automated Repair and Coding Agents}
\label{subsec:apr}

Automated program repair (APR) produces candidate patches for a reported
defect and keeps those that an oracle accepts, usually the project's
tests~\cite{legoues2019apr}. A patch can satisfy the oracle it was checked
against without fixing the defect, a problem known as patch
overfitting~\cite{smith2015overfitting}. \system{} has no test that encodes
the misuse: a patch is accepted when it passes the deterministic checks and
the behavior review, applies, and builds, and none of these observes whether
the violation is gone. The evaluation therefore asks the detector again: RVSec
monitors the original and the patched APK of each published pull request whose
finding and build could be reconstructed, and records whether the reported
violation still appears at the patched method (Section~\ref{subsec:dynval}).
That check is also indirect, because a report also disappears when the
exploration no longer reaches the patched code; a missing report therefore
counts as a fix only when the method was executed, or when the patch removed
the governed call from it. The behavior review is meant to reject the opposite
failure before publication, a patch that changes the code but not what the
monitor observes (Section~\ref{sec:pipeline}); the dynamic validation shows
whether such patches were still published.

Large language models changed how candidate patches are produced. Pretrained
models repaired more benchmark defects than earlier APR
tools~\cite{xia2023apr} and have been evaluated on API-misuse
repair~\cite{zhang2023apimisuse}. These approaches take the reported defect as
given. The maintainers who answered our pull requests questioned exactly that
premise---whether the violation matters in their code and what the change
would break (Section~\ref{sec:results})---which is why \system{} decides
whether a finding is real and has a fix that keeps the application's behavior
before it generates any edit.

Coding agents run a model in a loop with tools: the model reads and edits
files and runs commands until it decides the task is
done~\cite{yang2024sweagent}. SWE-bench measures such systems on real GitHub
issues and accepts a patch when tests taken from the pull request that
resolved the issue pass~\cite{jimenez2024swebench}, and SWE-agent showed that
the interface through which an agent views and edits a repository changes how
many issues it resolves~\cite{yang2024sweagent}. Commercial agents such as the
Codex CLI~\cite{openai2026codex} and Claude Code~\cite{anthropic2026claudecode}
run the same loop on a developer's machine. Two points shape our evaluation.
First, the oracle defines what ``resolved'' means, so a patch that passes the
issue's tests can still overfit them~\cite{smith2015overfitting}. Second, an
agent follows a process only as far as it follows its instructions, whereas a
pipeline such as \system{} runs each stage in code and checks its output
before the next one starts. The comparison of Section~\ref{subsec:protocol}
gives coding agents \system{}'s process as instructions, including the port of
the patch to the branch tip, so the difference it measures is between a
process that is instructed and one that is enforced.

\subsection{Tool-Generated Contributions to Open Source}
\label{subsec:bots}

Bots and tools already open pull requests in many open-source projects: in a
study of popular GitHub projects, about a quarter used bots, and maintainers
judged them useful but limited~\cite{wessel2018bots}. Automated pull requests
that upgrade out-of-date dependencies led projects to upgrade more often, but
many were left unmerged, and developers cited the risk of breaking changes and
the effort of understanding the change~\cite{mirhosseini2017autopr}. Pull
requests opened by bots are merged less often and wait longer for a response
than those opened by people~\cite{wyrich2021botprs}.

\system{} differs from these tools in what it sends and how. A dependency
update can be checked against a release; a fix for an API misuse changes the
project's own code, and whether it is wanted depends on what that code
protects. Every pull request and issue in this study was approved by the
author before it was opened and names RVSec as the analyzer; the later texts
are also short and written for the maintainer, follow the repository's
contribution template when one exists, and declare AI assistance when that
template asks. Section~\ref{subsec:form} shows how maintainers responded to
the form of the first submissions and how the text changed in response.

\subsection{LLMs as Judges}
\label{subsec:judges-bg}

Strong LLMs have been used as judges of open-ended model outputs: on
chat-assistant questions their preferences agreed with human raters about as
often as humans agreed with each other, but they favored answers by position,
favored longer answers, and tended to favor their own
outputs~\cite{zheng2023judge}. The order in which candidates are shown can
change a judge's verdict~\cite{wang2024fair}, and LLM evaluators recognize and
favor their own generations~\cite{panickssery2024selfpref}. For software
engineering artifacts, LLM ratings approach human agreement on some annotation
tasks but not others, and agreement between models signals when they can be
trusted~\cite{ahmed2025annotation}. The comparison of
Section~\ref{subsec:protocol} uses two judges from different providers and
addresses these biases in its design: cards are scored in random order; one
reading shows only the patch, without its title and description; scores are
reported per judge, with the difference between what a judge gives its own
model family and what the other judge gives it; and a separate probe asks a
judge to name the arm behind a card. A residual risk remains: when the judges
disagree, the final score is the principal judge's, which belongs to the same
family as one agent arm, while the other judge is the model of another arm.
The reference answers the judges score against were revised twice after
results had been seen; every version is kept, and the readings are reported
with that history.
```
Confira se `subsec:form` (seção How a Contribution Reads) e `sec:lessons` existem com esses nomes.

### C2. Parágrafo de posicionamento · linhas 227–234 · média
"Observed security findings" contradiz a política de PR sem impacto de segurança. Nenhum dos 18 PRs incorporados tem impacto confirmado.
```latex
Table~\ref{tab:related} relates \system{} to detection and repair work.
Detectors, RVSec among them, end with a report; \system{} starts from that
report and treats it as a claim to check against the source revision the APK
was built from, without assuming that a confirmed violation has security
impact. Repair tools and coding agents end with a patch that a test suite or a
build accepts; \system{} can also end with a reasoned decision not to change
the code, counts that decision as an outcome, and keeps the build result apart
from any claim about security. Tools that open pull requests on their own,
such as dependency-update bots, typically propose changes that an existing
oracle can check, such as a newer release~\cite{mirhosseini2017autopr};
\system{} proposes changes to a project's own code, each one reviewed and
approved by a person before it is opened, and its evaluation includes how
maintainers answered them and whether RVSec still reports the violation on the
patched APK.
```

### C3. Trabalho mais próximo · linhas 268–274 · média
O texto afirmava, sem verificação, que Zhang et al. usam "method-level repair pairs". Também havia espaço antes de `~\cite` por causa da quebra de linha. Versão sem o CDRep, que pode entrar se for conferido (C17):
```latex
The closest conceptual work is a preprint by Zhang et al., which evaluates
pretrained language models on API-misuse repair~\cite{zhang2023apimisuse}.
The present study differs from it in four ways. First, it starts from runtime
findings on APKs and repairs the source revision each APK was built from, so
that the fix can reach the project itself. Second, it treats a reasoned
decision not to change the code as a valid outcome when the violation is not
real, has no fix that keeps the application's behavior, or depends on a
decision outside the repository. Third, it records every intermediate claim,
decision, plan, edit, and validation result, so that each can be inspected
apart from the final patch. Fourth, its patches were submitted as pull
requests to the projects' maintainers, and those whose finding and APK build
could be traced were checked again by RVSec on the original and the patched
APK, rather than being scored only on a benchmark.
```

### C4. Entradas novas no `referencias.bib`, todas conferidas
```bibtex
@article{legoues2019apr,
  author  = {Le Goues, Claire and Pradel, Michael and Roychoudhury, Abhik},
  title   = {Automated Program Repair},
  journal = {Communications of the ACM}, year = {2019},
  volume  = {62}, number = {12}, pages = {56--65}, doi = {10.1145/3318162}
}
@inproceedings{smith2015overfitting,
  author    = {Smith, Edward K. and Barr, Earl T. and Le Goues, Claire and Brun, Yuriy},
  title     = {Is the Cure Worse Than the Disease? {O}verfitting in Automated Program Repair},
  booktitle = {Proceedings of the 10th Joint Meeting on Foundations of Software Engineering (ESEC/FSE)},
  year = {2015}, pages = {532--543}, publisher = {ACM}, doi = {10.1145/2786805.2786825}
}
@inproceedings{yang2024sweagent,
  author    = {Yang, John and Jimenez, Carlos E. and Wettig, Alexander and Lieret, Kilian and Yao, Shunyu and Narasimhan, Karthik and Press, Ofir},
  title     = {{SWE-agent}: Agent-Computer Interfaces Enable Automated Software Engineering},
  booktitle = {Advances in Neural Information Processing Systems}, volume = {37},
  year = {2024}, pages = {50528--50652}, publisher = {Curran Associates, Inc.},
  doi = {10.52202/079017-1601}
}
@misc{openai2026codex,
  author = {{OpenAI}}, title = {Codex {CLI}},
  howpublished = {\url{https://github.com/openai/codex}}, year = {2026},
  note = {Version \pending{TP/OP version}. Accessed: Nov. 17, 2026}
}
@misc{anthropic2026claudecode,
  author = {{Anthropic}}, title = {Claude Code},
  howpublished = {\url{https://code.claude.com/docs/en/overview}}, year = {2026},
  note = {Version \pending{SP2 version}. Accessed: Nov. 17, 2026}
}
@inproceedings{mirhosseini2017autopr,
  author    = {Mirhosseini, Samim and Parnin, Chris},
  title     = {Can Automated Pull Requests Encourage Software Developers to Upgrade Out-of-Date Dependencies?},
  booktitle = {Proceedings of the 32nd IEEE/ACM International Conference on Automated Software Engineering (ASE)},
  year = {2017}, pages = {84--94}, publisher = {IEEE}, doi = {10.1109/ASE.2017.8115621}
}
@article{wessel2018bots,
  author  = {Wessel, Mairieli and de Souza, Bruno Mendes and Steinmacher, Igor and Wiese, Igor S. and Polato, Ivanilton and Chaves, Ana Paula and Gerosa, Marco A.},
  title   = {The Power of Bots: Characterizing and Understanding Bots in {OSS} Projects},
  journal = {Proceedings of the ACM on Human-Computer Interaction},
  volume = {2}, number = {CSCW}, year = {2018}, pages = {182:1--182:19}, doi = {10.1145/3274451}
}
@inproceedings{wyrich2021botprs,
  author    = {Wyrich, Marvin and Ghit, Raoul and Haller, Tobias and M{\"u}ller, Christian},
  title     = {Bots Don't Mind Waiting, Do They? {C}omparing the Interaction With Automatically and Manually Created Pull Requests},
  booktitle = {Proceedings of the 3rd IEEE/ACM International Workshop on Bots in Software Engineering (BotSE)},
  year = {2021}, pages = {6--10}, publisher = {IEEE}, doi = {10.1109/BotSE52550.2021.00009}
}
@inproceedings{zheng2023judge,
  author    = {Zheng, Lianmin and Chiang, Wei-Lin and Sheng, Ying and Zhuang, Siyuan and Wu, Zhanghao and Zhuang, Yonghao and Lin, Zi and Li, Zhuohan and Li, Dacheng and Xing, Eric P. and Zhang, Hao and Gonzalez, Joseph E. and Stoica, Ion},
  title     = {Judging {LLM}-as-a-Judge with {MT-Bench} and {Chatbot Arena}},
  booktitle = {Advances in Neural Information Processing Systems (Datasets and Benchmarks Track)},
  volume = {36}, year = {2023}, pages = {46595--46623}, publisher = {Curran Associates, Inc.},
  doi = {10.52202/075280-2020}
}
@inproceedings{ahmed2025annotation,
  author    = {Ahmed, Toufique and Devanbu, Premkumar and Treude, Christoph and Pradel, Michael},
  title     = {Can {LLMs} Replace Manual Annotation of Software Engineering Artifacts?},
  booktitle = {Proceedings of the 22nd IEEE/ACM International Conference on Mining Software Repositories (MSR)},
  year = {2025}, pages = {526--538}, publisher = {IEEE}, doi = {10.1109/MSR66628.2025.00086}
}
@inproceedings{panickssery2024selfpref,
  author    = {Panickssery, Arjun and Bowman, Samuel R. and Feng, Shi},
  title     = {{LLM} Evaluators Recognize and Favor Their Own Generations},
  booktitle = {Advances in Neural Information Processing Systems}, volume = {37},
  year = {2024}, pages = {68772--68802}, publisher = {Curran Associates, Inc.},
  doi = {10.52202/079017-2197}
}
@inproceedings{wang2024fair,
  author    = {Wang, Peiyi and Li, Lei and Chen, Liang and Cai, Zefan and Zhu, Dawei and Lin, Binghuai and Cao, Yunbo and Kong, Lingpeng and Liu, Qi and Liu, Tianyu and Sui, Zhifang},
  title     = {Large Language Models are not Fair Evaluators},
  booktitle = {Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers)},
  year = {2024}, pages = {9440--9450}, address = {Bangkok, Thailand},
  publisher = {Association for Computational Linguistics}, doi = {10.18653/v1/2024.acl-long.511}
}
```
O `\pending` dentro do campo `note` pode quebrar o BibTeX. Se quebrar, use texto simples `[TODO]` até as versões serem confirmadas.

### C5. Do relatório RVSec ao código-fonte · linhas 354–377 · alta
A Fundamentação (linhas 197–200) e a introdução (linha 94) prometem explicações que a seção não dá: o import do RVSec, a dobra de ecos, a origem app/biblioteca, os nomes ofuscados, a resolução Kotlin/KMP e o IV gerado pelo provedor. O parágrafo antigo das linhas 374–377 é apagado e fica fundido no texto abaixo.
```latex
\subsection{From an RVSec Report to Source}

RVSec reports enter as findings through an importer. Each report code carries
RVSec's own label, and a data table in \system{} states what kind of statement
each label makes: a value outside what the check admits, calls that left the
rule's order, an origin the monitor did not observe, an origin it observed but
does not admit, or an echo of a report already made about the object's
producer. An echo is folded into the other findings of the method it repeats;
an echo whose origin lies elsewhere stays a finding of its own. A finding is
marked as application or library code from the app's own packages, which the
F-Droid step reads from the namespaces of every module in the repository, and
reports on class names produced by obfuscation are set aside with that reason,
since no checkout can resolve them. When \system{} itself starts the RVSec run,
the APK is built, where the F-Droid recipe of the same revision allows, without
minification, so that runtime locations map back to source. For every RVSec
finding, the runtime observation that triggered the report stays part of the
evidence, and its label tells the analysis what the report can establish: an
unobserved origin states what the monitor saw, not what the object is, so
source evidence can answer it---for instance, an IV that the cipher provider
generated is random although the monitor cannot trace it.

For each finding, the workflow resolves the source repository, clones or
reuses it, records the documented base branch, and reads its guidance files.
It then maps the bytecode-level method to a source declaration. Kotlin
value-class name mangling and compiler-added member suffixes are removed; a
coroutine maps to the function its compiled class names, and a lambda or
coroutine body that names no source function maps to the one function of its
outer class that uses the governed API or, when the outer class never names
the API, to the one function it calls in the same Gradle module that does; a
method missing from the resolved file is looked up in the package's other
files; and in a Kotlin Multiplatform project the Android source set is
preferred to desktop or iOS code the APK never contains. A match that remains
ambiguous leaves the finding unresolved instead of guessed. The extractor then
takes the exact method, imports, surrounding class context, and call sites.
Tree-sitter parsers build conservative Java and Kotlin indexes, from which the
analysis collects callers, same-name declarations, possible overrides, symbol
references, value consumers, tests, dependency declarations, minimum Android
SDK, and relevant Gradle configuration; relations that cannot be resolved
without a complete JVM classpath remain explicitly ambiguous.
```
Com isso, a ponte da Fundamentação (linhas 197–200) passa a ser: `Before any edit, \system{} maps the reported bytecode method back to source, checks the rule against the platform's documented behavior, and lets the adjudication conclude that a tracing limit is not a misuse (Section~\ref{sec:pipeline}).`

### C6. Corpus de pesquisa · linhas 394–409 · média
```latex
When enabled, external research is a separate model call restricted to
official documentation and primary sources. It answers unresolved API or
platform questions and returns source-to-claim mappings. Web or repository
content is treated as untrusted data: it cannot instruct the pipeline, decide
which files to edit, or authorize publication. Research has a shared part,
about the governed API and the rule independently of the finding, and a case
part, which is always researched. A completed answer to the shared part is
kept in a research corpus and served again, for up to 90 days, only to a
byte-identical request (same prompts, output schema, model, reasoning effort,
output limit, and search tool); any other request is researched in full. The
runs behind the case study and the published pull requests predate the
corpus; the comparison's runs read it and record the state they started from
(Section~\ref{subsec:frozen}).
```

### C7. Origem das especificações · depois da linha 392 · baixa
```latex
Specifications are written by a model from the rule's text and official
documentation and can be audited deterministically against the rule each one
cites---its methods, events, order, constraints, and forbidden calls---so that
an event a specification drops or a value it adds is visible before it shapes
an analysis.
```

### C8. Redraft e replanejamento · linhas 505–509 · baixa
Substitua as frases sobre redraft e replanejamento por:
```latex
A draft's slip is redrafted on the same plan with the scenario as
feedback, and the plan's tests are regenerated with a production file whose
behavior that feedback changes. After the plan's last draft, or after two
drafts in a row regress because of the plan, the unit is replanned with the
scenarios as constraints. A unit is replanned at most once, whatever the
reason (regressions, a patch without effect, or drafts that fail only their
own checks), and when the new plan is not taken, the first plan's remaining
drafts still run.
```

### C9. Lista de desfechos · depois da linha 527 · média
```latex
Each finding ends in exactly one recorded outcome, which the later sections
use: a ready pull request (or one of ours already open); a patch without a
pull request, because it did not apply or build or was held; not applicable,
because the code changed upstream or the file left the target branch; a fix
the adjudication proposed but that was refused before any draft, or one the
drafts never produced; an issue for the application or for a bundled library;
a confirmed violation without a safe correction; a violation without security
impact; an external contract that prevents the correction; a decision that
depends on the maintainer; a recognized false positive; insufficient
evidence; or not analyzed.
```

### C10. Memória · linhas 529–539 · baixa
```latex
\system{} reads three kinds of earlier work. Maintainer responses to its pull
requests and issues, refreshed from GitHub, reach adjudication, planning, and
the behavior review for findings of the same specification, so a premise a
maintainer rejected is not proposed again, and a finding whose earlier pull
request the maintainers closed is not published again. Earlier attempts at the
same finding are read only when they reached a patch; an analysis that stopped
before one is not read back, so one run's sampling does not become the next
run's evidence. Facts a deterministic signal confirmed, such as an import that
a passing build of the same repository accepted, are kept as data and never
change code or prompts by themselves. For evaluation, a run can start from a
copy of the learning store that holds no attempt history, so that repeated
runs of a finding do not read one another.
```

### C11. Replay estático e ponteiro para a validação dinâmica · linhas 571–574 · média
```latex
When a static rule exists for the reported specification, it is re-run on the
code before and after the patch as a measurement. These checks establish that
a patch applies to the current branch and builds there, not that it removes
the runtime violation or preserves behavior. Whether RVSec still reports the
violation on the patched APK is measured outside the pipeline, by the dynamic
validation of the published pull requests (Section~\ref{subsec:dynval}).
```

### C12. Parágrafo de desenho na introdução · linhas 121–129, que inclui a linha 127 de F7 · média
```latex
choice is to separate responsibilities. Deterministic components collect
source, repository relations, build configuration, and policy; LLM stages
extract claims, adjudicate the finding, plan a patch, draft the edits, review
the finished patch against the plan and the whole file, and write the
pull-request text, each under a versioned schema; deterministic validators
reject unsupported claims, incomplete plans, patches with known failure shapes,
and text that describes edits absent from the diff. Accepted patches are
applied in an isolated Git worktree at the tip of the target branch, merged or
ported there when the code has moved, and built before anyone is asked to
publish them. Maintainer responses to earlier pull requests for the same
specification are retained and read by later analyses. Findings from other
detectors in SARIF or Semgrep JSON format enter the same workflow, but they are
not evaluated here.
```

### C13. Lições · linhas 1574–1605 · média
**Reaching the code** (linhas 1574–1578):
```latex
\textbf{Reaching the code is the first bottleneck.} RVSec reports a violation
only when exploration executes the code, and Monkey and APE rarely get past
login screens or configuration steps. Cryptographic code behind an account or
a server setting was therefore underrepresented, independently of how
\system{} handles a finding once it exists. The same limit constrains checking
a patch: in the original campaign, only DroidBot, Humanoid, and ARES had
reached FreeOTP's reported method, and Monkey and APE did not reach it in any
of the three runs on the original APK; in \pending{N\_INC} further cases the
rerun did not reach the patched method. Which explorers run decides which
findings exist and which fixes can be confirmed.
```
É possível acrescentar "22 of the 38 explored applications in the queues of October 22–26 produced no finding in their own code", mas só depois de confirmar o número nos logs da fila.

**Lição nova**, depois de "A patch ages with the repository". Fica curta, porque os detalhes estão na RQ7:
```latex
\textbf{A passing rerun is narrower than a fix, and a failing one is not always
a defect.} RVSec monitors a whole specification, so a patch can remove the
reported constraint and still leave another, as in the TLS pull requests whose
protocol name changed while other arguments of \texttt{SSLContext.init} were
still reported; and a maintainer can merge a revision that deliberately keeps
what the rule rejects, as ShortMessageSecure did with \texttt{HmacSHA1}. A
rerun is therefore read constraint by constraint and beside the maintainer's
decision, not as a verdict on the pull request (\researchq{7}).
```
**Small, plain changes** (linhas 1601–1605). O texto atual tem afirmações não medidas.
```latex
\textbf{Small, plain changes are reviewed; large ones are discussed.}
Proposals that changed one call and explained the risk in a few sentences were
usually merged or closed without debate; Traffic Light merged one the day
after it was opened. Changes that also renamed values, touched settings, or
migrated stored data drew questions about scope: Crazyflie's reviewer asked to
remove the obsolete helper instead, ShortMessageSecure kept its algorithm and
accepted only the explicit key size, and RedReader and InterTune declined to
change values their stored data depends on.
```

### C14. Implicações para pesquisadores · linhas 1497–1515 · média
O primeiro ponto lista categorias que não são as do pipeline. Substitua o primeiro ponto e acrescente o segundo e o terceiro. Os atuais "evidence completeness" e "infrastructure failures" viram o quarto e o quinto.
```latex
First, evaluations of repair for security findings should measure more than
patch generation and compilation, and should count abstention as an outcome.
The outcomes \system{} records---a recognized false positive, a violation
without security impact, a confirmed violation without a safe fix, an external
contract, a fix already upstream, an issue for the maintainers, and a pull
request ready for review---let an abstention rate be reported beside patch
validity, so that a system is not rewarded for risky edits in under-specified
cases.

Second, a comparison between a pipeline and a coding agent must give both the
same task. Our earlier design let the agents stop at the analyzed commit while
\system{} also carried the patch to the branch tip, and it was replaced for
that reason. The scoring rule also shapes the result: when taking no action is
acceptable, an arm that abstains more scores higher, so the number of pull
requests proposed and accepted should be reported beside the share of
acceptable runs.

Third, rerunning the detector on the patched program is a useful oracle with
known limits: it depends on reaching the code, it checks the specification's
constraints rather than the intent of the change, and it cannot tell a fix
from a patch that hides the call.
```

### C15. Praticantes · linhas 1519–1524 · baixa
Substitua as duas últimas frases ("The preview can summarize... more actionable than a model-generated diff without provenance") por:
```latex
The run records why a warning matters, which assumptions remain, and what
validation is still missing for the person who approves; the text sent to the
maintainers stays short and describes only the change. Each of these is a
record a reviewer can check against the code, which a diff alone does not
provide.
```

### C16. Tabela `tab:artifacts` · linhas 850–875 · baixa
Mantenha a tabela, porque S6 a cita, e acrescente antes do `\bottomrule`:
```latex
Judged card & Delivery kind, title, text, diff, build flag; judge scores and notes \\
RVSec rerun & Original and patched APK, violations and coverage at the patched method \\
```

### C17. Referências opcionais, só depois de conferidas · baixa
Elas não entram em C1–C3 enquanto não forem verificadas.
- **CDRep**: Ma, Lo, Li, Deng, ASIA CCS 2016. Pode virar uma linha da `tab:related` e uma frase em C3. É preciso confirmar se o reparo é sobre bytecode e por template.
- **Egele et al.**: CCS 2013, CryptoLint. Seria uma frase antes da linha 170.
- **Qi et al.** (ISSTA 2015), **Agentless**, **"Solved issues" do SWE-bench**, **Repairnator**, **SapFix**, **estudos do Dependabot**, **Wessel et al. 2021 ("Don't Disturb Me")**, **Watanabe 2025**, **Li 2025** e **Wang 2025 (LLM-as-judge em SE)**. Todos estão no achado `bib-new-entries`, com metadados não conferidos.

---

## 4. Clareza e estilo

- **E1. Macros do preâmbulo** (linhas 17–20). Acrescente `\pending` (seção 0) e apague `\finding` e `\findings`, que nunca são usados.
- **E2. Grafia americana.** `behaviour` vira `behavior` nas linhas 42, 116 e 1342. `analysed` vira `analyzed` nas linhas 379, 940, 957, 1285 e 1769. Os textos novos deste plano já usam "analyzed". Confira os poucos "analysed" que vieram das propostas (S12) e uniformize.
- **E3. Palavras-chave** (linhas 66–69):
  ```latex
  \begin{IEEEkeywords}
  cryptographic API misuse, runtime verification, Android, large language
  models, program repair, coding agents, open-source contributions, false
  positives
  \end{IEEEkeywords}
  ```
- **E4. Roteiro do artigo** (linhas 149–155):
  ```latex
  The remainder of this paper is organized as follows.
  Section~\ref{sec:background} introduces runtime detection of cryptographic
  API misuses, automated repair and coding agents, tool-generated contributions
  to open source, and LLM judges, and positions this work.
  Section~\ref{sec:pipeline} describes the \system{} pipeline.
  Section~\ref{sec:settings} states the research questions and describes a dated
  case study of the pipeline as it stood in October 2026, the two pre-registered
  evaluations, and how their cases were selected, collected, and analyzed.
  Section~\ref{sec:results} reports the results, Section~\ref{sec:discussion}
  discusses implications and threats to validity, and
  Section~\ref{sec:conclusion} concludes the paper.
  ```
- **E5. Repetições entre introdução e fundamentação** (linhas 79–85 e 188–190). Troque "Detection is a well-studied problem; acting on a report is not." por `Detection has been studied extensively; acting on a report has received much less attention.` Remova da introdução `; an Android Keystore identifier may be absent from a rule written for desktop JCA providers`, para que o exemplo fique só na fundamentação.
- **E6. Seção de salvaguardas** (linhas 469–485). Condense em um parágrafo, para abrir espaço para C5, F7 e F9:
  ```latex
  These safeguards reduce the authority assigned to model output, but they do
  not remove trusted assumptions: the semantic index is conservative rather
  than a complete JVM analysis, a resolved call may still depend on runtime
  dispatch, reflection, or generated code, and official documentation describes
  an API contract rather than the deployed provider or endpoint. Unresolved
  assumptions are recorded and stay visible to the reviewer. The pipeline also
  separates a \emph{generation blocker}, under which producing code would be
  unsupported, from a \emph{publication blocker}, under which a patch may be
  applied and built locally but not presented as ready for adoption, and from a
  \emph{review assumption}, a claim that a maintainer or a targeted test must
  confirm.
  ```
- **E7. Glossário de desfechos e de braços.** Use os mesmos nomes nas Seções 3, 5 e 6: "ready pull request (a committed draft that builds at the branch tip)", "false positive", "violation without security impact", "confirmed violation without a safe fix", "external contract prevents the fix", "not applicable", "insufficient evidence". Para os braços: {\system}, Codex Terra (TP), Claude Sonnet (SP2), Codex Sol (OP). `\texttt{}` fica só para identificadores de configuração.
- **E8. `tab:sample`.** Legenda: `Case inventory: the case study, the published pull requests, and their overlap. Pull requests and findings are counted separately.` A tabela passa a ser citada em S5, F19 e F21.
- **E9. Seção "How a Contribution Reads"** (linhas 1466 e 1473–1475). Linha 1466: `On October 20, a short pull request (about 60 words) in plain paragraphs for a KeyStore finding in the same project was merged the next day with thanks (\#238).` Linhas 1473–1475: `...and all of them came from the inquiries or the templated period. None of the 34 later pull requests (12 with short sections and 22 in plain paragraphs) had drawn one by November 17, 2026; the six of them closed after review were closed for technical reasons or a contribution policy.` Para replicação, salve o script que gera `tab:pr-format` e cite-o.
- **E10. Corte opcional de páginas.** Reduza "Implications for Researchers" se ficar longo. Fundir a RQ4 na RQ1 exigiria renumerar todas as RQs; não recomendo a três dias da entrega.

---

## 5. Itens bloqueados por dados pendentes

| Resultado pendente | Placeholders que dependem dele |
|---|---|
| **Opção A**: fase 1 do OP (em execução), SP2 r3–r4, fase 2 (porte) de TP/SP2/OP, juízes v3 dos cartões novos, kappa e viés de família nas leituras v3, custo fase 1+2 por braço, variação entre réplicas dos agentes | F1 (C/TP/SP2/OP); F3; F6 item 5; S12 (colunas da `tab:optiona`, contagens do porte, frase de PRs aceitos e recusados, kappas, variação dos agentes, Answer RQ6); S14 (quarto achado); F23 (custo dos agentes e primeira frase de "Pipeline or agent?"); S16 (oração condicional); F25 |
| **Decisão: prova de cegueira nos braços TP/SP2/OP** (rodar ou declarar como limitação) | S8 (`\emph{Blinding}`); S12 (Judges and blinding) |
| **Validação dinâmica** (corte 18/11 23:59): ReadYou e InterTune rep 2, FreeOTP ×2 com DroidBot, your-local-weather rep 2 | F1 (N/M); S10 e S4 (data final); S13 (`tab:dynval`, resultado do FreeOTP, Answer RQ7); S14 (terceiro achado); S15 (resultado do FreeOTP em Construct); C13 (N_INC); F25 |
| **Recálculo da RQ5** por script versionado, com janela fixa (recomendado: até `subset_bitbanana_scope6`) | F22 (N, CALLS, TOTAL, MED, PR, ISS, FP, IE, K, ALL); S4 (linha RQ5); ajuste das linhas 890–894 ("over all runs") |
| **Versões do Codex e do Claude Code** usadas nos braços da opção A | C4 (`note` das entradas `@misc`) |
| **URL do repositório de replicação** (decisão sua: pública ou "on request") | F26 |
| **Regenerar `inventario.json`** a partir de `outputs/prs_reports/20261117` (não é dado pendente, é uma ação) | coerência de F2, S5 e `tab:sample` |
| **Leituras do protocolo e v2 de C/TP/SP2** (decisão sua: nota de rodapé ou só no artefato) | S8 (frase das versões; hoje diz "kept with the data") |

---

## Achados refutados
Nenhum. Todas as verificações deram "confirmed" ou "partly", e quando foi "partly" usei a versão corrigida.

## Conflitos entre achados e como os resolvi
- **Numeração das RQs:** RQ6 = comparação e RQ7 = validação dinâmica (seção 0). Rejeitei a validação dentro da RQ2 e a troca RQ6/RQ7.
- **Data do levantamento:** "13/11 rechecado sem mudança" e "17/11 com 31" vinham de achados diferentes. Usei 17/11 com 31, porque as respostas mudaram mesmo que os estados não.
- **`tab:artifacts`:** apagar ou acrescentar linhas? Mantive e acrescentei, porque S6 a cita.
- **"Blind" no resumo:** troquei por "not told which side produced each delivery". **Correção (17/11, depois da síntese):** a sonda NÃO identificou o C. No cartão inteiro o juiz disse "C" em 25 de 40 cartões, e só 8 deles eram de C, com confiança de 0,28 a 0,35; só no patch acertou C em 1 de 10. "Blind-judged" pode ficar, com a ressalva. A sonda nos braços TP/SP2/OP foi pré-registrada em 17/11 (docs/preregistro_opcao_a_20261116.md).

## Decisões do usuário sobre a seção 0 e a seção 5 (17/11)
- Numeração: RQ6 = comparação (opção A), RQ7 = validação dinâmica.
- Prova de cegueira: rodar nos braços TP, SP2, OP e C (pré-registrada antes dos juízes).
- RQ5: recalcular por script versionado, com janela fixa.
- Repositório de replicação: público.

## Decisões do usuário sobre o parecer da banca (17/11, docs/parecer_banca_20261117.md)
- A4: S, T e O voltam como bloco declarado (RQ6 em duas partes); desvio datado no pré-registro da opção A.
- A3: só as leituras v3 (autor, com regra 4, e patch); uma frase declara que as leituras anteriores existiram, por que foram substituídas (erros nos gabaritos) e onde estão os dados.
- A2: não incluir a frase sobre o C ter sido corrigido nas mesmas unidades. Os placeholders `<commit>` saem do texto mesmo assim.
- C1: não incluir o cruzamento estado do PR × impacto de segurança. Consequência para o texto: não afirmar que PRs incorporados removeram vulnerabilidades; eles corrigem violações reportadas pelo RVSec.
- B2: classificar os diffs de todos os casos "corrigido" (mudou o valor/algoritmo, mudou a sequência, removeu a chamada, só deslocou/envolveu) e checar se a mesma especificação aparece num site novo no APK corrigido.
- D1: seção de ética curta. Projetos e PRs podem ser nomeados (ex.: traffic-light recusou uma issue e incorporou um PR); não citar mantenedores por nome ou login.
- D3: builds sem sandbox entram em ameaças; isolar o build num container entra em trabalhos futuros.
- **Referências não conferidas** (CDRep, Agentless, Repairnator, Dependabot etc.): saíram do texto e foram para C17.
- **Grocy #1012:** não tinha mensagem de protocolo, então a frase separa esse caso de Fread e zSMTH.
- **Piloto da validação dinâmica:** o texto não diz "same build settings", porque o JDK do piloto era fixo.

---

## Impacto estimado em páginas (descritivo)
O PDF tem hoje 17 páginas.
- **O que sai:** o desenho antigo da RQ6 (três tabelas e cerca de duas páginas de texto), o protocolo antigo, os parágrafos antigos de ameaças, o parágrafo "Model comparison" (resumido e movido) e as salvaguardas condensadas.
- **O que entra:**
  - 3 subseções de fundamentação e cerca de 12 referências;
  - pipeline maior (C5, F7, F9, C9);
  - protocolo da opção A;
  - método da validação dinâmica e RQ7 com tabela;
  - quatro tabelas novas: `tab:rq-data`, `tab:optiona`, `tab:closures`, `tab:pr-spec`.

O mais provável é que as adições pesem mais que as remoções. Depois de compilar, se precisar cortar, os candidatos são E6, E10, a lista de desfechos C9 (pode virar nota) e o parágrafo de escolha do modelo em S7.

## Ordem sugerida de aplicação
1. **Preâmbulo e bibliografia:** `\pending` (E1), entradas C4, correções F28. Compile para conferir que as citações resolvem.
2. **Dados do levantamento:** regenere o inventário e aplique F2.
3. **Pipeline** (não depende de dado pendente): F7, C5, F11, C7, C6, F12, E6, F8, C8, F10, C9, C10, F9, C11, F13.
4. **Fundamentação:** C1, C2, F14, C3, F27, E5 e a ponte das linhas 197–200 (em C5).
5. **Study Settings:** S1, S2, S3, F15, F16, S4, S5, F17, C16, S6, F18, S7, S8, S9.
6. **Results RQ1–RQ5:** S10, F19, S11, F20 (com `tab:pr-state` movida), F21, F22.
7. **Esqueletos da RQ6 e da RQ7:** S12, S13. Apague todos os `\ref{tab:agents|tab:replicas|tab:blind}`.
8. **Discussão até o fim:** S14, E9, C14, C15, F23, C13, F24, S15, S16, F25, F26.
9. **Por último, resumo e introdução**, que resumem o resto: F1, E3, F4, F3, F5, C12, F6, E4, E2, E7, E8.
10. **Depois dos dados:** preencha os `\pending` (seção 5), rode `grep -n '\\pending\|TODO-\|<commit>' main.tex` até voltar vazio, compile com `latexmk -pdf` e informe erros e número de páginas.

Lembretes do CLAUDE.md: aplique por script no WSL, depois recarregue a aba do editor (File: Revert File) antes de salvar.