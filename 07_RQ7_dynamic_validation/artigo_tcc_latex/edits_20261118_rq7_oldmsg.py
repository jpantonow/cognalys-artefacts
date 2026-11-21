"""2026-11-18: deviation 5 of docs/preregistro_validacao_prs_20261115.md. The seven pull requests excluded because
their recorded report used RVSec's earlier message format were built and run with the current RVSec (Monkey and
APE, 600 s), labeled by the specification at the method. Counts from outputs/dynamic_validation_20261114/
results_prs.json with Session #2241 as incorporated (merged 2026-11-17, 20:39 BRT): 28 corrected, 7 not corrected,
3 inconclusive, 2 base does not reproduce, 9 not checked. New corrected diffs (Giggity #543, WiFiAnalyzer #618,
Crazyflie #103) all remove the governed call; classified by the author alone."""
from pathlib import Path
import shutil
import sys
tex = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/artigo_tcc_latex/main.tex")
t = tex.read_text(encoding="utf-8")


def once(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:70])
    t = t.replace(old, new)


# abstract
once("reported violation no longer appeared in the patched method for 25 of\nthe 32 pull requests whose finding it reproduced,",
     "reported violation no longer appeared in the patched method for 28 of\nthe 38 pull requests whose finding it reproduced,")
# contribution
once("which RVSec reruns the original and the patched APK of the 33 published pull\n  requests that met the inclusion criteria: the reported violation no longer\n  appeared in 25 of the 32 whose original APK reproduced it; and",
     "which RVSec reruns the original and the patched APK of 40 published pull\n  requests (33 that met the inclusion criteria and seven added by a declared\n  deviation): the reported violation no longer appeared in 28 of the 38 whose\n  original APK reproduced it; and")
# research-question table
once("& 33 of 49 PRs, Nov.~14--17 \\\\", "& 40 of 49 PRs, Nov.~14--18 \\\\")
# method: third deviation
once("this classification was not registered and is descriptive.",
     "this classification was not registered and is descriptive. A third\ndeviation, declared after the results of the 33 were known, added the seven\npull requests excluded because their recorded report used an earlier message\nformat of RVSec, which no current detector line matched: they were built and\nrun with the current RVSec under the same settings, and labeled by the\nspecification alone, any violation of it at the method counting.")
# RQ7
once("Table~\\ref{tab:dynval} reports the 33 published pull requests included under\nSection~\\ref{subsec:dynval}.",
     "Table~\\ref{tab:dynval} reports the 40 published pull requests run under\nSection~\\ref{subsec:dynval}: the 33 included and the seven added by the third\ndeviation.")
once("Incorporated & 9 & 3 & 0 & 0 & 7 & 19 \\\\", "Incorporated & 10 & 4 & 0 & 1 & 4 & 19 \\\\")
once("Closed       & 5 & 1 & 1 & 0 & 3 & 10 \\\\", "Closed       & 7 & 1 & 1 & 0 & 1 & 10 \\\\")
once("Open         & 11 & 0 & 2 & 1 & 6 & 20 \\\\", "Open         & 11 & 2 & 2 & 1 & 4 & 20 \\\\")
once("Total        & 25 & 4 & 3 & 1 & 16 & 49 \\\\", "Total        & 28 & 7 & 3 & 2 & 9 & 49 \\\\")
once("pull requests maintainers closed (whoBIRD, rssreader, RedReader, BetterUntis,\nNetwork Survey):",
     "pull requests maintainers closed (whoBIRD, rssreader, RedReader, BetterUntis,\nNetwork Survey, Giggity, WiFiAnalyzer):")
once("which only adds a test.\n",
     "which only adds a test.\n\nOf the seven pull requests added by the third deviation, Giggity \\#543,\nWiFiAnalyzer \\#618, and Crazyflie \\#103 no longer showed the violation, and\nthe original APK of News \\#1716 did not reproduce it. In DSub2000 \\#160,\nFeeder \\#1210, and etesync \\#301 a violation of the specification remained:\nthe three addressed a protocol message the current specification no longer\nemits, and what remained at the call (a null key manager or random source, an\napplication-defined trust manager) was already on the original APK and outside\nthe pull request.\n")
once("An adversarial second reading of each corrected diff tried to show that it\nmerely relocated or wrapped the call.",
     "An adversarial second reading of each corrected diff (the author's reading\nalone for the three added by the third deviation) tried to show that it\nmerely relocated or wrapped the call.")
once("required form with the same material, one removes the governed call, and none",
     "required form with the same material, four remove the governed call, and none")
once("25 of the 32 whose original APK reproduced it and still appeared in 4;\nthree were inconclusive, and in one the original APK did not reproduce the\nreport.",
     "28 of the 38 whose original APK reproduced it and still appeared in 7, three\nof them at constraints the pull request did not address; three were\ninconclusive, and in two the original APK did not reproduce the report.")
# principal findings
once("specification appeared at the patched method in 22 of the\n33 checkable cases (one removed the call), and in three more only messages the\noriginal APK already showed remained; a violation still appeared in four; exploration missed the method in\nthree, and the original APK did not reproduce the report in\none (Section~\\ref{subsec:rq7}). Of these, one kept a choice the maintainer asked to\npreserve, one left a call sequence the monitor still rejects, one changed\nonly a desktop implementation, and one only added a test.",
     "specification appeared at the patched method in 25 of the\n40 checkable cases (two removed the call), and in three more only messages the\noriginal APK already showed remained; a violation still appeared in seven; exploration missed the method in\nthree, and the original APK did not reproduce the report in\ntwo (Section~\\ref{subsec:rq7}). Of the seven, one kept a choice the maintainer asked to\npreserve, one left a call sequence the monitor still rejects, one changed\nonly a desktop implementation, one only added a test, and three addressed a\nmessage the current specification no longer emits.")
# limitations
once("The dynamic validation covers 33 of the 49 pull requests: 16 were excluded\nbefore any run because their specification is outside RVSec's cryptographic\nset, their finding lacks a complete identity or detector line, or no build\nrecorded the analyzed APK.",
     "The dynamic validation covers 40 of the 49 pull requests: nine were excluded\nbefore any run because their specification is outside RVSec's cryptographic\nset, their finding lacks a complete identity, no build recorded the analyzed\nAPK, or the pull request addresses another finding; seven first excluded for\nan earlier message format were added by a declared deviation.")
# conclusion
once("appeared in the patched method for 25 of the 32 pull\nrequests it could check.",
     "appeared in the patched method for 28 of the 38 pull\nrequests it could check.")

if "--dry" in sys.argv:
    print("dry run ok")
else:
    shutil.copy2(tex, tex.with_name("main.tex.bak_20261118_oldmsg"))
    tex.write_text(t, encoding="utf-8")
    print("applied")
