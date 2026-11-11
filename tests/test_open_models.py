"""scripts/open_models: the preinstalled Harbor agents and the Bedrock token minting.

Both need packages the odyssey package does not depend on (harbor, botocore), so they
run in the Python behind the `harbor` CLI and are skipped where it is missing. No
network call and no AWS credential is used.
"""

import json
import subprocess
from pathlib import Path

import pytest

from test_compile import _harbor_python

SCRIPTS = Path(__file__).parents[1] / "scripts" / "open_models"

AGENTS_CHECK = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["ODYSSEY_SECRET_ENV"] = "OPENAI_API_KEY, MSWEA_API_KEY"
os.environ["OPENAI_API_KEY"] = "tok-123"
os.environ.pop("MSWEA_API_KEY", None)
import harbor_agents as h
from harbor.environments.docker.docker import DockerEnvironment
from harbor.agents.installed.kimi_cli import KimiCli
from harbor.agents.installed.mini_swe_agent import MiniSweAgent
args = ["exec", "-e", "HARBOR_KIMI_API_KEY=tok-123", "-e", "KIMI_SHARE_DIR=/x", "-e", "OPENAI_API_KEY=tok-123",
        "-e", "OTHER=tok-1234", "main", "bash", "-c", "echo tok-123"]
out = h.hide_secret_args(args)
exported = os.environ.get("HARBOR_KIMI_API_KEY")
del os.environ["ODYSSEY_SECRET_ENV"]
unchanged = h.hide_secret_args(args) == args
kimi = h.KimiCliPreinstalled(logs_dir=__import__("pathlib").Path("/tmp"), model_name="openai/us.moonshotai.kimi-k3",
                              base_url="https://example.invalid/v1")
cfg = json.loads(kimi._build_config_json("openai", "us.moonshotai.kimi-k3"))
off = h.KimiCliPreinstalled(logs_dir=__import__("pathlib").Path("/tmp"), model_name="openai/x", thinking="false")
cfg_off = json.loads(off._build_config_json("openai", "x"))
print(json.dumps({
    "kimi_cfg": [cfg.get("default_thinking"), cfg["models"]["model"].get("capabilities"),
                 cfg["providers"]["harbor"]["base_url"], cfg["providers"]["harbor"]["type"]],
    "kimi_off": [cfg_off.get("default_thinking"), cfg_off["models"]["model"].get("capabilities")],
    "out": out,
    "exported": exported,
    "patched": DockerEnvironment._run_docker_compose_command is h._run_docker_compose_command,
    "names": [h.KimiCliPreinstalled.name(), h.MiniSweAgentPreinstalled.name()],
    "subclass": [issubclass(h.KimiCliPreinstalled, KimiCli), issubclass(h.MiniSweAgentPreinstalled, MiniSweAgent)],
    "no_secret_env": unchanged,
}))
"""


def test_preinstalled_agents_hide_secrets_from_the_command_line():
    python = _harbor_python()
    if python is None:
        pytest.skip("harbor not importable")
    proc = subprocess.run([python, "-c", AGENTS_CHECK, str(SCRIPTS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    # Only exact secret values are rewritten; a value that merely contains it, a
    # non-secret variable and the command itself are left alone.
    assert got["out"] == ["exec", "-e", "HARBOR_KIMI_API_KEY", "-e", "KIMI_SHARE_DIR=/x", "-e", "OPENAI_API_KEY",
                          "-e", "OTHER=tok-1234", "main", "bash", "-c", "echo tok-123"]
    assert got["exported"] == "tok-123"
    assert got["patched"] is True
    assert got["names"] == ["kimi-cli", "mini-swe-agent"]
    assert got["subclass"] == [True, True]
    assert got["no_secret_env"] is True
    # kimi-cli gets thinking on (Bedrock's Kimi rejects reasoning_effort: null), unless turned off.
    assert got["kimi_cfg"] == [True, ["thinking"], "https://example.invalid/v1", "openai_legacy"]
    assert got["kimi_off"] == [None, None]


TOKEN_CHECK = r"""
import base64, json, sys, urllib.parse
sys.path.insert(0, sys.argv[1])
import bedrock_token as b
from botocore.credentials import Credentials
policy = b.session_policy(["us.moonshotai.kimi-k3", "zai.glm-5"], "123456789012")
tok = b.bearer_token(Credentials("ASIAEXAMPLE", "secret", "session"), "us-east-1", 30)
url = base64.b64decode(tok[len(b.TOKEN_PREFIX):]).decode()
q = urllib.parse.parse_qs(urllib.parse.urlsplit("https://" + url).query)
print(json.dumps({"policy": policy, "prefix": tok.startswith("bedrock-api-key-"), "host": url.split("/")[0],
                  "action": q["Action"], "expires": q["X-Amz-Expires"], "version": q["Version"],
                  "has_secret": "secret" in url}))
"""


def test_bedrock_token_policy_and_format():
    python = _harbor_python()
    if python is None:
        pytest.skip("harbor's python (which ships botocore) not found")
    proc = subprocess.run([python, "-c", TOKEN_CHECK, str(SCRIPTS)], capture_output=True, text=True)
    if "No module named 'botocore'" in proc.stderr:
        pytest.skip("botocore not importable")
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    invoke, bearer = got["policy"]["Statement"]
    assert invoke["Action"] == ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    assert invoke["Resource"] == [
        "arn:aws:bedrock:*::foundation-model/moonshotai.kimi-k3",
        "arn:aws:bedrock:*:123456789012:inference-profile/us.moonshotai.kimi-k3",
        "arn:aws:bedrock:*:123456789012:inference-profile/global.moonshotai.kimi-k3",
        "arn:aws:bedrock:*::foundation-model/zai.glm-5",
        "arn:aws:bedrock:*:123456789012:inference-profile/us.zai.glm-5",
        "arn:aws:bedrock:*:123456789012:inference-profile/global.zai.glm-5",
    ]
    assert bearer == {"Effect": "Allow", "Action": ["bedrock:CallWithBearerToken"], "Resource": "*"}
    assert got["prefix"] and got["host"] == "bedrock.amazonaws.com"
    assert got["action"] == ["CallWithBearerToken"] and got["expires"] == ["1800"] and got["version"] == ["1"]
    assert got["has_secret"] is False  # the secret key signs the URL but is not in it


def test_spend_watch_counts_subagents_and_matches_the_converter():
    """spend_watch prices the same calls the ATIF trajectory holds, subagents included."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("spend_watch", SCRIPTS / "spend_watch.py")
    sw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sw)

    def ev(kind, payload=None):
        return json.dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": kind, "payload": payload or {}}})

    def sub(kind, payload=None):
        return ev("SubagentEvent", {"agent_id": "a1", "event": {"type": kind, "payload": payload or {}}})

    usage = lambda n: {"token_usage": {"input_other": n, "input_cache_read": 10 * n, "output": n}}
    log = "\n".join([
        ev("TurnBegin"), ev("StepBegin", {"n": 1}), ev("StatusUpdate", usage(1)),
        ev("StepBegin", {"n": 2}), sub("TurnBegin"), sub("StepBegin"), sub("StatusUpdate", usage(5)),
        ev("StatusUpdate", usage(2)), ev("StatusUpdate", usage(3)),  # last update of a step wins
        "not json", sub("StepBegin"), sub("StatusUpdate", usage(7)), ev("TurnEnd"),
    ])
    got = sw.step_usages(log)
    assert sorted(u["input_other"] for u in got) == [1, 3, 5, 7]
    price = {"input": 1.0, "cache_read": 0.1, "cache_write": 1.25, "output": 10.0}
    assert abs(sw.cost(got, price) - (16 * 1.0 + 160 * 0.1 + 16 * 10.0) / 1e6) < 1e-12


KIMI_SUBAGENT_CHECK = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import harbor_agents as h
u = lambda n: {"token_usage": {"input_other": n, "input_cache_read": 10 * n, "output": n}}
e = lambda t, p=None: {"type": t, "payload": p or {}}
s = lambda t, p=None: {"type": "SubagentEvent", "payload": {"agent_id": "a1", "event": e(t, p)}}
events = [e("TurnBegin"), e("StepBegin", {"n": 1}), e("StatusUpdate", u(1)),
          e("StepBegin", {"n": 2}), s("TurnBegin"), s("StepBegin", {"n": 1}), s("StatusUpdate", u(5)), s("TurnEnd"),
          e("StatusUpdate", u(2)), e("TurnEnd")]
agent = h.KimiCliPreinstalled(logs_dir=Path("/tmp"), model_name="openai/us.moonshotai.kimi-k3")
t = agent._convert_events_to_trajectory(events).to_json_dict()
plain = h.KimiCli._convert_events_to_trajectory(agent, [x for x in events if x["type"] != "SubagentEvent"]).to_json_dict()
print(json.dumps({"main": [st["metrics"]["prompt_tokens"] for st in t["steps"]],
                  "subs": [[st["metrics"]["prompt_tokens"] for st in sub["steps"]] for sub in t["subagent_trajectories"]],
                  "ids": [sub["trajectory_id"] for sub in t["subagent_trajectories"]],
                  "schema": t["schema_version"], "total": t["final_metrics"]["total_prompt_tokens"],
                  "plain_main": [st["metrics"]["prompt_tokens"] for st in plain["steps"]]}))
"""


def test_kimi_wrapper_keeps_subagent_calls(tmp_path):
    python = _harbor_python()
    if python is None:
        pytest.skip("harbor not importable")
    proc = subprocess.run([python, "-c", KIMI_SUBAGENT_CHECK, str(SCRIPTS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["main"] == got["plain_main"] == [11, 22]   # input_other + cache read
    assert got["subs"] == [[55]] and got["ids"] == ["kimi-subagent-a1"]
    assert got["schema"] == "ATIF-v1.7" and got["total"] == 88


MINI_REJECTED_CHECK = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import harbor_agents as h
from harbor.agents.installed.mini_swe_agent import convert_mini_swe_agent_to_atif
from harbor.models.agent.context import AgentContext
resp = lambda n: {"id": f"c{n}", "usage": {"prompt_tokens": n, "completion_tokens": 1,
                  "prompt_tokens_details": {"cached_tokens": 0}},
                  "choices": [{"message": {"content": None, "tool_calls": [
                      {"id": f"t{n}", "type": "function", "function": {"name": "bash", "arguments": ""}}]}}]}
traj = {"info": {"model_stats": {"instance_cost": 0.5}}, "messages": [
    {"role": "system", "content": "sys"}, {"role": "user", "content": "task"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "function": {"name": "bash", "arguments": "{}"}}],
     "extra": {"response": resp(100)}},
    {"role": "tool", "content": "out", "tool_call_id": "a"},
    {"role": "user", "content": "Tool call error", "extra": {"interrupt_type": "FormatError", "response": resp(200)}},
    {"role": "exit", "content": "LimitsExceeded", "extra": {"exit_status": "LimitsExceeded"}}]}
steps = lambda t: [s["metrics"]["prompt_tokens"] for s in t["steps"] if s.get("metrics")]
before = convert_mini_swe_agent_to_atif(traj, "s").to_json_dict()
patched, moved = h.surface_rejected_calls(traj)
after = convert_mini_swe_agent_to_atif(patched, "s").to_json_dict()
same, none = h.surface_rejected_calls({"messages": traj["messages"][:4]})
d = Path(sys.argv[2]); (d / "mini-swe-agent.trajectory.json").write_text(json.dumps(traj))
ctx = AgentContext()
h.MiniSweAgentPreinstalled(logs_dir=d, model_name="openai/zai.glm-5").populate_context_post_run(ctx)
written = json.loads((d / "trajectory.json").read_text())
print(json.dumps({"before": steps(before), "after": steps(after), "moved": moved, "none": none,
                  "totals": [before["final_metrics"]["total_prompt_tokens"], after["final_metrics"]["total_prompt_tokens"]],
                  "written": steps(written), "ctx": ctx.n_input_tokens}))
"""


def test_mini_wrapper_counts_rejected_replies(tmp_path):
    """A reply mini-swe-agent rejects (FormatError) was billed; it must be its own ATIF step."""
    python = _harbor_python()
    if python is None:
        pytest.skip("harbor not importable")
    proc = subprocess.run([python, "-c", MINI_REJECTED_CHECK, str(SCRIPTS), str(tmp_path)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["before"] == [100]                      # Harbor alone drops the rejected call
    assert got["after"] == got["written"] == [100, 200] and got["moved"] == 1 and got["none"] == 0
    assert got["totals"] == [300, 300] and got["ctx"] == 300  # each call counted once
