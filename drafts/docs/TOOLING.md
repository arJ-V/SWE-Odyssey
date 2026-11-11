# SWE-Odyssey tooling notes (earlier top-level README)

A continual-learning benchmark in progress. A coding agent works through a
sequence of tickets in one codebase, and we measure whether what it learns on
earlier tickets changes how it handles later ones, by comparing each ticket
solved warm (after earlier tickets) with the same ticket solved cold. This repo
holds the v0 tooling. It turns a
hand-authored sequence into [Harbor](https://docs.harborframework.com/tasks/multi-step)
multi-step tasks, checks the tickets, and turns Harbor job output into
per-segment cost rows. The contract is [docs/SPEC.md](../../docs/SPEC.md).

## Quickstart

```sh
uv sync                                                       # Python >= 3.11; Harbor 0.22 and Docker on PATH
uv run odyssey validate drafts/sequences/toy-ledger --repeat 3       # ticket contract, inside the sequence image
uv run odyssey design drafts/sequences/toy-ledger --design williams  # show the orderings only
uv run odyssey compile drafts/sequences/toy-ledger --out build/tasks --design williams \
    --code-state forward-merge --framing informed --feedback none
harbor run -p build/tasks/toy-ledger__fm-inf-nofb__w01-<hash> -a oracle     # every step should score 1
harbor run -p build/tasks/toy-ledger__fm-inf-nofb__w01-<hash> -a claude-code -m anthropic/<model> --resume-trajectory
uv run odyssey account jobs/ --out segments.csv               # one row per segment
uv run odyssey analyze segments.csv                           # warm/cold ratios, position curve
```

Ordering ids are a row number plus a hash of the row's tickets (`w01-3fa2c1`),
so a later compile with another `--subset` or `--seed` never reuses a task
directory name for different tickets; `compile` prints the paths.

`odyssey validate --no-docker` runs the same checks on the host for quick
iteration, on the sequence's `base_tree` (or `--tree <dir|tarball>`); add
`--subsets` to also check every start state forward-merge can produce. `uv run pytest` runs the unit tests. If `uv run odyssey`
reports `No module named 'odyssey'` on macOS, the venv's `.pth` files carry the
`hidden` flag and Python skips them. Run `chflags -R nohidden .venv`, or use
`uv run python -m odyssey.cli` from the repo root.

## Real sequences

`drafts/sequences/httrack-2026` is the first sequence built from a real repository
(ten stacked, already-merged HTTrack fixes from July 2026; C, autotools). Its
base tree exists only in the image, so it checks in `base.manifest`
(`odyssey base-manifest`) instead of a `base_tree`. The recipe, helper scripts
(`scripts/real_sequence/`) and pitfalls are in
[drafts/docs/REAL_SEQUENCE_GUIDE.md](REAL_SEQUENCE_GUIDE.md).

## Network allowlist (`--egress allowlist`)

A real sequence's fixes are public upstream, so an agent with internet access
can fetch them. `--egress allowlist` (opt-in; the default `open` compiles
exactly as before) uses Harbor 0.22's egress-control sidecar so that the agent
container reaches only `api.anthropic.com` and `api.openai.com`, bakes Claude
Code and Codex into the image (the trial cannot download them), runs the
verifier with no network, and logs every allowed and denied connection where
the agent cannot reach it. Mechanism, allowlist and limits: docs/SPEC.md
section 11.

```sh
uv run odyssey compile drafts/sequences/httrack-2026 --out build/tasks \
    --ordering HTT-01,HTT-02,HTT-03 --egress allowlist        # arm fm-inf-nofb-egress
harbor run -p build/tasks/httrack-2026__fm-inf-nofb-egress__x915b20 -a oracle   # every step 1
harbor run -p build/tasks/httrack-2026__fm-inf-nofb-egress__x915b20 -a claude-code -m anthropic/<model>
uv run odyssey account jobs/ --out segments.csv   # egress_allowed, egress_denied, egress_denied_targets per segment
```

Open-weight models (Kimi K3, GLM-5, MiniMax M2.5 on Amazon Bedrock) run with
`--egress-allow-host` (adds the model endpoint to the allowlist) and
`--agent-cli` (bakes the chosen harness into the image); helper scripts are in
`scripts/open_models/`.

Do not pass `--allow-environment-host` / `--allow-agent-host` to such runs
(`odyssey account` warns if a run did). To check a compiled task without a
model call, run the reachability probe as the agent; it needs Docker and makes
only unauthenticated requests:

```sh
PYTHONPATH=scripts/egress harbor run -p <task> -a probe_agent:EgressProbeAgent
python3 -I scripts/egress/check_probe.py jobs/<job>     # every probe as the policy expects
scripts/egress/box_check.sh drafts/sequences/httrack-2026 HTT-01,HTT-02,HTT-03 HTT-01 <work-dir>
```

`box_check.sh` compiles one ordering and one cold ticket with the option,
runs oracle, nop, the probe and `--install-only` for claude-code and codex,
plus the probe on an open compile as a control, and prints rewards, probe
verdicts and the egress columns.

## Arms

An arm is the compile-time options below plus the run-time Harbor flags. Each
ordering × arm is one task directory, `<sequence>__<arm>__<ordering-id>`.

| option | values | effect |
|---|---|---|
| `--code-state` | `forward-merge` (default, `fm`) | Tree at segment k = base + gold patches of the tickets at positions 1..k-1. Agent edits are discarded. |
| | `all-defects` (`ad`) | Tree reset to base before every segment. |
| | `single-defect` (`sd`) | Base + every other ticket's gold patch. The start state depends only on the ticket. Segment 1 then shows the fix of every later ticket, so orderings longer than one ticket need `--allow-leak` (use it for cold runs). |
| | `carry` | The agent's own edits accumulate (git history is still reset to one commit). Realism arm. |
| `--framing` | `informed` (default, `inf`) / `natural` (`nat`) | Informed adds a short header: "ticket k of N", the tree policy, where memory persists. |
| `--feedback` | `none` (`nofb`) / `binary` (`fb`) | Binary writes `accepted`/`rejected` to `/var/odyssey/feedback/<k>.txt` and tells the agent where to find it. |
| `--seed-notes FILE` | (`notes-<hash>`) | Copies expert notes to `<memory_dir>/NOTES.md` before segment 1 (onboarding-notes arm). The hash names the notes file, so different notes are different arms. |
| `--egress` | `open` (default) / `allowlist` (`egress`) | Allowlist: the agent container reaches only the two model APIs; agent CLIs baked into the image; per-segment denied-connection counts in `odyssey account`. |
| run time | `-a/-m`, `--resume-trajectory` or not | Harness and model. With `--resume-trajectory` the conversation carries over. Without it, a fresh session starts each segment and only files carry over. |

Memory (`memory_dir` and the repo's `repo_memory_files`) survives every policy.
Header wording lives in `odyssey/templates/framing.toml`. The step scripts are
in `odyssey/templates/`. What the grader trusts, and what it deliberately does
not defend against, is in docs/SPEC.md sections 5 and 10.
