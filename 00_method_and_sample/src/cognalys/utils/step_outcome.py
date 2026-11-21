#!/usr/bin/env python3
"""Telling "some of it failed" apart from "none of it worked".

Every step of this pipeline is a subprocess that loops over many projects,
and each of those loops already survives one project failing: a clone that
times out, a Gradle build that cannot resolve its SDK, a repository whose
documented branch is gone. The loop catches it, records it, and moves to the
next project.

The exit code then threw that away. `return 1 if failed else 0` reports the
same thing whether one project out of twenty failed or all twenty did, and
run_pipeline.py stops the whole run on any non-zero step. So one bad APK in a
batch ended the run for the nineteen that were fine -- after the step had
already done the work of processing them -- and they never reached
extraction, analysis or a pull request.

`--ignore-step-errors` existed for this and is too blunt to use: it also
marches past a step that achieved nothing at all, into steps that then have
nothing to work on, and forces the final exit code to 0 so nothing
downstream can tell the difference either.

A third code separates the two cases. A step that got some projects through
reports PARTIAL_SUCCESS_EXIT_CODE, the run continues with the survivors and
the failures are named in the summary; a step that got nothing through still
fails the run, because continuing past it only wastes time.
"""
from __future__ import annotations


# Chosen outside the range the step scripts already use: 0 success, 1
# failure, 2 usage error (argparse, and apk_build's own bad-arguments path).
PARTIAL_SUCCESS_EXIT_CODE = 3


def step_exit_code(succeeded: int, failed: int) -> int:
    """The exit code for a step that processed `succeeded` + `failed` units.

    A unit is whatever the step iterates over -- a repository, a project, a
    finding. Nothing failing is success; nothing succeeding is failure; any
    mixture is partial, and the caller decides whether to go on with what
    survived.

    A step that had nothing to do at all reports success. It did not fail:
    an empty batch is a fact about the input, and the steps that genuinely
    cannot proceed on empty input say so themselves.
    """
    if failed <= 0:
        return 0
    if succeeded <= 0:
        return 1
    return PARTIAL_SUCCESS_EXIT_CODE


def describe_partial(step: str, succeeded: int, failed: int) -> str:
    """One line naming what survived, for the operator watching the run."""
    return (
        f"Step '{step}' finished partially: {succeeded} succeeded, "
        f"{failed} failed. Continuing with the {succeeded} that succeeded; "
        f"the failures are recorded in this step's own report."
    )
