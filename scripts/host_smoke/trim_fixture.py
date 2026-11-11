"""Trim a (real) Harbor-shaped trial dir into a small, content-free regression fixture.

Keeps every ATIF field accounting reads (timestamps, sources, models, metrics, tool-call
ids/names, sidechain flags, copied-context flags); replaces message text by a short hash
and drops tool arguments and outputs, so no repo content, paths or secrets survive."""
import hashlib, json, sys
from pathlib import Path

src, dst, task_name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

def h(v):
    return "m:" + hashlib.sha1(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()[:10] if v else ""

def trim_step(s):
    out = {k: s[k] for k in ("step_id", "timestamp", "source", "model_name", "llm_call_count",
                             "is_copied_context") if k in s}
    out["message"] = h(s.get("message"))
    if s.get("metrics"):
        m = dict(s["metrics"])
        m.pop("prompt_token_ids", None); m.pop("completion_token_ids", None); m.pop("logprobs", None)
        out["metrics"] = m
    if s.get("tool_calls"):
        out["tool_calls"] = [{"tool_call_id": t.get("tool_call_id"), "function_name": t.get("function_name"),
                              "arguments": {}} for t in s["tool_calls"]]
    if s.get("observation"):
        out["observation"] = {"results": [{"source_call_id": r.get("source_call_id"), "content": ""}
                                          for r in s["observation"].get("results") or []]}
    ex = s.get("extra") or {}
    out["extra"] = {k: ex[k] for k in ("is_sidechain", "agent_id") if k in ex}
    return out

res = json.loads((src / "result.json").read_text())
res["config"]["task"] = {"path": task_name}
for sr in res["step_results"]:
    ar = sr["agent_result"]
    sr["agent_result"] = {k: ar.get(k) for k in ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")}
    t = json.loads((src / "steps" / sr["step_name"] / "agent" / "trajectory.json").read_text())
    t["agent"] = {k: t["agent"].get(k) for k in ("name", "version", "model_name")}
    t["steps"] = [trim_step(s) for s in t["steps"]]
    d = dst / "steps" / sr["step_name"] / "agent"; d.mkdir(parents=True, exist_ok=True)
    (d / "trajectory.json").write_text(json.dumps(t, indent=1) + "\n")
(dst / "result.json").write_text(json.dumps(res, indent=1) + "\n")
