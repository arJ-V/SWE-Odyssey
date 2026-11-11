"""Re-convert a kimi-cli run's wire log into trajectory.json with its subagents.

    <harbor's python> scripts/open_models/regen_kimi_trajectory.py <model> <agent-dir> [<agent-dir> ...]

For runs made before KimiCliPreinstalled embedded kimi-cli's subagents: Harbor 0.22's
converter drops `SubagentEvent`s, so those trajectories miss the subagents' calls. This
keeps Harbor's file as trajectory.harbor-0.22.json (once) and rewrites trajectory.json
from <agent-dir>/kimi-cli.txt with the wrapper's converter. <model> is the run's
`-m` value, e.g. openai/us.moonshotai.kimi-k3. Needs no network and no credential.
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harbor_agents  # noqa: E402
from harbor.models.agent.context import AgentContext  # noqa: E402


def main(argv: list[str]) -> int:
    model, dirs = argv[0], [Path(d) for d in argv[1:]]
    for agent_dir in dirs:
        original = agent_dir / "trajectory.json"
        kept = agent_dir / "trajectory.harbor-0.22.json"
        if original.exists() and not kept.exists():
            shutil.copyfile(original, kept)
        agent = harbor_agents.KimiCliPreinstalled(logs_dir=agent_dir, model_name=model)
        context = AgentContext()
        agent.populate_context_post_run(context)
        data = json.loads(original.read_text())
        subs = data.get("subagent_trajectories") or []
        print(f"{agent_dir}: {len(data['steps'])} main steps, {len(subs)} subagents with "
              f"{sum(len(s['steps']) for s in subs)} steps; prompt {context.n_input_tokens}, "
              f"cached {context.n_cache_tokens}, completion {context.n_output_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
