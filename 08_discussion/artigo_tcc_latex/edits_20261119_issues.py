"""2026-11-19: what the issues drew (GitHub replies read on 19/11)."""
import shutil

P = "/mnt/c/Users/Antonow/Documents/tcc_v3/artigo_tcc_latex/main.tex"
NL = "|"
R = [
(r"welcome a pull request; 11 received a reply, and all are now closed, four by|maintainers. The other eight reported a finding with no safe fix and asked how|to handle it; four received a response and two remain open.",
 r"welcome a pull request; 11 received a reply, and all are now closed, four by|maintainers and the other 21 by us on the day they were opened. The other|eight described a finding with no safe fix and asked how to handle it; four|received a response and two remain open. Three of these answers supplied what|the rule could not know (a fallback kept for usability, users' self-signed|servers, a checksum algorithm set by a remote source), and the DNSToggle|maintainer fixed the finding in a release."),
(r"three objected to the approach as automated. After September",
 r"three objected to the approach as automated. None could weigh the finding,|which the inquiry did not describe, and only one of the four invitations was|followed by a pull request (Tokn, incorporated). After September"),
]
s = open(P, encoding="utf-8").read()
for a, b in R:
    a, b = a.replace(NL, "\n"), b.replace(NL, "\n")
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)
shutil.copy(P, P + ".bak_20261119_issues")
open(P, "w", encoding="utf-8").write(s)
print("applied", len(R))
