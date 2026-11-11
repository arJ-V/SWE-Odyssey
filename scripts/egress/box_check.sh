#!/usr/bin/env bash
# Docker-box check of `odyssey compile --egress allowlist` (docs/SPEC.md section 11).
# Runs from a pushed copy of the repo; no API key is read or passed, no model is called.
#
#   box_check.sh <sequence-dir> <T1,T2,T3> <cold-ticket> <work-dir>
#
# Compiles one ordering (forward-merge) and one cold ticket with --egress allowlist, then
# for each task runs Harbor's oracle (every step should score 1: grading works with the
# verifier phase at no-network), nop (every step 0) and probe_agent.py (the reachability
# probe of probe.sh, run in the agent phase of every step), plus `--install-only` for
# claude-code and codex (Harbor's agent setup must succeed without downloading anything).
# Prints rewards per step, the probe verdicts (check_probe.py) and the egress columns of
# `odyssey account`.
set -uo pipefail
seq_dir="$1"
ordering="$2"
cold="$3"
work="$4"
here="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$here"          # lets Harbor import probe_agent:EgressProbeAgent
od=(uv run --quiet --python 3.12 python -m odyssey.cli)
tasks="$work/tasks"
jobs="$work/jobs"
rm -rf "$tasks" "$jobs"
mkdir -p "$tasks/seq" "$tasks/cold" "$jobs"
"${od[@]}" compile "$seq_dir" --out "$tasks/seq" --ordering "$ordering" --egress allowlist | head -1
"${od[@]}" compile "$seq_dir" --out "$tasks/cold" --design cold --subset "$cold" --egress allowlist | head -1

run() {
    local name="$1"; shift
    local start; start=$(date +%s)
    harbor run "$@" -o "$jobs" --job-name "$name" -n 1 -y >"$jobs/$name.log" 2>&1
    echo "$name exit $? in $(( $(date +%s) - start ))s"
}
for kind in cold seq; do
    run "oracle-$kind" -p "$tasks/$kind" -a oracle
    run "nop-$kind" -p "$tasks/$kind" -a nop
    run "probe-$kind" -p "$tasks/$kind" -a probe_agent:EgressProbeAgent
done
run install-claude-code -p "$tasks/cold" -a claude-code --install-only
run install-codex -p "$tasks/cold" -a codex --install-only
# Control: the same probe on the default (open) compile must find the deny_* hosts open,
# which shows the probe can tell the two apart.
mkdir -p "$tasks/open"
"${od[@]}" compile "$seq_dir" --out "$tasks/open" --design cold --subset "$cold" | head -1
run probe-open-cold -p "$tasks/open" -a probe_agent:EgressProbeAgent

python3 -I - "$jobs" <<'EOF'
import glob, json, os, sys
jobs = sys.argv[1]
for job in ("oracle-cold", "nop-cold", "probe-cold", "oracle-seq", "nop-seq", "probe-seq",
            "install-claude-code", "install-codex", "probe-open-cold"):
    for rj in sorted(glob.glob(f"{jobs}/{job}/*/result.json")):
        d = json.load(open(rj))
        steps = d.get("step_results") or [dict(d, step_name="(single)")]
        row = []
        for s in steps:
            vr = (s.get("verifier_result") or {}).get("rewards") or {}
            row.append(f"{s.get('step_name')}:{vr.get('reward')}/e{vr.get('error')}"
                       + (" EXC:" + s["exception_info"].get("exception_type", "") if s.get("exception_info") else ""))
        exc = d.get("exception_info")
        setup = d.get("agent_setup") or {}
        print(job, os.path.basename(os.path.dirname(rj)), row,
              "TRIAL-EXC:" + str(exc.get("exception_type")) + " " + str(exc.get("exception_message"))[:300] if exc else "",
              f"agent_setup={setup.get('started_at')}..{setup.get('finished_at')}" if job.startswith("install") else "")
EOF
python3 -I "$here/check_probe.py" "$jobs/probe-cold" "$jobs/probe-seq"
echo "probe check exit $? (0 = every probe as the allowlist policy expects)"
echo "--- control, open egress (failures expected):"
python3 -I "$here/check_probe.py" "$jobs/probe-open-cold" | grep -c ' FAIL deny_' | sed 's/^/deny_* probes that got through with open egress: /'
"${od[@]}" account "$jobs" --out "$work/segments.csv"
python3 -I - "$work/segments.csv" <<'EOF'
import csv, sys
for r in csv.DictReader(open(sys.argv[1])):
    print(r["job"], r["step_name"] or "-", "reward", r["reward"], "allowed", r["egress_allowed"],
          "denied", r["egress_denied"], r["egress_denied_targets"][:200], "|", r["warnings"][:120])
EOF
