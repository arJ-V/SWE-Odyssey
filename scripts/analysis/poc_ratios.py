#!/usr/bin/env python3
"""Reproduce the httrack pilot's warm-to-cold ratios from the accounting rows.

    python3 scripts/analysis/poc_ratios.py [--out jobs/poc-httrack/summary.json]

Inputs (written by `odyssey account`), listed per system in SYSTEMS below:
    jobs/screen-httrack/segments.csv   first cold run of every ticket (Opus 5.5, GPT-6.1 Sol)
    jobs/poc-httrack/segments.csv      their second cold run and six warm orderings
    jobs/r2-httrack/segments.csv       cheaper models (two cold runs, warm orderings) and
                                       the carry arm (the agent keeps its own code)

For each warm ticket-run the metric is divided by the mean of that ticket's cold runs for
the same system. Ratios are combined as geometric means: by position, and over positions
2 to 6 (position 1 of a warm sequence has no history, so it is a cold run in disguise and
is reported but kept out of the headline). Intervals are 95% percentile bootstrap,
resampling the six sequences with replacement and, independently, each ticket's cold runs.
Failed runs are kept at what they cost. Fixed seed, so the output is reproducible.
"""
import argparse
import collections
import csv
import json
import math
import random
import statistics as st
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HTTRACK_TICKETS = ["HTT-03", "HTT-04", "HTT-05", "HTT-06", "HTT-09", "HTT-10"]
# label -> where its cold runs and warm sequences are. A cold source is (file, job) or
# (file, None, model) for the screen file, whose job names are per ticket.
SCREEN = "jobs/screen-httrack/segments.csv"
POC = "jobs/poc-httrack/segments.csv"
R2 = "jobs/r2-httrack/segments.csv"
DUCK = "jobs/duck-paid/segments.csv"
ATLAS = "jobs/atlas-paid/segments.csv"
# open-weight model rows, with each model's three warm jobs (-warm-a/b/c) renamed to -warm
ATLAS_KIMI = "jobs/atlas-kimi/segments.csv"
ATLAS_TICKETS = ["ATL-%d" % i for i in range(1, 8)]
DUCK_TICKETS = ["DDB-03", "DDB-04", "DDB-06", "DDB-07", "DDB-08", "DDB-10"]
SYSTEMS = {
    "Claude Code + Opus 5.5 (reset)": {"cold": [(SCREEN, None, "claude-opus-5-5"), (POC, "opus55-cold2")], "warm": (POC, "opus55-warm")},
    "Codex + GPT-6.1 Sol (reset)": {"cold": [(SCREEN, None, "gpt-6.1-sol"), (POC, "gpt61sol-cold2")], "warm": (POC, "gpt61sol-warm")},
    "Claude Code + Opus 5.5 (carry)": {"cold": [(SCREEN, None, "claude-opus-5-5"), (POC, "opus55-cold2")], "warm": (R2, "opus55-carry")},
    "Codex + GPT-6.1 Sol (carry)": {"cold": [(SCREEN, None, "gpt-6.1-sol"), (POC, "gpt61sol-cold2")], "warm": (R2, "gpt61sol-carry")},
    "Claude Code + Sonnet 5.5 (reset)": {"cold": [(R2, "sonnet55-cold1"), (R2, "sonnet55-cold2")], "warm": (R2, "sonnet55-warm")},
    "Claude Code + Haiku 5.5 (reset)": {"cold": [(R2, "haiku55-cold1"), (R2, "haiku55-cold2")], "warm": (R2, "haiku55-warm")},
    "Codex + GPT-6.1 Terra (reset)": {"cold": [(R2, "terra61-cold1"), (R2, "terra61-cold2")], "warm": (R2, "terra61-warm")},
    "Codex + GPT-6 Luna (reset)": {"cold": [(R2, "luna6-cold1"), (R2, "luna6-cold2")], "warm": (R2, "luna6-warm")},
    "atlas: Claude Code + Opus 5.5": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS, "opus55-cold1"), (ATLAS, "opus55-cold2")], "warm": (ATLAS, "opus55-warm")},
    "atlas: Codex + GPT-6.1 Sol": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS, "gpt61sol-cold1"), (ATLAS, "gpt61sol-cold2")], "warm": (ATLAS, "gpt61sol-warm")},
    "atlas: Claude Code + Sonnet 5.5": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS, "sonnet55-cold1"), (ATLAS, "sonnet55-cold2")], "warm": (ATLAS, "sonnet55-warm")},
    "atlas: Claude Code + Haiku 5.5": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS, "haiku55-cold1"), (ATLAS, "haiku55-cold2")], "warm": (ATLAS, "haiku55-warm")},
    "atlas: Codex + GPT-6.1 Terra": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS, "terra61-cold1"), (ATLAS, "terra61-cold2")], "warm": (ATLAS, "terra61-warm")},
    "atlas: Codex + GPT-6 Luna": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS, "luna6-cold1"), (ATLAS, "luna6-cold2")], "warm": (ATLAS, "luna6-warm")},
    "atlas: DeepSeek Harness + Kimi K3": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS_KIMI, "kimi-cold1"), (ATLAS_KIMI, "kimi-cold2")], "warm": (ATLAS_KIMI, "kimi-warm")},
    "atlas: DeepSeek Harness + Kimi K3.1": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS_KIMI, "kimi31-cold1"), (ATLAS_KIMI, "kimi31-cold2")], "warm": (ATLAS_KIMI, "kimi31-warm")},
    "atlas: DeepSeek Harness + DeepSeek V3.2": {"tickets": ATLAS_TICKETS, "last": 7, "cold": [(ATLAS_KIMI, "deepseek-cold1"), (ATLAS_KIMI, "deepseek-cold2")], "warm": (ATLAS_KIMI, "deepseek-warm")},
    "duckdb: Codex + GPT-6.1 Sol (reset)": {"tickets": DUCK_TICKETS, "cold": [(DUCK, "gpt61sol-cold"), (DUCK, "gpt61sol-cold2")], "warm": (DUCK, "gpt61sol-warm")},
}
METRICS = ["it_delta_net", "sdc_usd", "n_llm_calls", "agent_seconds"]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def gmean(values):
    return math.exp(st.mean(math.log(v) for v in values))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--boot", type=int, default=4000)
    args = ap.parse_args()

    cache = {}

    def rows_of(path):
        if path not in cache:
            f = REPO / path
            cache[path] = list(csv.DictReader(open(f))) if f.exists() else []
        return cache[path]

    random.seed(7)
    out = {}
    for label, spec in SYSTEMS.items():
        TICKETS = spec.get("tickets", HTTRACK_TICKETS)
        cold = collections.defaultdict(list)
        for src in spec["cold"]:
            for r in rows_of(src[0]):
                if r.get("ticket") not in TICKETS or not r.get("model"):
                    continue
                if (src[1] and r["job"] == src[1]) or (len(src) == 3 and r["model"] == src[2]):
                    cold[r["ticket"]].append(r)
        warm = [r for r in rows_of(spec["warm"][0]) if r["job"] == spec["warm"][1]]
        # A sequence in which any ticket died on a provider rate limit is not a measurement
        # of the system: drop the whole sequence and say so.
        bad = {r["ordering_id"] for r in warm if "RateLimit" in (r.get("exception") or "")}
        n_all = len({r["ordering_id"] for r in warm})
        warm = [r for r in warm if r["ordering_id"] not in bad]
        if bad:
            print(f"\n{label}: dropped {len(bad)} of {n_all} sequences hit by provider rate limits")
        if not warm or any(not cold[t] for t in TICKETS):
            print(f"\n{label}: not enough rows yet (warm {len(warm)}, cold {sum(len(cold[t]) for t in TICKETS)})")
            continue
        model = label
        seqs = collections.defaultdict(list)
        for r in warm:
            seqs[r["ordering_id"]].append(r)
        res = {
            "sequences_dropped_for_rate_limits": len(bad),
            "sequences_used": len(seqs),
            "refusals": sum(1 for r in warm if "Refusal" in (r.get("exception") or "")),
            "warm_ticket_runs": len(warm),
            "warm_solved": sum(int(num(r["reward"])) for r in warm),
            "cold_runs_per_ticket": {t: len(cold[t]) for t in TICKETS},
            "cold_solved": sum(int(num(r["reward"])) for t in TICKETS for r in cold[t]),
            "orderings": sorted(seqs),
        }
        print(f"\n{label}: warm {res['warm_solved']}/{res['warm_ticket_runs']} solved, "
              f"cold {res['cold_solved']}/{sum(res['cold_runs_per_ticket'].values())} solved")
        for metric in METRICS:
            def estimate(seq_ids, cold_pick):
                cm = {t: st.mean(num(r[metric]) for r in cold_pick[t]) for t in TICKETS}
                by_pos = collections.defaultdict(list)
                for s in seq_ids:
                    for r in seqs[s]:
                        if num(r[metric]) > 0 and cm[r["ticket"]] > 0:
                            by_pos[int(r["position"])].append(num(r[metric]) / cm[r["ticket"]])
                pos = {p: gmean(v) for p, v in by_pos.items()}
                late = gmean([x for p, v in by_pos.items() if p >= 2 for x in v])
                return pos, late

            pos, late = estimate(list(seqs), cold)
            ids = list(seqs)
            boots, boot_pos = [], collections.defaultdict(list)
            for _ in range(args.boot):
                sample = [random.choice(ids) for _ in ids]
                cold_pick = {t: [random.choice(cold[t]) for _ in cold[t]] for t in TICKETS}
                p, l = estimate(sample, cold_pick)
                boots.append(l)
                for k, v in p.items():
                    boot_pos[k].append(v)
            boots.sort()
            lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
            res[metric] = {
                "cold_mean_per_ticket": {t: round(st.mean(num(r[metric]) for r in cold[t]), 4) for t in TICKETS},
                "by_position": {p: round(pos[p], 2) for p in sorted(pos)},
                "pos2plus": round(late, 2),
                "ci95": [round(lo, 2), round(hi, 2)],
            }
            print(f"  {metric:13s} positions 2+: {late:.2f}  95% [{lo:.2f}, {hi:.2f}]   "
                  f"by position: {res[metric]['by_position']}")
        out[label] = res
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
