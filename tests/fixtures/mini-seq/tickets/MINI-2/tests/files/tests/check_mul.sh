#!/usr/bin/env bash
set -euo pipefail
source ./math.sh
[ "$(mul 3 4)" = 12 ] && [ "$(mul 0 9)" = 0 ]
