"""Tests for odyssey.accounting against synthetic Harbor 0.22 job directories.

The factory below writes the same on-disk shapes Harbor does (TrialResult in result.json,
steps/<name>/agent/trajectory.json in ATIF-v1.7 with Claude-Code-style metrics), so the
accounting code is exercised exactly as it will be on a real job.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from odyssey import accounting as acc

PRICES = acc.PriceSheet.load(acc.DEFAULT_PRICES)
OPUS = acc.PriceSheet.load(acc.DEFAULT_PRICES).lookup("claude-opus-5-5").standard


# ----------------------------------------------------------------------------- factory

@dataclass
class Call:
    """One LLM call as Claude Code's converter records it in ATIF."""

    ts: str
    prompt: int
    completion: int
    cached: int = 0
    write: int = 0
    text: str = ""
    tool_output: str | None = "ok"
    model: str | None = None

    def step(self, step_id: int) -> dict:
        call_id = f"toolu_{self.ts}_{self.prompt}"
        step = {
            "step_id": step_id,
            "timestamp": self.ts,
            "source": "agent",
            "model_name": self.model or "claude-opus-5-5",
            "message": self.text or f"thinking at {self.ts}",
            "llm_call_count": 1,
            "metrics": {
                "prompt_tokens": self.prompt,
                "completion_tokens": self.completion,
                "cached_tokens": self.cached,
                "extra": {
                    "cache_creation_input_tokens": self.write,
                    "cache_read_input_tokens": self.cached,
                    "service_tier": "standard",
                },
            },
            "extra": {"is_sidechain": False},
        }
        if self.tool_output is not None:
            step["tool_calls"] = [{
                "tool_call_id": call_id,
                "function_name": "Bash",
                "arguments": {"command": "ls"},
            }]
            step["observation"] = {"results": [
                {"source_call_id": call_id, "content": self.tool_output}
            ]}
        return step


@dataclass
class Turn:
    """A user instruction followed by the calls it triggered."""

    user_ts: str
    instruction: str
    calls: list[Call]


def trajectory(turns: list[Turn], model: str = "claude-opus-5-5") -> dict:
    steps: list[dict] = []
    for turn in turns:
        steps.append({
            "step_id": len(steps) + 1,
            "timestamp": turn.user_ts,
            "source": "user",
            "message": turn.instruction,
            "extra": {"is_sidechain": False},
        })
        for call in turn.calls:
            steps.append(call.step(len(steps) + 1))
    return {
        "schema_version": "ATIF-v1.7",
        "session_id": "sess-1",
        "agent": {"name": "claude-code", "version": "2.9.0", "model_name": model},
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": sum(c.prompt for t in turns for c in t.calls),
            "total_completion_tokens": sum(c.completion for t in turns for c in t.calls),
            "total_cached_tokens": sum(c.cached for t in turns for c in t.calls),
            "total_steps": len(steps),
        },
    }


@dataclass
class StepSpec:
    name: str
    start: str
    end: str
    turns: list[Turn] | None          # trajectory content written for this step; None = no file
    reward: float | None = 1.0
    exception: str | None = None
    cost_usd: float | None = 0.05


@dataclass
class TaskSpec:
    name: str = "toy__arm__o1"
    tickets: list[tuple[str, str, str]] = field(
        default_factory=lambda: [("LED-1", "A", "core"), ("LED-2", "A", "core"),
                                 ("LED-3", "N", "isolated")])
    embed_in_toml: bool = False


def step_name(position: int, ticket: str) -> str:
    """Mirror of odyssey.compile.step_name for orderings shorter than 100."""
    return f"s{position:02d}-{ticket.lower()}"


def make_task(root: Path, spec: TaskSpec) -> Path:
    task_dir = root / "tasks" / spec.name
    task_dir.mkdir(parents=True)
    # Same shape as odyssey.compile.build_manifest.
    manifest = {
        "odyssey_manifest_version": 1,
        "sequence": "toy-ledger",
        "task_name": f"odyssey/{spec.name}",
        "arm": "fm-inf-nofb",
        "arm_options": {"code_state": "forward-merge", "framing": "informed",
                        "feedback": "none", "seed_notes": None},
        "ordering": [t for t, _, _ in spec.tickets],
        "ordering_id": "o1",
        "steps": [
            {"name": step_name(i, t), "position": i, "ticket": t, "cluster": c, "role": r}
            for i, (t, c, r) in enumerate(spec.tickets, start=1)
        ],
    }
    toml = 'schema_version = "1.4"\n'
    if spec.embed_in_toml:
        toml += f"[metadata]\nodyssey_manifest = {json.dumps(json.dumps(manifest))}\n"
    else:
        (task_dir / "odyssey.json").write_text(json.dumps(manifest))
    for step in manifest["steps"]:
        toml += f'\n[[steps]]\nname = "{step["name"]}"\n'
    (task_dir / "task.toml").write_text(toml)
    return task_dir


def make_trial(
    root: Path,
    task_dir: Path,
    steps: list[StepSpec],
    *,
    trial: str = "toy__arm__o1__abc123",
    resume: bool = False,
    model: str = "claude-opus-5-5",
    single_step: bool = False,
) -> Path:
    """Write jobs/<job>/<trial>/ the way Harbor 0.22 leaves it after a trial."""
    job_dir = root / "jobs" / "job-1"
    trial_dir = job_dir / trial
    trial_dir.mkdir(parents=True)
    # Job-level files that discovery must not mistake for trials.
    (job_dir / "result.json").write_text(json.dumps({"id": "j", "n_total_trials": 1}))
    (job_dir / "config.json").write_text("{}")

    def step_result(spec: StepSpec) -> dict:
        exc = None
        if spec.exception:
            exc = {"exception_type": spec.exception, "exception_message": "boom",
                   "exception_traceback": "", "occurred_at": spec.end}
        return {
            "step_name": spec.name,
            "agent_result": None if spec.turns is None and spec.cost_usd is None else {
                "n_input_tokens": 999_999, "n_cache_tokens": 0, "n_output_tokens": 1,
                "cost_usd": spec.cost_usd, "rollout_details": None, "metadata": None,
            },
            "verifier_result": None if spec.reward is None else {
                "rewards": {"reward": spec.reward}},
            "exception_info": exc,
            "agent_execution": {"started_at": spec.start, "finished_at": spec.end},
            "verifier": {"started_at": spec.end, "finished_at": spec.end},
        }

    config = {
        "task": {"path": str(task_dir)},
        "trial_name": trial,
        "trials_dir": str(job_dir),
        "agent": {"name": "claude-code", "model_name": f"anthropic/{model}",
                  "resume_trajectory": resume},
    }
    result = {
        "id": "00000000-0000-0000-0000-000000000001",
        "task_name": task_dir.name,
        "trial_name": trial,
        "trial_uri": trial_dir.as_uri(),
        "task_id": {"path": str(task_dir)},
        "source": None,
        "task_checksum": "x",
        "config": config,
        "agent_info": {"name": "claude-code", "version": "2.9.0",
                       "model_info": {"name": model, "provider": "anthropic"}},
        "agent_result": None,
        "verifier_result": None,
        "exception_info": None,
        "started_at": steps[0].start,
        "finished_at": steps[-1].end,
        "step_results": None if single_step else [step_result(s) for s in steps],
    }
    if single_step:
        only = step_result(steps[0])
        result.update({
            "agent_result": only["agent_result"],
            "verifier_result": only["verifier_result"],
            "exception_info": only["exception_info"],
            "agent_execution": only["agent_execution"],
        })
    (trial_dir / "config.json").write_text(json.dumps(config))
    (trial_dir / "result.json").write_text(json.dumps(result))

    for spec in steps:
        agent_dir = trial_dir / "agent" if single_step else trial_dir / "steps" / spec.name / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        if spec.turns is not None:
            (agent_dir / "trajectory.json").write_text(json.dumps(trajectory(spec.turns, model)))
    return trial_dir


def cost(prompt: int, cached: int, write: int, completion: int, rates=OPUS) -> float:
    fresh = prompt - cached - write
    return (fresh * rates.input + write * rates.cache_write + cached * rates.cache_read
            + completion * rates.output) / 1e6


# Two calls per segment; prompts are consistent with prompt = input + cache read + write.
S1_CALLS = [
    Call("2026-11-10T10:01:00Z", prompt=10_000, completion=500, write=9_000),
    Call("2026-11-10T10:02:00Z", prompt=12_000, completion=300, cached=9_000, write=2_500),
]
S2_CALLS = [
    Call("2026-11-10T10:07:00Z", prompt=14_000, completion=400, cached=12_000, write=1_500),
    Call("2026-11-10T10:08:00Z", prompt=15_000, completion=200, cached=14_000, write=800),
]
W1 = ("2026-11-10T10:00:30Z", "2026-11-10T10:05:00Z")
W2 = ("2026-11-10T10:06:30Z", "2026-11-10T10:10:00Z")


def by_position(rows: list[dict]) -> dict[int, dict]:
    return {r["position"]: r for r in rows}


# ----------------------------------------------------------------------------- tests

def test_fresh_multistep_trial(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "A", "core")]))
    fresh_s2 = [  # a fresh session: step 2 starts from an empty context
        Call("2026-11-10T10:07:00Z", prompt=11_000, completion=400, write=10_000),
        Call("2026-11-10T10:08:00Z", prompt=13_000, completion=200, cached=10_000, write=2_000),
    ]
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix LED-1", S1_CALLS)]),
        StepSpec("s02-led-2", *W2, [Turn("2026-11-10T10:06:40Z", "fix LED-2", fresh_s2)],
                 reward=0.0),
    ])
    rows = by_position(acc.account_trial(trial, PRICES))

    r1 = rows[1]
    assert (r1["sequence"], r1["ticket"], r1["cluster"], r1["role"]) == (
        "toy-ledger", "LED-1", "A", "core")
    assert r1["arm"] == "fm-inf-nofb"
    assert r1["ordering_id"] == "o1" and r1["ordering_length"] == 2
    assert (r1["agent"], r1["model"], r1["resumed"]) == ("claude-code", "claude-opus-5-5", False)
    assert (r1["n_llm_calls"], r1["n_tool_calls"]) == (2, 2)
    assert (r1["prompt_tokens"], r1["cached_tokens"], r1["cache_write_tokens"],
            r1["completion_tokens"]) == (22_000, 9_000, 11_500, 800)
    assert r1["sdc_usd"] == pytest.approx(
        cost(10_000, 0, 9_000, 500) + cost(12_000, 9_000, 2_500, 300))
    # First call: whole prompt is new. Second: growth minus the re-sent reply.
    assert r1["it_delta"] == (10_000 + 500) + (12_000 - 10_000 - 500 + 300)
    assert r1["incremental_tokens"] == r1["it_delta"]
    assert r1["it_startup_tokens"] == 10_000
    assert r1["reported_cost_usd"] == 0.05
    assert r1["agent_seconds"] == 270.0
    assert r1["reward"] == 1.0 and r1["exception"] is None

    r2 = rows[2]
    assert r2["reward"] == 0.0 and r2["resumed"] is False
    assert r2["prompt_tokens"] == 24_000
    assert r2["it_delta"] == (11_000 + 400) + (13_000 - 11_000 - 400 + 200)
    # Harbor's own per-step totals (999,999 in the fixture) disagree and are flagged.
    assert "harness n_input_tokens" in r2["warnings"]
    assert "window" not in r2["warnings"]


def test_resumed_trial_does_not_double_count(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
    t1 = Turn("2026-11-10T10:00:40Z", "fix LED-1", S1_CALLS)
    t2 = Turn("2026-11-10T10:06:40Z", "fix LED-2", S2_CALLS)
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [t1]),
        # Claude Code under --continue: step 2's trajectory re-parses step 1's session too.
        StepSpec("s02-led-2", *W2, [t1, t2]),
    ], resume=True)
    rows = by_position(acc.account_trial(trial, PRICES))

    r2 = rows[2]
    assert r2["resumed"] is True
    assert r2["n_llm_calls"] == 2
    assert r2["prompt_tokens"] == 14_000 + 15_000
    assert r2["completion_tokens"] == 600
    # First call of the resumed segment grows from the last call of segment 1.
    assert r2["it_delta"] == (14_000 - 12_000 - 300 + 400) + (15_000 - 14_000 - 400 + 200)
    assert r2["it_startup_tokens"] == 0
    assert r2["sdc_usd"] == pytest.approx(
        cost(14_000, 12_000, 1_500, 400) + cost(15_000, 14_000, 800, 200))
    # All three attribution signals agree. (The fixture's harness totals, 999,999, fit
    # neither scope, so the harness cost is left empty and that is the only warning.)
    assert "window" not in r2["warnings"] and "seen" not in r2["warnings"]
    assert (r2["reported_cost_usd"], r2["reported_cost_scope"]) == (None, "unknown")
    total_prompt = sum(r["prompt_tokens"] for r in rows.values())
    assert total_prompt == sum(c.prompt for c in S1_CALLS + S2_CALLS)


def test_resumed_trial_without_repeated_history_uses_carry(tmp_path: Path) -> None:
    """Per-invocation trajectories (opencode-style): the predecessor comes from step 1."""
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix LED-1", S1_CALLS)]),
        StepSpec("s02-led-2", *W2, [Turn("2026-11-10T10:06:40Z", "fix LED-2", S2_CALLS)]),
    ], resume=True)
    r2 = by_position(acc.account_trial(trial, PRICES))[2]
    assert r2["it_delta"] == (14_000 - 12_000 - 300 + 400) + (15_000 - 14_000 - 400 + 200)
    assert r2["it_startup_tokens"] == 0


def test_clock_skew_falls_back_to_dedup(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
    t1 = Turn("2026-11-10T10:00:40Z", "fix LED-1", S1_CALLS)
    t2 = Turn("2026-11-10T10:06:40Z", "fix LED-2", S2_CALLS)
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [t1]),
        # Host clock one hour ahead of the container: the window matches nothing.
        StepSpec("s02-led-2", "2026-11-10T11:06:30Z", "2026-11-10T11:10:00Z", [t1, t2]),
    ], resume=True)
    r2 = by_position(acc.account_trial(trial, PRICES))[2]
    assert r2["n_llm_calls"] == 2 and r2["prompt_tokens"] == 29_000
    assert "clock skew" in r2["warnings"]


def test_novel_call_outside_window_is_attributed_and_flagged(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core")]))
    stray = Call("2026-11-10T09:00:00Z", prompt=5_000, completion=50)
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", [stray, *S1_CALLS])]),
    ])
    r1 = acc.account_trial(trial, PRICES)[0]
    # The window only warns: a call is never dropped because of a timestamp.
    assert r1["n_llm_calls"] == 3 and r1["prompt_tokens"] == 27_000
    assert "1 novel steps outside the agent window attributed anyway" in r1["warnings"]


def test_partial_clock_skew_drops_nothing(tmp_path: Path) -> None:
    """Container clock 8 s ahead of the host: the last calls of each window fall outside it."""
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
    late_s1 = [*S1_CALLS, Call("2026-11-10T10:05:07Z", prompt=13_000, completion=100, cached=12_000)]
    late_s2 = [*S2_CALLS, Call("2026-11-10T10:10:07Z", prompt=16_000, completion=100, cached=15_000)]
    for resume in (False, True):
        root = tmp_path / ("resume" if resume else "fresh")
        task = make_task(root, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
        t1 = Turn("2026-11-10T10:00:40Z", "fix LED-1", late_s1)
        t2 = Turn("2026-11-10T10:06:40Z", "fix LED-2", late_s2)
        trial = make_trial(root, task, [
            StepSpec("s01-led-1", *W1, [t1]),
            StepSpec("s02-led-2", *W2, [t1, t2] if resume else [t2]),
        ], resume=resume)
        rows = by_position(acc.account_trial(trial, PRICES))
        assert rows[1]["n_llm_calls"] == 3 and rows[1]["prompt_tokens"] == 35_000
        assert rows[2]["n_llm_calls"] == 3 and rows[2]["prompt_tokens"] == 45_000
        assert "attributed anyway" in rows[2]["warnings"]
        assert "segments attribute" not in rows[2]["warnings"]  # reconciles under resume


def test_compaction_resets_without_negative_or_new_tokens(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core")]))
    calls = [
        Call("2026-11-10T10:01:00Z", prompt=150_000, completion=1_000, cached=140_000, write=9_000),
        # Compaction: the harness summarised; the prompt collapses to 20k.
        Call("2026-11-10T10:02:00Z", prompt=20_000, completion=600, write=19_000),
        Call("2026-11-10T10:03:00Z", prompt=23_000, completion=300, cached=19_000, write=3_500),
    ]
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", calls)]),
    ])
    r1 = acc.account_trial(trial, PRICES)[0]
    assert r1["compactions"] == 1
    assert r1["it_delta"] == (150_000 + 1_000) + (0 + 600) + (23_000 - 20_000 - 600 + 300)


def test_failed_step_and_aborted_trial(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec())  # three planned segments
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", S1_CALLS)],
                 reward=0.0, exception="AgentTimeoutError"),
        # Setup failed: no agent ran, no verifier, no trajectory; Harbor then aborts.
        StepSpec("s02-led-2", *W2, None, reward=None, exception="RuntimeError", cost_usd=None),
    ])
    rows = by_position(acc.account_trial(trial, PRICES))
    assert set(rows) == {1, 2, 3}
    assert rows[1]["exception"] == "AgentTimeoutError" and rows[1]["reward"] == 0.0
    assert rows[1]["prompt_tokens"] == 22_000
    r2 = rows[2]
    assert r2["exception"] == "RuntimeError" and r2["reward"] is None
    assert r2.get("prompt_tokens") is None and r2.get("sdc_usd") is None
    assert "no trajectory.json" in r2["warnings"]
    assert r2["ordering_length"] == 3
    # The segment the trial never reached is still a row, so analysis can count it.
    r3 = rows[3]
    assert (r3["ticket"], r3["exception"], r3["reward"]) == ("LED-3", acc.NOT_RUN, None)
    assert r3["step_name"] == "s03-led-3" and r3["ordering_length"] == 3
    assert "never ran" in r3["warnings"]


def test_cold_single_step_trial_with_manifest_in_task_toml(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(name="toy__cold__LED-3",
                                        tickets=[("LED-3", "N", "isolated")],
                                        embed_in_toml=True))
    trial = make_trial(tmp_path, task, [
        StepSpec("", *W1, [Turn("2026-11-10T10:00:40Z", "fix LED-3", S1_CALLS)]),
    ], single_step=True)
    rows = acc.account_trial(trial, PRICES)
    assert len(rows) == 1
    r = rows[0]
    assert (r["position"], r["ticket"], r["cluster"], r["ordering_length"]) == (1, "LED-3", "N", 1)
    assert r["prompt_tokens"] == 22_000 and r["resumed"] is False
    assert "manifest" not in r["warnings"]


def test_unknown_model_leaves_sdc_empty(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core")]))
    calls = [Call(c.ts, c.prompt, c.completion, c.cached, c.write, model="mystery-9")
             for c in S1_CALLS]
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", calls)]),
    ], model="mystery-9")
    r = acc.account_trial(trial, PRICES)[0]
    assert r["sdc_usd"] is None and r["sdc_neutral_usd"] is None
    assert "no price for model(s) mystery-9" in r["warnings"]
    assert r["prompt_tokens"] == 22_000  # tokens are still counted


def test_missing_manifest_falls_back_to_step_names(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core")]))
    (task / "odyssey.json").unlink()
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", S1_CALLS)]),
    ])
    r = acc.account_trial(trial, PRICES)[0]
    assert (r["position"], r["ticket"], r["cluster"]) == (1, "s01-led-1", None)
    assert "odyssey manifest not found" in r["warnings"]


def test_price_lookup_and_long_context_band() -> None:
    assert PRICES.lookup("anthropic/claude-opus-5-5") is PRICES.lookup("claude-opus-5-5")
    assert PRICES.lookup("claude-sonnet-5-5-20261001") is PRICES.lookup("claude-sonnet-5-5")
    # Bedrock ids stack a date and a version suffix.
    assert PRICES.lookup("us.anthropic.claude-opus-5-5-20261001-v1:0") is PRICES.lookup("claude-opus-5-5")
    assert PRICES.lookup("anthropic.claude-haiku-5-5-20261001-v2") is PRICES.lookup("claude-haiku-5-5")
    assert PRICES.lookup("openai/gpt-5.6-sol") is not None
    assert PRICES.lookup("gpt-9") is None
    haiku = PRICES.lookup("claude-haiku-5-5")
    assert haiku.rates_for(100_000).input == 0.10
    assert haiku.rates_for(100_001).input == 0.50


def test_codex_style_reasoning_and_cache_write_keys() -> None:
    step = {
        "step_id": 1, "timestamp": "2026-11-10T10:00:00Z", "source": "agent",
        "message": "", "llm_call_count": 1,
        "metrics": {"prompt_tokens": 1000, "completion_tokens": 100, "cached_tokens": 800,
                    "extra": {"reasoning_output_tokens": 60, "total_tokens": 1100,
                              "cache_write_input_tokens": 150}},
    }
    reduced = acc._reduce_step(step, "main", "gpt-5.6-sol")
    # total == prompt + completion, so reasoning is already inside completion.
    assert reduced.completion == 100
    assert (reduced.cache_write, reduced.write_recorded) == (150, True)
    step["metrics"]["extra"]["total_tokens"] = 1160
    assert acc._reduce_step(step, "main", None).completion == 160


def test_arm_label_accepts_dict_or_string() -> None:
    assert acc._arm_label({"arm": "fm-inf-nofb"}) == "fm-inf-nofb"
    assert acc._arm_label({"arm": {"framing": "natural", "code_state": "carry"}}) == (
        "code_state=carry,framing=natural")


def test_approx_tokens_is_stable() -> None:
    assert acc.approx_tokens("") == 0
    assert acc.approx_tokens("def add(a, b):\n    return a + b") == 13
    assert acc.approx_tokens("internationalization 2026") == 5 + 2


def test_cli_writes_spec_columns(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
    make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", S1_CALLS)]),
        StepSpec("s02-led-2", *W2, [Turn("2026-11-10T10:06:40Z", "fix", S2_CALLS)]),
    ])
    out = tmp_path / "segments.csv"
    assert acc.main([str(tmp_path / "jobs"), "--out", str(out)]) == 0
    with open(out, newline="") as handle:
        reader = csv.DictReader(handle)
        spec_columns = acc.COLUMNS[:acc.COLUMNS.index("exception") + 1]
        assert reader.fieldnames[:len(spec_columns)] == spec_columns
        rows = list(reader)
    assert [r["position"] for r in rows] == ["1", "2"]
    assert rows[0]["job"] == "job-1" and rows[0]["resumed"] == "false"


# ----------------------------------------------------------------------------- real shapes

REAL = Path(__file__).parent / "fixtures" / "harbor-real"


def _real_rows(trial: str) -> dict[int, dict]:
    """Rows for a trimmed real trial (tests/fixtures/harbor-real)."""
    trial_dir = REAL / "jobs" / "host-smoke" / trial
    return by_position(acc.account_trial(trial_dir, PRICES, [REAL / "tasks"]))


# Per-invocation usage printed by the CLIs themselves for segments 2 and 3: Claude Code's
# `result.usage` and the increase of Codex's thread-cumulative `turn.completed.usage`.
# Under resume Harbor's step totals are session-cumulative; accounting must recover these.
CLAUDE_RESUME_TRUTH = {  # cached, cache write, completion; CLI total_cost_usd (cumulative)
    2: (178_264, 8_138, 2_369, 0.009893925000000001),
    3: (182_066, 5_000, 1_830, 0.013255585000000004),
}
CODEX_RESUME_TRUTH = {  # prompt, cached, cache write, completion
    2: (221_395 - 103_167, 208_761 - 91_616, 12_111 - 11_282, 2_126 - 1_334),
    3: (427_313 - 221_395, 413_719 - 208_761, 12_899 - 12_111, 3_364 - 2_126),
}


def test_real_claude_code_resume_is_decumulated() -> None:
    rows = _real_rows("claude-code-resume")
    result = json.loads((REAL / "jobs/host-smoke/claude-code-resume/result.json").read_text())
    harness = [s["agent_result"] for s in result["step_results"]]
    for pos, (cached, write, completion, cli_cost) in CLAUDE_RESUME_TRUTH.items():
        r = rows[pos]
        assert r["resumed"] is True and r["warnings"] == ""
        assert (r["cached_tokens"], r["cache_write_tokens"], r["completion_tokens"]) == (
            cached, write, completion)
        assert r["reported_cost_scope"] == "cumulative"
        assert r["reported_cost_usd"] == pytest.approx(cli_cost - harness[pos - 2]["cost_usd"])
        # The frozen haiku prices reproduce the CLI's own per-segment cost.
        assert r["sdc_usd"] == pytest.approx(r["reported_cost_usd"], rel=1e-3)
    # Segments partition the session: their prompts sum to Harbor's final cumulative total.
    assert sum(r["prompt_tokens"] for r in rows.values()) == harness[-1]["n_input_tokens"]


def test_real_codex_resume_is_decumulated() -> None:
    rows = _real_rows("codex-resume")
    for pos, truth in CODEX_RESUME_TRUTH.items():
        r = rows[pos]
        assert (r["prompt_tokens"], r["cached_tokens"], r["cache_write_tokens"],
                r["completion_tokens"]) == truth
        assert r["reported_cost_scope"] == "cumulative" and r["warnings"] == ""
        assert r["sdc_usd"] == pytest.approx(r["reported_cost_usd"], rel=1e-3)
        assert r["it_startup_tokens"] == 0


def test_real_claude_code_fresh_matches_harness() -> None:
    rows = _real_rows("claude-code-fresh")
    result = json.loads((REAL / "jobs/host-smoke/claude-code-fresh/result.json").read_text())
    for pos, step in enumerate(result["step_results"], start=1):
        r = rows[pos]
        assert r["resumed"] is False and r["warnings"] == ""
        assert r["prompt_tokens"] == step["agent_result"]["n_input_tokens"]
        assert r["reported_cost_scope"] == "segment"
        assert r["sdc_usd"] == pytest.approx(step["agent_result"]["cost_usd"], rel=1e-3)
        assert r["it_startup_tokens"] > 0  # every fresh session pays the harness preamble


# ----------------------------------------------------------------------------- review fixes


def test_recompiled_task_dir_does_not_relabel_old_trials(tmp_path: Path) -> None:
    """The manifest on disk now names other steps: the old trial must not inherit its tickets."""
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core")]))
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", S1_CALLS)]),
    ])
    assert acc.account_trial(trial, PRICES)[0]["ticket"] == "LED-1"
    # Recompiled in place with another ticket (as a positional design id once allowed).
    manifest = json.loads((task / "odyssey.json").read_text())
    manifest["steps"] = [{"name": "s01-led-3", "position": 1, "ticket": "LED-3", "cluster": "N",
                          "role": "isolated"}]
    (task / "odyssey.json").write_text(json.dumps(manifest))
    rows = acc.account_trial(trial, PRICES)
    assert [r["ticket"] for r in rows] == [None]          # no silent LED-3 label
    assert "not in the task's manifest" in rows[0]["warnings"]


def test_grader_error_is_carried_to_the_row(tmp_path: Path) -> None:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", *W1, [Turn("2026-11-10T10:00:40Z", "fix", S1_CALLS)], reward=0.0),
        StepSpec("s02-led-2", *W2, [Turn("2026-11-10T10:06:40Z", "fix", S2_CALLS)], reward=0.0),
    ])
    result = json.loads((trial / "result.json").read_text())
    result["step_results"][0]["verifier_result"]["rewards"] = {"reward": 0, "error": 1}
    result["step_results"][1]["verifier_result"]["rewards"] = {"reward": 0, "error": 0}
    (trial / "result.json").write_text(json.dumps(result))
    rows = by_position(acc.account_trial(trial, PRICES))
    assert (rows[1]["grader_error"], rows[2]["grader_error"]) == (True, False)


def _side_step(step_id, ts, source, prompt=None, completion=None, cached=0, text="x"):
    step = {"step_id": step_id, "timestamp": ts, "source": source, "message": text,
            "extra": {"is_sidechain": True}}  # Harbor 0.22: no agent id survives conversion
    if source == "agent":
        step.update({"model_name": "claude-opus-5-5", "llm_call_count": 1, "metrics": {
            "prompt_tokens": prompt, "completion_tokens": completion, "cached_tokens": cached,
            "extra": {"cache_creation_input_tokens": prompt - cached - 3,
                      "cache_read_input_tokens": cached}}})
    return step


def _main_step(step_id, ts, source, prompt=None, completion=None, cached=0):
    step = _side_step(step_id, ts, source, prompt, completion, cached)
    step["extra"] = {"is_sidechain": False}
    return step


def _write_sidechain_trial(tmp_path: Path, steps: list[dict]) -> Path:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core")]))
    trial = make_trial(tmp_path, task, [
        StepSpec("s01-led-1", "2026-11-10T09:59:00Z", "2026-11-10T10:05:00Z",
                 [Turn("2026-11-10T10:00:00Z", "x", [])]),
    ])
    traj = {"schema_version": "ATIF-v1.7", "session_id": "sid1",
            "agent": {"name": "claude-code", "model_name": "claude-opus-5-5"}, "steps": steps}
    (trial / "steps" / "s01-led-1" / "agent" / "trajectory.json").write_text(json.dumps(traj))
    return trial


def test_sequential_subagents_without_agent_ids(tmp_path: Path) -> None:
    """Shape produced by Harbor 0.22's own Claude Code converter (agentId dropped)."""
    steps = [
        _main_step(1, "2026-11-10T10:00:00Z", "user"),
        _main_step(2, "2026-11-10T10:00:01Z", "agent", 20_000, 100),
        _side_step(3, "2026-11-10T10:00:02Z", "user", text="explore module X"),
        _side_step(4, "2026-11-10T10:00:03Z", "agent", 9_000, 200),
        _side_step(5, "2026-11-10T10:00:10Z", "agent", 40_000, 300, cached=9_000),
        _side_step(6, "2026-11-10T10:00:20Z", "user", text="explore module Y"),
        _side_step(7, "2026-11-10T10:00:21Z", "agent", 9_100, 200),
        _side_step(8, "2026-11-10T10:00:30Z", "agent", 39_000, 300, cached=9_100),
        _main_step(9, "2026-11-10T10:01:00Z", "agent", 21_000, 50, cached=20_000),
    ]
    r = acc.account_trial(_write_sidechain_trial(tmp_path, steps), PRICES)[0]
    new_in = 20_000 + 9_000 + (40_000 - 9_000 - 200) + 9_100 + (39_000 - 9_100 - 200) + (21_000 - 20_000 - 100)
    completions = 100 + 200 + 300 + 200 + 300 + 50
    assert r["it_delta"] == new_in + completions == 100_650
    assert r["compactions"] == 0
    assert r["it_startup_tokens"] == 20_000 + 9_000 + 9_100
    # Only the main chain's first prompt is harness overhead; subagent prompts are real context.
    assert r["it_startup_main_tokens"] == 20_000
    assert r["it_delta_net"] == 100_650 - 20_000


def test_interleaved_subagents_are_separated_by_their_caches() -> None:
    steps = [
        _main_step(1, "2026-11-10T10:00:01Z", "agent", 20_000, 100),
        _side_step(2, "2026-11-10T10:00:02Z", "user"),
        _side_step(3, "2026-11-10T10:00:02Z", "user"),
        _side_step(4, "2026-11-10T10:00:03Z", "agent", 9_000, 200, cached=8_000),
        _side_step(5, "2026-11-10T10:00:04Z", "agent", 9_100, 200, cached=8_000),
        _side_step(6, "2026-11-10T10:00:05Z", "agent", 30_000, 300, cached=9_000),
        _side_step(7, "2026-11-10T10:00:06Z", "agent", 25_000, 300, cached=9_100),
        _side_step(8, "2026-11-10T10:00:07Z", "agent", 31_000, 100, cached=30_000),
    ]
    reduced = acc._flatten({"steps": steps}, "main", "claude-opus-5-5")
    chains = [r.chain for r in reduced]
    assert chains[0] == "main"
    a, b = chains[3], chains[4]
    assert a != b and a.startswith("side:anon") and b.startswith("side:anon")
    assert chains[5:] == [a, b, a]


def test_resumed_segments_do_not_carry_subagent_chains() -> None:
    carry = {"main": acc.AtifStep(key="m", chain="main", timestamp=None, copied=False,
                                  is_llm_call=True, n_llm_calls=1, model="claude-opus-5-5",
                                  has_usage=True, prompt=10_000, completion=100),
             "side:anon1": acc.AtifStep(key="s", chain="side:anon1", timestamp=None, copied=False,
                                        is_llm_call=True, n_llm_calls=1, model="claude-opus-5-5",
                                        has_usage=True, prompt=40_000, completion=300)}
    sub = acc.AtifStep(key="n", chain="side:anon1", timestamp=None, copied=False, is_llm_call=True,
                       n_llm_calls=1, model="claude-opus-5-5", has_usage=True, prompt=9_000, completion=200)
    seg = acc.segment_tokens([sub], [True], dict(carry), resumed=True, prices=PRICES)
    assert seg.it_delta == 9_000 + 200 and seg.compactions == 0
