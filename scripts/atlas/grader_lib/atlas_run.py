#!/usr/bin/env python3
"""Run a ticket's checks and record each as a JUnit report in $ATLAS_OUT (used by tickets' run.sh).

    atlas_run.py pytest --name NAME FILE...        pytest on the files -> $ATLAS_OUT/junit-NAME.xml
    atlas_run.py check  --name NAME -- CMD ARG...  any command; exit 0 = pass -> junit-NAME.xml (one test)
    atlas_run.py details                           $ATLAS_OUT/details.json from every junit-*.xml and
                                                   the ticket's grading lists ($ATLAS_GRADING)

Each subcommand exits with the check's own status, so run.sh can chain them. test.sh decides the
verdict from run.sh's exit code AND these reports (verdict.py); neither alone is trusted.
pytest runs with -I -B, no cache provider, no config file (-c /dev/null) and its rootdir in a
scratch directory, so nothing in the agent's tree or in /memory can configure it. It runs through
atlas_pytest.py, which reports a pytest.fail() raised by a fixture as the test's failure rather
than an error (graders fail that way when a pipeline never converges).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape


def out_dir() -> Path:
    path = Path(os.environ["ATLAS_OUT"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_pytest(name: str, files: list[str]) -> int:
    out = out_dir()
    junit = out / f"junit-{name}.xml"
    junit.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas-pytest-") as rootdir:
        # atlas_pytest.py = pytest plus one plugin: pytest.fail() from a fixture is the test's
        # failure, not an error (see its docstring).
        cmd = [sys.executable, "-I", "-B", str(Path(__file__).resolve().with_name("atlas_pytest.py")),
               "-p", "no:cacheprovider", "-c", os.devnull,
               "--rootdir", rootdir, f"--junitxml={junit}", "-o", "junit_family=xunit2",
               "-rA", "-v", *files]
        print(f"[atlas_run] {' '.join(cmd)}", flush=True)
        return subprocess.call(cmd, cwd=rootdir)


def run_check(name: str, command: list[str]) -> int:
    out = out_dir()
    start = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True)
    seconds = time.monotonic() - start
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    failure = ""
    if result.returncode != 0:
        tail = (result.stdout + result.stderr)[-4000:]
        failure = f'<failure message="exit {result.returncode}">{escape(tail)}</failure>'
    (out / f"junit-{name}.xml").write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite name="{escape(name)}" tests="1" '
        f'failures="{1 if failure else 0}" errors="0" skipped="0" time="{seconds:.3f}">'
        f'<testcase classname="atlas.check" name="{escape(name)}" time="{seconds:.3f}">{failure}</testcase>'
        f'</testsuite></testsuites>\n')
    return result.returncode


def parse_junit(path: Path) -> dict[str, str]:
    """test name -> passed | failed | error | skipped."""
    outcomes = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        name = case.get("name", "")
        if case.find("error") is not None:
            outcomes[name] = "error"
        elif case.find("failure") is not None:
            outcomes[name] = "failed"
        elif case.find("skipped") is not None:
            outcomes[name] = "skipped"
        else:
            outcomes[name] = "passed"
    return outcomes


def details() -> int:
    out = out_dir()
    outcomes: dict[str, str] = {}
    for junit in sorted(out.glob("junit-*.xml")):
        try:
            outcomes.update(parse_junit(junit))
        except ET.ParseError as exc:
            outcomes[f"<unreadable {junit.name}>"] = "error"
            print(f"[atlas_run] {junit}: {exc}", file=sys.stderr)
    grading = {}
    if os.environ.get("ATLAS_GRADING") and Path(os.environ["ATLAS_GRADING"]).is_file():
        grading = json.loads(Path(os.environ["ATLAS_GRADING"]).read_text())
    groups = {"fail_to_pass": grading.get("fail_to_pass", []), "pass_to_pass": grading.get("pass_to_pass", [])}
    listed = {t for names in groups.values() for t in names}
    groups["other"] = sorted(t for t in outcomes if t not in listed)
    result = {}
    for group, names in groups.items():
        counts = {"tests": len(names), "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for test in names:
            status = outcomes.get(test, "missing")
            key = {"passed": "passed", "failed": "failed", "skipped": "skipped"}.get(status, "errors")
            counts[key] += 1
        counts["outcomes"] = {t: outcomes.get(t, "missing") for t in names}
        result[group] = counts
    (out / "details.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pytest")
    p.add_argument("--name", required=True)
    p.add_argument("files", nargs="+")
    c = sub.add_parser("check")
    c.add_argument("--name", required=True)
    c.add_argument("command", nargs=argparse.REMAINDER)
    sub.add_parser("details")
    args = parser.parse_args()
    if args.cmd == "pytest":
        return run_pytest(args.name, args.files)
    if args.cmd == "check":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("check needs a command after --")
        return run_check(args.name, command)
    return details()


if __name__ == "__main__":
    sys.exit(main())
