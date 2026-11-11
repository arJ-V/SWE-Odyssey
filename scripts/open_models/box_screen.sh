#!/usr/bin/env bash
# Cold screen: run every compiled task dir in <tasks-dir> with one model, one after another.
#
#   <token on stdin> | box_screen.sh <model-key> <tasks-dir> <jobs-dir> <job-prefix> [cap-usd-per-ticket]
#
# Reads the Bedrock bearer token once from stdin and hands it to box_run.sh on its
# stdin for each ticket (printf is a builtin, so the token is never an argv). Mint the
# token with a lifetime covering the whole screen (bedrock_token.py --minutes, max 720).
set -uo pipefail
key=$1 tasks=$2 jobs=$3 prefix=$4 cap=${5:-3}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IFS= read -r token || true
for task in "$tasks"/*__c[0-9]*-*; do
  [ -d "$task" ] || continue
  cell=$(basename "$task" | sed 's/.*__\(c[0-9]*\)-.*/\1/')
  echo "=== $cell $(date -u +%H:%M:%S)"
  printf '%s\n' "$token" | "$here/box_run.sh" "$key" "$task" "$jobs" "$prefix-$cell" "$cap"
done
echo "screen done $(date -u +%H:%M:%S)"
