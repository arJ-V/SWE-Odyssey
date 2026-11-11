#!/usr/bin/env bash
# Host-side Claude Code experiment emulating Harbor's per-step agent dirs (fresh vs --continue).
# Usage: run_claude_host.sh <out-dir> <mode: fresh|resume>   (ANTHROPIC_API_KEY must be exported)
set -euo pipefail
OUT="$1"; MODE="$2"
REPO="$(cd "$(dirname "$0")/../.." && pwd)/drafts/sequences/toy-ledger"
PY="${HARBOR_PYTHON:-$(dirname "$(readlink -f "$(command -v harbor)")")/python3}"
now() { "$PY" -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())'; }
rm -rf "$OUT/$MODE"; mkdir -p "$OUT/$MODE/app" "$OUT/$MODE/live"
cp -R "$REPO/environment/repo/." "$OUT/$MODE/app/"
(cd "$OUT/$MODE/app" && git init -q && git add -A && git -c user.name=x -c user.email=x@x commit -qm base)
k=0
for t in LED-1 LED-3 LED-4; do
  k=$((k+1)); step="s0$k-$(echo $t | tr A-Z a-z)"
  if [ "$MODE" = fresh ]; then cfg="$OUT/$MODE/live/sessions-$k"; cont=""; else cfg="$OUT/$MODE/live/sessions"; cont="--continue"; [ $k = 1 ] && cont=""; fi
  mkdir -p "$cfg"
  { cat "$REPO/tickets/$t/ticket.md"; printf '\nWork only in the current directory. You cannot run shell commands: read and edit files only. Keep the fix minimal.\n'; } > "$OUT/$MODE/live/instr-$k.md"
  start=$(now)
  (cd "$OUT/$MODE/app" && env -i HOME="$HOME" PATH="$PATH" ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
      CLAUDE_CONFIG_DIR="$cfg" DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
      claude --verbose --output-format=stream-json --model claude-haiku-5-5 \
        --permission-mode acceptEdits --allowedTools Read Edit Write Glob Grep --disallowedTools Bash \
        --max-turns 25 --max-budget-usd 0.30 $cont --print < "$OUT/$MODE/live/instr-$k.md" \
      > "$OUT/$MODE/live/claude-code.txt" 2>&1) || echo "claude exit $? on $step"
  end=$(now)
  d="$OUT/$MODE/steps/$step/agent"; mkdir -p "$d"
  cp -R "$cfg" "$d/sessions"; cp "$OUT/$MODE/live/claude-code.txt" "$d/claude-code.txt"
  printf '{"started_at": "%s", "finished_at": "%s"}\n' "$start" "$end" > "$OUT/$MODE/steps/$step/agent_execution.json"
  echo "$MODE $step done: $(grep -c '' "$d/claude-code.txt") stream lines; jsonl files: $(find "$d/sessions/projects" -name '*.jsonl' | wc -l | tr -d ' ')"
done
