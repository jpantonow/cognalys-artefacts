#!/usr/bin/env python3
"""Checks that run before the pipeline spends its first token.

A pipeline run costs hours of wall clock and paid model calls, and the
failures recorded across the project's 120 runs were all of a kind that costs
seconds to find beforehand:

    run_20260907_180505  NameError: name 'reported_violation' is not defined
    run_20260907_185056  UnboundLocalError: 'violation'
    run_20260907_210458  OpenAI HTTP 400 cyber_policy, 23 minutes in

Each was diagnosed from a burnt run and fixed afterwards, because nothing
looked at the code before the run started. That is the gap these checks close.

They deliberately stop there. What belongs here is a question about the code
this run is about to execute -- is every declared dependency importable, does
anything in it raise on the line it reaches -- answered in seconds from the
state on disk. Running the unit suite here was a different thing wearing the
same coat: it gates a run on the health of the *tests*, which can disagree
with the source for reasons that have nothing to do with whether this run
works, and it fails on a tree that is merely being edited while the run
starts. Keeping the tests green is the test suite's job, at the moment
someone changes the code; it is not a toll booth in front of every run.

The checks are ordered. A missing declared dependency makes static analysis
fail for a reason that has nothing to do with the code, so it is reported on
its own and the later check is not run: one root cause should produce one
message.

``run_pipeline.py --skip-preflight`` bypasses all of it, for the case where
the operator knows what is broken and wants the run anyway.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path


# A requirements line names a distribution; an import names a module. Every
# requirement this project declares follows the usual convention, so the
# mapping is mechanical. A distribution whose module is named differently
# (PyYAML -> yaml, for example) would need an entry here.
DISTRIBUTION_MODULE_OVERRIDES: dict[str, str] = {}


@dataclass
class PreflightReport:
    """What the checks found, and which ones actually ran."""

    problems: list[str] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _module_name(requirement: str) -> str:
    """The module a requirement line installs, or "" if it names none."""
    name = requirement.strip()
    if not name or name.startswith("#") or name.startswith("-"):
        return ""
    for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";"):
        name = name.split(separator)[0]
    name = name.strip()
    if not name:
        return ""
    return DISTRIBUTION_MODULE_OVERRIDES.get(name, name.replace("-", "_"))


def check_declared_dependencies(root: Path) -> list[str]:
    """Every requirement in requirements.txt is importable.

    tree-sitter has been declared since the semantic index was written and was
    not installed in the interpreter running the pipeline, so semantic_index
    fell back to its lexical path on every finding of every run: callers went
    unresolved, coverage_requirements.caller_files came back empty, and the
    evidence packets were quietly weaker than the code believed. Nothing
    reported it, because the fallback is deliberate and silent by design --
    it exists for machines that genuinely cannot build the parser.

    A declared dependency is a promise about the environment, so a missing one
    is reported here rather than discovered as degraded output later.

    Run from a checkout, the promise is requirements.txt, the whole
    experimental environment. A cognalys package installed into
    site-packages promised only what it declares (its requirements without
    extras): numpy, pandas and pulp serve the sampling scripts, and demanding
    them refused every run of a minimal install.
    """
    declared = _installed_package_requirements(root)
    if declared is not None:
        lines, install = declared, f"{sys.executable} -m pip install cognalys"
    else:
        requirements = root / "requirements.txt"
        if not requirements.is_file():
            return [f"requirements.txt not found at {requirements}"]
        lines = requirements.read_text(encoding="utf-8").splitlines()
        install = f"{sys.executable} -m pip install -r requirements.txt"
    missing = []
    for line in lines:
        module = _module_name(line)
        if not module:
            continue
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(f"{line.strip()} (import {module})")
    if not missing:
        return []
    return [
        "declared dependencies are not importable by "
        f"{sys.executable}: " + ", ".join(missing)
        + ". Install them with: " + install
    ]


def _installed_package_requirements(root: Path) -> list[str] | None:
    """The cognalys distribution's own requirements, when it is what runs.

    Only for a package installed into site-packages. Code running from a
    checkout's src/ (directly, or through an editable install) is the
    experiment's code, and requirements.txt applies to it.
    """
    import importlib.metadata

    import cognalys

    if Path(cognalys.__file__).resolve().parents[1].name not in {"site-packages",
                                                                  "dist-packages"}:
        return None
    try:
        requirements = importlib.metadata.requires("cognalys")
    except importlib.metadata.PackageNotFoundError:
        return None
    return [line for line in requirements or [] if "extra ==" not in line]


# pyflakes reports style alongside defects, and this project re-exports
# symbols on purpose -- validate_applied_solutions.py alone imports 40 names
# it never uses, each marked "# noqa: F401". Blocking a run on those would
# make the gate something to skip rather than something to trust, so only
# messages that predict an actual runtime failure count. Everything listed
# here either raises or is rejected by the interpreter; nothing here is a
# matter of taste. Widen it by adding a class name, not by loosening the rule.
BLOCKING_PYFLAKES_MESSAGES = (
    # NameError / UnboundLocalError: the two that cost runs 180505 and 185056.
    "UndefinedName", "UndefinedLocal", "UndefinedExport",
    # Rejected at compile time.
    "DuplicateArgument", "ReturnOutsideFunction", "YieldOutsideFunction",
    "ContinueOutsideLoop", "BreakOutsideLoop", "DefaultExceptNotLast",
    "TwoStarredExpressions", "TooManyExpressionsInStarredAssignment",
    "ImportStarNotPermitted", "LateFutureImport", "FutureFeatureNotDefined",
    "ForwardAnnotationSyntaxError", "InvalidPrintSyntax",
    # Raise when the line executes.
    "RaiseNotImplemented",
    "PercentFormatInvalidFormat", "PercentFormatExpectedMapping",
    "PercentFormatExpectedSequence", "PercentFormatMissingArgument",
    "PercentFormatPositionalCountMismatch", "PercentFormatStarRequiresSequence",
    "PercentFormatUnsupportedFormatCharacter",
    "PercentFormatMixedPositionalAndNamed",
    "StringDotFormatInvalidFormat", "StringDotFormatMissingArgument",
    "StringDotFormatMixingAutomatic",
)


def _blocking_message_types():
    """Resolve the blocking names against the installed pyflakes.

    Reported rather than skipped when a name is gone: a check that silently
    stops checking is worse than no check, and this list is the whole
    substance of the gate.
    """
    from pyflakes import messages as pyflakes_messages

    resolved, unknown = [], []
    for name in BLOCKING_PYFLAKES_MESSAGES:
        message_type = getattr(pyflakes_messages, name, None)
        if message_type is None:
            unknown.append(name)
        else:
            resolved.append(message_type)
    return tuple(resolved), unknown


class _BlockingReporter:
    """Collects only the pyflakes messages that predict a runtime failure."""

    def __init__(self, blocking):
        self.blocking = blocking
        self.findings: list[str] = []

    def flake(self, message):
        if isinstance(message, self.blocking):
            self.findings.append(str(message))

    def syntaxError(self, filename, msg, lineno, offset, text):
        # Never reachable through a normal run -- the module would not import
        # -- but a file that cannot be parsed is also a file pyflakes did not
        # check, so it must not pass silently.
        self.findings.append(f"{filename}:{lineno}: syntax error: {msg}")

    def unexpectedError(self, filename, msg):
        self.findings.append(f"{filename}: could not be checked: {msg}")


def check_static_analysis(root: Path) -> list[str]:
    """No name in the code about to run is undefined or possibly unbound.

    This is the check that would have caught run_20260907_180505 and
    run_20260907_185056 in under a second each, instead of after four minutes
    of model calls that produced nothing.
    """
    try:
        from pyflakes import api as pyflakes_api
    except ImportError:
        return [
            "pyflakes is declared in requirements.txt but is not importable "
            f"by {sys.executable}; static analysis could not run"
        ]

    blocking, unknown = _blocking_message_types()
    problems = []
    if unknown:
        problems.append(
            "the installed pyflakes does not define these message types, so "
            "they were not checked: " + ", ".join(unknown)
        )
    reporter = _BlockingReporter(blocking)
    for directory in ("src",):
        target = root / directory
        if target.is_dir():
            pyflakes_api.checkRecursive([str(target)], reporter)
    if reporter.findings:
        shown = sorted(reporter.findings)[:20]
        remaining = len(reporter.findings) - len(shown)
        problems.append("static analysis found runtime-fatal defects:")
        problems.extend(shown)
        if remaining > 0:
            problems.append(f"... and {remaining} more")
    return problems


def run_preflight(root: Path) -> PreflightReport:
    """Run the checks in dependency order and report what they found."""
    report = PreflightReport()

    dependency_problems = check_declared_dependencies(root)
    report.checks_run.append("declared dependencies")
    if dependency_problems:
        # The remaining checks import the same interpreter's packages and
        # would fail again for this one reason. Report the cause, not its
        # consequences.
        report.problems.extend(dependency_problems)
        return report

    report.checks_run.append("static analysis")
    report.problems.extend(check_static_analysis(root))

    return report
