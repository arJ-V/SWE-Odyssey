"""A Harbor agent that runs scripts/egress/probe.sh inside the task container.

It runs during the agent phase of every step, under exactly the network policy
a real agent gets, and makes no model call. Use with Harbor's import-path form:

    PYTHONPATH=<repo>/scripts/egress harbor run -p <task> -a probe_agent:EgressProbeAgent

Each step's output lands in the trial's agent logs as egress_probe.txt
(steps/<name>/agent/egress_probe.txt for multi-step tasks).
"""

from __future__ import annotations

from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

PROBE = Path(__file__).with_name("probe.sh")
REMOTE = "/tmp/odyssey_egress_probe.sh"


class EgressProbeAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "egress-probe"

    def version(self) -> str:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await environment.upload_file(PROBE, REMOTE)
        result = await environment.exec(f"bash {REMOTE}; rm -f {REMOTE}", timeout_sec=900)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        text = (result.stdout or "") + (result.stderr or "")
        (self.logs_dir / "egress_probe.txt").write_text(text)
