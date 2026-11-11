"""Per-segment token and cost accounting for SWE-Odyssey runs on Harbor 0.22.

`odyssey account <jobs-dir-or-trial-dir>... --out segments.csv [--prices prices.toml]`

One Harbor trial of a compiled task is one *session*; each of its steps is one *segment*
(SPEC section 7). This module reads, per trial:

- ``result.json`` (Harbor ``TrialResult``): per-step rewards, timings, exceptions and the
  harness-reported ``agent_result``; for single-step (cold) trials the same fields sit at
  the top level and the trial is treated as one segment at position 1.
- the odyssey manifest (``odyssey.json`` in the task dir named by ``config.task.path``,
  or the copy embedded in ``task.toml`` ``[metadata]``), for ticket/position/cluster/role.
- ``steps/<name>/agent/trajectory.json`` (ATIF), the only source of per-LLM-call tokens.

Why not trust Harbor's per-step totals: under ``--resume-trajectory`` Claude Code appends
to one session file and Harbor's parser reads all of it, so step k's ``n_input_tokens`` is
cumulative over steps 1..k; Claude Code's ``total_cost_usd`` (Harbor's ``cost_usd``) is
session-cumulative too (measured). We therefore rebuild every number
from ATIF calls and attribute each call to exactly one segment. ``reported_cost_usd`` is
made per-segment by `_resumed_reported_cost` and its ``reported_cost_scope`` column says how.

Segment attribution of an ATIF step (see `attribute_steps`): a step belongs to the first
segment whose trajectory contains it. Concretely,
  (a) steps flagged ``is_copied_context`` are skipped;
  (b) a step whose stable key (timestamp + token counts + message hash) was already seen in
      an earlier segment's trajectory of the same trial is a repeat and is skipped;
  (c) every other step is attributed, whatever its timestamp. The step's ``agent_execution``
      window (+/- ``--clock-slack-sec``) only produces warnings: Harbor stamps the window
      with the host clock and ATIF with the container's, so a window filter would drop real
      calls whenever the clocks disagree by more than the slack. Nothing is ever dropped,
      so the segments of a resumed session partition its calls (checked on the last row).

Incremental tokens: completion tokens plus context that
enters for the first time in the segment. Two estimators are emitted:

it_delta (primary; also written as ``incremental_tokens``)
    For consecutive LLM calls i-1, i in the same conversation chain:
        new_in_i = max(0, prompt_i - prompt_{i-1} - completion_{i-1})
    i.e. the growth of the prompt that is not explained by the previous reply being
    re-sent. A call with no predecessor (first call of a fresh session or of a subagent)
    counts its whole prompt as new. A drop in prompt length (compaction, context editing)
    clamps to zero, so a reset is never counted as negative or as new context.
    it_delta = sum over attributed calls of (new_in_i + completion_i).
    Known failure cases:
      * Fresh sessions pay the harness system prompt + tool schemas (often 10-25k tokens)
        on their first call; resumed segments do not. ``it_startup_tokens`` reports the
        part of it_delta that came from predecessor-less calls (main chain and subagents);
        ``it_startup_main_tokens`` only the main chain's, which is harness overhead, and
        ``it_delta_net`` = it_delta - it_startup_main_tokens is the default analysis metric.
        (A subagent's first prompt is real new context and stays in it_delta_net.)
      * If the previous call's reasoning tokens are not re-sent (some harnesses strip
        thinking), subtracting the full completion under-counts new input by up to that
        reasoning; clamping hides the error when it exceeds the real growth.
      * At a compaction the summary re-enters the prompt but is counted only if the
        summarising call itself appears in the trajectory (as completion tokens).
      * Harbor 0.22 does not copy Claude Code's ``agentId`` into ATIF, so subagent
        (sidechain) steps carry no chain id. `split_anonymous_sidechains` starts a new
        chain at each sidechain user step (the Task prompt) and matches each later call to
        the open chain whose last prompt its cache read covers. Interleaved subagents
        whose caches do not identify them can still be mis-assigned.
it_content (cross-check)
    it_content = completion tokens + approx_tokens(input content of the segment's ATIF
    steps): user/system messages and tool observation text. Uses `approx_tokens`, a fixed
    provider-independent approximation (see its docstring), so its level differs from the
    provider tokenizer by a roughly constant factor; ratios within one pairing are what it
    is for. Known failure cases: it does not see the system prompt, tool schemas, injected
    memory/CLAUDE.md that the harness never logs as a step, or truncated observations;
    images count a flat 1,500 tokens.

SDC (``sdc_usd``): every attributed call is priced with the frozen sheet for its own model
(subagents may use a cheaper model): fresh input (prompt - cache read - cache write) at
``input``, cache writes at ``cache_write`` (Anthropic 1h writes at ``cache_write_1h`` when
the split is recorded), cache reads at ``cache_read``, completion at ``output``; the
long-context table applies to the whole call when its prompt exceeds the threshold. An
unknown model leaves ``sdc_usd`` empty and adds a warning (never a silent zero).
``sdc_neutral_usd`` is a cache-neutral variant: new context (from
it_delta) at the cache-write rate, the rest of the prompt at the cache-read rate, output at
the output rate; it removes cache-TTL luck from the dollar figure.

Egress (``egress_*``, tasks compiled with ``--egress allowlist``, SPEC section 11): Harbor
collects the egress sidecar's connection log after every step's agent phase into
``steps/<name>/artifacts/odyssey-egress.jsonl`` (``artifacts/`` for a one-step trial). The
log is cumulative, so a segment's connections are the lines its snapshot adds to the
previous step's: connections logged after step k-1's collection, through step k's setup
and agent phase. ``egress_denied`` counts denied connections (gost ``bypass: <host:port>``),
``egress_allowed`` admitted ones (``proxy: <host:port>``), each connection once (by gost's
``sid``); ``egress_denied_targets`` lists the denied targets with counts. A denied request
to a code host is an agent trying to fetch outside the container.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("odyssey.account")

DEFAULT_PRICES = Path(__file__).with_name("prices.toml")

# SPEC section 7 columns first, then our additions (documented in the module docstring).
COLUMNS = [
    "job", "trial", "sequence", "arm", "ordering_id", "position", "ticket", "cluster",
    "role", "agent", "model", "resumed", "reward", "n_llm_calls", "n_tool_calls",
    "prompt_tokens", "cached_tokens", "cache_write_tokens", "completion_tokens",
    "incremental_tokens", "sdc_usd", "reported_cost_usd", "agent_seconds", "exception",
    # additions
    "step_name", "ordering_length", "it_delta", "it_content", "it_startup_tokens",
    "sdc_neutral_usd", "compactions", "reported_cost_scope", "warnings",
    "it_startup_main_tokens", "it_delta_net", "grader_error",
    # --egress allowlist tasks (SPEC section 11); empty for open-egress runs
    "egress_allowed", "egress_denied", "egress_denied_targets",
]

# exception value of a planned segment that never ran (the trial stopped before it).
NOT_RUN = "not_run"

# Relative tolerance when matching Harbor's per-step token totals to ATIF sums.
HARNESS_MATCH_TOLERANCE = 0.01

# A prompt that shrinks by more than both of these between consecutive calls is counted
# as a compaction event (for the `compactions` column only; IT clamps any drop to zero).
COMPACTION_MIN_DROP_TOKENS = 2000
COMPACTION_MIN_DROP_FRACTION = 0.10

IMAGE_TOKENS = 1500


# ----------------------------------------------------------------------------- prices

@dataclass(frozen=True)
class Rates:
    """USD per million tokens for one price band of one model."""

    input: float
    cache_write: float
    cache_write_1h: float
    cache_read: float
    output: float


@dataclass(frozen=True)
class ModelPrice:
    standard: Rates
    long_context: Rates | None = None
    long_context_threshold: int | None = None

    def rates_for(self, prompt_tokens: int) -> Rates:
        """Long-context prices apply to the whole request above the threshold."""
        if (
            self.long_context is not None
            and self.long_context_threshold is not None
            and prompt_tokens > self.long_context_threshold
        ):
            return self.long_context
        return self.standard


def _rates(table: dict[str, Any]) -> Rates:
    write = float(table["cache_write"])
    return Rates(
        input=float(table["input"]),
        cache_write=write,
        cache_write_1h=float(table.get("cache_write_1h", write)),
        cache_read=float(table["cache_read"]),
        output=float(table["output"]),
    )


class PriceSheet:
    """Frozen price sheet loaded from prices.toml, with forgiving model-name matching."""

    def __init__(self, models: dict[str, ModelPrice]):
        self.models = models

    @classmethod
    def load(cls, path: Path) -> "PriceSheet":
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        models: dict[str, ModelPrice] = {}
        for name, table in data.get("models", {}).items():
            long_table = table.get("long_context")
            models[name.lower()] = ModelPrice(
                standard=_rates(table),
                long_context=_rates(long_table) if long_table else None,
                long_context_threshold=(
                    int(long_table["threshold_tokens"]) if long_table else None
                ),
            )
        return cls(models)

    def lookup(self, model: str | None) -> ModelPrice | None:
        """Match `anthropic/claude-opus-5-5`, `claude-opus-5-5-20261001`, etc."""
        if not model:
            return None
        for candidate in _model_name_candidates(model):
            if candidate in self.models:
                return self.models[candidate]
        return None


_MODEL_SUFFIXES = (r"@.*$", r"-\d{8}$", r"-v\d+(?::\d+)?$", r"\[.*\]$")


def _model_name_candidates(model: str) -> list[str]:
    """The name, then the name with version/date/region suffixes stripped one after another.

    Suffixes stack (Bedrock: `claude-opus-5-5-20261001-v1:0`), so stripping repeats
    until nothing more comes off.
    """
    name = model.strip().lower()
    name = name.rsplit("/", 1)[-1]                     # provider/model
    name = re.sub(r"^(?:[a-z]{2}\.)?anthropic\.", "", name)  # bedrock-style prefixes
    candidates = [name]
    changed = True
    while changed:
        changed = False
        for pattern in _MODEL_SUFFIXES:
            stripped = re.sub(pattern, "", candidates[-1])
            if stripped != candidates[-1]:
                candidates.append(stripped)
                changed = True
    return candidates


# ----------------------------------------------------------------------------- tokens

_TOKEN_PIECE = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]")


def approx_tokens(text: str) -> int:
    """Deterministic, provider-independent token estimate.

    Rule: split into letter runs, digit runs and single non-space symbols; a letter run
    costs ceil(len/4) tokens, a digit run ceil(len/3), each symbol one token; whitespace
    is free. On English prose and source code this lands within roughly +/-25% of modern
    BPE tokenizers. It is used only for it_content, whose purpose is within-pairing ratios,
    so a stable bias matters less than determinism and zero dependencies.
    """
    total = 0
    for piece in _TOKEN_PIECE.findall(text):
        if piece[0].isalpha():
            total += math.ceil(len(piece) / 4)
        elif piece[0].isdigit():
            total += math.ceil(len(piece) / 3)
        else:
            total += 1
    return total


def _content_tokens(content: Any) -> int:
    """Approximate tokens of an ATIF message/observation (str or list of ContentPart)."""
    if content is None:
        return 0
    if isinstance(content, str):
        return approx_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image":
                total += IMAGE_TOKENS
            elif isinstance(part, dict):
                total += approx_tokens(str(part.get("text") or ""))
            else:
                total += approx_tokens(str(part))
        return total
    return approx_tokens(json.dumps(content, sort_keys=True, default=str))


# ----------------------------------------------------------------------------- ATIF steps

@dataclass
class AtifStep:
    """One ATIF step reduced to what accounting needs."""

    key: str
    chain: str
    timestamp: datetime | None
    copied: bool
    is_llm_call: bool
    n_llm_calls: int
    model: str | None
    has_usage: bool = False
    prompt: int = 0
    cached: int = 0
    write_5m: int = 0
    write_1h: int = 0
    write_recorded: bool = False
    completion: int = 0
    n_tool_calls: int = 0
    input_content_tokens: int = 0
    source: str | None = None

    @property
    def cache_write(self) -> int:
        return self.write_5m + self.write_1h


def parse_time(value: Any) -> datetime | None:
    """Parse ISO-8601 (with `Z` or offset); naive times are taken as UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _cache_writes(extra: dict[str, Any]) -> tuple[int, int, bool]:
    """(5m-or-default writes, 1h writes, recorded?) from Metrics.extra.

    Claude Code keeps Anthropic usage keys: `cache_creation_input_tokens` and optionally
    `cache_creation: {ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`. Codex records
    `cache_write_input_tokens` when the API reports it.
    """
    split = extra.get("cache_creation")
    if isinstance(split, dict) and (
        "ephemeral_5m_input_tokens" in split or "ephemeral_1h_input_tokens" in split
    ):
        return (
            _int(split.get("ephemeral_5m_input_tokens")),
            _int(split.get("ephemeral_1h_input_tokens")),
            True,
        )
    for key in ("cache_creation_input_tokens", "cache_write_input_tokens"):
        if key in extra and extra[key] is not None:
            return _int(extra[key]), 0, True
    return 0, 0, False


def _completion_tokens(metrics: dict[str, Any]) -> int:
    """Completion tokens including reasoning.

    OpenAI usage counts reasoning inside output_tokens, so it is not added again. The one
    exception we can detect: when the recorded total equals prompt + completion + reasoning,
    the producer reported reasoning separately and we add it.
    """
    completion = _int(metrics.get("completion_tokens"))
    extra = metrics.get("extra") or {}
    reasoning = _int(extra.get("reasoning_output_tokens"))
    total = extra.get("total_tokens")
    prompt = _int(metrics.get("prompt_tokens"))
    if reasoning and isinstance(total, int) and total == prompt + completion + reasoning:
        completion += reasoning
    return completion


def _step_key(step: dict[str, Any], metrics: dict[str, Any]) -> str:
    """Stable identity of a step across re-generated trajectories of one session."""
    tool_ids = [tc.get("tool_call_id") for tc in step.get("tool_calls") or []]
    body = json.dumps(
        [step.get("message"), step.get("reasoning_content"), tool_ids],
        sort_keys=True, default=str,
    )
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
    return "|".join(
        str(x) for x in (
            step.get("timestamp"), step.get("source"), metrics.get("prompt_tokens"),
            metrics.get("completion_tokens"), metrics.get("cached_tokens"), digest,
        )
    )


ANONYMOUS_SIDECHAIN = "side:?"


def _chain_of(step: dict[str, Any], default: str) -> str:
    extra = step.get("extra") or {}
    if extra.get("is_sidechain"):
        agent_id = extra.get("agent_id") or extra.get("agentId")
        return f"side:{agent_id}" if agent_id else ANONYMOUS_SIDECHAIN
    return default


def split_anonymous_sidechains(steps: list[AtifStep]) -> None:
    """Give sidechain steps without an agent id one chain per subagent, in place.

    Harbor 0.22's Claude Code converter drops `agentId`, so every subagent would share
    one chain and a new subagent's first call would be differenced against another
    subagent's last call. Rules, in trajectory order:
      * a sidechain user step (the Task prompt) opens a new chain;
      * a call whose cache read covers the last prompt of an open chain continues the
        chain with the largest such prompt (the cache is that chain's prefix);
      * otherwise it is the first call of the oldest chain that has no call yet;
      * otherwise it continues the chain with the largest last prompt not above its own
        prompt, or opens a new chain.
    Other sidechain steps (no usage) join the most recently opened chain.
    """
    chains: list[dict[str, Any]] = []   # {"id": str, "last": AtifStep | None}

    def new_chain() -> dict[str, Any]:
        chain = {"id": f"side:anon{len(chains) + 1}", "last": None}
        chains.append(chain)
        return chain

    for step in steps:
        if step.chain != ANONYMOUS_SIDECHAIN:
            continue
        if step.source == "user":
            step.chain = new_chain()["id"]
            continue
        if not step.has_usage:
            step.chain = (chains[-1] if chains else new_chain())["id"]
            continue
        started = [c for c in chains if c["last"] is not None]
        cached = [c for c in started if step.cached > 0 and c["last"].prompt <= step.cached]
        grown = [c for c in started if c["last"].prompt <= step.prompt]
        pending = [c for c in chains if c["last"] is None]
        if cached:
            chain = max(cached, key=lambda c: c["last"].prompt)
        elif pending:
            chain = pending[0]
        elif grown:
            chain = max(grown, key=lambda c: c["last"].prompt)
        else:
            chain = new_chain()
        step.chain = chain["id"]
        chain["last"] = step


def _reduce_step(step: dict[str, Any], chain: str, default_model: str | None) -> AtifStep:
    metrics = step.get("metrics") or {}
    source = step.get("source")
    llm_count = step.get("llm_call_count")
    is_llm = source == "agent" and (bool(metrics) or (llm_count or 0) > 0)
    write_5m, write_1h, recorded = _cache_writes(metrics.get("extra") or {})

    input_tokens = 0
    if source in ("user", "system"):
        input_tokens += _content_tokens(step.get("message"))
    for result in (step.get("observation") or {}).get("results") or []:
        input_tokens += _content_tokens(result.get("content"))

    return AtifStep(
        key=_step_key(step, metrics),
        chain=_chain_of(step, chain),
        timestamp=parse_time(step.get("timestamp")),
        copied=bool(step.get("is_copied_context")),
        is_llm_call=is_llm,
        n_llm_calls=(llm_count if isinstance(llm_count, int) else 1) if is_llm else 0,
        model=step.get("model_name") or default_model,
        has_usage=is_llm and _int(metrics.get("prompt_tokens")) > 0,
        prompt=_int(metrics.get("prompt_tokens")),
        cached=_int(metrics.get("cached_tokens")),
        write_5m=write_5m,
        write_1h=write_1h,
        write_recorded=recorded,
        completion=_completion_tokens(metrics),
        n_tool_calls=len(step.get("tool_calls") or []),
        input_content_tokens=input_tokens,
        source=source,
    )


def load_trajectory(path: Path, fallback_model: str | None) -> list[AtifStep] | None:
    """Flatten an ATIF file, its continuation files and embedded subagents into steps.

    Returns None when the file is missing or unreadable.
    """
    data = _read_json(path)
    if data is None:
        return None
    steps: list[AtifStep] = []
    seen_files = {path.resolve()}
    current: dict[str, Any] | None = data
    while current is not None:
        steps.extend(_flatten(current, "main", fallback_model))
        ref = current.get("continued_trajectory_ref")
        current = None
        if ref:
            nxt = (path.parent / ref).resolve()
            if nxt in seen_files:
                break
            seen_files.add(nxt)
            current = _read_json(nxt)
            if current is None:
                log.warning("%s: continuation %s missing", path, ref)
    return steps


def _flatten(traj: dict[str, Any], chain: str, fallback_model: str | None) -> list[AtifStep]:
    model = (traj.get("agent") or {}).get("model_name") or fallback_model
    out = [_reduce_step(s, chain, model) for s in traj.get("steps") or []]
    split_anonymous_sidechains(out)
    for i, sub in enumerate(traj.get("subagent_trajectories") or []):
        sub_chain = "sub:" + str(sub.get("trajectory_id") or i)
        out.extend(_flatten(sub, sub_chain, model))
    return out


# ----------------------------------------------------------------------------- attribution

@dataclass
class Window:
    start: datetime | None
    end: datetime | None
    slack: timedelta

    def contains(self, ts: datetime) -> bool:
        if self.start is not None and ts < self.start - self.slack:
            return False
        if self.end is not None and ts > self.end + self.slack:
            return False
        return True

    @property
    def usable(self) -> bool:
        return self.start is not None or self.end is not None


def attribute_steps(
    steps: list[AtifStep], window: Window, seen_earlier: set[str], resumed: bool,
    earlier_windows: list[Window] = (),
) -> tuple[list[bool], list[str]]:
    """Decide which steps of one segment's trajectory belong to that segment.

    Returns a parallel list of booleans and warning strings. A step belongs to the
    segment if it is not copied context and was not seen in an earlier segment's
    trajectory: the segment whose trajectory first holds a call pays for it. The
    agent window never excludes a step (host and container clocks can disagree, and a
    timed-out agent's late calls surface in the next trajectory); it only warns.
    """
    warnings: list[str] = []
    novel = [not s.copied and s.key not in seen_earlier for s in steps]
    timed = [s.timestamp is not None and window.usable for s in steps]
    outside = [n and t and not window.contains(s.timestamp)
               for s, n, t in zip(steps, novel, timed)]
    if any(outside):
        warnings.append(
            f"{sum(outside)} novel steps outside the agent window attributed anyway "
            "(clock skew, or calls a timed-out agent made after its step ended)")
        strict_earlier = [Window(w.start, w.end, timedelta(0)) for w in earlier_windows if w.usable]
        in_earlier = sum(1 for s, o in zip(steps, outside) if o and any(
            w.contains(s.timestamp) for w in strict_earlier))
        if in_earlier:
            warnings.append(f"{in_earlier} of them fall inside an earlier segment's window: "
                            "possible double count if the trajectory was re-generated differently")
    # Judged without slack: the previous segment's last calls can sit within the slack
    # of this window's start when segments run back to back, and are correctly skipped.
    strict = Window(window.start, window.end, timedelta(0))
    repeats_in_window = sum(
        1 for s, n, t in zip(steps, novel, timed)
        if not s.copied and not n and t and strict.contains(s.timestamp)
    )
    if repeats_in_window:
        warnings.append(f"{repeats_in_window} steps inside window already seen earlier")
    repeats = sum(1 for s, n in zip(steps, novel) if not s.copied and not n)
    if repeats and not resumed:
        warnings.append(f"{repeats} repeated steps in a non-resumed segment")
    return novel, warnings


# ----------------------------------------------------------------------------- segment math

@dataclass
class SegmentTokens:
    n_llm_calls: int = 0
    n_tool_calls: int = 0
    prompt: int = 0
    cached: int = 0
    cache_write: int | None = None
    completion: int = 0
    it_delta: int = 0
    it_content: int = 0
    it_startup: int = 0
    it_startup_main: int = 0
    compactions: int = 0
    sdc: float | None = 0.0
    sdc_neutral: float | None = 0.0
    warnings: list[str] = field(default_factory=list)


def _call_cost(rates: Rates, step: AtifStep) -> float:
    fresh = max(0, step.prompt - step.cached - step.cache_write)
    usd = (
        fresh * rates.input
        + step.write_5m * rates.cache_write
        + step.write_1h * rates.cache_write_1h
        + step.cached * rates.cache_read
        + step.completion * rates.output
    )
    return usd / 1e6


def _neutral_cost(rates: Rates, step: AtifStep, new_in: int) -> float:
    resent = max(0, step.prompt - new_in)
    usd = new_in * rates.cache_write + resent * rates.cache_read + step.completion * rates.output
    return usd / 1e6


def segment_tokens(
    steps: list[AtifStep],
    belongs: list[bool],
    carry: dict[str, AtifStep],
    resumed: bool,
    prices: PriceSheet,
) -> SegmentTokens:
    """Sum token fields, both IT estimators and SDC over the attributed steps.

    `carry` maps chain id -> last LLM call of the previous segment; it supplies the
    predecessor for the first call of a resumed segment whose trajectory does not repeat
    the earlier calls. It is updated in place for the next segment. Subagent chains are
    not carried: a later segment's subagent is a new conversation.
    """
    seg = SegmentTokens()
    any_write_recorded = False
    unknown_models: set[str] = set()
    previous: dict[str, AtifStep] = (
        {k: v for k, v in carry.items() if not k.startswith("side:")} if resumed else {})

    for step, mine in zip(steps, belongs):
        if step.copied:
            continue
        # Only calls with recorded usage can anchor the prompt-growth chain; an LLM step
        # without metrics (e.g. an interrupted call) would look like a reset to zero.
        prev = previous.get(step.chain) if step.has_usage else None
        if step.has_usage:
            previous[step.chain] = step
        if not mine:
            continue

        seg.n_tool_calls += step.n_tool_calls
        seg.it_content += step.input_content_tokens
        if not step.is_llm_call:
            continue

        if not step.has_usage:
            new_in = 0
        elif prev is None:
            new_in = step.prompt
            seg.it_startup += new_in
            if step.chain == "main":
                seg.it_startup_main += new_in
        else:
            new_in = max(0, step.prompt - prev.prompt - prev.completion)
            drop = prev.prompt - step.prompt
            if (
                drop > COMPACTION_MIN_DROP_TOKENS
                and drop > COMPACTION_MIN_DROP_FRACTION * prev.prompt
            ):
                seg.compactions += 1

        seg.n_llm_calls += step.n_llm_calls
        seg.prompt += step.prompt
        seg.cached += step.cached
        seg.completion += step.completion
        seg.it_delta += new_in + step.completion
        seg.it_content += step.completion
        if step.write_recorded:
            any_write_recorded = True
            seg.cache_write = (seg.cache_write or 0) + step.cache_write

        price = prices.lookup(step.model)
        if price is None:
            unknown_models.add(str(step.model))
            continue
        rates = price.rates_for(step.prompt)
        seg.sdc = (seg.sdc or 0.0) + _call_cost(rates, step)
        seg.sdc_neutral = (seg.sdc_neutral or 0.0) + _neutral_cost(rates, step, new_in)

    if not any_write_recorded:
        seg.cache_write = None
    if unknown_models:
        seg.sdc = None
        seg.sdc_neutral = None
        seg.warnings.append("no price for model(s) " + ",".join(sorted(unknown_models)))
    carry.clear()
    carry.update(previous)
    return seg


# ----------------------------------------------------------------------------- trial input

def _read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("cannot read %s: %s", path, exc)
        return None


def _is_trial_result(data: Any) -> bool:
    return isinstance(data, dict) and "trial_name" in data and "task_name" in data


def discover_trials(paths: Iterable[Path]) -> list[Path]:
    """Trial dirs under the given job dirs / trial dirs (any depth), sorted."""
    found: set[Path] = set()
    for root in paths:
        if not root.exists():
            log.warning("%s does not exist", root)
            continue
        candidates = [root / "result.json"] + sorted(root.rglob("result.json"))
        for candidate in candidates:
            if "steps" in candidate.relative_to(root).parts:
                continue
            if candidate.is_file() and _is_trial_result(_read_json(candidate)):
                found.add(candidate.parent.resolve())
    return sorted(found)


def _task_dir_candidates(result: dict, trial_dir: Path, tasks_roots: list[Path]) -> list[Path]:
    task_cfg = (result.get("config") or {}).get("task") or {}
    out: list[Path] = []
    raw = task_cfg.get("path")
    if raw:
        p = Path(raw).expanduser()
        out.append(p)
        if not p.is_absolute():
            # Harbor resolves relative task paths against the cwd of `harbor run`, which is
            # usually the repo root; try the trial's ancestors as plausible cwds.
            out.extend(parent / p for parent in trial_dir.parents)
    name = result.get("task_name")
    for root in tasks_roots:
        if raw:
            out.append(root / Path(raw).name)
        if name:
            out.append(root / str(name).rsplit("/", 1)[-1])
    return out


def load_manifest(
    result: dict, trial_dir: Path, tasks_roots: list[Path]
) -> dict[str, Any] | None:
    """odyssey.json from the task dir, else the copy in task.toml [metadata]."""
    for task_dir in _task_dir_candidates(result, trial_dir, tasks_roots):
        manifest = _read_json(task_dir / "odyssey.json")
        if isinstance(manifest, dict):
            return manifest
        toml_path = task_dir / "task.toml"
        if toml_path.is_file():
            try:
                with open(toml_path, "rb") as handle:
                    meta = tomllib.load(handle).get("metadata") or {}
            except (OSError, tomllib.TOMLDecodeError):
                continue
            # `odyssey compile` writes [metadata] odyssey_manifest = "<json>".
            embedded = meta.get("odyssey_manifest") or meta.get("odyssey")
            if isinstance(embedded, str):
                try:
                    embedded = json.loads(embedded)
                except json.JSONDecodeError:
                    embedded = None
            if isinstance(embedded, dict):
                return embedded
    return None


def _first(d: dict, *keys: str) -> Any:
    for key in keys:
        if d.get(key) not in (None, ""):
            return d[key]
    return None


def _arm_label(manifest: dict) -> str | None:
    label = _first(manifest, "arm_id", "arm_name")
    if label:
        return str(label)
    arm = manifest.get("arm")
    if isinstance(arm, dict):
        return ",".join(f"{k}={arm[k]}" for k in sorted(arm) if arm[k] not in (None, ""))
    return str(arm) if arm is not None else None


@dataclass
class ManifestStep:
    position: int | None = None
    ticket: str | None = None
    cluster: str | None = None
    role: str | None = None
    name: str | None = None
    warning: str | None = None


def _manifest_entry(entry: dict, index: int) -> ManifestStep:
    return ManifestStep(
        position=entry.get("position") if isinstance(entry.get("position"), int) else index + 1,
        ticket=_first(entry, "ticket", "ticket_id"),
        cluster=entry.get("cluster"),
        role=entry.get("role"),
        name=_first(entry, "name", "step", "step_name"),
    )


def manifest_steps(manifest: dict | None, step_names: list[str]) -> list[ManifestStep]:
    """Per-step manifest entries aligned to the executed `step_names`, by name only.

    The manifest is read from the task dir as it is on disk now, which may have been
    recompiled since the run. Step names carry the ticket id, so a name that is not in
    the manifest means the labels no longer describe what ran: the ticket is left empty
    with a warning rather than taken from whatever now sits at that position. Only a
    single-step (cold) trial, whose one step has no name, falls back to the manifest's
    single entry.
    """
    if not manifest:
        return [ManifestStep(position=i + 1) for i in range(len(step_names))]
    entries = [e for e in manifest.get("steps") or [] if isinstance(e, dict)]
    by_name = {str(_first(e, "name", "step", "step_name")): e for e in entries
               if _first(e, "name", "step", "step_name")}
    out: list[ManifestStep] = []
    for index, name in enumerate(step_names):
        if name in by_name:
            out.append(_manifest_entry(by_name[name], index))
        elif not name and len(step_names) == 1 and len(entries) == 1:
            out.append(_manifest_entry(entries[0], index))
        else:
            out.append(ManifestStep(position=index + 1, warning=(
                f"step {name or '(single step)'} not in the task's manifest (task dir "
                "recompiled since the run?); ticket left empty")))
    return out


@dataclass
class SegmentInput:
    step_name: str
    agent_dir: Path
    agent_result: dict
    verifier_result: dict | None
    exception_info: dict | None
    agent_execution: dict | None


def segment_inputs(result: dict, trial_dir: Path) -> list[SegmentInput]:
    """Multi-step trials: one per executed step. Single-step: one at the trial level."""
    step_results = result.get("step_results")
    if step_results:
        return [
            SegmentInput(
                step_name=str(sr.get("step_name")),
                agent_dir=trial_dir / "steps" / str(sr.get("step_name")) / "agent",
                agent_result=sr.get("agent_result") or {},
                verifier_result=sr.get("verifier_result"),
                exception_info=sr.get("exception_info"),
                agent_execution=sr.get("agent_execution"),
            )
            for sr in step_results
        ]
    return [SegmentInput(
        step_name="",
        agent_dir=trial_dir / "agent",
        agent_result=result.get("agent_result") or {},
        verifier_result=result.get("verifier_result"),
        exception_info=result.get("exception_info"),
        agent_execution=result.get("agent_execution"),
    )]


def _grader_error(verifier_result: dict | None) -> bool | None:
    """test.sh's `error` key: 1 when grading itself failed, so reward 0 is not the agent's."""
    rewards = (verifier_result or {}).get("rewards") or {}
    if "error" not in rewards:
        return None
    return bool(rewards["error"])


def _reward(verifier_result: dict | None) -> float | None:
    rewards = (verifier_result or {}).get("rewards") or {}
    if "reward" in rewards:
        return rewards["reward"]
    if len(rewards) == 1:
        return next(iter(rewards.values()))
    return None


def _agent_and_model(result: dict) -> tuple[str | None, str | None]:
    info = result.get("agent_info") or {}
    agent_cfg = (result.get("config") or {}).get("agent") or {}
    agent = info.get("name") or agent_cfg.get("name") or agent_cfg.get("import_path")
    model = (info.get("model_info") or {}).get("name") or agent_cfg.get("model_name")
    return agent, (model.rsplit("/", 1)[-1] if isinstance(model, str) else None)


# ----------------------------------------------------------------------------- per trial

def account_trial(
    trial_dir: Path,
    prices: PriceSheet,
    tasks_roots: list[Path] | None = None,
    clock_slack_sec: float = 5.0,
) -> list[dict[str, Any]]:
    """Rows for one trial directory: one per executed segment, plus one `not_run` row per
    planned segment the trial never reached (so an aborted session cannot hide failures)."""
    result = _read_json(trial_dir / "result.json") or _read_json(trial_dir / "results.json")
    if not _is_trial_result(result):
        log.warning("%s: no readable result.json; skipped", trial_dir)
        return []

    inputs = segment_inputs(result, trial_dir)
    manifest = load_manifest(result, trial_dir, tasks_roots or [])
    trial_warnings: list[str] = []
    if manifest is None:
        trial_warnings.append("odyssey manifest not found")
    meta = manifest_steps(manifest, [s.step_name for s in inputs])
    planned = len((manifest or {}).get("steps") or []) or None

    agent, model = _agent_and_model(result)
    resume_flag = bool(((result.get("config") or {}).get("agent") or {}).get("resume_trajectory"))
    multi_step = bool(result.get("step_results"))
    ordering = (manifest or {}).get("ordering")
    common = {
        "job": trial_dir.parent.name,
        "trial": result.get("trial_name"),
        "sequence": _first(manifest or {}, "sequence", "sequence_id"),
        "arm": _arm_label(manifest or {}),
        "ordering_id": (manifest or {}).get("ordering_id")
        or ("-".join(map(str, ordering)) if isinstance(ordering, list) else None),
        "agent": agent,
        "model": model,
        "ordering_length": planned or len(inputs),
    }

    egress = EgressTracker(trial_dir, manifest, result)
    trial_warnings.extend(egress.trial_warnings)

    seen: set[str] = set()
    carry: dict[str, AtifStep] = {}
    rows: list[dict[str, Any]] = []
    previous_harness: dict | None = None
    windows: list[Window] = []
    last_steps: list[AtifStep] | None = None
    for index, (seg_in, seg_meta) in enumerate(zip(inputs, meta)):
        resumed = multi_step and resume_flag and index > 0
        row = {
            **common,
            "position": seg_meta.position,
            "ticket": seg_meta.ticket if manifest else (seg_in.step_name or result.get("task_name")),
            "cluster": seg_meta.cluster,
            "role": seg_meta.role,
            "resumed": resumed,
            "reward": _reward(seg_in.verifier_result),
            "grader_error": _grader_error(seg_in.verifier_result),
            "reported_cost_usd": seg_in.agent_result.get("cost_usd"),
            "reported_cost_scope": "segment" if not resumed else "unknown",
            "agent_seconds": _seconds(seg_in.agent_execution),
            "exception": (seg_in.exception_info or {}).get("exception_type"),
            "step_name": seg_in.step_name,
        }
        warnings = list(trial_warnings)
        if seg_meta.warning:
            warnings.append(seg_meta.warning)
            log.warning("%s/%s: %s", row["trial"], seg_in.step_name or "-", seg_meta.warning)
        steps = load_trajectory(seg_in.agent_dir / "trajectory.json", model)
        window = _window(seg_in.agent_execution, clock_slack_sec)
        if steps is None:
            warnings.append("no trajectory.json")
            _fill_from_harness(row, seg_in.agent_result, resumed, model, prices, warnings)
        else:
            last_steps = steps
            belongs, attr_warnings = attribute_steps(steps, window, seen, resumed, windows)
            warnings.extend(attr_warnings)
            seen.update(s.key for s in steps)
            seg = segment_tokens(steps, belongs, carry, resumed, prices)
            warnings.extend(seg.warnings)
            if not resumed:
                warnings.extend(_harness_mismatch(seg, seg_in.agent_result))
            else:
                cost, scope, note = _resumed_reported_cost(
                    seg.prompt, seg_in.agent_result, previous_harness)
                row["reported_cost_usd"], row["reported_cost_scope"] = cost, scope
                warnings.extend(note)
            row.update({
                "n_llm_calls": seg.n_llm_calls,
                "n_tool_calls": seg.n_tool_calls,
                "prompt_tokens": seg.prompt,
                "cached_tokens": seg.cached,
                "cache_write_tokens": seg.cache_write,
                "completion_tokens": seg.completion,
                "incremental_tokens": seg.it_delta,
                "it_delta": seg.it_delta,
                "it_content": seg.it_content,
                "it_startup_tokens": seg.it_startup,
                "it_startup_main_tokens": seg.it_startup_main,
                "it_delta_net": seg.it_delta - seg.it_startup_main,
                "sdc_usd": seg.sdc,
                "sdc_neutral_usd": seg.sdc_neutral,
                "compactions": seg.compactions,
            })
        row.update(egress.segment(seg_in.step_name, multi_step, warnings))
        for w in warnings:
            log.info("%s/%s: %s", row["trial"], seg_in.step_name or "-", w)
        row["warnings"] = warnings
        rows.append(row)
        windows.append(window)
        previous_harness = seg_in.agent_result

    if rows and resume_flag and multi_step and last_steps is not None:
        rows[-1]["warnings"].extend(_reconcile(rows, last_steps))
    rows += _not_run_rows(manifest, meta, common, trial_warnings)
    if any(r["exception"] == NOT_RUN for r in rows):
        log.warning("%s: %d of %d planned segments executed", trial_dir, len(inputs), planned)
    for row in rows:
        row["warnings"] = "; ".join(row["warnings"])
    return rows


# ----------------------------------------------------------------------------- egress

EGRESS_LOG = "odyssey-egress.jsonl"


def egress_connections(lines: Iterable[str]) -> list[tuple[str, str, str]]:
    """(connection key, "allowed"|"denied", "host:port") per connection decision in gost lines.

    gost (debug level, JSON) logs ``proxy: <host:port>, whitelist: true`` when the allowlist
    admits a connection and ``bypass: <host:port>[, whitelist: true]`` when it refuses one,
    sometimes twice per connection; the key (gost's ``sid``) lets callers count each once.
    """
    out = []
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = entry.get("msg") if isinstance(entry, dict) else None
        if not isinstance(msg, str):
            continue
        for prefix, verdict in (("bypass: ", "denied"), ("proxy: ", "allowed")):
            if msg.startswith(prefix):
                target = msg[len(prefix):].split(",", 1)[0].strip()
                out.append((str(entry.get("sid") or f"line-{index}"), verdict, target))
    return out


class EgressTracker:
    """Per-segment egress counts from the cumulative sidecar log snapshots of one trial."""

    def __init__(self, trial_dir: Path, manifest: dict | None, result: dict):
        options = ((manifest or {}).get("arm_options") or {}).get("egress") or {}
        self.trial_dir = trial_dir
        self.expected = isinstance(options, dict) and options.get("mode") == "allowlist"
        self.previous: list[str] = []
        self.seen: set[tuple[str, str]] = set()
        self.trial_warnings: list[str] = []
        config = result.get("config") or {}
        widened = [h for part in ("agent", "environment")
                   for h in ((config.get(part) or {}).get("extra_allowed_hosts") or [])]
        if self.expected and widened:
            self.trial_warnings.append(
                f"run widened the egress allowlist with {', '.join(map(str, widened))}")

    def segment(self, step_name: str, multi_step: bool, warnings: list[str]) -> dict[str, Any]:
        base = self.trial_dir / "steps" / step_name if multi_step else self.trial_dir
        path = base / "artifacts" / EGRESS_LOG
        if not path.is_file():
            if self.expected:
                warnings.append("egress log missing (task compiled with --egress allowlist)")
            return {}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if lines[:len(self.previous)] == self.previous:
            new = lines[len(self.previous):]
        else:
            warnings.append("egress log does not continue the previous step's; counted whole")
            new = lines
        self.previous = lines
        allowed = 0
        denied: Counter[str] = Counter()
        for key, verdict, target in egress_connections(new):
            if (key, verdict) in self.seen:
                continue
            self.seen.add((key, verdict))
            if verdict == "denied":
                denied[target] += 1
            else:
                allowed += 1
        return {
            "egress_allowed": allowed,
            "egress_denied": sum(denied.values()),
            "egress_denied_targets": " ".join(f"{t}={n}" for t, n in sorted(
                denied.items(), key=lambda item: (-item[1], item[0]))),
        }


def _reconcile(rows: list[dict[str, Any]], last_steps: list[AtifStep]) -> list[str]:
    """Under resume the last trajectory holds the whole session: segments must add up to it."""
    session = {s.key: s.prompt for s in last_steps if s.is_llm_call and not s.copied}
    attributed = sum(r.get("prompt_tokens") or 0 for r in rows)
    total = sum(session.values())
    if total and attributed != total:
        return [f"segments attribute {attributed} prompt tokens but the session's last "
                f"trajectory holds {total}"]
    return []


def _not_run_rows(
    manifest: dict | None, meta: list[ManifestStep], common: dict[str, Any], trial_warnings: list[str],
) -> list[dict[str, Any]]:
    """One row per planned step that has no step result (Harbor stopped the trial early).

    Only when every executed step matched the manifest: a manifest that no longer
    describes the run cannot say what was planned either.
    """
    if any(m.warning for m in meta):
        return []
    ran = {m.name for m in meta if m.name}
    out = []
    for index, entry in enumerate((manifest or {}).get("steps") or []):
        if not isinstance(entry, dict):
            continue
        step = _manifest_entry(entry, index)
        if step.name in ran or (index < len(meta) and not step.name):
            continue
        out.append({
            **common,
            "position": step.position,
            "ticket": step.ticket,
            "cluster": step.cluster,
            "role": step.role,
            "resumed": None,
            "reward": None,
            "grader_error": None,
            "exception": NOT_RUN,
            "step_name": step.name,
            "warnings": [*trial_warnings, "planned segment never ran (an earlier step stopped the trial)"],
        })
    return out


def _close(a: int, b: int) -> bool:
    return abs(a - b) <= HARNESS_MATCH_TOLERANCE * max(abs(a), abs(b), 1)


def _resumed_reported_cost(
    segment_prompt: int, agent_result: dict, previous: dict | None
) -> tuple[float | None, str, list[str]]:
    """Per-segment harness cost for a resumed segment, and the scope it was found in.

    Measured on Harbor 0.22 + Claude Code: under --continue both
    the step's token totals and its `cost_usd` cover the whole session so far, because
    the parser reads the appended session file and Claude Code's `total_cost_usd` is
    session-cumulative. Other harnesses may report per invocation. The token totals tell
    which: if they equal this segment's ATIF prompt tokens the step is segment-local; if
    their increase over the previous step does, they are cumulative and the cost is
    differenced the same way. Anything else is left empty rather than guessed.
    """
    cost = agent_result.get("cost_usd")
    tokens = agent_result.get("n_input_tokens")
    if not isinstance(tokens, int) or tokens <= 0 or cost is None:
        return None, "unknown", []
    if _close(tokens, segment_prompt):
        return cost, "segment", []
    prev_tokens = (previous or {}).get("n_input_tokens")
    prev_cost = (previous or {}).get("cost_usd")
    if isinstance(prev_tokens, int) and prev_cost is not None and _close(
        tokens - prev_tokens, segment_prompt
    ):
        return cost - prev_cost, "cumulative", []
    return None, "unknown", [
        f"harness totals match neither this segment nor a cumulative session "
        f"(n_input_tokens {tokens}, cost_usd {cost}); reported_cost_usd left empty"
    ]


def _seconds(timing: dict | None) -> float | None:
    start = parse_time((timing or {}).get("started_at"))
    end = parse_time((timing or {}).get("finished_at"))
    return round((end - start).total_seconds(), 3) if start and end else None


def _window(timing: dict | None, slack_sec: float) -> Window:
    return Window(
        start=parse_time((timing or {}).get("started_at")),
        end=parse_time((timing or {}).get("finished_at")),
        slack=timedelta(seconds=slack_sec),
    )


def _harness_mismatch(seg: SegmentTokens, agent_result: dict) -> list[str]:
    """Flag fresh segments whose ATIF sum disagrees with Harbor's own total by >1%."""
    reported = agent_result.get("n_input_tokens")
    if not isinstance(reported, int) or reported == 0:
        return []
    if abs(reported - seg.prompt) > 0.01 * reported:
        return [f"ATIF prompt tokens {seg.prompt} != harness n_input_tokens {reported}"]
    return []


def _fill_from_harness(
    row: dict, agent_result: dict, resumed: bool, model: str | None,
    prices: PriceSheet, warnings: list[str],
) -> None:
    """Without a trajectory, a fresh segment can still use Harbor's per-step totals.

    Resumed segments cannot (their totals may be cumulative), so they stay empty. Cache
    writes are not in AgentContext, so SDC prices all uncached input as fresh input.
    """
    if resumed or agent_result.get("n_input_tokens") is None:
        return
    prompt = _int(agent_result.get("n_input_tokens"))
    cached = _int(agent_result.get("n_cache_tokens"))
    completion = _int(agent_result.get("n_output_tokens"))
    row.update({"prompt_tokens": prompt, "cached_tokens": cached,
                "completion_tokens": completion})
    warnings.append("token fields from harness totals; IT not computable")
    price = prices.lookup(model)
    if price is None:
        warnings.append(f"no price for model(s) {model}")
        return
    pseudo = AtifStep(key="", chain="main", timestamp=None, copied=False, is_llm_call=True,
                      n_llm_calls=0, model=model, prompt=prompt, cached=cached,
                      completion=completion)
    # Long-context bands are per call and calls are unknown here: standard band only.
    row["sdc_usd"] = _call_cost(price.standard, pseudo)


# ----------------------------------------------------------------------------- CLI

def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".") if value else "0"
    return str(value)


def write_csv(rows: list[dict[str, Any]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([_format(row.get(col)) for col in COLUMNS])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="odyssey account",
        description="Emit one row per segment (SPEC section 7) from Harbor job/trial dirs.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="job dirs or trial dirs")
    parser.add_argument("--out", type=Path, default=Path("segments.csv"))
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--tasks-root", type=Path, action="append", default=[],
                        help="where compiled task dirs live, if config.task.path moved")
    parser.add_argument("--clock-slack-sec", type=float, default=5.0,
                        help="tolerance around each step's agent_execution window")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    prices = PriceSheet.load(args.prices)
    trials = discover_trials(args.paths)
    if not trials:
        log.error("no Harbor trial results found under %s", ", ".join(map(str, args.paths)))
        return 1
    rows: list[dict[str, Any]] = []
    for trial_dir in trials:
        rows.extend(account_trial(trial_dir, prices, args.tasks_root, args.clock_slack_sec))
    write_csv(rows, args.out)
    n_warn = sum(1 for r in rows if r.get("warnings"))
    print(f"{len(rows)} segments from {len(trials)} trials -> {args.out}"
          f" ({n_warn} with warnings)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
