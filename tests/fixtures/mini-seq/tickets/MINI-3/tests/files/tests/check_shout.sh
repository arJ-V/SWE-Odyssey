#!/usr/bin/env bash
set -euo pipefail
source ./text.sh
[ "$(shout hello)" = HELLO ]
