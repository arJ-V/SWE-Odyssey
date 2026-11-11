#!/usr/bin/env bash
# Host-side Codex experiment emulating Harbor's codex steps (fresh vs `exec resume --last`).
# Sandboxed (workspace-write), unlike Harbor's in-container bypass. OPENAI_API_KEY must be exported.
# Usage: run_codex_host.sh <out-dir> <fresh|resume> <model>
set -euo pipefail
OUT="$1"; MODE="$2"; MODEL="$3"
CODEX="${CODEX_BIN:-codex}"   # e.g. npm install --prefix /tmp/codex-cli @openai/codex
REPO="$(cd "$(dirname "$0")/../.." && pwd)/drafts/sequences/toy-ledger"
PY="${HARBOR_PYTHON:-$(dirname "$(readlink -f "$(command -v harbor)")")/python3}"
now() { "$PY" -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())'; }
R="$OUT/codex-$MODE"; rm -rf "$R"; mkdir -p "$R/app" "$R/live"
cp -R "$REPO/environment/repo/." "$R/app/"
(cd "$R/app" && git init -q && git add -A && git -c user.name=x -c user.email=x@x commit -qm base)
k=0
for t in LED-1 LED-3 LED-4; do
  k=$((k+1)); step="s0$k-$(echo $t | tr A-Z a-z)"
  home="$R/live/home-$k"; sub=""
  if [ "$MODE" = resume ] && [ $k -gt 1 ]; then
    # Harbor: copy the previous step's /logs/agent/sessions into a fresh CODEX_HOME, then resume --last.
    mkdir -p "$home"; cp -R "$R/steps/$prev/agent/sessions" "$home/sessions"; sub="resume --last"
  fi
  mkdir -p "$home"
  instr="$(cat "$REPO/tickets/$t/ticket.md"; printf '\nWork only in the current directory. Keep the fix minimal.\n')"
  start=$(now)
  (cd "$R/app" && env -i HOME="$HOME" PATH="$PATH" CODEX_HOME="$home" CODEX_API_KEY="$OPENAI_API_KEY" \
      "$CODEX" exec $sub -c sandbox_mode=workspace-write --skip-git-repo-check --model "$MODEL" --json \
        -c model_reasoning_effort=low -- "$instr" </dev/null > "$R/live/codex.txt" 2>&1) || echo "codex exit $? on $step"
  end=$(now)
  d="$R/steps/$step/agent"; mkdir -p "$d"
  [ -d "$home/sessions" ] && cp -R "$home/sessions" "$d/sessions"
  cp "$R/live/codex.txt" "$d/codex.txt"
  printf '{"started_at": "%s", "finished_at": "%s"}\n' "$start" "$end" > "$R/steps/$step/agent_execution.json"
  echo "$MODE $step: rollout files $(find "$d/sessions" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' '); $(tail -c 300 "$d/codex.txt" | tr '\n' ' ' | cut -c1-200)"
  rm -rf "$home"   # Harbor deletes CODEX_HOME after every run
  prev="$step"
done
