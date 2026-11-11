#!/usr/bin/env bash
# ATL-5 grader (contract: sequences/atlas-commerce/AUTHORING.md, "run.sh").
# test_outputs.py, then, only if every test passes, the account-wide wildcard-role guard (with its
# scope file beside it). A convergence timeout in the session fixture (pytest.fail in
# `pipeline_state`) is reported as the tests' failure, not an error, by atlas_run's pytest entry
# point (grader_lib/atlas_pytest.py); no outcome changes.
set -u
run() { "$ATLAS_PYTHON" -I -B "$ATLAS_LIB/atlas_run.py" "$@"; }
run pytest --name atl5 "$ATLAS_TICKET_DIR/test_outputs.py"
status=$?
if [ "$status" -eq 0 ]; then
    run check --name iam_wildcard_role_guard -- "$ATLAS_PYTHON" -I -B "$ATLAS_TICKET_DIR/iam_wildcard_role_guard.py"
    status=$?
fi
run details
exit "$status"
