#!/usr/bin/env bash
# Run one compiled SWE-Odyssey task with an open-weight model on Amazon Bedrock.
#
#   <token on stdin> | box_run.sh <model-key> <task-dir> <jobs-dir> <job-name> [cap-usd]
#
# model-key: kimi-k3 | kimi-k3.1 | glm-5 | minimax-m2.5 | deepseek-v3.2 | qwen3-coder-next
#            (kimi-cli or mini-swe-agent), or the same names prefixed dsh- (the DeepSeek
#            Harness for every model, e.g. dsh-glm-5; tasks compiled with --agent-cli dsh).
# ODYSSEY_HARBOR_EXTRA: more `harbor run` arguments, e.g. "--resume-trajectory" (dsh only).
# The short-lived Bedrock bearer token (scripts/open_models/bedrock_token.py) is read from
# stdin into this process's environment only: it is never an argument, never written to
# disk by this script, and harbor_agents.py keeps it off docker's command line too.
# cap-usd (default 3): the run is stopped when its spend, computed from the harness's own
# per-call token usage and odyssey/prices.toml, passes the cap (kimi-cli: a watchdog
# reads the live wire log; mini-swe-agent: its own --cost-limit with registered prices).
set -uo pipefail
key=$1 task=$2 jobs=$3 job=$4 cap=${5:-3}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
export PATH="$HOME/.local/bin:$PATH" PYTHONPATH="$here"
BEDROCK_URL=https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1

IFS= read -r token || true
case "$token" in bedrock-api-key-*) ;; *) echo "no bearer token on stdin" >&2; exit 2 ;; esac

agent_args=() model= secret=
case "$key" in
  kimi-k3|kimi-k3.1)
    model="openai/us.moonshotai.${key}"
    agent_args=(-a harbor_agents:KimiCliPreinstalled --ak "base_url=$BEDROCK_URL")
    export OPENAI_API_KEY="$token" KIMI_MODEL_MAX_CONTEXT_SIZE=262144
    secret=OPENAI_API_KEY ;;
  glm-5|minimax-m2.5|deepseek-v3.2|qwen3-coder-next)
    case "$key" in
      glm-5) id=zai.glm-5 ;; minimax-m2.5) id=minimax.minimax-m2.5 ;;
      deepseek-v3.2) id=deepseek.v3.2 ;; qwen3-coder-next) id=qwen.qwen3-coder-next ;;
    esac
    model="openai/$id"
    # Prices from odyssey/prices.toml, so mini-swe-agent can enforce --cost-limit itself.
    registry="$(mktemp -d)/registry.json"
    python3 - "$repo/odyssey/prices.toml" "$id" "$model" > "$registry" <<'PY'
import json, sys, tomllib
p = tomllib.load(open(sys.argv[1], "rb"))["models"][sys.argv[2]]
entry = {"input_cost_per_token": p["input"] / 1e6, "output_cost_per_token": p["output"] / 1e6,
         "litellm_provider": "openai", "mode": "chat", "max_input_tokens": 200000,
         "max_output_tokens": 32000, "supports_function_calling": True}
print(json.dumps({sys.argv[3]: entry, sys.argv[2]: entry}))
PY
    agent_args=(-a harbor_agents:MiniSweAgentPreinstalled --ak "litellm_model_registry_file=$registry"
                --ak "cost_limit=$cap" --ae LITELLM_LOCAL_MODEL_COST_MAP=True)
    # OPENAI_API_KEY, not MSWEA_API_KEY: Harbor forwards only MSWEA_API_KEY when that is
    # set, and litellm inside mini-swe-agent then finds no OpenAI key (measured).
    export OPENAI_API_KEY="$token" OPENAI_BASE_URL="$BEDROCK_URL"
    secret=OPENAI_API_KEY ;;
  dsh-*)
    case "${key#dsh-}" in
      kimi-k3|kimi-k3.1) id="us.moonshotai.${key#dsh-}" ;; glm-5) id=zai.glm-5 ;;
      minimax-m2.5) id=minimax.minimax-m2.5 ;; deepseek-v3.2) id=deepseek.v3.2 ;;
      qwen3-coder-next) id=qwen.qwen3-coder-next ;; *) echo "unknown model key $key" >&2; exit 2 ;;
    esac
    model="openai/$id"
    # DshPreinstalled copies OPENAI_API_KEY into the container as DSH_BEDROCK_API_KEY and
    # enforces cap_usd with a watchdog inside the container (harbor_agents.py).
    agent_args=(-a harbor_agents:DshPreinstalled --ak "base_url=$BEDROCK_URL" --ak "cap_usd=$cap")
    export OPENAI_API_KEY="$token"
    secret=OPENAI_API_KEY ;;
  *) echo "unknown model key $key" >&2; exit 2 ;;
esac
read -r -a extra <<< "${ODYSSEY_HARBOR_EXTRA:-}"
unset token
export ODYSSEY_SECRET_ENV="$secret"

mkdir -p "$jobs"
watch_pid=
if [[ $key == kimi-* ]]; then
  # kimi-cli has no spend limit: stop the trial when the live wire log says the cap is reached.
  python3 "$here/spend_watch.py" "$jobs/$job" "$repo/odyssey/prices.toml" "us.moonshotai.${key}" "$cap" "$job" &
  watch_pid=$!
fi
for try in 1 2 3 4 5 6; do
  rm -rf "$jobs/$job"
  harbor run -p "$task" "${agent_args[@]}" ${extra[@]+"${extra[@]}"} -m "$model" -o "$jobs" --job-name "$job" -n 1 -y \
    > "$jobs/$job.log" 2>&1; rc=$?
  # Harbor's allowlist start-up probe times out when the box is loaded: retry.
  if grep -aq "is not supported by" "$jobs/$job.log"; then echo "try $try: egress probe timed out, retrying"; sleep 45; continue; fi
  break
done
[[ -n $watch_pid ]] && kill "$watch_pid" 2>/dev/null
echo "harbor exit $rc (try $try)"
tail -12 "$jobs/$job.log"
# The token must not have reached any output.
# (-f with a process substitution: printf is a builtin, so the token is never an argv.)
if grep -rlaF -f <(printf '%s\n' "$OPENAI_API_KEY") "$jobs/$job" "$jobs/$job.log" 2>/dev/null \
    | head -3 | grep -q .; then
  echo "KEY FOUND IN OUTPUT"; else echo "token scan: clean"; fi
grep -rlaE "bedrock-api-key-[A-Za-z0-9+/=]{20}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}" "$jobs/$job" "$jobs/$job.log" 2>/dev/null | head -3 | sed 's/^/KEY PATTERN IN: /'
# Files the agent container wrote as root (kimi-cli's share/telemetry) are unreadable here,
# so the scans above skip them silently: say so, and scan them with `sudo grep` if any.
unread=$(find "$jobs/$job" ! -readable 2>/dev/null | wc -l)
[ "$unread" -gt 0 ] && echo "WARNING: $unread paths unreadable to $(id -un) were not scanned (root-owned); re-run the pattern scan with sudo"
echo "pattern scan done"
