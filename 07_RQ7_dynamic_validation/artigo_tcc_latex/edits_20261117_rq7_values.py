"""2026-11-17 (user: "sim"): fill the RQ7 placeholders with the final dynamic-validation results
(outputs/dynamic_validation_20261114/results_prs.json after classify_origin_tools.py) and add the FreeOTP #524 note."""
from pathlib import Path
import shutil
tex = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/artigo_tcc_latex/main.tex")
t = tex.read_text(encoding="utf-8")
shutil.copy2(tex, tex.with_name("main.tex.bak_20261117_rq7"))


def once(old, new):
    global t
    assert t.count(old) == 1, (t.count(old), old[:70])
    t = t.replace(old, new)


# abstract
once("for \\pending{N} of\nthe \\pending{M} pull requests whose finding it reproduced, and no corrected\npatch merely hid the call from the monitor.",
     "for 22 of\nthe 32 pull requests whose finding it reproduced, and one corrected patch kept\nthe same key material and only changed how the monitor saw it.")
# contribution 4
once("requests that met the inclusion criteria: \\pending{result}; and",
     "requests that met the inclusion criteria: the reported violation no longer\n  appeared in 22 of the 32 whose original APK reproduced it; and")
# tab:rq-data and results guide
once("33 of 49 PRs, Nov.~14--\\pending{end} \\\\", "33 of 49 PRs, Nov.~14--17 \\\\")
once("from November 14 to \\pending{end date}, 2026.", "from November 14 to 17, 2026.")
# tab:dynval
once("Incorporated & \\pending{} & \\pending{} & \\pending{} & \\pending{} & 6 & 18 \\\\", "Incorporated & 9 & 3 & 0 & 0 & 6 & 18 \\\\")
once("Closed       & \\pending{} & \\pending{} & \\pending{} & \\pending{} & 3 & 10 \\\\", "Closed       & 5 & 1 & 1 & 0 & 3 & 10 \\\\")
once("Open         & \\pending{} & \\pending{} & \\pending{} & \\pending{} & 7 & 21 \\\\", "Open         & 8 & 3 & 2 & 1 & 7 & 21 \\\\")
once("Total        & \\pending{} & \\pending{} & \\pending{} & \\pending{} & 16 & 49 \\\\", "Total        & 22 & 7 & 3 & 1 & 16 & 49 \\\\")
# FreeOTP result in RQ7
once("\\pending{FreeOTP \\#521/\\#524 rerun with DroidBot: result}",
     "With DroidBot, the explorer of the original report, the FreeOTP violation\nappeared on the original APK in all three repetitions of both pull requests;\nit disappeared after \\#524, the production change, and remained after \\#521,\nwhich only adds a test.")
# diff classification paragraph
once("None of the corrected patches only hid the call from the monitor: an\nadversarial second reading of each corrected diff tried to show that it merely\nrelocated or wrapped the call. Twelve change the",
     "An adversarial second reading of each corrected diff tried to show that it\nmerely relocated or wrapped the call. Twelve change the")
once("required form with the same material, one removes the governed call, and none\nonly relocates it; no patched run",
     "required form with the same material, one removes the governed call, and none\nonly relocates it; no patched run")
once("then sees where the key came from, not because it sees less.\n\\pending{update the counts if the remaining cases add corrected ones}",
     "then sees where the key came from, not because it sees less. One corrected\npatch, FreeOTP \\#524, keeps the same key material: it rebuilds the\npassword-derived key as an AES key from its own bytes and adds a check that\nthe key is AES, so the monitor no longer reports the key's origin while the\nencryption is unchanged. This is the pattern the behavior review is meant to\nreject (Section~\\ref{sec:pipeline}), and it reached a merged pull request.")
# RQ7 answer
once("\\pending{X} of the 33 that could be checked and still appeared in\n\\pending{Y}; \\pending{Z} were inconclusive or did not reproduce on the\noriginal APK.",
     "22 of the 32 whose original APK reproduced it and still appeared in 7;\nthree were inconclusive, and in one the original APK did not reproduce the\nreport. One corrected patch only changed how the monitor saw the same key.")
# principal findings
for old, new in [("\\pending{N\\_CORR}", "22"), ("\\pending{N\\_VAL}", "32"), ("\\pending{N\\_REM}", "one"),
                 ("\\pending{N\\_NAO}", "seven"), ("\\pending{N\\_BASE}", "one"), ("\\pending{N\\_OUTRA}", "three"),
                 ("\\pending{N\\_MANT}", "one")]:
    once(old, new)
assert t.count("\\pending{N\\_INC}") == 2
t = t.replace("\\pending{N\\_INC}", "three")
# construct validity
once("(the classification of the\ncorrected diffs found none, Section~\\ref{subsec:rq7})",
     "(the classification of the\ncorrected diffs found one, FreeOTP \\#524, which kept the same key material;\nSection~\\ref{subsec:rq7})")
once("strategies, as Humanoid and ARES could not run here.\n\\pending{FreeOTP with DroidBot: result}",
     "strategies, as Humanoid and ARES could not run here; with them the violation\nreproduced on the original APK in three of three repetitions.")
# conclusion
once("appeared in the patched method for \\pending{N} of the \\pending{M} pull",
     "appeared in the patched method for 22 of the 32 pull")
tex.write_text(t, encoding="utf-8")
print("RQ7 filled; pending left:", t.count("\\pending{"))
