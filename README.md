# SWE-Odyssey

Do coding agents learn on the job?

## Why

A model's weights are fixed, but an agent that keeps working in one system accumulates
experience of it: where things live, how they are deployed, what broke last time. A human
engineer turns that experience into speed. An engineer who has worked in a system for a week
needs fewer steps for the next ticket than one who arrived this morning.

Coding benchmarks mostly miss this. They hand an agent one ticket from a clean start and
score pass or fail, so every ticket is the agent's first day. SWE-Odyssey measures the
continual-learning question directly: **when an agent has already worked in a system, does
it do the next ticket better than a fresh agent would?**

## What it measures

An agent works through a **sequence of tickets in one system**. Each ticket is measured twice:

- **cold**: a fresh agent with no history solves the ticket alone;
- **warm**: one agent solves every ticket in order, with its session resumed between tickets,
  so everything it saw and did earlier is available to it.

Learning shows up as the warm agent needing less than the cold one for the same ticket. The
result is the **warm ÷ cold ratio per ticket**; below 1 means experience helped.

| Measure | What it counts |
|---|---|
| solved | hidden tests pass |
| steps | model calls the agent made on the ticket |
| new work | new tokens in the ticket: what the model wrote plus context that entered its prompt for the first time |
| time | wall-clock time the agent spent on the ticket |
| dollars | every token of the ticket's model calls at list price |

Dollars are reported alongside but are not the point. A long session re-reads its own
history on every call, so an agent can learn, take fewer steps and still cost the same.

Ticket order is rotated across sequences so that position is not confused with difficulty.

## The current task: `atlas-commerce`

One small e-commerce backend (order intake, bulk fulfilment, merchant payments) running on a
local AWS emulator that needs no cloud account. Seven tickets change that one system. They mix
the kinds of work a platform engineer gets in a week: deploying new infrastructure, writing
application code, repairing something broken in production and tightening an access policy.

Between tickets the system is reset to a known state, so the agent carries over what it
learned but not its own leftovers. Details are in
[sequences/atlas-commerce/README.md](sequences/atlas-commerce/README.md).

## Preliminary results

<!-- results:start -->
Warm ÷ cold for tickets 2 to 7, with 95% intervals from resampling whole sequences. Below 1
means experience helped.

| System | Warm sequences | Solved cold | Solved warm | Steps | New work | Time | Dollars |
|---|---|---|---|---|---|---|---|
| Claude Code + Opus 5.5 | 5 | 11 of 14 | 32 of 35 | 0.74 (0.65–0.88) | 0.60 (0.55–0.66) | 1.00 (0.78–1.41) | 1.08 (0.90–1.41) |
| Claude Code + Sonnet 5.5 | 7 | 12 of 14 | 45 of 49 | 0.63 (0.58–0.68) | 0.53 (0.50–0.57) | 0.86 (0.77–0.95) | 0.79 (0.75–0.82) |
| Claude Code + Haiku 5.5 | 5 | 13 of 14 | 33 of 35 | 0.52 (0.45–0.62) | 0.48 (0.43–0.53) | 0.78 (0.65–0.98) | 2.39 (1.74–3.48) |
| Codex + GPT-6.1 Sol | 9 | 12 of 14 | 56 of 63 | 0.76 (0.67–0.87) | 0.91 (0.82–1.02) | 1.74 (1.39–2.20) | 1.30 (1.09–1.54) |
| Codex + GPT-6.1 Terra | 7 | 12 of 14 | 44 of 49 | 0.92 (0.80–1.08) | 0.83 (0.75–0.93) | 1.32 (1.14–1.54) | 1.37 (1.17–1.59) |
| Codex + GPT-6 Luna | 7 | 10 of 14 | 48 of 49 | 1.04 (0.86–1.26) | 1.07 (0.92–1.24) | 1.26 (1.09–1.48) | 1.87 (1.53–2.29) |

- **The Claude Code systems learn.** With experience of the system they take 26–48% fewer
  steps per ticket and do 40–52% less new work, starting from the second ticket.
- **The Codex systems mostly do not.** GPT-6.1 Sol takes about a quarter fewer steps and
  GPT-6.1 Terra does 17% less new work; their other intervals, and both of GPT-6 Luna's,
  include no change. Model and harness differ together here, so this is not a comparison of
  the models alone.
- **No system is reliably quicker on the clock.** Each call in a long session is slower,
  which cancels the saved steps for Claude Code and outweighs them for Codex. The time measure
  also leans toward warm by 10–25% (a first ticket with no history comes out at 0.74–0.92, not
  1), so a time ratio near 0.8 means no change.
- **Only one system is cheaper.** Sonnet 5.5 pays 21% less; the rest pay the same or more,
  because every call re-reads a longer history. Haiku 5.5 pays more than double because its
  price rises once a prompt passes 100,000 tokens.
- **Experience never hurt success.** Every system solved at least as large a share warm as
  cold, and GPT-6 Luna went from 10 of 14 cold to 48 of 49 warm.

Some GPT-6.1 Sol sequences hit a provider rate limit and are left out. Open-weight models are
being run.
<!-- results:end -->

These are pilot numbers from five to nine sequences per system, each run once, with two cold
runs per ticket. Read them as large or small effects, not precise ones.

## Run it

Needs Docker, [Harbor](https://docs.harborframework.com/tasks/multi-step) 0.22 and
[uv](https://docs.astral.sh/uv/). Model API keys go in the environment Harbor runs in.

```sh
# Compile: 14 rotated orderings of the tickets, and one cold task per ticket.
uv run python scripts/atlas/compile_atlas.py compile sequences/atlas-commerce --out build/atlas/williams --design williams
uv run python scripts/atlas/compile_atlas.py compile sequences/atlas-commerce --out build/atlas/cold --design cold

# Check the setup with the reference agent: every step should score 1.
harbor run -p build/atlas/williams/<one ordering> -a oracle -o jobs/atlas --job-name oracle -n 1 -y

# Cold: each ticket alone. Warm: every ordering in turn, session resumed between tickets.
harbor run -p build/atlas/cold -a claude-code -m anthropic/<model> -o jobs/atlas --job-name cold1 -n 1 -y
harbor run -p build/atlas/williams -a claude-code -m anthropic/<model> --resume-trajectory -o jobs/atlas --job-name warm -n 1 -y

# After each job: remove the trial's leftover containers.
python3 scripts/atlas/cleanup_job.py jobs/atlas/<job name>

# One row per ticket attempt, then the warm ÷ cold ratios.
uv run python -m odyssey.cli account jobs/atlas --out jobs/atlas/segments.csv
uv run python -m odyssey.cli analyze jobs/atlas/segments.csv                     # new work
uv run python -m odyssey.cli analyze jobs/atlas/segments.csv --metric sdc_usd    # dollars
```

## Status

This is an early pilot, built in a few days with heavy use of AI agents.

- No human has yet reviewed every ticket, grader and trajectory.
- Each model runs in its vendor's own agent at default settings, so differences between
  vendors are not model differences alone.
- Frontier models solve most of these tickets cold, so the signal is efficiency, not pass rate.
- The agent has root in the container that grades it and the task has no network restriction
  yet, so results rely on agents not trying to cheat.

## Repository layout

| Path | What |
|---|---|
| `sequences/atlas-commerce/` | the current task |
| `scripts/atlas/` | compile, validate and clean-up tools for it |
| `odyssey/` | shared tooling: compile sequences to Harbor tasks, validate, account, analyse |
| `docs/SPEC.md` | the contract every sequence follows |
| `tests/` | unit tests (`uv run pytest`; `uv run pytest scripts/atlas/test_compile_atlas.py` for the atlas compiler) |
| `drafts/` | earlier and unfinished task sets and notes; see [drafts/README.md](drafts/README.md) |

## Licence

MIT; see [LICENSE](LICENSE). Material from other projects under `drafts/` keeps its own
licence; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
