"""DeepSeek Harness (dsh) session logs -> ATIF, and the dsh profile patch.

Standard library only, so it is unit-tested without Harbor (tests/test_dsh.py) and
imported by the Harbor wrapper `harbor_agents.DshPreinstalled`.

What dsh 0.2.1-rc.4 writes (read in its source and measured, 2026-11-11): every session
is one append-only JSONL log, `$DSH_HOME/sessions/<cwd slug>/<session id>/session.v4.jsonl`
(plain lines because our profile patch sets `compression: none`; zstd frames by default).
Line 1 is the header (`type: session`, `delegationDepth`, `parentSession` for a
subagent, `isSeeded` for a fork). Every model call that returns ends in one event:
`assistant/message` (with `data.usage`, the adapter's usage block) or, when the stream
fails or is aborted, `assistant/attempt` (its usage, if the provider sent one, is a
`usage` chunk inside `data.stream`). A compaction summary call writes `compaction/summary`
with `data.usage`. Subagents write their own session logs next to the parent's.
`data.usage` counts are disjoint: `inputTokens` is uncached input, cache reads and
writes are separate (`cacheReadTokens`, `cacheWriteTokens`), `reasoningTokens` is part
of `outputTokens` when present.

ATIF mapping: one agent step per billed call with `prompt_tokens = input + cache read +
cache write`, `cached_tokens = cache read`, `completion_tokens = output`; tool results
become the observation of the call that requested them; user, system and runtime-context
messages become user/system steps. Subagent sessions become `subagent_trajectories`. A
resumed dsh session appends to the same log, so the trajectory of step k repeats steps
1..k-1 exactly (same timestamps, tokens and text): `odyssey account` attributes each call
to the first segment that holds it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DSH_VERSION = "0.2.1-rc.4"
DSH_BIN = "/opt/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"
DSH_NODE = "/opt/agent-cli/bin/node"
PROVIDER_ROUTE = "bedrock-openai"
CALL_EVENTS = ("assistant/message", "assistant/attempt", "compaction/summary")


# ----------------------------------------------------------------------------- profile patch

def _yaml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid YAML double-quoted scalar


def profile_patch(*, model: str, base_url: str, api_key_env: str, context_window: int,
                  max_tokens: int, reasoning_effort: str | None) -> str:
    """The `--patch` overlay over dsh's shipped headless profile.

    - llm-pi-ai: one hand-declared OpenAI Chat Completions route to Bedrock. The key is
      a reference (`apiKeyEnv`), never a value. Compat: no `store`, no `developer` role,
      `max_tokens`, usage in the stream, OpenAI-style `reasoning_effort`.
    - agent-default-model: that route and model, with the reasoning effort.
    - session-title-llm: off (it would make one extra model call per session).
    - session-persistence-jsonl: uncompressed JSONL, so the host and the spend watchdog
      read the log directly.
    - tool-web: off. Its search needs a DeepSeek API key and its fetch would only meet
      the egress allowlist; the other harnesses' fetch tools are refused the same way.
    """
    efforts = f"\n            reasoningEfforts:\n              {reasoning_effort}: {reasoning_effort}" \
        if reasoning_effort else "\n            reasoningEfforts: false"
    selection = f"\n    reasoningEffort: {reasoning_effort}" if reasoning_effort else ""
    return f"""# Written by scripts/open_models/harbor_agents.py (DshPreinstalled).
- id: llm-pi-ai
  config:
    providers:
      {PROVIDER_ROUTE}:
        displayName: Amazon Bedrock (OpenAI-compatible)
        api: openai-completions
        baseURL: {_yaml_str(base_url)}
        apiKeyEnv: {api_key_env}
        compat:
          supportsStore: false
          supportsDeveloperRole: false
          supportsReasoningEffort: true
          supportsUsageInStreaming: true
          maxTokensField: max_tokens
          thinkingFormat: openai
        models:
          - id: {_yaml_str(model)}
            contextWindow: {int(context_window)}
            maxTokens: {int(max_tokens)}{efforts}
- id: agent-default-model
  config:
    provider: {PROVIDER_ROUTE}
    model: {_yaml_str(model)}{selection}
- id: session-title-llm
  disabled: true
- id: tool-web
  disabled: true
- id: session-persistence-jsonl
  config:
    root: !!js dshHomePath('sessions')
    compression: none
"""


# ----------------------------------------------------------------------------- reading logs

def read_log(path: Path) -> list[dict]:
    """All complete events of one session log; a torn last line (live write) is skipped."""
    events = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def find_sessions(home: Path) -> dict[str, list[dict]]:
    """Session id -> events, for every session log under $DSH_HOME/sessions."""
    out: dict[str, list[dict]] = {}
    for path in sorted(Path(home).glob("sessions/**/session.v*.jsonl")):
        events = read_log(path)
        if events and events[0].get("type") == "session":
            out[str(events[0].get("id") or path.parent.name)] = events
    return out


def call_usage(event: dict) -> dict | None:
    """The usage block of a model-call event, or None (an attempt that never got usage)."""
    data = event.get("data") or {}
    if event.get("type") in ("assistant/message", "compaction/summary"):
        usage = data.get("usage")
        return usage if isinstance(usage, dict) else None
    if event.get("type") == "assistant/attempt":
        usage = None
        for record in data.get("stream") or []:
            chunk = record.get("chunk") if isinstance(record, dict) else None
            if isinstance(chunk, dict) and chunk.get("type") == "usage" and isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
        return usage
    return None


def _n(usage: dict, key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def usage_cost(usage: dict, price: dict) -> float:
    """Dollars of one call at a prices.toml entry (per million tokens)."""
    return (_n(usage, "inputTokens") * price["input"] + _n(usage, "cacheReadTokens") * price["cache_read"]
            + _n(usage, "cacheWriteTokens") * price["cache_write"] + _n(usage, "outputTokens") * price["output"]) / 1e6


def calls(sessions: dict[str, list[dict]], since_ms: int = 0) -> list[dict]:
    """Usage of every billed call in all sessions at or after since_ms (message ids deduplicated)."""
    seen: set[str] = set()
    out = []
    for events in sessions.values():
        for event in events:
            if event.get("type") not in CALL_EVENTS or (event.get("time") or 0) < since_ms:
                continue
            msg_id = ((event.get("data") or {}).get("message") or {}).get("id")
            if msg_id:
                if msg_id in seen:
                    continue
                seen.add(msg_id)
            usage = call_usage(event)
            if usage:
                out.append(usage)
    return out


# ----------------------------------------------------------------------------- ATIF

def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _text(content: Any, kinds: tuple[str, ...] = ("text",)) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") in kinds:
            value = block.get("text") or block.get("reasoning") or block.get("thinking") or ""
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts)


def _arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except json.JSONDecodeError:
        return {"raw": raw}
    return value if isinstance(value, dict) else {"value": value}


def _metrics(usage: dict) -> dict:
    cache_read, cache_write = _n(usage, "cacheReadTokens"), _n(usage, "cacheWriteTokens")
    metrics: dict[str, Any] = {
        "prompt_tokens": _n(usage, "inputTokens") + cache_read + cache_write,
        "completion_tokens": _n(usage, "outputTokens"),
    }
    if cache_read:
        metrics["cached_tokens"] = cache_read
    extra = {"dsh_usage": usage}
    if "cacheWriteTokens" in usage:
        extra["cache_write_input_tokens"] = cache_write
    if _n(usage, "reasoningTokens"):
        extra["reasoning_tokens"] = _n(usage, "reasoningTokens")
    metrics["extra"] = extra
    return metrics


def session_steps(events: list[dict], model_name: str | None, seen: set[str]) -> list[dict]:
    """ATIF steps (without step_id) of one session log, in log order."""
    steps: list[dict] = []
    by_call: dict[str, dict] = {}
    for event in events:
        kind, data = event.get("type"), event.get("data") or {}
        stamp = _iso(event.get("time"))
        if kind in ("user/message", "system/message"):
            message = data.get("message") if kind == "system/message" else data
            source_kind = ((message or {}).get("source") or {}).get("kind")
            text = _text((message or {}).get("content"))
            if text:
                steps.append({"timestamp": stamp, "source": "user" if source_kind == "user" else "system",
                              "message": text, "extra": {"dsh_event": kind, "dsh_source": source_kind}})
        elif kind in CALL_EVENTS:
            message = data.get("message") or {}
            msg_id = message.get("id")
            if msg_id:
                if msg_id in seen:  # a fork's copy of its parent's history
                    continue
                seen.add(msg_id)
            usage = call_usage(event)
            content = message.get("content") or []
            text = data.get("summary") if kind == "compaction/summary" else _text(content)
            step: dict[str, Any] = {
                "timestamp": stamp, "source": "agent",
                "model_name": model_name or ((message.get("source") or {}).get("model")),
                "message": text or ("(failed attempt)" if kind == "assistant/attempt" else "(tool use)"),
                "extra": {"dsh_event": kind, "dsh_seq": event.get("seq")},
            }
            reasoning = _text(content, ("reasoning", "thinking"))
            if reasoning:
                step["reasoning_content"] = reasoning
            tool_calls = [{"tool_call_id": b.get("id"), "function_name": b.get("name") or "?",
                           "arguments": _arguments(b.get("arguments"))}
                          for b in content if isinstance(b, dict) and b.get("type") == "tool-call" and b.get("id")]
            if tool_calls:
                step["tool_calls"] = tool_calls
                for call in tool_calls:
                    by_call[call["tool_call_id"]] = step
            if usage:
                step["metrics"] = _metrics(usage)
            else:  # no usage reached dsh (a failed attempt): nothing to count, kept for the record
                step["extra"]["no_usage"] = True
            if data.get("interrupted"):
                step["extra"]["interrupted"] = True
            steps.append(step)
        elif kind == "tool/result":
            message = data.get("message") or {}
            call_id = message.get("toolCallId") or (message.get("source") or {}).get("callId")
            owner = by_call.get(call_id)
            if owner is None:
                continue
            result = {"source_call_id": call_id, "content": _text(message.get("content"))}
            owner.setdefault("observation", {"results": []})["results"].append(result)
    return steps


def to_atif(sessions: dict[str, list[dict]], main_id: str | None, model_name: str | None,
            version: str = DSH_VERSION) -> dict | None:
    """ATIF-v1.7 trajectory of the main session with every other session as a subagent."""
    if not sessions:
        return None
    if main_id not in sessions:
        roots = [sid for sid, ev in sessions.items() if not ev[0].get("delegationDepth")]
        if not roots:
            return None
        main_id = max(roots, key=lambda sid: sessions[sid][0].get("createdAt") or 0)
    seen: set[str] = set()

    def build(sid: str, events: list[dict]) -> dict:
        steps = session_steps(events, model_name, seen)
        for i, step in enumerate(steps, start=1):
            step["step_id"] = i
        totals = [s["metrics"] for s in steps if "metrics" in s]
        return {
            "schema_version": "ATIF-v1.7",
            "session_id": sid,
            "agent": {"name": "dsh", "version": version, "model_name": model_name},
            "steps": steps,
            "final_metrics": {
                "total_prompt_tokens": sum(m["prompt_tokens"] for m in totals),
                "total_completion_tokens": sum(m["completion_tokens"] for m in totals),
                "total_cached_tokens": sum(m.get("cached_tokens", 0) for m in totals),
                "total_steps": len(steps),
            },
        }

    main = build(main_id, sessions[main_id])
    children = []
    for sid in sorted(s for s in sessions if s != main_id):
        child = build(sid, sessions[sid])
        if child["steps"]:
            child["trajectory_id"] = f"dsh-subagent-{sid}"
            children.append(child)
    if children:
        main["subagent_trajectories"] = children
        fm = main["final_metrics"]
        for child in children:
            for key in ("total_prompt_tokens", "total_completion_tokens", "total_cached_tokens"):
                fm[key] += child["final_metrics"][key]
    return main
