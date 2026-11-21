"""Write plus_prompt/inputs/<unit>.txt from the C arm's recorded requests (8be9f78).

Revision 2026-11-14 (critic review of process_block.txt). Each file has two
parts, in this order:

1. "The published rule behind each report": for every report the agent's
   prompt lists, in the prompt's order, the rule the C arm's adjudication
   received for it -- `reported_by_rule` for the primary finding and
   `joint_findings_rules` for the joined ones -- plus the primary finding's
   `reported_violation` / `reported_violation_section`. This is static
   library data (data/specifications, through verified_detector_semantics),
   not part of the holdout. The sections are checked identical across every
   adjudication request of every run (r1-r4) of the unit; the script stops
   if they are not.
2. The maintainer responses (as before), now headed with the report whose
   rule they answer: the pipeline computes them for the primary finding's
   rule only, which in two joint units is the report the prompt lists second.

GitHub @handles and commit hashes inside maintainer text are redacted
("@<contributor>", "<commit>"); every redaction is counted in
inputs_report.json. Requests from the later base-port process (pid differs,
solutions.base_port) are reported separately and not used.
"""
import json, os, glob, re, sys, hashlib
sys.path.insert(0, os.path.dirname(__file__))
from survey_lib import find_values, KEYS, ROOT, UNITS  # noqa

OUT = "/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113/plus_prompt/inputs"
os.makedirs(OUT, exist_ok=True)
TITLES = {
    "maintainer_responses_to_this_rule_elsewhere":
        "Earlier proposals, in other repositories, for the rule of the report on %s, and what their maintainers answered",
    "maintainer_responses_to_this_rule_in_this_repository":
        "Earlier proposals, in this repository, for the rule of the report on %s, and what its maintainers answered",
    "maintainer_responses_in_this_repository":
        "Earlier proposals, in this repository, for other rules, and what its maintainers answered",
}
EMPTY = ("Earlier proposals, in other repositories, for the rule of the report on %s: "
         "none with a maintainer response.\n")
PLAN_STAGES = ("patch_planning", "code_generation.coordinated_file")
RULE_KEYS = ("reported_by_rule", "joint_findings_rules")
HANDLE = re.compile(r"(?<![\w.@/])@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\b")
COMMIT = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b")


def h(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()[:10]


def redact(text, counter):
    text, n1 = HANDLE.subn("@<contributor>", text)
    text, n2 = COMMIT.subn("<commit>", text)
    counter["handles"] += n1
    counter["commits"] += n2
    return text


def strip_bots(entries):
    return [{k: v for k, v in e.items() if k != "automated_review_comments_not_from_the_maintainer"}
            for e in entries]


def simple(name):
    return str(name).rsplit(".", 1)[-1]


def report_label(row, rule):
    apis = " / ".join(simple(t) for t in (rule or {}).get("governed_api_types") or []) or row["spec"]
    return "%s.%s (%s)" % (simple(row["class"]), row["method"], apis)


def render_rule(row, rule, primary_finding):
    lines = ["Report on %s, rule %s" % (report_label(row, rule), row["spec"])]
    if primary_finding is not None:
        kind = primary_finding.get("reported_violation")
        section = primary_finding.get("reported_violation_section")
        if kind:
            lines.append("RVSec's record classifies this report as %s%s." % (
                kind, (", in the rule's %s section" % section) if section else ""))
    if not rule:
        lines.append("No published rule was supplied for this report.")
        return "\n".join(lines)
    lines.append("Rule source: %s, %s" % (rule.get("source_title", ""), rule.get("source_url", "")))
    lines.append("Types it governs: %s" % ", ".join(rule.get("governed_api_types") or []))
    if rule.get("protocol"):
        lines.append("Call order it requires: %s" % rule["protocol"])
    events = rule.get("events") or []
    if events:
        lines.append("Events it matches:")
        for e in events:
            shapes = ", ".join("`%s`" % p for p in e.get("api_patterns") or [])
            lines.append("- %s: %s%s" % (e.get("name", ""), e.get("matches", ""),
                                         (" Call shapes: %s." % shapes) if shapes else ""))
    sem = rule.get("detector_semantics") or {}
    if sem.get("claim"):
        lines.append("What RVSec checks under this rule: %s" % sem["claim"])
    for key, title in (("predicates", "Requirements the rule states:"),
                       ("limitations", "Limits of the check, recorded with the rule:")):
        if sem.get(key):
            lines.append(title)
            lines.extend("- %s" % p for p in sem[key])
    extra = set(sem) - {"claim", "predicates", "limitations"}
    if extra:
        raise SystemExit("unexpected detector_semantics field(s) %s" % extra)
    for key, title in (
            ("known_false_positive_shapes",
             "Reviewed false-positive shapes of the rule (a match read from the code means the report is wrong):"),
            ("non_security_violation_shapes",
             "Violations of this rule without a security stake, recorded with the rule (3.15):"),
            ("unreviewed_false_positive_shapes",
             "Possible false-positive shapes listed with the rule, never reviewed against it; "
             "they never support a dismissal (3.15):")):
        if rule.get(key):
            lines.append(title)
            lines.extend("- %s" % s for s in rule[key])
    extra = set(rule) - {"specification", "source_title", "source_url", "source_verified",
                         "governed_api_types", "protocol", "events", "detector_semantics",
                         "known_false_positive_shapes", "non_security_violation_shapes",
                         "unreviewed_false_positive_shapes"}
    if extra:
        raise SystemExit("unexpected rule field(s) %s" % extra)
    return "\n".join(lines)


def render_responses(key, entries, label, counter):
    title = TITLES[key] % label if "%s" in TITLES[key] else TITLES[key]
    lines = [title + ":", ""]
    for n, e in enumerate(entries, 1):
        lines.append("%d. Repository: %s" % (n, e.get("repository", "")))
        lines.append("   Outcome: %s" % e.get("outcome", ""))
        lines.append("   Proposal: %s" % redact(e.get("proposal", ""), counter))
        said = redact(e.get("maintainer_said", ""), counter)
        lines.append("   Maintainer said: %s" % (said if said else "(no comment)"))
        extra = set(e) - {"repository", "outcome", "proposal", "maintainer_said",
                          "automated_review_comments_not_from_the_maintainer"}
        if extra:
            raise SystemExit("unexpected field(s) %s" % extra)
        bots = e.get("automated_review_comments_not_from_the_maintainer") or []
        if bots:
            lines.append("   Automated review comments (not from the maintainer):")
            for b in bots:
                lines.append("   - %s%s: %s" % (b.get("author", ""),
                                                " (bot)" if b.get("author_is_bot") else "",
                                                redact(b.get("comment", ""), counter)))
        lines.append("")
    return "\n".join(lines)


report = {}
for unit in sorted(os.listdir(UNITS)):
    u = json.load(open(os.path.join(UNITS, unit, "unit.json")))
    adj, plan, late, rules = {}, {}, {}, {}
    adj_files = []
    for run in sorted(glob.glob(os.path.join(ROOT, unit, "r*"))):
        rn = os.path.basename(run)
        files = sorted(glob.glob(run + "/application_results/openai_prompt_logs/*_request.json"))
        base_port_pids = {os.path.basename(f).split("_")[2] for f in files if "solutions.base_port" in f}
        for f in files:
            pid = os.path.basename(f).split("_")[2]
            d = json.load(open(f))
            text = d.get("input", "")
            if not isinstance(text, str):
                text = json.dumps(text)
            stage = d.get("stage", "")
            if stage == "adjudication" and pid not in base_port_pids:
                adj_files.append(os.path.relpath(f, ROOT))
                fin = [v for v in find_values(text, "finding") if isinstance(v, dict) and "target" in v]
                fin = fin[0] if fin else {}
                section = {k: (find_values(text, k) or [None])[0] for k in RULE_KEYS}
                section["primary"] = {
                    "specification": fin.get("specification"),
                    "class": (fin.get("target") or {}).get("class"),
                    "method": (fin.get("target") or {}).get("method"),
                    "reported_violation": fin.get("reported_violation"),
                    "reported_violation_section": fin.get("reported_violation_section"),
                }
                rules.setdefault(h(section), (section, set()))[1].add(rn)
            for key in KEYS:
                for v in find_values(text, key):
                    if pid in base_port_pids:
                        late.setdefault(key, {}).setdefault(h(v), (v, set()))[1].add(rn)
                    elif stage.startswith("adjudication"):
                        adj.setdefault(key, {}).setdefault(h(v), (v, set()))[1].add(rn)
                    elif stage in PLAN_STAGES or stage.startswith("patch_planning"):
                        plan.setdefault(key, {}).setdefault(h(v), (v, set()))[1].add(rn)
    if len(rules) != 1:
        raise SystemExit("%s: rule sections differ across adjudication requests (%d variants)" % (unit, len(rules)))
    section = next(iter(rules.values()))[0]
    primary = section["primary"]
    by_spec = {}
    if section["reported_by_rule"]:
        by_spec[section["reported_by_rule"]["specification"]] = section["reported_by_rule"]
    for r in section["joint_findings_rules"] or []:
        by_spec[r["specification"]] = r
    rows = u["rows"]
    primary_rows = [r for r in rows if r["spec"] == primary["specification"]
                    and r["class"] == primary["class"] and r["method"] == primary["method"]]
    if len(primary_rows) != 1:
        raise SystemExit("%s: primary finding not found once among the prompt's reports" % unit)
    primary_row = primary_rows[0]
    rule_parts = [render_rule(r, by_spec.get(r["spec"]), primary if r is primary_row else None)
                  for r in rows]
    missing = [r["spec"] for r in rows if r["spec"] not in by_spec]
    primary_label = report_label(primary_row, by_spec.get(primary_row["spec"]))

    counter = {"handles": 0, "commits": 0}
    parts, notes = [], []
    for key in KEYS:
        if key not in adj and key not in plan:
            continue
        a = adj.get(key, {})
        if len(a) > 1:
            raise SystemExit("%s %s: adjudication sections differ across runs" % (unit, key))
        entries = next(iter(a.values()))[0] if a else None
        for v, runs in plan.get(key, {}).values():
            if entries is None:
                entries = strip_bots(v)
            if strip_bots(v) != strip_bots(entries):
                raise SystemExit("%s %s: plan section differs from adjudication beyond bots" % (unit, key))
            if v != entries:
                entries = v
                notes.append("plan/file-draft requests (%s) carried automated review comments the "
                             "adjudication did not (as_maintainer_position strips them); they are "
                             "included, labelled" % ",".join(sorted(runs)))
        if entries:
            parts.append(render_responses(key, entries, primary_label, counter))
    for key, vs in late.items():
        for v, runs in vs.values():
            if v and strip_bots(v) != strip_bots(adj.get(key) and next(iter(adj[key].values()))[0] or []):
                notes.append("LATE base-port process (%s) received a different %s: %s" % (
                    ",".join(sorted(runs)), key, [e["repository"] for e in v]))
    responses = "\n".join(parts).rstrip() + "\n" if parts else EMPTY % primary_label
    text = ("The published rule behind each report\n\n" + "\n\n".join(rule_parts) + "\n\n\n"
            + responses)
    with open(os.path.join(OUT, unit + ".txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    report[unit] = {
        "repo": u["repo_url"], "spec": u["spec"],
        "prompt_report_order": ["%s %s.%s" % (r["spec"], simple(r["class"]), r["method"]) for r in rows],
        "primary_finding": "%s %s.%s" % (primary["specification"], simple(primary["class"] or ""),
                                         primary["method"]),
        "primary_listed_position": rows.index(primary_row) + 1,
        "rules_rendered": sorted(by_spec),
        "rules_missing": missing,
        "rule_section_runs": sorted(next(iter(rules.values()))[1]),
        "keys": [k for k in KEYS if k in adj or k in plan],
        "repositories": sorted({e["repository"] for p in [adj, plan] for k in p for v, _ in p[k].values()
                                for e in v}),
        "redactions": counter,
        "adjudication_requests": sorted(set(adj_files)),
        "notes": notes,
    }
json.dump(report, open(os.path.join(os.path.dirname(__file__), "inputs_report.json"), "w"), indent=1)
for unit, r in report.items():
    print(unit, r["prompt_report_order"], "primary@%d" % r["primary_listed_position"],
          r["rules_missing"], r["redactions"], r["repositories"], r["notes"])
