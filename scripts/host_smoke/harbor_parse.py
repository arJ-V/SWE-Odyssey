"""Run Harbor's own ClaudeCode/Codex post-run parser on emulated step dirs and write a
Harbor-shaped result.json (multi-step) so `odyssey account` can be run on it."""
import json, sys, logging
from pathlib import Path
from harbor.models.agent.context import AgentContext

root = Path(sys.argv[1]); agent = sys.argv[2]; model = sys.argv[3]; resumed = sys.argv[4] == "resume"
task_dir = sys.argv[5] if len(sys.argv) > 5 else None
if agent == "claude-code":
    from harbor.agents.installed.claude_code import ClaudeCode as Cls
else:
    from harbor.agents.installed.codex import Codex as Cls
step_results = []
for step_dir in sorted((root / "steps").iterdir()):
    agent_dir = step_dir / "agent"
    a = Cls(logs_dir=agent_dir, model_name=model, logger=logging.getLogger("x"))
    ctx = AgentContext()
    a.populate_context_post_run(ctx)
    ex = json.loads((step_dir / "agent_execution.json").read_text())
    traj = json.loads((agent_dir / "trajectory.json").read_text())
    print(step_dir.name, "n_input", ctx.n_input_tokens, "n_cache", ctx.n_cache_tokens, "n_out", ctx.n_output_tokens,
          "cost", ctx.cost_usd, "atif_steps", len(traj["steps"]))
    step_results.append({"step_name": step_dir.name, "agent_result": ctx.model_dump(mode="json"),
                         "verifier_result": None, "exception_info": None, "agent_execution": ex})
result = {"trial_name": f"host-{agent}-{root.name}", "task_name": "odyssey/host-experiment",
          "config": {"agent": {"name": agent, "model_name": model, "resume_trajectory": resumed},
                     "task": {"path": task_dir} if task_dir else {}},
          "agent_info": {"name": agent, "model_info": {"name": model.split("/")[-1]}},
          "step_results": step_results}
(root / "result.json").write_text(json.dumps(result, indent=2))
