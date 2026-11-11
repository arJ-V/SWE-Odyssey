#!/usr/bin/env bash
# ATL-2 grader (contract: sequences/atlas-commerce/AUTHORING.md, "run.sh").
# The pytest checks, then, only if they all pass, the account-wide wildcard-role guard (with its
# scope file beside it).
set -u
run() { "$ATLAS_PYTHON" -I -B "$ATLAS_LIB/atlas_run.py" "$@"; }
run pytest --name atl2 "$ATLAS_TICKET_DIR/test_atl2.py"
status=$?
if [ "$status" -eq 0 ]; then
    run check --name iam_wildcard_role_guard -- "$ATLAS_PYTHON" -I -B "$ATLAS_TICKET_DIR/iam_wildcard_role_guard.py"
    status=$?
fi
run details
exit "$status"
