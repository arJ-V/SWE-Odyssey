#!/usr/bin/env bash
# ATL-6 grader (contract: sequences/atlas-commerce/AUTHORING.md, "run.sh").
# pytest on iam_gate.py, test_final_state.py and the envelope check (test_atl6.py) in one run;
# then, only if they all pass, the account-wide wildcard-role guard (with its scope file beside
# it). pytest comes from the grader venv, and the IAM gate's engine is the image's pinned
# /opt/iam-gate (resolved through /node_modules).
set -u
run() { "$ATLAS_PYTHON" -I -B "$ATLAS_LIB/atlas_run.py" "$@"; }
run pytest --name atl6 "$ATLAS_TICKET_DIR/iam_gate.py" "$ATLAS_TICKET_DIR/test_final_state.py" \
    "$ATLAS_TICKET_DIR/test_atl6.py"
status=$?
if [ "$status" -eq 0 ]; then
    run check --name iam_wildcard_role_guard -- "$ATLAS_PYTHON" -I -B "$ATLAS_TICKET_DIR/iam_wildcard_role_guard.py"
    status=$?
fi
run details
exit "$status"
