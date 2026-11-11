"""DeepSeek Harness support: `--agent-cli dsh`, the session-log -> ATIF
conversion and its accounting. The conversion is standard-library code
(scripts/open_models/dsh_session.py); Harbor's own ATIF model checks it where Harbor is
importable. No network call and no credential is used.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from odyssey import accounting as acc
from odyssey.compile import AGENT_CLI_NPM_DIR, ArmOptions, compile_task
from odyssey.design import explicit
from odyssey.spec import load_sequence
from test_accounting import PRICES, StepSpec, TaskSpec, make_task, make_trial
from test_compile import _harbor_python

SCRIPTS = Path(__file__).parents[1] / "scripts" / "open_models"
sys.path.insert(0, str(SCRIPTS))
import dsh_session as dsh  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mini-seq"
ORDER = ("MINI-2", "MINI-1", "MINI-3")
T0 = 1794359218000  # ms
MODEL = "openai/minimax.minimax-m2.5"
PRICE = {"input": 0.30, "output": 1.20, "cache_read": 0.30, "cache_write": 0.30}


# --- compile ------------------------------------------------------------------------

def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_agent_cli_dsh_block(tmp_path):
    seq = load_sequence(FIXTURE)
    plain = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist"), tmp_path / "plain")
    uv = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist", agent_clis=("kimi-cli",)), tmp_path / "uv")
    both = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist", agent_clis=("dsh", "kimi-cli")),
                        tmp_path / "both")
    base = (uv / "environment" / "Dockerfile").read_text()
    dockerfile = (both / "environment" / "Dockerfile").read_text()
    assert dockerfile.startswith(base)  # Claude Code/Codex, then the uv tools, unchanged
    block = dockerfile[len(base):]
    assert block.startswith("# --- appended by `odyssey compile --egress allowlist --agent-cli dsh`")
    assert "COPY odyssey-dsh/package.json odyssey-dsh/package-lock.json /opt/dsh/" in block
    assert "npm-cli.js ci" in block and "--version" in block
    assert "@@" not in block and "FROM " not in block
    run = block[block.index("RUN "):]
    lines = run.rstrip("\n").splitlines()
    assert all(line.endswith("\\") for line in lines[:-1]) and not lines[-1].endswith("\\")
    # The lockfile pins dsh and every dependency to the npm registry with an integrity hash.
    lock = json.loads((both / "environment" / AGENT_CLI_NPM_DIR / "package-lock.json").read_text())
    pkgs = {k: v for k, v in lock["packages"].items() if k}
    assert pkgs["node_modules/@deepseek-ai/dsh"]["version"] == "0.2.1-rc.4"
    assert pkgs["node_modules/@deepseek-ai/dsh"]["integrity"].startswith("sha512-8gxl6zOi6vx1sDolH")
    assert all(v["resolved"].startswith("https://registry.npmjs.org/") and v["integrity"].startswith("sha512-")
               for v in pkgs.values() if not v.get("link"))
    package = json.loads((both / "environment" / AGENT_CLI_NPM_DIR / "package.json").read_text())
    assert package["dependencies"] == lock["packages"][""]["dependencies"] == {"@deepseek-ai/dsh": "0.2.1-rc.4"}
    # The lockfile is part of the image's build context (not dockerignored).
    assert AGENT_CLI_NPM_DIR not in (both / "environment" / ".dockerignore").read_text()
    manifest = json.loads((both / "odyssey.json").read_text())
    assert manifest["arm_options"]["egress"]["agent_clis"] == ["dsh", "kimi-cli"]
    # dsh alone: the Claude/Codex block then dsh, no uv block.
    only = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist", agent_clis=("dsh",)), tmp_path / "only")
    text = (only / "environment" / "Dockerfile").read_text()
    assert text.startswith((plain / "environment" / "Dockerfile").read_text()) and "uv tool install" not in text
    # Without dsh nothing new is written.
    assert not (uv / "environment" / AGENT_CLI_NPM_DIR).exists()
    assert _tree(uv) == _tree(compile_task(seq, explicit(ORDER), ArmOptions(
        egress="allowlist", agent_clis=("kimi-cli",)), tmp_path / "uv2"))


def test_profile_patch_has_no_secret_and_turns_off_extras():
    patch = dsh.profile_patch(model="zai.glm-5", base_url="https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
                              api_key_env="DSH_BEDROCK_API_KEY", context_window=202752, max_tokens=32768,
                              reasoning_effort="high")
    assert "apiKeyEnv: DSH_BEDROCK_API_KEY" in patch and "bedrock-api-key" not in patch
    assert 'model: "zai.glm-5"' in patch and "reasoningEffort: high" in patch and "high: high" in patch
    for row in ("session-title-llm", "tool-web"):
        assert f"- id: {row}\n  disabled: true" in patch
    assert "compression: none" in patch and "contextWindow: 202752" in patch
    off = dsh.profile_patch(model="m", base_url="https://x.invalid/v1", api_key_env="K", context_window=1,
                            max_tokens=1, reasoning_effort=None)
    assert "reasoningEfforts: false" in off and "reasoningEffort:" not in off


# --- session logs -> ATIF ----------------------------------------------------------------

def _ev(seq, kind, dt, **data):
    return {"type": kind, "seq": seq, "time": T0 + dt, "data": data}


def _call(seq, dt, msg_id, text, usage, tool=None, reasoning=None):
    content = []
    if reasoning:
        content.append({"type": "reasoning", "text": reasoning})
    content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool-call", "id": tool, "name": "bash", "arguments": '{"command":"ls"}'})
    return _ev(seq, "assistant/message", dt, turn=1, step=seq,
               message={"role": "assistant", "content": content, "id": msg_id,
                        "source": {"kind": "model", "provider": "bedrock-openai", "model": "minimax.minimax-m2.5"}},
               usage=usage, stream=[])


def _result(seq, dt, call_id, text):
    return _ev(seq, "tool/result", dt, message={"role": "tool", "toolCallId": call_id,
                                                  "content": [{"type": "text", "text": text}]})


def _user(seq, dt, text, kind="user"):
    return _ev(seq, "user/message", dt, role="user", content=[{"type": "text", "text": text}], source={"kind": kind})


def _sessions(resumed: bool):
    main = [
        {"type": "session", "version": 4, "id": "session-main", "createdAt": T0, "cwd": "/app", "delegationDepth": 0},
        _ev(1, "system/message", 1, message={"role": "system", "content": [{"type": "text", "text": "You are dsh."}]}),
        _user(2, 2, "fix ticket 1"),
        _user(3, 3, "Current working directory: /app", kind="runtime-context"),
        _call(4, 1000, "m1", "look", {"inputTokens": 5000, "outputTokens": 100, "totalTokens": 5100}, tool="c1",
              reasoning="think"),
        _result(5, 1100, "c1", "a.c b.c"),
        # A failed stream that still reported usage, then a retry.
        _ev(6, "assistant/attempt", 2000, turn=1, step=2, stream=[
            {"type": "chunk", "time": T0 + 2000, "chunk": {"type": "usage", "usage": {"inputTokens": 50, "outputTokens": 5,
                                                                                      "cacheReadTokens": 5000}}},
            {"type": "chunk", "time": T0 + 2001, "chunk": {"type": "finish", "reason": {"kind": "error"}}}]),
        _ev(7, "assistant/attempt", 2500, turn=1, step=2, stream=[]),  # no usage: not a billed call
        _call(8, 3000, "m2", "done", {"inputTokens": 200, "outputTokens": 40, "totalTokens": 5340,
                                      "cacheReadTokens": 5100}),
        _ev(9, "compaction/summary", 4000, summary="the story so far", provider="bedrock-openai",
            model="minimax.minimax-m2.5", usage={"inputTokens": 6000, "outputTokens": 300}),
    ]
    child = [
        {"type": "session", "version": 4, "id": "child-1", "createdAt": T0 + 500, "cwd": "/app",
         "parentSession": "session-main", "isSeeded": False, "origin": "subagent", "delegationDepth": 1},
        _user(1, 600, "explore"),
        _call(2, 900, "s1", "found it", {"inputTokens": 3000, "outputTokens": 60}),
    ]
    if resumed:
        main += [
            _user(10, 600_000, "fix ticket 2"),
            _call(11, 601_000, "m3", "ok", {"inputTokens": 400, "outputTokens": 30, "cacheReadTokens": 6400}),
        ]
    return {"session-main": main, "child-1": child}


def test_session_to_atif():
    traj = dsh.to_atif(_sessions(False), "session-main", MODEL)
    steps = traj["steps"]
    assert [s["source"] for s in steps] == ["system", "user", "system", "agent", "agent", "agent", "agent", "agent"]
    assert [s["step_id"] for s in steps] == list(range(1, 9))
    first = steps[3]
    assert first["metrics"]["prompt_tokens"] == 5000 and first["metrics"]["completion_tokens"] == 100
    assert first["reasoning_content"] == "think" and first["message"] == "look"
    assert first["tool_calls"] == [{"tool_call_id": "c1", "function_name": "bash", "arguments": {"command": "ls"}}]
    assert first["observation"] == {"results": [{"source_call_id": "c1", "content": "a.c b.c"}]}
    attempt, empty, second, summary = steps[4:]
    assert attempt["metrics"]["prompt_tokens"] == 5050 and attempt["metrics"]["cached_tokens"] == 5000
    assert "metrics" not in empty and empty["extra"]["no_usage"]
    assert second["metrics"] == {"prompt_tokens": 5300, "completion_tokens": 40, "cached_tokens": 5100,
                                 "extra": {"dsh_usage": {"inputTokens": 200, "outputTokens": 40,
                                                         "totalTokens": 5340, "cacheReadTokens": 5100}}}
    assert summary["message"] == "the story so far" and summary["metrics"]["prompt_tokens"] == 6000
    (sub,) = traj["subagent_trajectories"]
    assert sub["trajectory_id"] == "dsh-subagent-child-1" and sub["steps"][-1]["metrics"]["prompt_tokens"] == 3000
    fm = traj["final_metrics"]
    assert fm["total_prompt_tokens"] == 5000 + 5050 + 5300 + 6000 + 3000
    assert fm["total_completion_tokens"] == 100 + 5 + 40 + 300 + 60
    # Usage for the spend cap and Harbor's totals: every billed call, by time.
    assert len(dsh.calls(_sessions(False))) == 5
    assert len(dsh.calls(_sessions(False), since_ms=T0 + 2500)) == 2
    assert dsh.usage_cost({"inputTokens": 1_000_000, "outputTokens": 1_000_000}, PRICE) == pytest.approx(1.5)


def test_fork_copies_are_not_counted_twice():
    sessions = _sessions(False)
    fork = [{"type": "session", "id": "fork-1", "createdAt": T0 + 5000, "isSeeded": True, "delegationDepth": 1},
            _call(1, 1000, "m1", "look", {"inputTokens": 5000, "outputTokens": 100})]  # the parent's call, copied
    sessions["fork-1"] = fork
    traj = dsh.to_atif(sessions, "session-main", MODEL)
    assert [t["trajectory_id"] for t in traj["subagent_trajectories"]] == ["dsh-subagent-child-1"]
    assert len(dsh.calls(sessions)) == 5


def test_find_sessions_reads_logs_and_skips_a_torn_line(tmp_path):
    for sid, events in _sessions(True).items():
        d = tmp_path / "sessions" / "--app--" / sid
        d.mkdir(parents=True)
        (d / "session.v4.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events) + '{"type": "torn')
    found = dsh.find_sessions(tmp_path)
    assert set(found) == {"session-main", "child-1"} and len(found["session-main"]) == 12


HARBOR_CHECK = r"""
import json, sys
from harbor.models.trajectories import Trajectory
Trajectory.model_validate(json.loads(sys.stdin.read()))
print("ok")
"""


def test_atif_validates_in_harbor():
    python = _harbor_python()
    if python is None:
        pytest.skip("harbor not importable")
    traj = dsh.to_atif(_sessions(True), "session-main", MODEL)
    proc = subprocess.run([python, "-c", HARBOR_CHECK], input=json.dumps(traj), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_resumed_dsh_session_is_accounted_once_per_call(tmp_path):
    """Two steps of one resumed dsh session: step 2's trajectory repeats step 1's calls."""
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "B", "core")]))
    w1 = ("2026-11-10T10:00:00Z", "2026-11-10T10:05:00Z")
    trial = make_trial(tmp_path, task, [StepSpec("s01-led-1", *w1, None), StepSpec("s02-led-2", *w1, None)],
                       resume=True, model="minimax.minimax-m2.5")
    s1 = dsh.to_atif(_sessions(False), "session-main", MODEL)
    s2 = dsh.to_atif(_sessions(True), "session-main", MODEL)
    for name, traj in (("s01-led-1", s1), ("s02-led-2", s2)):
        (trial / "steps" / name / "agent" / "trajectory.json").write_text(json.dumps(traj))
    # Windows from the session times, and Harbor's per-step totals as the wrapper sets them.
    result = json.loads((trial / "result.json").read_text())
    iso = dsh._iso
    windows = [(iso(T0 - 1000), iso(T0 + 5000)), (iso(T0 + 599_000), iso(T0 + 602_000))]
    for step, (start, end), prompt in zip(result["step_results"], windows, (24_350, 6_800)):
        step["agent_execution"] = {"started_at": start, "finished_at": end}
        step["agent_result"]["n_input_tokens"] = prompt
    (trial / "result.json").write_text(json.dumps(result))
    rows = {r["position"]: r for r in acc.account_trial(trial, PRICES)}
    assert rows[1]["n_llm_calls"] == 5 and rows[1]["prompt_tokens"] == 24_350
    assert rows[2]["n_llm_calls"] == 1 and rows[2]["prompt_tokens"] == 6_800
    assert rows[2]["cached_tokens"] == 6_400 and rows[2]["resumed"] is True
    assert rows[1]["sdc_usd"] == pytest.approx((24_350 * 0.30 + 505 * 1.20) / 1e6)
    for row in rows.values():
        assert row["warnings"] in ("", None, []), row["warnings"]


WRAPPER_CHECK = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import harbor_agents as h
from harbor.models.agent.context import AgentContext
logs = Path(sys.argv[2])
agent = h.DshPreinstalled(logs_dir=logs, model_name="openai/minimax.minimax-m2.5", cap_usd="1.5")
off = h.DshPreinstalled(logs_dir=logs, model_name="openai/zai.glm-5", reasoning_effort="none")
ctx = AgentContext()
agent.populate_context_post_run(ctx)
traj = json.loads((logs / "trajectory.json").read_text())
print(json.dumps({
    "name": h.DshPreinstalled.name(), "resume": h.DshPreinstalled.SUPPORTS_RESUME, "atif": h.DshPreinstalled.SUPPORTS_ATIF,
    "price": agent._price(), "cap": agent._cap, "effort": [agent._effort, off._effort],
    "steps": len(traj["steps"]), "subagents": len(traj.get("subagent_trajectories") or []),
    "ctx": [ctx.n_input_tokens, ctx.n_cache_tokens, ctx.n_output_tokens, ctx.cost_usd],
}))
"""


def test_wrapper_converts_a_resumed_session(tmp_path):
    python = _harbor_python()
    if python is None:
        pytest.skip("harbor not importable")
    for sid, events in _sessions(True).items():
        d = tmp_path / "dsh-home" / "sessions" / "--app--" / sid
        d.mkdir(parents=True)
        (d / "session.v4.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (tmp_path / "dsh").mkdir()
    (tmp_path / "dsh" / "session-id").write_text("session-main")
    (tmp_path / "dsh" / "since-1.txt").write_text(f"{T0}\n")
    (tmp_path / "dsh" / "since-2.txt").write_text(f"{T0 + 599_000}\n")
    proc = subprocess.run([python, "-c", WRAPPER_CHECK, str(SCRIPTS), str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["name"] == "dsh" and got["resume"] and got["atif"]
    assert got["price"] == PRICE and got["cap"] == 1.5 and got["effort"] == ["high", None]
    assert got["steps"] == 10 and got["subagents"] == 1
    # Harbor's per-step totals cover only the last invocation (the resumed step).
    assert got["ctx"] == [6800, 6400, 30, pytest.approx((6800 * 0.30 + 30 * 1.20) / 1e6)]
