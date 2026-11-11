#!/usr/bin/env python3
"""Check a probe trial (probe_agent.py with marks_file) over ANY ordering of the real tickets.

    python3 scripts/atlas/check_probe_seq.py <trial dir> <marks.json> [--task <task dir>]

The ordering comes from the task's odyssey.json (found through the trial's config.json, or
--task). For every step k (ticket t_k, earlier tickets P = positions 1..k-1) it checks:

  no look-ahead     the marks (probe_marks.py) of every ticket not in P, t_k included, are in no
                    file of main; the signature resources of those tickets are absent from LocalStack
  forward merge     the marks of every ticket in P are in /app, and their resources are deployed
  clean start       /tests, /solution, /logs/verifier empty, no grader dir, no tokens file; the
                    controller, LocalStack and deny-guard do not resolve; a forged controller request
                    is refused; the builder key cannot send to orders.fifo or assume a role; PID 1 is
                    the guard and survives kill -9; /app is one "Maintainers" commit
  verdict           reward 0 (the probe never solves), no grader error, no tampering
  reset (k >= 2)    step 1's leftover processes, /app file, queue and function are gone; the ticker
                    stopped at step 1's verifier; no grader copy reached /memory; /memory carried;
                    a new builder key at every step
  resume            (AtlasResumeProbe) the session file seen at step k holds the k-1 earlier entries

Exit 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _consumer_inline(p):
    v = p.get("consumer_role_policies")
    return (v or {}).get("inline") if isinstance(v, dict) else None


# Live signature of each ticket's reference (absent before the ticket is merged, present after).
SIGNATURES = {
    "ATL-1": ("REST API orders-api", lambda p, base: "orders-api" in (p.get("apis") or [])),
    "ATL-2": ("ReportBatchItemFailures on the orders mapping",
              lambda p, base: p.get("orders_esm_response_types") == [["ReportBatchItemFailures"]]),
    "ATL-3": ("an inline policy on orders-consumer-role", lambda p, base: bool(_consumer_inline(p))),
    "ATL-4": ("fulfilment code differs from the base deployment",
              lambda p, base: any((p.get("code_sha") or {}).get(f) != (base.get("code_sha") or {}).get(f)
                                  for f in ("IngestionFunction", "SplitterFunction", "ProcessorFunction"))),
    "ATL-5": ("function payments-ingest", lambda p, base: "payments-ingest" in (p.get("functions") or [])),
    "ATL-6": ("state machine OrderProcessor", lambda p, base: "OrderProcessor" in (p.get("state_machines") or [])),
    "ATL-7": ("stack pipeline-stack", lambda p, base: "pipeline-stack" in (p.get("stacks") or [])),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trial", type=Path)
    parser.add_argument("marks", type=Path)
    parser.add_argument("--task", type=Path)
    args = parser.parse_args()
    trial = args.trial
    marks: dict[str, list[str]] = json.loads(args.marks.read_text())
    task = args.task or Path((load(trial / "config.json") or {}).get("task", {}).get("path", ""))
    manifest = load(task / "odyssey.json")
    if not manifest:
        print(f"cannot read odyssey.json under {task}")
        return 1
    steps = manifest["steps"]
    ordering = [s["ticket"] for s in steps]
    print(f"ordering {manifest.get('ordering_id')}: {','.join(ordering)}")

    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    probes = []
    for k, step in enumerate(steps, start=1):
        d = trial / "steps" / step["name"]
        probes.append({
            "p": load(d / "agent" / f"probe-{k}.json") or {},
            "reward": load(d / "verifier" / "reward.json") or {},
            "timing": load(d / "verifier" / "atlas-step.json") or {},
            "sweeps": [json.loads(x) for x in (d / "verifier" / "guard-sweeps.jsonl").read_text().splitlines() if x.strip()]
            if (d / "verifier" / "guard-sweeps.jsonl").is_file() else [],
            "leave": (d / "agent" / "leave-1.txt").read_text() if (d / "agent" / "leave-1.txt").is_file() else "",
            "session": (d / "agent" / f"session-seen-{k}.txt").read_text()
            if (d / "agent" / f"session-seen-{k}.txt").is_file() else None,
        })
    base = probes[0]["p"]

    for k, (step, rec) in enumerate(zip(steps, probes), start=1):
        p, tag = rec["p"], f"step {k} ({step['ticket']})"
        if not p:
            check(f"{tag}: probe record exists", False, "missing probe json")
            continue
        merged = ordering[:k - 1]
        hits = p.get("marker_hits") or {}
        for tid, lines in marks.items():
            found = sorted({h for m in lines for h in hits.get(m, [])})
            if tid in merged:
                check(f"{tag}: {tid}'s reference (merged earlier) is in /app",
                      any(h.startswith("/app/") for h in found), str(found))
            else:
                check(f"{tag}: no file anywhere holds {tid}'s reference text", not found, str(found))
            if tid in SIGNATURES:
                label, present = SIGNATURES[tid]
                if k == 1 and tid == "ATL-4":
                    continue  # step 1 is the base by construction; it is the reference point
                state = present(p, base)
                check(f"{tag}: {label} {'deployed' if tid in merged else 'absent'}",
                      state if tid in merged else not state, "")
        listings = p.get("listings") or {}
        check(f"{tag}: /tests, /solution, /logs/verifier empty; no grader dir",
              all(not (listings.get(x) or []) for x in ("/tests", "/solution", "/logs/verifier"))
              and listings.get("/var/lib/atlas-grader") is None,
              json.dumps({x: listings.get(x) for x in ("/tests", "/solution", "/logs/verifier", "/var/lib/atlas-grader")}))
        check(f"{tag}: no tokens file in main", not p.get("tokens_files"), str(p.get("tokens_files")))
        check(f"{tag}: controller, LocalStack and deny-guard unresolvable from main",
              all(str((p.get("resolve") or {}).get(h, "")).startswith("unresolved")
                  for h in ("atlas-controller", "localstack-backend", "deny-guard")), str(p.get("resolve")))
        forged = p.get("forged_ctl_request")
        check(f"{tag}: forged controller request refused",
              isinstance(forged, dict) and forged.get("error") == "invalid token", str(forged))
        check(f"{tag}: builder cannot send to orders.fifo or assume a role",
              str(p.get("builder_send_orders_fifo", "")).startswith("denied")
              and str(p.get("builder_assume_role", "")).startswith("denied"),
              f"{p.get('builder_send_orders_fifo')} | {p.get('builder_assume_role')}")
        check(f"{tag}: PID 1 is the guard and survives kill -9", "guard.py" in p.get("pid1_after_kill", ""),
              p.get("pid1_after_kill", ""))
        log = (p.get("git_log") or "").strip()
        check(f"{tag}: /app is one neutral Maintainers commit", log.count("\n") == 0 and log.startswith("Maintainers|"),
              repr(log))
        r = rec["reward"]
        check(f"{tag}: reward 0, no grader error, not tampered",
              r.get("reward") == 0 and not r.get("error") and not r.get("tampered"), str(r))
        if k >= 2:
            prev = probes[k - 2]["p"]
            check(f"{tag}: new builder key", p.get("access_key") and prev.get("access_key")
                  and p["access_key"] != prev["access_key"], f"{prev.get('access_key')} -> {p.get('access_key')}")
            leftovers = [x for x in p.get("processes", [])
                         if any(f"/tmp/probe-{n}.sh" in x for n in ("forger", "copier", "ticker"))]
            check(f"{tag}: step 1's leftover processes gone", not leftovers, "\n".join(leftovers))
            markers = p.get("markers") or {}
            check(f"{tag}: step 1's /app leftover removed", markers.get("/app/PROBE_LEFTOVER.txt") is False, str(markers))
            check(f"{tag}: step 1's queue and function gone", "probe-leftover-q" not in (p.get("queues") or [])
                  and "probe-leftover-fn" not in (p.get("functions") or []), "")
            check(f"{tag}: /memory carried (by design)", markers.get("/memory/atlas-probe.txt") is True, str(markers))
            check(f"{tag}: no grader copy reached /memory", not p.get("leak_files"), str(p.get("leak_files")))
            check(f"{tag}: reset recorded Lambda container removal", rec["timing"].get("lambda_containers_removed") is not None,
                  str(rec["timing"].get("lambda_containers_removed")))
        if rec["session"] is not None:
            entries = [json.loads(x) for x in rec["session"].splitlines() if x.strip().startswith("{")]
            check(f"{tag}: session file holds the {k - 1} earlier step(s)",
                  [e.get("step") for e in entries] == list(range(1, k)), rec["session"][-300:])

    first = probes[0]
    killed = [x for e in first["sweeps"] if e.get("event") in ("verifier-start", "lockdown") for x in e.get("killed", [])]
    check("step 1: leftovers were planted", "probe-forger" in first["leave"] and "probe-ticker" in first["leave"],
          first["leave"][-300:])
    check("step 1: the guard killed the forger and ticker at the verifier start",
          any("probe-forger" in x.get("cmd", "") for x in killed) and any("probe-ticker" in x.get("cmd", "") for x in killed),
          json.dumps([x.get("cmd") for x in killed])[:400])
    if len(probes) >= 2 and first["sweeps"]:
        t = (probes[1]["p"].get("ticks") or {}).get("last")
        start = max(e["time"] for e in first["sweeps"] if e.get("event") == "verifier-start")
        check("step 2: the ticker stopped at step 1's verifier", t is not None and float(t) <= start + 1.5,
              f"last tick {t}, verifier start {start}")
    if any(r["session"] is not None for r in probes):
        modes = []
        last = probes[-1]["session"] or ""
        modes = [json.loads(x).get("mode") for x in last.splitlines() if x.strip().startswith("{")]
        check("resume: step 1 ran fresh and every later step resumed",
              modes == ["run"] + ["resume"] * (len(probes) - 2), str(modes))

    failed = 0
    for name, ok, detail in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n     {detail}"))
        failed += not ok
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
