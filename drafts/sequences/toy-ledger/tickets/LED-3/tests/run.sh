#!/usr/bin/env bash
# Grader for one toy-ledger ticket (SPEC section 5).
#
# cwd is a grading copy of the repository prepared by the step's test.sh:
# pytest.ini, tests/ and every conftest.py come from the segment's trusted start
# tree, not from the agent, and the hidden tests sit under tests_hidden/.
# Exit 0 iff the ticket is resolved: the fail-to-pass (F2P) tests and the
# pass-to-pass (P2P) slice of the visible suite all ran and all passed. If
# ODYSSEY_OUT is set, per-group counts go to $ODYSSEY_OUT/details.json.
#
# Only the three variables below differ between tickets.
set -euo pipefail

TICKET="LED-3"
F2P=(tests_hidden/test_led3_balance_sheet_equity.py)
P2P=(tests/test_reports.py tests/test_ledger.py)

PYTHON="${ODYSSEY_PYTHON:-python3}"
# -I: the grading copy is never on sys.path (no shadowing pytest or stdlib
# modules from the repo root) and PYTHON* variables are ignored; -B: no
# bytecode. lib/ is put on the path by the trusted pytest.ini (`pythonpath`).
PY=("$PYTHON" -I -B)

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

run_group() {
    # run_group <name> <test files...>: run pytest, leave junit xml in $work.
    local name="$1"
    shift
    echo "== $TICKET $name: $*"
    "${PY[@]}" -m pytest -c pytest.ini --rootdir . -q -p no:cacheprovider -W error \
        --junitxml="$work/$name.xml" "$@"
}

f2p_status=0
p2p_status=0
run_group f2p "${F2P[@]}" || f2p_status=$?
run_group p2p "${P2P[@]}" || p2p_status=$?

# The verdict needs both a zero exit and a junit report that shows tests ran
# and none failed: code that ends the pytest process early with status 0 (for
# example os._exit(0) at import time) leaves no report and does not pass.
verdict=0
"${PY[@]}" - "$work" "$TICKET" "$f2p_status" "$p2p_status" "${ODYSSEY_OUT:-}" <<'PY' || verdict=$?
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

work, ticket = Path(sys.argv[1]), sys.argv[2]
f2p_status, p2p_status = int(sys.argv[3]), int(sys.argv[4])
out = sys.argv[5]


def counts(name, status):
    path = work / f"{name}.xml"
    result = {"exit_code": status, "report": path.exists(),
              "tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    if not path.exists():
        return result
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        result["report"] = False
        return result
    for suite in [root] if root.tag == "testsuite" else root.iter("testsuite"):
        for key, attr in (("tests", "tests"), ("failed", "failures"), ("errors", "errors"), ("skipped", "skipped")):
            result[key] += int(suite.get(attr, 0))
    result["passed"] = result["tests"] - result["failed"] - result["errors"] - result["skipped"]
    return result


def clean(group):
    return (group["exit_code"] == 0 and group["report"] and group["tests"] > 0
            and group["failed"] == 0 and group["errors"] == 0)


f2p, p2p = counts("f2p", f2p_status), counts("p2p", p2p_status)
resolved = clean(f2p) and clean(p2p)
if out:
    Path(out).mkdir(parents=True, exist_ok=True)
    details = {"ticket": ticket, "resolved": resolved, "fail_to_pass": f2p, "pass_to_pass": p2p}
    (Path(out) / "details.json").write_text(json.dumps(details, indent=2) + "\n")
sys.exit(0 if resolved else 1)
PY

if [ "$verdict" -eq 0 ]; then
    echo "== $TICKET resolved"
    exit 0
fi
echo "== $TICKET not resolved (f2p exit $f2p_status, p2p exit $p2p_status)"
exit 1
