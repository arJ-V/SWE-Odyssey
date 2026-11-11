#!/usr/bin/env python3
"""pytest entry point for atlas graders (called by atlas_run.py; not meant to be run by hand).

    python -I -B atlas_pytest.py <pytest args>

It runs pytest in this process with one plugin: a `pytest.fail()` raised while a test's fixtures
are set up is reported as that test's FAILURE instead of an ERROR. Graders use this pattern
for "the pipeline never converged" (a fixture that calls pytest.fail after a bounded wait for the
pipeline to settle), and the atlas validator reserves `error` for a
broken grader (an import error, a missing package, a crashed fixture). Only `pytest.fail` from
setup is relabelled: every other setup exception stays an error, and the outcome is unchanged
either way (the test does not pass, pytest exits non-zero).
"""

from __future__ import annotations

import sys

import pytest


class SetupFailIsFailure:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if (call.when == "setup" and report.failed and call.excinfo is not None
                and call.excinfo.errisinstance(pytest.fail.Exception)):
            # junitxml writes <failure> for a failed "call" report and <error> for any other phase.
            report.when = "call"


if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:], plugins=[SetupFailIsFailure()]))
