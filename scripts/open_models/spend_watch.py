"""Stop a kimi-cli trial when its spend passes a cap.

    spend_watch.py <job-dir> <prices.toml> <price-key> <cap-usd> <job-name>

kimi-cli has no spend limit of its own. Every 20 s this reads the live wire log Harbor
mounts at <job-dir>/<trial>/agent/kimi-cli.txt, prices the token usage of each step the
way Harbor's converter does (the last StatusUpdate of a step), including the steps of
kimi-cli's subagents (wrapped in SubagentEvent), and once the total passes the cap stops
the agent: it kills the kimi process inside the trial's main container, so the agent
phase ends and the verifier still grades the tree, as on a timeout. It prints one line
per minute and a final line; it never sees or prints a credential.

History: the first version sent SIGINT to `harbor run` and counted only the main agent.
On HTT-01 (2026-11-10) Harbor kept the trial running after SIGINT and a subagent's 33
calls were not counted, so a $2.50 cap ended as $4.75. The container kill below worked
live on 2026-11-11 (HTT-07, cap $0.40, stopped at $0.428 by the wire log); calls in flight
at the kill are billed by Bedrock but never reach the wire log (2 calls, about $0.04).
"""

import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path


def step_usages(text: str) -> list[dict]:
    """Token usage of every model call in a kimi-cli wire log: main agent and subagents."""
    streams: dict[str, list[dict]] = {}
    for line in text.splitlines():
        try:
            msg = json.loads(line, strict=False)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict) or msg.get("method") != "event":
            continue
        event = msg.get("params") or {}
        agent = "main"
        if event.get("type") == "SubagentEvent":
            payload = event.get("payload") or {}
            agent = "sub:" + str(payload.get("agent_id"))
            event = payload.get("event") or {}
        streams.setdefault(agent, []).append(event)
    usages = []
    for events in streams.values():
        current = None
        for event in events:
            kind, payload = event.get("type"), event.get("payload") or {}
            if kind == "StepBegin":
                if current:
                    usages.append(current)
                current = {}
            elif kind == "StatusUpdate" and current is not None and payload.get("token_usage"):
                current = payload["token_usage"]
        if current:
            usages.append(current)
    return usages


def stop_agent(job_dir: Path) -> None:
    """Kill kimi inside each running trial's main container (Harbor names it <trial>...main-1)."""
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    for trial in (p.name.lower() for p in job_dir.iterdir() if p.is_dir()):
        for name in names:
            if name.startswith(trial) and name.endswith("main-1"):
                subprocess.run(["docker", "exec", name, "pkill", "-TERM", "-f", "kimi --config-file"], check=False)
                print(f"spend_watch: stopped kimi in {name}", flush=True)


def cost(usages: list[dict], price: dict) -> float:
    usd = 0.0
    for u in usages:
        usd += ((u.get("input_other") or 0) * price["input"] + (u.get("input_cache_read") or 0) * price["cache_read"]
                + (u.get("input_cache_creation") or 0) * price["cache_write"] + (u.get("output") or 0) * price["output"])
    return usd / 1e6


def main() -> int:
    job_dir, prices, key, cap, job = Path(sys.argv[1]), sys.argv[2], sys.argv[3], float(sys.argv[4]), sys.argv[5]
    price = tomllib.load(open(prices, "rb"))["models"][key]
    last_print = 0.0
    while True:
        time.sleep(20)
        logs = list(job_dir.glob("*/agent/kimi-cli.txt")) + list(job_dir.glob("*/steps/*/agent/kimi-cli.txt"))
        usages = [u for p in logs for u in step_usages(p.read_text(errors="replace"))]
        usd = cost(usages, price)
        if time.time() - last_print > 60:
            print(f"spend_watch {job}: {len(usages)} calls, ${usd:.3f} of ${cap:.2f}", flush=True)
            last_print = time.time()
        if usd > cap:
            print(f"spend_watch {job}: cap ${cap:.2f} passed (${usd:.3f}); stopping the agent", flush=True)
            stop_agent(job_dir)
            return 1


if __name__ == "__main__":
    sys.exit(main())
