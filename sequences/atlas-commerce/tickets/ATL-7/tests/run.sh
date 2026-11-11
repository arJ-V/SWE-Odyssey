#!/usr/bin/env bash
# ATL-7 grader (contract: sequences/atlas-commerce/AUTHORING.md, "run.sh").
# pytest on test_outputs.py (with policy_semantics.py beside it), then, only if every test
# passes, the account-wide wildcard-role guard (with its scope file). pytest 8.4.1 comes from the
# grader venv, nothing is installed at grade time, and the report goes to $ATLAS_OUT as JUnit.
set -u
run() { "$ATLAS_PYTHON" -I -B "$ATLAS_LIB/atlas_run.py" "$@"; }
run pytest --name atl7 "$ATLAS_TICKET_DIR/test_outputs.py"
status=$?
if [ "$status" -eq 0 ]; then
    run check --name iam_wildcard_role_guard -- "$ATLAS_PYTHON" -I -B "$ATLAS_TICKET_DIR/iam_wildcard_role_guard.py"
    status=$?
fi
run details
exit "$status"
