#!/usr/bin/env bash
set -euo pipefail
source ./math.sh
[ "$(add 2 3)" = 5 ] && [ "$(add -4 4)" = 0 ]
