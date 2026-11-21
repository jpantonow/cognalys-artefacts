#!/usr/bin/env python3
"""Which files a patch changes, answered once from the adjudicated plan.

The v2 patch plan already states the edits. The legacy projection then
re-derived facts from it -- which paths count as reviewed, whether the change
is multi-file -- and the scope validator re-derived its own answers from the
projection's output. Two derivations of the same facts, checked against each
other rather than against the plan, produced four defects in one session:

  * a required NEW TEST FILE counted as a second changed file, so a
    one-production-file fix reported multi_file_change_required while the
    adjudicator answered FIX_SUPPORTED -- a combination the validator rejects
    outright, and the reason me.blog.korn123.easydiary's Zip Slip finding
    abstained with INCOMPLETE_MULTI_FILE_SCOPE in run_20260908_151705;
  * minimum_planned_files deliberately allows a review-only plan to be one
    file while the multi_file_change_required check demanded two, so the same
    plan passed one gate and died on the other inside a single function;
  * coverage files handed to the adjudicator as mandatory evidence were never
    credited as reviewed, because only plan edits and semantic relations were;
  * "is this a test file" had four regexes in four modules.

One object, built from the plan, answers all of it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# Gradle/Maven layout. Deliberately the narrow form: a path is a test because
# of where it lives, not because its name happens to contain "Test".
_TEST_PATH = re.compile(r"(?:^|/)src/(?:test|androidTest)/", re.IGNORECASE)


def is_test_path(source_file_path: str) -> bool:
    """Whether a planned edit is a test rather than a production change."""
    return bool(_TEST_PATH.search(str(source_file_path)))


@dataclass(frozen=True)
class PatchScope:
    """The files an adjudicated patch plan changes."""

    production_files: frozenset[str] = frozenset()
    test_files: frozenset[str] = frozenset()

    @classmethod
    def from_required_edits(cls, required_edits: Iterable[dict]) -> "PatchScope":
        production, tests = set(), set()
        for item in required_edits or ():
            if not isinstance(item, dict):
                continue
            path = str(item.get("source_file_path", "")).strip()
            if not path:
                continue
            (tests if is_test_path(path) else production).add(path)
        return cls(frozenset(production), frozenset(tests))

    @property
    def all_files(self) -> frozenset[str]:
        return self.production_files | self.test_files

    @property
    def is_multi_file(self) -> bool:
        """Whether the patch genuinely coordinates a change across files.

        Production files only. A required regression test is not evidence
        that a change is coordinated -- it is evidence the change is tested --
        and counting it turned every single-file fix that ships a test into a
        contradictory multi-file one.
        """
        return len(self.production_files) > 1

    def action_for(self, source_file_path: str) -> str:
        """The reviewed_files action label for a planned edit."""
        return "required_test" if is_test_path(source_file_path) else "edit"
