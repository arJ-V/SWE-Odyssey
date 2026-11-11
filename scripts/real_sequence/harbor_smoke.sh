#!/usr/bin/env bash
# Harbor oracle/nop smoke for a sequence (drafts/docs/REAL_SEQUENCE_GUIDE.md, step 10).
# Runs on the Docker box from a pushed copy of the repo; no API keys involved.
#
#   harbor_smoke.sh <sequence-dir> <T1,T2,T3> <jobs-dir> [cold-subset]
#
# Compiles the ordering under forward-merge and carry, plus --design cold over
# cold-subset (default: the same tickets), then runs Harbor's oracle and nop
# agents on every task with -n 1 (so parallel trials never race an image
# build) and prints one line per trial with each step's reward and error flag.
# HARBOR_COLD_N=<k> runs the cold tasks k at a time instead; use it only when
# the environment image is already in the Docker build cache (every trial's
# build is then a cache hit, so nothing races).
set -uo pipefail
seq_dir="$1"
ordering="$2"
jobs="$3"
cold="${4:-$2}"
export PATH="$HOME/.local/bin:$PATH"
od=(uv run --quiet --python 3.12 python -m odyssey.cli)
tasks="$jobs/tasks"
rm -rf "$tasks"
mkdir -p "$tasks/seq" "$tasks/cold"
for pol in forward-merge carry; do
    "${od[@]}" compile "$seq_dir" --out "$tasks/seq" --ordering "$ordering" \
        --code-state "$pol" --framing informed --feedback none | tail -1
done
"${od[@]}" compile "$seq_dir" --out "$tasks/cold" --design cold --subset "$cold" | tail -1

for agent in oracle nop; do
    for kind in seq cold; do
        name="$agent-$kind"
        n=1
        [ "$kind" = cold ] && n="${HARBOR_COLD_N:-1}"
        start=$(date +%s)
        harbor run -p "$tasks/$kind" -a "$agent" -o "$jobs" --job-name "$name" -n "$n" -y \
            >"$jobs/$name.log" 2>&1
        echo "$name exit $? in $(( $(date +%s) - start ))s"
    done
done

python3 - "$jobs" <<'EOF'
import glob, json, os, sys
jobs = sys.argv[1]
for job in ("oracle-seq", "nop-seq", "oracle-cold", "nop-cold"):
    for rj in sorted(glob.glob(f"{jobs}/{job}/*/result.json")):
        d = json.load(open(rj))
        row = []
        for s in d.get("step_results") or []:
            vr = (s.get("verifier_result") or {}).get("rewards") or {}
            row.append(f"{s.get('step_name')}:{vr.get('reward')}/e{vr.get('error')}"
                       + (" EXC" if s.get("exception_info") else ""))
        exc = d.get("exception_info")
        print(job, os.path.basename(os.path.dirname(rj)), row,
              "TRIAL-EXC:" + str(exc.get("exception_type")) if exc else "")
EOF
