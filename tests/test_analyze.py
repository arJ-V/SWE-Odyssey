"""Tests for odyssey.analyze on simulated segments.csv files with known effects."""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

import pytest

from odyssey import analyze as an
from odyssey.accounting import COLUMNS

# (ticket, cluster, mean log cost when cold)
TICKETS = [("T1", "A", 11.0), ("T2", "A", 11.5), ("T3", "B", 12.0), ("T4", "B", 12.3),
           ("T5", "N", 10.8)]


def simulate(
    path: Path,
    *,
    ramp: float,
    related_effect: float = 1.0,
    n_cold: int = 30,
    n_warm: int = 60,
    sigma: float = 0.3,
    session_sd: float = 0.1,
    p_fail: float = 0.0,
    seed: int = 1,
    arm: str = "fm-inf-nofb",
    arm_shift: float = 1.0,
    startup: int = 0,
    append: bool = False,
) -> Path:
    """Write a segments.csv with a step ramp: every position >= 2 costs `ramp` x cold.

    Warm segments whose cluster appeared earlier in the session also get `related_effect`.
    Failures happen independently of cost with probability `p_fail`. `arm_shift`
    multiplies every segment of the arm (e.g. seeded notes that help from segment 1);
    `startup` is the harness start-up prompt a fresh session pays on its first call:
    it is in `it_delta` of position-1 segments only (warm segments resume), never in
    `it_delta_net`. `append` adds the rows to an existing file.
    """
    rng = random.Random(seed)
    rows = []

    def row(trial: str, position: int, length: int, ticket: str, cluster: str,
            log_cost: float, resumed: bool) -> dict:
        net = round(math.exp(log_cost + math.log(arm_shift)))
        return {
            "job": "sim", "trial": f"{arm}-{trial}", "sequence": "toy", "arm": arm,
            "position": position, "ticket": ticket, "cluster": cluster, "role": "core",
            "agent": "claude-code", "model": "claude-opus-5-5",
            "resumed": "true" if resumed else "false",
            "reward": 0.0 if rng.random() < p_fail else 1.0,
            "it_delta": net + (0 if resumed else startup), "it_delta_net": net,
            "it_startup_main_tokens": 0 if resumed else startup, "ordering_length": length,
        }

    for ticket, cluster, mu in TICKETS:
        for i in range(n_cold):
            u = rng.gauss(0, session_sd)
            rows.append(row(f"cold-{ticket}-{i}", 1, 1, ticket, cluster,
                            mu + u + rng.gauss(0, sigma), False))
    for s in range(n_warm):
        order = TICKETS[:]
        rng.shuffle(order)
        u = rng.gauss(0, session_sd)
        seen: set[str] = set()
        for position, (ticket, cluster, mu) in enumerate(order, start=1):
            effect = 0.0
            if position >= 2:
                effect += math.log(ramp)
                if cluster in seen:
                    effect += math.log(related_effect)
            seen.add(cluster)
            rows.append(row(f"warm-{s}", position, len(order), ticket, cluster,
                            mu + u + effect + rng.gauss(0, sigma), position >= 2))

    with open(path, "a" if append else "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if not append:
            writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in COLUMNS})
    return path


def run(path: Path, **kwargs) -> dict:
    segs, _ = an.load_segments(path, kwargs.pop("metric", "it_delta"), 1.0)
    results = an.analyze(segs, n_boot=kwargs.pop("n_boot", 300), **kwargs)
    assert len(results) == 1
    conditions = results[0]["conditions"]
    assert [(c["arm"], c["mode"]) for c in conditions] == [("fm-inf-nofb", "resume")]
    return {**results[0], **conditions[0]}


def near(value: float, target: float, log_tol: float = 0.08) -> bool:
    return abs(math.log(value) - math.log(target)) < log_tol


def test_recovers_known_ramp(tmp_path: Path) -> None:
    res = run(simulate(tmp_path / "s.csv", ramp=0.7))
    pooled = res["pooled"]["complete_case"]
    assert pooled["n_tickets"] == 5
    assert near(pooled["R"], 0.7)
    lo, hi = res["bootstrap"]["complete_case"]["ci95"]
    assert lo < 0.7 < hi and hi - lo < 0.25
    curve = {p["position"]: p["ratio_vs_cold"] for p in res["position_curve"]}
    assert near(curve[1], 1.0, 0.12)
    for position in (2, 3, 4, 5):
        assert near(curve[position], 0.7, 0.15)
    for ticket, stats in res["cold_by_ticket"].items():
        mu = dict((t, m) for t, _, m in TICKETS)[ticket]
        assert near(stats["geo_mean"], math.exp(mu), 0.15)
        assert stats["success_rate"] == 1.0


def test_null_ramp_is_not_detected(tmp_path: Path) -> None:
    res = run(simulate(tmp_path / "s.csv", ramp=1.0, seed=7))
    assert near(res["pooled"]["complete_case"]["R"], 1.0)
    lo, hi = res["bootstrap"]["complete_case"]["ci95"]
    assert lo < 1.0 < hi


def test_related_predecessor_contrast(tmp_path: Path) -> None:
    res = run(simulate(tmp_path / "s.csv", ramp=1.0, related_effect=0.6, seed=3))
    rel = res["related"]
    assert rel["n_related"] > 0 and rel["n_unrelated"] > 0
    assert near(rel["related_vs_unrelated"], 0.6, 0.12)
    assert near(rel["unrelated_vs_cold"], 1.0, 0.1)
    lo, hi = res["bootstrap"]["related_vs_unrelated"]["ci95"]
    assert lo < 0.6 < hi


def test_failures_complete_case_and_par2(tmp_path: Path) -> None:
    res = run(simulate(tmp_path / "s.csv", ramp=0.7, p_fail=0.3, seed=5))
    # Failures independent of cost: complete-case stays unbiased.
    assert near(res["pooled"]["complete_case"]["R"], 0.7, 0.1)
    # PAR-2 charges failures the same 2 x cap in both arms, which pulls R toward 1 in
    # expectation; with sampling noise in the success rates we only check the range.
    par2 = res["pooled"]["par2"]["R"]
    assert 0.6 < par2 < 1.0
    ticket = res["per_ticket"][0]
    assert 0.5 < ticket["warm_success_rate"] < 0.9 and ticket["cap"] > 0


def test_singleton_cold_definition(tmp_path: Path) -> None:
    res = run(simulate(tmp_path / "s.csv", ramp=0.7, seed=11), cold_def="singleton")
    assert all(e["n_cold"] == 30 for e in res["per_ticket"])
    assert near(res["pooled"]["complete_case"]["R"], 0.7)


def test_cli_writes_markdown_and_json(tmp_path: Path) -> None:
    csv_path = simulate(tmp_path / "s.csv", ramp=0.7, n_cold=5, n_warm=6)
    out = tmp_path / "report.md"
    assert an.main([str(csv_path), "--metric", "it_delta", "--out", str(out),
                    "--bootstrap", "50"]) == 0
    text = out.read_text()
    assert "# SWE-Odyssey ramp analysis: `it_delta`" in text
    assert "Pooled R, complete-case" in text and "Position curve" in text
    data = json.loads(out.with_suffix(".json").read_text())
    assert data["meta"]["metric"] == "it_delta"
    assert data["pairings"][0]["conditions"][0]["pooled"]["complete_case"]["R"] > 0


def test_unknown_metric_column_is_an_error(tmp_path: Path) -> None:
    csv_path = simulate(tmp_path / "s.csv", ramp=0.7, n_cold=2, n_warm=2)
    with pytest.raises(SystemExit):
        an.main([str(csv_path), "--metric", "nope"])


# ----------------------------------------------------------------------------- review fixes


def conditions_by_arm(path: Path, **kwargs) -> dict[str, dict]:
    segs, _ = an.load_segments(path, kwargs.pop("metric", "it_delta"), 1.0)
    results = an.analyze(segs, n_boot=kwargs.pop("n_boot", 200), **kwargs)
    return {c["arm"]: c for c in results[0]["conditions"]}


@pytest.mark.parametrize("cold_def", ["exposure0", "singleton"])
def test_cold_baseline_is_matched_to_the_arm(tmp_path: Path, cold_def: str) -> None:
    """Two arms, no ramp in either; the notes arm is 2x cheaper from segment 1 on.

    With a pooled cold baseline the plain arm showed R ~ 1.37 and the notes arm
    R ~ 0.70, both with CIs excluding 1.
    """
    path = tmp_path / "s.csv"
    simulate(path, ramp=1.0, seed=21)
    simulate(path, ramp=1.0, seed=22, arm="fm-inf-nofb-notes-3fa2c1", arm_shift=0.5, append=True)
    blocks = conditions_by_arm(path, cold_def=cold_def)
    for arm in ("fm-inf-nofb", "fm-inf-nofb-notes-3fa2c1"):
        r = blocks[arm]["pooled"]["complete_case"]["R"]
        lo, hi = blocks[arm]["bootstrap"]["complete_case"]["ci95"]
        assert near(r, 1.0, 0.1) and lo < 1.0 < hi, (arm, r, lo, hi)


def test_cold_key() -> None:
    assert an.cold_key("fm-inf-nofb") == an.cold_key("fm-nat-fb") == an.cold_key("carry-inf-nofb")
    assert an.cold_key("sd-inf-nofb") != an.cold_key("fm-inf-nofb")
    assert an.cold_key("fm-inf-nofb-notes-aaaaaa") != an.cold_key("fm-inf-nofb-notes-bbbbbb")
    assert an.cold_key("fm-inf-nofb-notes-aaaaaa") == an.cold_key("carry-nat-fb-notes-aaaaaa")


def test_net_metric_removes_the_startup_confound(tmp_path: Path) -> None:
    """No ramp; fresh first segments pay a fixed 17.8k start-up prompt, resumed ones do not."""
    path = simulate(tmp_path / "s.csv", ramp=1.0, seed=31, startup=17_800)
    gross = run(path, metric="it_delta")
    net = run(path, metric="it_delta_net")
    # The confound: gross it_delta reports "learning" that is only harness overhead.
    assert gross["pooled"]["complete_case"]["R"] < 0.9
    assert gross["bootstrap"]["complete_case"]["ci95"][1] < 1.0
    assert near(net["pooled"]["complete_case"]["R"], 1.0, 0.1)
    lo, hi = net["bootstrap"]["complete_case"]["ci95"]
    assert lo < 1.0 < hi
    # The report shows both, so the start-up share is visible.
    out = tmp_path / "r.md"
    assert an.main([str(path), "--out", str(out), "--bootstrap", "20"]) == 0
    text = out.read_text()
    assert "ramp analysis: `it_delta_net`" in text and "on gross `it_delta`" in text


def test_bootstrap_keeps_the_point_estimates_ticket_set(tmp_path: Path) -> None:
    """A ticket with a single warm session drops out of many replicates; those are invalid."""
    path = simulate(tmp_path / "s.csv", ramp=1.0, seed=41, n_warm=12)
    rows = list(csv.DictReader(open(path)))
    # T1 keeps exactly one warm success: every other warm T1 segment fails.
    warm_t1 = [r for r in rows if r["ticket"] == "T1" and r["position"] != "1"]
    for r in warm_t1[1:]:
        r["reward"] = "0.0"
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    res = run(path, n_boot=400)
    assert res["pooled"]["complete_case"]["n_tickets"] == 5
    boot = res["bootstrap"]["complete_case"]
    assert 0.2 < boot["invalid_share"] < 0.5          # about 1/e of replicates lose T1
    assert boot["n_valid"] == round(400 * (1 - boot["invalid_share"]))


def test_grader_errors_excluded_and_unrun_segments_count_as_failures(tmp_path: Path) -> None:
    path = simulate(tmp_path / "s.csv", ramp=1.0, seed=51, n_cold=4, n_warm=4)
    rows = list(csv.DictReader(open(path)))
    rows[0]["grader_error"] = "true"                    # a cold T1 segment the grader broke
    unrun = dict(rows[-1], exception="not_run", reward="", it_delta="", it_delta_net="")
    rows.append(dict(unrun, trial=unrun["trial"] + "-x", position="2", ordering_length="5"))
    rows.append(dict(rows[1], ticket=""))              # manifest mismatch: no ticket
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    segs, dropped = an.load_segments(path, "it_delta", 1.0)
    assert dropped["grader error (excluded)"] == 1
    assert dropped["no ticket (manifest mismatch; excluded)"] == 1
    assert dropped["planned but never ran (counted as failures)"] == 1
    assert len(segs) == len(rows) - 2
    unrun_seg = next(s for s in segs if s.not_run)
    assert not unrun_seg.success and unrun_seg.value is None


def test_single_defect_arm_is_flagged(tmp_path: Path) -> None:
    path = simulate(tmp_path / "s.csv", ramp=1.0, seed=61, n_cold=3, n_warm=3, arm="sd-inf-nofb")
    blocks = conditions_by_arm(path, n_boot=10)
    assert blocks["sd-inf-nofb"]["single_defect_leak"] is True
    out = tmp_path / "r.md"
    assert an.main([str(path), "--metric", "it_delta", "--out", str(out), "--bootstrap", "10"]) == 0
    assert "single-defect start trees contain every later ticket's fix" in out.read_text()
