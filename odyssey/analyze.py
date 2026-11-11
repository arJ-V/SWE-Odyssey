"""Ramp analysis of segments.csv (SPEC section 8).

`odyssey analyze segments.csv [--metric it_delta_net|sdc_usd|...] [--out report.md] [--json out.json]`

What it computes, per *pairing* (agent x model) and, inside it, per *warm condition*
(arm x session mode, where mode is `resume` if the session carried its conversation and
`fresh` otherwise):

- Cold stats per ticket: n, success rate, geometric mean and CV of the metric over
  successful cold segments. "Cold" is exposure 0, and it is matched to the warm
  condition's arm, because position 1 differs between arms by construction (seeded
  notes, the single-defect start tree, the informed-framing header):
    * `--cold exposure0` (default): position-1 segments of the same arm. Resume and
      fresh sessions are pooled here: their first segments are identical runs.
    * `--cold singleton`: orderings of length 1 whose arm has the same position-1
      start (`cold_key`: single-defect or not, which notes file), whatever the framing
      (a one-ticket ordering gets no framing header).
- Warm/cold log-ratio per ticket, log(GM warm) - log(GM cold), and the pooled geometric
  ratio R = exp(mean over tickets of the per-ticket log-ratios); tickets weigh equally,
  so R answers "for the average ticket in this set". Warm = position >= 2.
- 95% percentile CI for R from a cluster bootstrap over sessions (Harbor trials):
  memory makes segments of one session dependent, so whole sessions are resampled,
  stratified into sessions that contain warm segments of the condition and the rest.
  Every replicate averages over the same tickets as the point estimate; a replicate in
  which one of them has no data is invalid, and the share of invalid replicates is
  reported.
- Default metric `it_delta_net`: incremental tokens without the harness start-up
  prompt of the main conversation, which fresh sessions pay and resumed ones do not
  (gross `it_delta` would push resume R below 1 and squeeze fresh R toward 1 with no
  learning at all). The report also shows R on gross `it_delta` when that column exists.
- Rows whose grader failed (`grader_error`) are excluded; planned segments that never
  ran (`exception = not_run`) count as failures.
- Position curve: per position, n, success rate and the ticket-adjusted ratio
  exp(mean(log x - mean log cold of its ticket)).
- Related-predecessor contrast: warm segments whose cluster appeared earlier in the same
  session vs those whose cluster did not; ratio of their ticket-adjusted geometric means.
- Failures two ways: complete-case (successes only; biased toward 1 when experience changes
  success) and PAR-2 (a failure costs 2 x cap_t, successes are
  capped at cap_t, where cap_t = --cap-mult x the median cold value of ticket t, or the
  ticket's largest observed value when it has no cold data).

Only the standard library is used; numbers are small enough that a 2,000-replicate
bootstrap runs in seconds. A censored (AFT/Tobit) mixed model
is out of scope for v0 (SPEC section 9).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

COLD_DEFINITIONS = ("exposure0", "singleton")


@dataclass
class Seg:
    """One segment row reduced to what the analysis needs."""

    session: str
    pairing: str
    arm: str
    mode: str            # resume | fresh (set after all rows of the session are read)
    ticket: str
    cluster: str
    position: int
    ordering_length: int
    success: bool
    value: float | None  # the metric; None when missing or not positive
    related: bool = False
    not_run: bool = False


# ----------------------------------------------------------------------------- loading

def _float(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def load_segments(path: Path, metric: str, success_threshold: float) -> tuple[list[Seg], dict]:
    """Read segments.csv; returns segments plus counts of rows dropped and why."""
    dropped: dict[str, int] = defaultdict(int)
    segs: list[Seg] = []
    resumed_sessions: set[str] = set()
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if metric not in (reader.fieldnames or []):
            raise SystemExit(f"metric column {metric!r} not in {path}")
        for row in reader:
            position = _float(row.get("position"))
            if position is None:
                dropped["no position"] += 1
                continue
            if not row.get("ticket"):
                dropped["no ticket (manifest mismatch; excluded)"] += 1
                continue
            if row.get("grader_error") == "true":
                dropped["grader error (excluded)"] += 1
                continue
            not_run = row.get("exception") == "not_run"
            session = f"{row.get('job', '')}/{row.get('trial', '')}"
            if row.get("resumed") == "true":
                resumed_sessions.add(session)
            reward = _float(row.get("reward"))
            value = _float(row.get(metric))
            if not_run:
                dropped["planned but never ran (counted as failures)"] += 1
            elif value is not None and value <= 0:
                dropped["metric <= 0 (kept for success rates)"] += 1
                value = None
            elif value is None:
                dropped["metric missing (kept for success rates)"] += 1
            segs.append(Seg(
                session=session,
                pairing=f"{row.get('agent', '')} | {row.get('model', '')}",
                arm=row.get("arm", "") or "",
                mode="fresh",
                ticket=row.get("ticket", "") or "",
                cluster=row.get("cluster", "") or "",
                position=int(position),
                ordering_length=int(_float(row.get("ordering_length")) or 0),
                success=reward is not None and reward >= success_threshold - 1e-9,
                value=value,
                not_run=not_run,
            ))
    _annotate_sessions(segs, resumed_sessions)
    return segs, dict(dropped)


def _annotate_sessions(segs: list[Seg], resumed_sessions: set[str]) -> None:
    """Set the session mode and the related-predecessor flag (same cluster seen earlier)."""
    by_session: dict[str, list[Seg]] = defaultdict(list)
    for seg in segs:
        by_session[seg.session].append(seg)
    for session, rows in by_session.items():
        mode = "resume" if session in resumed_sessions else "fresh"
        for seg in rows:
            seg.mode = mode
            seg.related = bool(seg.cluster) and any(
                other.cluster == seg.cluster and other.position < seg.position
                for other in rows
            )
            if seg.ordering_length == 0:
                seg.ordering_length = max(r.position for r in rows)


# ----------------------------------------------------------------------------- statistics

def geo_mean(values: Iterable[float]) -> float | None:
    logs = [math.log(v) for v in values]
    return math.exp(statistics.fmean(logs)) if logs else None


def cv(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.stdev(values) / statistics.fmean(values)


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile of an already sorted list (q in [0, 1])."""
    if not sorted_values:
        return math.nan
    pos = q * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def is_cold(seg: Seg, cold_def: str) -> bool:
    """Exposure 0 regardless of arm (used for the pooled, descriptive cold table)."""
    if cold_def == "singleton":
        return seg.ordering_length == 1
    return seg.position == 1


def cold_key(arm: str) -> str:
    """What makes position 1 of an arm differ from another arm's (see `odyssey compile` arm ids).

    Arm ids read `<code>-<framing>-<feedback>[-notes-<hash>]`. At position 1 only the
    single-defect start tree (`sd`) and the notes file change what the agent gets;
    feedback starts at position 2 and a one-ticket ordering has no framing header.
    """
    parts = arm.split("-")
    start = "sd" if parts[0] == "sd" else "base"
    notes = ""
    if "notes" in parts:
        rest = parts[parts.index("notes") + 1:]
        notes = "notes-" + (rest[0] if rest else "")
    return f"{start}|{notes}"


def is_cold_for(seg: Seg, arm: str, cold_def: str) -> bool:
    """Cold segments that may serve as the baseline for warm segments of `arm`."""
    if cold_def == "singleton":
        return seg.ordering_length == 1 and cold_key(seg.arm) == cold_key(arm)
    return seg.position == 1 and seg.arm == arm


def cold_ticket_stats(cold: list[Seg]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ticket, rows in sorted(_by_ticket(cold).items()):
        values = [s.value for s in rows if s.success and s.value is not None]
        out[ticket] = {
            "n": len(rows),
            "n_success": sum(s.success for s in rows),
            "success_rate": sum(s.success for s in rows) / len(rows),
            "n_cost": len(values),
            "geo_mean": geo_mean(values),
            "cv": cv(values),
        }
    return out


def _by_ticket(segs: Iterable[Seg]) -> dict[str, list[Seg]]:
    out: dict[str, list[Seg]] = defaultdict(list)
    for seg in segs:
        out[seg.ticket].append(seg)
    return out


def _caps(cold: list[Seg], everything: list[Seg], cap_mult: float) -> dict[str, float]:
    """Per-ticket PAR-2 cap: cap_mult x median cold value, else the largest value seen."""
    caps: dict[str, float] = {}
    for ticket, rows in _by_ticket(everything).items():
        cold_values = [s.value for s in cold if s.ticket == ticket and s.value is not None]
        if cold_values:
            caps[ticket] = cap_mult * statistics.median(cold_values)
        else:
            seen = [s.value for s in rows if s.value is not None]
            if seen:
                caps[ticket] = max(seen)
    return caps


def _cc_logs(rows: list[Seg]) -> list[float]:
    return [math.log(s.value) for s in rows if s.success and s.value is not None]


def _par2_logs(rows: list[Seg], cap: float | None) -> list[float]:
    if cap is None or cap <= 0:
        return []
    out = []
    for s in rows:
        if not s.success:
            out.append(math.log(2 * cap))
        elif s.value is not None:
            out.append(math.log(min(s.value, cap)))
    return out


def _log_ratio(cold_logs: list[float], warm_logs: list[float]) -> float | None:
    if not cold_logs or not warm_logs:
        return None
    return statistics.fmean(warm_logs) - statistics.fmean(cold_logs)


def estimate(
    segs: list[Seg], arm: str, mode: str, cold_def: str, cap_mult: float,
    tickets: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """All warm-vs-cold quantities for one condition from one (possibly resampled) dataset.

    `tickets` fixes, per measure, the ticket set the pooled R averages over (the
    bootstrap passes the point estimate's); R is None if any of them lacks data.
    """
    cold = [s for s in segs if is_cold_for(s, arm, cold_def)]
    warm = [s for s in segs if s.position >= 2 and s.arm == arm and s.mode == mode]
    caps = _caps(cold, cold + warm, cap_mult)
    cold_by, warm_by = _by_ticket(cold), _by_ticket(warm)

    per_ticket = []
    for ticket in sorted(set(cold_by) | set(warm_by)):
        c, w = cold_by.get(ticket, []), warm_by.get(ticket, [])
        cap = caps.get(ticket)
        per_ticket.append({
            "ticket": ticket, "n_cold": len(c), "n_warm": len(w),
            "warm_success_rate": (sum(s.success for s in w) / len(w)) if w else None,
            "cold_success_rate": (sum(s.success for s in c) / len(c)) if c else None,
            "cap": cap,
            "complete_case": _log_ratio(_cc_logs(c), _cc_logs(w)),
            "par2": _log_ratio(_par2_logs(c, cap), _par2_logs(w, cap)),
        })

    pooled = {}
    for name in ("complete_case", "par2"):
        by_ticket = {e["ticket"]: e[name] for e in per_ticket if e[name] is not None}
        if tickets is not None:
            wanted = tickets[name]
            lrs = [by_ticket[t] for t in sorted(wanted) if t in by_ticket]
            valid = len(lrs) == len(wanted) and bool(lrs)
        else:
            lrs = list(by_ticket.values())
            valid = bool(lrs)
        pooled[name] = {
            "R": math.exp(statistics.fmean(lrs)) if valid else None,
            "n_tickets": len(lrs),
            "tickets": sorted(by_ticket) if tickets is None else sorted(tickets[name]),
        }

    cold_mean_log = {
        t: statistics.fmean(v) for t, rows in cold_by.items() if (v := _cc_logs(rows))
    }
    return {
        "per_ticket": per_ticket,
        "pooled": pooled,
        "position_curve": _position_curve(segs, arm, mode, cold_mean_log),
        "related": _related_contrast(warm, cold_mean_log),
    }


def _adjusted(rows: list[Seg], cold_mean_log: dict[str, float]) -> list[float]:
    """log(x) minus the cold mean log of the row's ticket, successes only."""
    return [
        math.log(s.value) - cold_mean_log[s.ticket]
        for s in rows
        if s.success and s.value is not None and s.ticket in cold_mean_log
    ]


def _position_curve(
    segs: list[Seg], arm: str, mode: str, cold_mean_log: dict[str, float]
) -> list[dict[str, Any]]:
    rows_by_pos: dict[int, list[Seg]] = defaultdict(list)
    for s in segs:
        if s.arm == arm and s.mode == mode and s.ordering_length > 1:
            rows_by_pos[s.position].append(s)
    curve = []
    for position in sorted(rows_by_pos):
        rows = rows_by_pos[position]
        adj = _adjusted(rows, cold_mean_log)
        curve.append({
            "position": position,
            "n": len(rows),
            "success_rate": sum(s.success for s in rows) / len(rows),
            "n_cost": len(adj),
            "ratio_vs_cold": math.exp(statistics.fmean(adj)) if adj else None,
        })
    return curve


def _related_contrast(warm: list[Seg], cold_mean_log: dict[str, float]) -> dict[str, Any]:
    rel = _adjusted([s for s in warm if s.related], cold_mean_log)
    unrel = _adjusted([s for s in warm if not s.related], cold_mean_log)
    return {
        "n_related": len(rel),
        "n_unrelated": len(unrel),
        "related_vs_cold": math.exp(statistics.fmean(rel)) if rel else None,
        "unrelated_vs_cold": math.exp(statistics.fmean(unrel)) if unrel else None,
        "related_vs_unrelated": (
            math.exp(statistics.fmean(rel) - statistics.fmean(unrel)) if rel and unrel else None
        ),
    }


# ----------------------------------------------------------------------------- bootstrap

def cluster_bootstrap(
    segs: list[Seg], arm: str, mode: str, cold_def: str, cap_mult: float,
    n_boot: int, seed: int,
) -> dict[str, Any]:
    """Percentile CIs for the pooled ratios and the related contrast.

    Sessions are resampled with replacement within two strata (sessions holding warm
    segments of this condition, and all other sessions), so every replicate has both
    warm and cold data whenever the original does.
    """
    sessions: dict[str, list[Seg]] = defaultdict(list)
    for s in segs:
        sessions[s.session].append(s)
    warm_ids = sorted(k for k, rows in sessions.items()
                      if any(r.position >= 2 and r.arm == arm and r.mode == mode for r in rows))
    other_ids = sorted(set(sessions) - set(warm_ids))
    point = estimate(segs, arm, mode, cold_def, cap_mult)["pooled"]
    fixed = {name: set(point[name]["tickets"]) for name in ("complete_case", "par2")}
    rng = random.Random(seed)
    draws: dict[str, list[float]] = defaultdict(list)
    for _ in range(n_boot):
        picked = rng.choices(warm_ids, k=len(warm_ids)) + (
            rng.choices(other_ids, k=len(other_ids)) if other_ids else [])
        sample = [seg for sid in picked for seg in sessions[sid]]
        est = estimate(sample, arm, mode, cold_def, cap_mult, tickets=fixed)
        for name in ("complete_case", "par2"):
            if est["pooled"][name]["R"] is not None:
                draws[name].append(est["pooled"][name]["R"])
        if est["related"]["related_vs_unrelated"] is not None:
            draws["related_vs_unrelated"].append(est["related"]["related_vs_unrelated"])
    out = {}
    for name in ("complete_case", "par2", "related_vs_unrelated"):
        values = sorted(draws.get(name, []))
        out[name] = {"ci95": [percentile(values, 0.025), percentile(values, 0.975)] if values else None,
                     "n_valid": len(values),
                     "invalid_share": (1 - len(values) / n_boot) if n_boot else None}
    out["n_boot"] = n_boot
    out["n_sessions"] = {"warm_stratum": len(warm_ids), "other_stratum": len(other_ids)}
    return out


# ----------------------------------------------------------------------------- driver

def analyze(
    segs: list[Seg], cold_def: str = "exposure0", cap_mult: float = 3.0,
    n_boot: int = 2000, seed: int = 0,
) -> list[dict[str, Any]]:
    """Results per pairing, each with its cold stats and one block per warm condition."""
    results = []
    by_pairing: dict[str, list[Seg]] = defaultdict(list)
    for s in segs:
        by_pairing[s.pairing].append(s)
    for pairing, rows in sorted(by_pairing.items()):
        cold = [s for s in rows if is_cold(s, cold_def)]
        conditions = sorted({(s.arm, s.mode) for s in rows if s.position >= 2})
        blocks = []
        for arm, mode in conditions:
            relevant = _condition_sessions(rows, arm, mode, cold_def)
            block = {"arm": arm, "mode": mode, "cold_key": cold_key(arm),
                     "single_defect_leak": arm.startswith("sd-"),
                     **estimate(relevant, arm, mode, cold_def, cap_mult)}
            block["bootstrap"] = cluster_bootstrap(
                relevant, arm, mode, cold_def, cap_mult, n_boot, seed)
            blocks.append(block)
        results.append({
            "pairing": pairing,
            "n_segments": len(rows),
            "n_sessions": len({s.session for s in rows}),
            "cold_by_ticket": cold_ticket_stats(cold),
            "conditions": blocks,
        })
    return results


def _condition_sessions(rows: list[Seg], arm: str, mode: str, cold_def: str) -> list[Seg]:
    """Rows of every session that has a matching cold segment or a warm segment of the condition."""
    keep = {
        s.session for s in rows
        if is_cold_for(s, arm, cold_def) or (s.position >= 2 and s.arm == arm and s.mode == mode)
    }
    return [s for s in rows if s.session in keep]


# ----------------------------------------------------------------------------- report

def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if math.isnan(value):
            return "-"
        return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:.{digits}f}"
    return str(value)


def _ratio_with_ci(pooled: dict, boot: dict, name: str) -> str:
    r = pooled[name]["R"]
    b = boot.get(name, {})
    ci = b.get("ci95")
    text = _fmt(r)
    if r is not None and ci:
        text += f" [{_fmt(ci[0])}, {_fmt(ci[1])}]"
    text += f" (tickets={pooled[name]['n_tickets']}"
    if r is not None and b.get("invalid_share") is not None:
        text += f"; {b['invalid_share']:.0%} of replicates invalid"
    return text + ")"


def render_markdown(meta: dict, results: list[dict]) -> str:
    lines = [
        f"# SWE-Odyssey ramp analysis: `{meta['metric']}`",
        "",
        f"Source: `{meta['source']}`. Cold = `{meta['cold']}`; success = reward >= "
        f"{meta['success_threshold']}; PAR-2 cap = {meta['cap_mult']} x cold median per ticket; "
        f"{meta['n_boot']} cluster-bootstrap replicates over sessions (seed {meta['seed']}).",
        "",
        "Ratios are warm/cold geometric-mean ratios: below 1 means warm segments cost less.",
        "Each warm condition is compared with cold segments of its own arm "
        "(exposure0: position 1 of that arm; singleton: one-ticket runs with the same cold key).",
        "Complete-case drops failures and is biased toward 1 when experience changes success;",
        "PAR-2 charges each failure twice the ticket cap. A replicate is invalid when one of the",
        "point estimate's tickets has no data in it.",
    ]
    if meta["dropped"]:
        lines += ["", "Rows without a usable metric: " + ", ".join(
            f"{k}: {v}" for k, v in meta["dropped"].items())]
    for res in results:
        lines += ["", f"## {res['pairing']}", "",
                  f"{res['n_segments']} segments in {res['n_sessions']} sessions.", "",
                  "### Cold segments per ticket (all arms pooled; descriptive only)", "",
                  "| ticket | n | success | geo-mean (successes) | CV |",
                  "|---|---|---|---|---|"]
        for ticket, st in res["cold_by_ticket"].items():
            lines.append(f"| {ticket} | {st['n']} | {_fmt(st['success_rate'], 2)} | "
                         f"{_fmt(st['geo_mean'])} | {_fmt(st['cv'], 2)} |")
        for block in res["conditions"]:
            lines += _condition_markdown(block)
    return "\n".join(lines) + "\n"


def _condition_markdown(block: dict) -> list[str]:
    boot = block["bootstrap"]
    rel = block["related"]
    lines = [
        "", f"### Warm condition: arm `{block['arm'] or '-'}`, mode `{block['mode']}`", "",
    ]
    if block.get("single_defect_leak"):
        lines += ["**Warning:** single-defect start trees contain every later ticket's fix, so warm "
                  "segments of this arm can copy fixes seen earlier. Do not pool this arm's ratios "
                  "with other arms.", ""]
    lines += [
        f"Cold key `{block.get('cold_key', '')}`. Sessions resampled: "
        f"{boot['n_sessions']['warm_stratum']} warm, {boot['n_sessions']['other_stratum']} cold-only.", "",
        f"- Pooled R, complete-case: {_ratio_with_ci(block['pooled'], boot, 'complete_case')}",
        f"- Pooled R, PAR-2: {_ratio_with_ci(block['pooled'], boot, 'par2')}",
    ]
    if block.get("gross"):
        g = block["gross"]
        lines.append(f"- Same, on gross `it_delta` (includes the harness start-up prompt of fresh "
                     f"sessions): complete-case {_fmt(g['complete_case']['R'])}, "
                     f"PAR-2 {_fmt(g['par2']['R'])}")
    lines += [
        "", "| ticket | n cold | n warm | cold success | warm success | "
        "log-ratio (CC) | log-ratio (PAR-2) |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in block["per_ticket"]:
        lines.append(
            f"| {e['ticket']} | {e['n_cold']} | {e['n_warm']} | "
            f"{_fmt(e['cold_success_rate'], 2)} | {_fmt(e['warm_success_rate'], 2)} | "
            f"{_fmt(e['complete_case'])} | {_fmt(e['par2'])} |")
    lines += ["", "Position curve (ticket-adjusted ratio vs cold, successes only):", "",
              "| position | n | success | ratio vs cold |", "|---|---|---|---|"]
    for p in block["position_curve"]:
        lines.append(f"| {p['position']} | {p['n']} | {_fmt(p['success_rate'], 2)} | "
                     f"{_fmt(p['ratio_vs_cold'])} |")
    ci = boot.get("related_vs_unrelated", {}).get("ci95")
    contrast = _fmt(rel["related_vs_unrelated"])
    if rel["related_vs_unrelated"] is not None and ci:
        contrast += f" [{_fmt(ci[0])}, {_fmt(ci[1])}]"
    lines += ["", "Related-predecessor contrast (warm segments; same cluster seen earlier "
              "in the session or not):", "",
              f"- related: n={rel['n_related']}, ratio vs cold {_fmt(rel['related_vs_cold'])}",
              f"- unrelated: n={rel['n_unrelated']}, ratio vs cold "
              f"{_fmt(rel['unrelated_vs_cold'])}",
              f"- related / unrelated: {contrast}"]
    return lines


def _attach_gross(results: list[dict], args) -> None:
    """Point estimates on gross it_delta next to the net ones, so readers see the start-up share."""
    try:
        gross_segs, _ = load_segments(args.segments, "it_delta", args.success_threshold)
    except SystemExit:
        return
    gross = {(r["pairing"], b["arm"], b["mode"]): b["pooled"]
             for r in analyze(gross_segs, args.cold, args.cap_mult, 0, args.seed) for b in r["conditions"]}
    for res in results:
        for block in res["conditions"]:
            block["gross"] = gross.get((res["pairing"], block["arm"], block["mode"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="odyssey analyze",
                                     description="Warm/cold ramp analysis of segments.csv.")
    parser.add_argument("segments", type=Path)
    parser.add_argument("--metric", default="it_delta_net",
                        help="numeric column to analyse (it_delta_net, it_delta, it_content, sdc_usd, ...)")
    parser.add_argument("--out", type=Path, help="Markdown report (default: stdout)")
    parser.add_argument("--json", type=Path,
                        help="machine-readable results (default: --out with .json suffix)")
    parser.add_argument("--cold", choices=COLD_DEFINITIONS, default="exposure0")
    parser.add_argument("--success-threshold", type=float, default=1.0)
    parser.add_argument("--cap-mult", type=float, default=3.0)
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    segs, dropped = load_segments(args.segments, args.metric, args.success_threshold)
    results = analyze(segs, args.cold, args.cap_mult, args.bootstrap, args.seed)
    if args.metric == "it_delta_net":
        _attach_gross(results, args)
    meta = {"source": str(args.segments), "metric": args.metric, "cold": args.cold,
            "success_threshold": args.success_threshold, "cap_mult": args.cap_mult,
            "n_boot": args.bootstrap, "seed": args.seed, "dropped": dropped}
    report = render_markdown(meta, results)
    json_path = args.json or (args.out.with_suffix(".json") if args.out else None)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    if json_path:
        json_path.write_text(json.dumps({"meta": meta, "pairings": results}, indent=2),
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
