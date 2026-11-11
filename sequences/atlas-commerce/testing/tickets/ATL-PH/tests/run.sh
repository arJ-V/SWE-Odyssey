#!/usr/bin/env bash
set -u
"$ATLAS_PYTHON" -I -B "$ATLAS_LIB/atlas_run.py" pytest --name placeholder "$ATLAS_TICKET_DIR/test_placeholder.py"
status=$?
"$ATLAS_PYTHON" -I -B "$ATLAS_LIB/atlas_run.py" details
exit "$status"
