"""Harbor 0.22 agents for open-weight models under `odyssey compile --egress allowlist`.

Load with `harbor run --agent-import-path harbor_agents:<Class>` and this directory on
PYTHONPATH. Each class is Harbor's own adapter with two changes:

1. install() does not download anything. Harbor's kimi-cli and mini-swe-agent adapters
   fetch uv from astral.sh and the tool from PyPI at trial start, which the allowlist
   blocks; `odyssey compile --agent-cli <name>` bakes the pinned tool into the image
   instead. install() only checks that it is there and fails closed if it is not.
2. Secrets never go on a command line. Harbor passes per-exec environment variables as
   `docker compose exec -e KEY=VALUE`, which any process on the host can read with `ps`.
   For every variable whose value equals the value of a variable named in
   ODYSSEY_SECRET_ENV (comma-separated, e.g. "OPENAI_API_KEY"), the `-e KEY=VALUE`
   argument becomes `-e KEY`, and docker takes the value from its own environment,
   which Harbor builds from this process's environment. The agent inside the container
   still sees the value (it needs it to call the model).

Everything else (run command, ATIF conversion, token accounting) is Harbor's code, except
for `DshPreinstalled` (the DeepSeek Harness), for which Harbor 0.22 has no adapter: its
run command, spend cap and ATIF conversion are ours (dsh_session.py, dsh_spend_watch.js).
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tomllib
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.installed.kimi_cli import KimiCli
from harbor.agents.installed.mini_swe_agent import MiniSweAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.docker.docker import DockerEnvironment

_PATH = 'export PATH="$HOME/.local/bin:$PATH"; '


def _secret_values() -> dict[str, str]:
    names = [n.strip() for n in os.environ.get("ODYSSEY_SECRET_ENV", "").split(",") if n.strip()]
    return {os.environ[n]: n for n in names if os.environ.get(n)}


def hide_secret_args(args: list[str]) -> list[str]:
    """Rewrite `-e KEY=<secret>` to `-e KEY` and export KEY=<secret> in this process.

    Docker resolves a bare `-e KEY` from the environment of the `docker compose`
    process, which Harbor starts with this process's environment.
    """
    secrets = _secret_values()
    if not secrets:
        return args
    out = list(args)
    for i in range(len(out) - 1):
        if out[i] != "-e" or "=" not in out[i + 1]:
            continue
        key, value = out[i + 1].split("=", 1)
        if value in secrets:
            os.environ[key] = value
            out[i + 1] = key
    return out


_original_run = DockerEnvironment._run_docker_compose_command


async def _run_docker_compose_command(self, command, *args, **kwargs):
    return await _original_run(self, hide_secret_args(list(command)), *args, **kwargs)


DockerEnvironment._run_docker_compose_command = _run_docker_compose_command


async def _require(agent, environment: BaseEnvironment, check: str, name: str) -> None:
    result = await environment.exec(command=_PATH + check)
    if result.return_code != 0:
        raise RuntimeError(
            f"{name} is not installed in the image and the egress allowlist blocks a download; "
            f"compile the task with `odyssey compile --egress allowlist --agent-cli {name}`")
    agent.logger.debug(f"{name} is already available; skipping install")


class KimiCliPreinstalled(KimiCli):
    """Moonshot's kimi-cli (Harbor adapter `kimi-cli`), preinstalled, thinking on.

    Harbor's config gives the model no capabilities, so kimi-cli turns thinking off and
    its OpenAI-compatible provider sends `reasoning_effort: null`, which Bedrock's Kimi
    K3 rejects with HTTP 400 (measured 2026-11-10). With the `thinking` capability and
    `default_thinking`, kimi-cli sends `reasoning_effort: "high"` (its own choice for
    thinking mode) and round-trips `reasoning_content`. `--ak thinking=false` restores
    Harbor's config.
    """

    def __init__(self, *args, thinking: bool | str = True, **kwargs):
        self._thinking = str(thinking).lower() not in ("false", "0", "no", "off")
        super().__init__(*args, **kwargs)

    def _build_config_json(self, provider: str, model: str) -> str:
        config = json.loads(super()._build_config_json(provider, model))
        if self._thinking:
            config["default_thinking"] = True
            config["models"]["model"]["capabilities"] = ["thinking"]
        return json.dumps(config)

    def _convert_events_to_trajectory(self, events):
        """Harbor's conversion, plus kimi-cli's subagents as ATIF subagent_trajectories.

        kimi-cli runs subagents (its Agent/Task tool) whose model calls reach the wire
        log only wrapped in `SubagentEvent`s, which Harbor's converter skips: their
        tokens were missing from the trajectory (measured on HTT-01: 33 of 119 calls).
        Each subagent's inner events are converted the same way and embedded, so
        `odyssey account` counts them on their own chain.
        """
        main = [e for e in events if e.get("type") != "SubagentEvent"]
        inner: dict[str, list] = {}
        for e in events:
            if e.get("type") != "SubagentEvent":
                continue
            payload = e.get("payload") or {}
            if isinstance(payload.get("event"), dict):
                inner.setdefault(str(payload.get("agent_id") or "unknown"), []).append(payload["event"])
        trajectory = super()._convert_events_to_trajectory(main)
        if trajectory is None or not inner:
            return trajectory
        children = []
        for agent_id, sub_events in inner.items():
            child = super()._convert_events_to_trajectory(sub_events)
            if child is None:
                continue
            child.schema_version = "ATIF-v1.7"
            child.trajectory_id = f"kimi-subagent-{agent_id}"
            children.append(child)
        if not children:
            return trajectory
        trajectory.schema_version = "ATIF-v1.7"
        trajectory.subagent_trajectories = children
        fm = trajectory.final_metrics
        if fm is not None:
            for child in children:
                cm = child.final_metrics
                if cm is None:
                    continue
                fm.total_prompt_tokens = (fm.total_prompt_tokens or 0) + (cm.total_prompt_tokens or 0)
                fm.total_completion_tokens = (fm.total_completion_tokens or 0) + (cm.total_completion_tokens or 0)
                fm.total_cached_tokens = (fm.total_cached_tokens or 0) + (cm.total_cached_tokens or 0)
        return trajectory

    async def install(self, environment: BaseEnvironment) -> None:
        await _require(self, environment, "kimi --version", "kimi-cli")


def surface_rejected_calls(trajectory: dict) -> tuple[dict, int]:
    """Give every billed model reply its own assistant message (mini-swe-agent 2.x trajectory).

    When mini-swe-agent rejects a reply (FormatError, e.g. a tool call with empty
    arguments), it stores the model's response, usage included, on the `user` feedback
    message. Harbor 0.22's converter takes step metrics only from assistant messages, so
    those calls vanished from the ATIF steps that `odyssey account` sums, while
    final_metrics and Harbor's n_input_tokens still counted them (measured on GLM-5,
    HTT-05: 78 of 79 calls, 16,632 prompt tokens missing, under the 1% cross-check).
    This inserts an assistant message carrying that response before the feedback message
    and removes the response from it, so each call is counted once, as its own step.
    """
    messages, out, moved = trajectory.get("messages") or [], [], 0
    for message in messages:
        extra = message.get("extra") or {}
        response = extra.get("response")
        if (message.get("role") not in ("assistant", None) and isinstance(response, dict)
                and response.get("usage")):
            reply = ((response.get("choices") or [{}])[0].get("message") or {})
            out.append({"role": "assistant", "content": reply.get("content") or "",
                        "tool_calls": reply.get("tool_calls"),
                        "extra": {"response": response, "timestamp": extra.get("timestamp"),
                                  "odyssey_note": f"reply rejected by mini-swe-agent ({extra.get('interrupt_type')})"}})
            message = {**message, "extra": {k: v for k, v in extra.items() if k != "response"}}
            moved += 1
        out.append(message)
    return {**trajectory, "messages": out}, moved


class MiniSweAgentPreinstalled(MiniSweAgent):
    """mini-swe-agent (Harbor adapter `mini-swe-agent`), preinstalled; rejected replies counted."""

    async def install(self, environment: BaseEnvironment) -> None:
        await _require(self, environment, "mini-swe-agent --help >/dev/null", "mini-swe-agent")

    def populate_context_post_run(self, context) -> None:
        super().populate_context_post_run(context)  # totals already include rejected replies
        source = self.logs_dir / "mini-swe-agent.trajectory.json"
        if not source.exists():
            return
        from harbor.agents.installed.mini_swe_agent import convert_mini_swe_agent_to_atif
        patched, moved = surface_rejected_calls(json.loads(source.read_text()))
        if not moved:
            return
        atif = convert_mini_swe_agent_to_atif(patched, self.session_id or "odyssey")
        (self.logs_dir / "trajectory.json").write_text(json.dumps(atif.to_json_dict(), indent=2))


# ----------------------------------------------------------------------------- DeepSeek Harness

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dsh_session  # noqa: E402  (same directory; standard library only)

_PRICES = Path(__file__).resolve().parents[2] / "odyssey" / "prices.toml"
_WATCH_JS = Path(__file__).resolve().parent / "dsh_spend_watch.js"
# Context windows declared to dsh (it compacts at min(0.8 W, W - max_tokens - 65,536)).
# Kimi K3: 262,144, as kimi-cli was told; GLM-5 and MiniMax M2.5: their config limits.
_DSH_CONTEXT = {"us.moonshotai.kimi-k3": 262144, "global.moonshotai.kimi-k3": 262144,
                "us.moonshotai.kimi-k3.1": 262144, "zai.glm-5": 202752, "minimax.minimax-m2.5": 196608}


class DshPreinstalled(BaseInstalledAgent):
    """DeepSeek Harness (`dsh --profile headless`, npm @deepseek-ai/dsh) on Bedrock's OpenAI endpoint.

    Baked into the image by `odyssey compile --agent-cli dsh`; install() only checks it.
    Per run: a profile patch (dsh_session.profile_patch) routes the agent to
    `--ak base_url=...` with the model of `-m openai/<bedrock id>` and `reasoning_effort`
    (`--ak reasoning_effort=high`, the default; `none` sends none), telemetry off
    (DSH_TELEMETRY_DISABLED), no approval prompts (DSH_PERMISSION_MODE=danger-full-access:
    the container is the sandbox). The bearer token reaches dsh only as the environment
    variable DSH_BEDROCK_API_KEY, copied from `api_key_env` (default OPENAI_API_KEY, which
    box_run.sh lists in ODYSSEY_SECRET_ENV, so it never appears on docker's command line).
    The task goes in on stdin. DSH_HOME is /logs/agent/dsh-home, so the session logs are
    collected with the agent logs and survive into a resumed step.

    Spend cap (`--ak cap_usd=3`): dsh_spend_watch.js runs beside dsh in the container and
    stops it when the run's logged usage, priced with odyssey/prices.toml, passes the cap;
    the step then ends normally and the verifier grades the tree.

    Resume (`harbor run --resume-trajectory`): a resumed step runs
    `dsh --profile headless --session-id <id>` on the session the previous step left in
    /logs/agent/dsh-home, so the first request of the step carries the whole history.
    """

    SUPPORTS_ATIF = True
    SUPPORTS_RESUME = True
    _DIR = "/logs/agent/dsh"
    _HOME = "/logs/agent/dsh-home"

    def __init__(self, *args, base_url: str | None = None, reasoning_effort: str | None = "high",
                 context_window: int | str | None = None, max_tokens: int | str = 32768,
                 cap_usd: float | str = 3.0, api_key_env: str = "OPENAI_API_KEY", **kwargs):
        super().__init__(*args, **kwargs)
        self._base_url = base_url or "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"
        effort = str(reasoning_effort).lower() if reasoning_effort is not None else None
        self._effort = None if effort in (None, "", "none", "off", "false") else effort
        self._context = int(context_window) if context_window else None
        self._max_tokens = int(max_tokens)
        self._cap = float(cap_usd)
        self._api_key_env = api_key_env

    @staticmethod
    def name() -> str:
        return "dsh"

    def get_version_command(self) -> str | None:
        return f"{dsh_session.DSH_NODE} {dsh_session.DSH_BIN} --version"

    async def install(self, environment: BaseEnvironment) -> None:
        await _require(self, environment, f"{dsh_session.DSH_NODE} {dsh_session.DSH_BIN} --version", "dsh")

    @property
    def _model(self) -> str:
        if not self.model_name:
            raise ValueError("dsh needs -m openai/<bedrock model id>")
        return self.model_name.split("/", 1)[-1]

    def _price(self) -> dict:
        models = tomllib.loads(_PRICES.read_text())["models"]
        if self._model not in models:
            raise ValueError(f"no price for {self._model} in {_PRICES}: the spend cap cannot be enforced")
        p = models[self._model]
        return {k: float(p[k]) for k in ("input", "output", "cache_read", "cache_write")}

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context) -> None:
        token = os.environ.get(self._api_key_env)
        if not token:
            raise ValueError(f"dsh: no bearer token in ${self._api_key_env}")
        patch = dsh_session.profile_patch(
            model=self._model, base_url=self._base_url, api_key_env="DSH_BEDROCK_API_KEY",
            context_window=self._context or _DSH_CONTEXT.get(self._model, 131072),
            max_tokens=self._max_tokens, reasoning_effort=self._effort)
        local = self.logs_dir / "dsh-upload"
        local.mkdir(parents=True, exist_ok=True)
        (local / "bedrock.patch.yml").write_text(patch)
        await environment.exec(command=f"mkdir -p {self._DIR} {self._HOME}", user="root")
        await environment.upload_file(local / "bedrock.patch.yml", f"{self._DIR}/bedrock.patch.yml")
        await environment.upload_file(_WATCH_JS, f"{self._DIR}/dsh_spend_watch.js")
        node, bin_ = dsh_session.DSH_NODE, dsh_session.DSH_BIN
        d, q = self._DIR, shlex.quote
        resume = (
            f'sid=$(cat {d}/session-id 2>/dev/null); '
            f'[ -n "$sid" ] || {{ echo "dsh: no session to resume in {d}/session-id" >&2; exit 3; }}; '
            'set -- --session-id "$sid"; ') if self._resume else "set --; "
        command = (
            "set +e; "
            f"export DSH_HOME={self._HOME} DSH_TELEMETRY_DISABLED=1 DSH_PERMISSION_MODE=danger-full-access; "
            f"n=$(( $(ls {d}/run-*.jsonl 2>/dev/null | wc -l) + 1 )); "
            f'printf "%s" "$ODYSSEY_DSH_TASK" > {d}/task-$n.txt; unset ODYSSEY_DSH_TASK; '
            + resume +
            f"since=$({node} -e 'process.stdout.write(String(Date.now()))'); echo $since > {d}/since-$n.txt; "
            f'{node} {bin_} --profile headless --patch {d}/bedrock.patch.yml --json "$@" - '
            f"< {d}/task-$n.txt > {d}/run-$n.jsonl 2> {d}/run-$n.stderr & pid=$!; "
            f"{node} {d}/dsh_spend_watch.js {self._HOME}/sessions $pid $since {self._cap} "
            f"{q(json.dumps(self._price()))} {d}/capped-$n.json >> {d}/spend-$n.log 2>&1 & wpid=$!; "
            "wait $pid; rc=$?; wait $wpid; "
            f"{node} -e 'const l=require(\"fs\").readFileSync(process.argv[1],\"utf8\").split(\"\\n\")[0];"
            "try{const e=JSON.parse(l);if(e.sessionId)process.stdout.write(e.sessionId)}catch{}' "
            f"{d}/run-$n.jsonl > {d}/session-id.new && [ -s {d}/session-id.new ] && mv {d}/session-id.new {d}/session-id; "
            f'echo "dsh exit $rc" > {d}/exit-$n.txt; tail -c 2000 {d}/run-$n.stderr >&2; '
            f'[ -f {d}/capped-$n.json ] && {{ echo "dsh stopped at the spend cap" >&2; exit 0; }}; exit $rc'
        )
        try:
            await self.exec_as_agent(environment, command=command,
                                     env={"DSH_BEDROCK_API_KEY": token, "ODYSSEY_DSH_TASK": instruction})
        finally:
            # The logs are written by the container's user (root, mode 0600); Harbor reads
            # them on the host as another user.
            await environment.exec(command=f"chmod -R a+rX {self._DIR} {self._HOME} 2>/dev/null; true",
                                   user="root")

    def populate_context_post_run(self, context) -> None:
        home, d = self.logs_dir / "dsh-home", self.logs_dir / "dsh"
        sessions = dsh_session.find_sessions(home)
        if not sessions:
            return
        main_id = (d / "session-id").read_text().strip() if (d / "session-id").exists() else None
        trajectory = dsh_session.to_atif(sessions, main_id, self.model_name, self.version() or dsh_session.DSH_VERSION)
        if trajectory is None:
            return
        try:
            from harbor.models.trajectories import Trajectory
            from harbor.utils.trajectory_utils import format_trajectory_json
            text = format_trajectory_json(Trajectory.model_validate(trajectory).to_json_dict())
        except Exception:
            self.logger.exception("dsh trajectory failed ATIF validation; writing it unvalidated")
            text = json.dumps(trajectory, indent=2)
        (self.logs_dir / "trajectory.json").write_text(text)
        # Harbor's per-step totals: this invocation's calls only (a resumed session's log
        # also holds the earlier steps' calls).
        runs = sorted(d.glob("since-*.txt"), key=lambda p: int(p.stem.split("-")[1]))
        since = int(runs[-1].read_text().strip() or 0) if runs else 0
        usages = dsh_session.calls(sessions, since)
        context.n_input_tokens = sum(u.get("inputTokens", 0) + u.get("cacheReadTokens", 0)
                                     + u.get("cacheWriteTokens", 0) for u in usages)
        context.n_cache_tokens = sum(u.get("cacheReadTokens", 0) for u in usages)
        context.n_output_tokens = sum(u.get("outputTokens", 0) for u in usages)
        try:
            price = self._price()
            context.cost_usd = round(sum(dsh_session.usage_cost(u, price) for u in usages), 6)
        except ValueError:
            pass
