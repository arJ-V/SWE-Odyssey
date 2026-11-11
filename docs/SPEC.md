# SWE-Odyssey infrastructure spec (v0)

This is the contract between the pieces of the benchmark tooling. It is deliberately
neutral about decisions that are still open (which repo, which tickets, which arms the
pilot buys); those are parameters here, not constants.

## 1. Vocabulary

- **Sequence**: one codebase snapshot plus a pool of tickets that all apply to it.
- **Ticket**: one unit of engineering work (issue text, hidden tests, gold patch).
- **Ordering**: a permutation (or sub-list) of ticket ids. A cold run is an ordering of length 1.
- **Segment**: one ticket at one position inside one ordering. The unit of observation.
- **Arm**: the experimental condition a compiled task encodes (code-state policy, framing,
  feedback, seeded notes) plus the run-time condition (harness, model, resume or fresh).
- **Session**: one Harbor trial of one compiled task. The unit of resampling.

## 2. How a sequence maps onto Harbor

Harbor 0.22 has native multi-step tasks: `[[steps]]` in `task.toml`, one directory per
step under `steps/<name>/` with `instruction.md`, `tests/test.sh`, `solution/solve.sh`
and `workdir/` (uploaded into the container's cwd before the step; `workdir/setup.sh` is
executed). One container lives for the whole trial, each step is verified separately and
gets its own agent logs, tokens and timing. `--resume-trajectory` continues the agent's
own conversation on steps 2..N for agents that support it (claude-code, codex, opencode).

Step order is fixed in `task.toml`, so **one ordering x one arm = one generated task
directory**. `odyssey compile` generates them.

## 3. Sequence source layout (authored by hand or by an importer)

```
sequences/<sequence-id>/
  sequence.toml
  environment/            # Docker build context (or use environment.docker_image)
  tickets/<ticket-id>/
    ticket.md             # the issue text shown to the agent
    gold.patch            # reference fix, `git apply`-able at the base snapshot
    tests/
      run.sh              # grader entry point (see section 5)
      files/...           # hidden test files, laid out repo-relative
  notes/ORACLE_NOTES.md   # optional: expert onboarding notes for the oracle-notes arm
```

### sequence.toml

```toml
schema = 1

[sequence]
id = "toy-ledger"
description = "Tiny ledger library with five independent defects"
workdir = "/app"                 # repo root in the container; must equal the image WORKDIR
memory_dir = "/memory"           # outside the repo; never touched by resets
repo_memory_files = ["CLAUDE.md", "AGENTS.md", "NOTES.md"]   # repo-relative, survive resets
agent_timeout_sec = 1800
verifier_timeout_sec = 600

[environment]
context = "environment"          # or: docker_image = "repo/image:tag"
base_tree = "environment/repo"   # optional: host copy of the image's base snapshot
# base_manifest = "base.manifest" # or: hash manifest of a base that exists only in the image
cpus = 2
memory_mb = 4096
storage_mb = 10240
allow_internet = true

[grading]                        # optional; see section 5
protected_paths = ["pytest.ini", "tests"]   # default []
# protected_names defaults to conftest.py, pytest.py, _pytest, sitecustomize.py,
# usercustomize.py, *.pth

[[tickets]]
id = "LED-1"
title = "Rounding drifts on split payments"
cluster = "A"                    # relatedness label; tickets sharing a label are "related"
role = "core"                    # core | trap | isolated  (free-form, carried to analysis)
```

Paths default to `tickets/<id>/ticket.md`, `tickets/<id>/gold.patch`, `tickets/<id>/tests`.

`base_tree`, when given, must have the same content as the image's base snapshot
(`.git`, `__pycache__`, `.pytest_cache`, `.DS_Store` and `*.pyc` are ignored on both sides). `odyssey compile`
hashes it into a manifest; `setup.sh` and `test.sh` refuse a `base.tar.gz` that does not
match it, and `odyssey validate` (Docker mode) checks the image against it.

A real repository is usually built by the Dockerfile (fetched at a snapshot commit, fixes
reverse-applied), so there is no host copy of its tree. Such a sequence declares
`base_manifest` instead (exclusive with `base_tree`): a checked-in file in the same
`<sha256>  ./path` / `link <target>  ./path` format, written from the built image by
`odyssey base-manifest <sequence> [--image IMG] --out <sequence>/base.manifest`. Compile, the
step scripts and validate use it exactly as they use a manifest hashed from `base_tree`.
The manifest pins the image's base content, so the base must be deterministic (source only:
no build outputs, which embed dates and paths); a rebuild that changes the tree fails
`setup.sh` instead of grading against a different base. `odyssey validate --no-docker` on
such a sequence needs `--tree`, and checks that tree against the manifest.

### Image contract

- The image contains the **base snapshot** at `workdir`: every ticket's defect present,
  as a git repository with exactly one commit (no history that could reveal fixes).
- A pristine copy of the base tree is stored at `/opt/odyssey/base.tar.gz` (tar of the
  workdir contents without `.git`). It contains the defects, never the fixes.
- `git`, `bash`, `tar`, `patch`, `sha256sum`, `cmp`, `find`, `xargs` are installed.
  `memory_dir` exists and is writable.
- Hidden tests and gold patches are **not** in the image.
- Nothing in the image names the experiment beyond the fixed paths above (for example,
  a neutral system git identity for the agent's own commits).

### Ticket contract

- `gold.patch` applies cleanly at the base snapshot, and all gold patches apply together
  in any order (disjoint hunks).
- At base: the ticket's `run.sh` fails. At base + its own gold patch: passes. At base +
  every gold patch: passes. At base + every other ticket's gold patch: fails.
  (`odyssey validate` checks all four; `--subsets` also checks every subset of the other
  tickets' gold, i.e. every start state forward-merge can produce.)
- "Fails" means a clean failure: when `run.sh` writes `details.json` with
  `fail_to_pass` / `pass_to_pass` counts, validate requires F2P tests to fail without
  collection or import errors and P2P to pass in full. A grader without that file is
  judged on its exit code alone, and the check says so. The shape is language-neutral:
  each group has `tests`, `passed`, `failed`, `errors` (a test that did not run, did not
  build or left no result counts here) and `skipped`.
- Validate options for real sequences: `--jobs N` runs N grader checks at once (each in
  its own container, named `odyssey-validate-<sequence>-*`); `--no-network` runs every
  check with `--network none` whatever `allow_internet` says, proving the build and tests
  need no network; `--subsets --subset-sample K [--seed S]` checks a stated sample of K
  subsets per ticket instead of all 2^(T-1)-2 (first every single other gold, then every
  "all others but one", then a seeded random fill; the check names each subset run);
  `--only IDS` restricts the per-ticket grader checks while iterating.

## 4. Code-state policies (compile-time, `--code-state`)

What the working tree looks like when segment k starts. Memory (`memory_dir` and
`repo_memory_files`) is preserved under every policy.

| policy | tree at start of segment k | notes |
|---|---|---|
| `forward-merge` (default) | base + gold patches of the tickets at positions 1..k-1 | Past tickets are "merged by the maintainers"; future defects stay latent. No fix for a future ticket is ever in the container. Agent edits do not persist. |
| `all-defects` | base | Every defect present every time; earlier defects reappear. |
| `single-defect` | base + gold patches of every ticket except the current one | Start state depends only on the ticket. **Segment 1 contains the fix of every later ticket**, which the agent can copy into memory, so a warm single-defect run measures memory use, not learning. `odyssey compile` refuses orderings longer than one ticket unless `--allow-leak` is given, and `odyssey analyze` flags the arm. Use it for cold runs. |
| `carry` | whatever the agent left | The agent's own edits accumulate. Realism arm; breaks order-independence. Git is still renewed (below), so the agent's branches, stashes and commits do not carry. |

Every `setup.sh` first kills every process left from the previous segment (section 5,
process sweep) and scrubs `/tests`, `/solution` and `/logs/verifier`. For the three reset
policies it then rebuilds the tree from `/opt/odyssey/base.tar.gz`, checks it against the
compiled base manifest (if the sequence has `base_tree`; a mismatch fails the step),
applies the step's `state.patch`, and restores memory files. Under every policy it
replaces `.git` with a fresh repository holding one commit (neutral author
"Maintainers", fixed date, message "Initial commit"), so no diff between segments can be
read from history and none of the agent's git state (config, hooks, locks, a half-done
rebase) survives. All git calls run with `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=/dev/null`, `core.hooksPath=/dev/null` and `core.fsmonitor=false`,
because the agent can write `~/.gitconfig`, `/etc/gitconfig` and `.git/config`.

Nothing records the segment's position in the container: there is no `segment.json`,
and `/var/odyssey/feedback/` exists only under `--feedback binary`.

## 5. Grading

Per step, Harbor uploads the step's `tests/` to `/tests` (after the agent's turn) and
runs `/tests/test.sh`. Its trust rule: it trusts only what is in that upload (itself, the
ticket's grader, the segment's `start/state.patch` and `start/base.manifest`) and the
base tarball once it matches the manifest. Everything the agent can write (the working
tree, `.git`, git config, running processes) is input, not infrastructure. It:

1. Writes a provisional `reward.json` of `{"reward": 0, "error": 1}`, so a crash or a
   Harbor timeout still leaves a reward.
2. **Process sweep**: kills every process except PID 1, its own ancestry, and whatever
   was already running within 5 s of the container's boot (the keepalive and any
   services the image starts). A loop the agent left behind can therefore neither
   rewrite `reward.json` after grading nor edit the tree while it is copied.
3. Rebuilds the segment's **trusted start tree** in `/tmp/odyssey-grade-<n>/trusted`:
   base tarball (checked against `base.manifest`; a mismatch is a grader error) plus
   `state.patch`. Under `carry` the trusted tree is the base alone.
4. Copies the agent's tree without `.git` to `/tmp/odyssey-grade-<n>/tree`; the agent's
   tree is never modified by grading. The grading copy has no `.git`; a `run.sh` that
   needs one must `git init` it.
5. Saves the agent's diff against the segment start to `/logs/verifier/segment.patch`,
   computed in a repository of the grader's own (never the agent's `.git`); memory files
   are excluded. Reset policies diff against the trusted start tree; `carry` diffs
   against the tree the previous segment's grader saw, kept in
   `/var/lib/odyssey-grader/carry.git`.
6. Makes the copy gradeable: every `protected_names` match is removed and the trusted
   tree's matches are copied back; every `protected_paths` entry is replaced by the
   trusted tree's version (or removed); every top-level entry of the hidden files that
   the trusted tree does not have (e.g. `tests_hidden/`) is removed; then `ticket/files/`
   is overlaid. Symlinks the agent planted on any of these paths are replaced by real
   directories first, so no copy can be redirected outside the grading copy.
7. Runs the ticket's `run.sh` with cwd = the grading copy and `ODYSSEY_OUT=/logs/verifier`,
   under a deadline of 80% of the verifier timeout. Exit code 0 means resolved. `run.sh`
   may write `$ODYSSEY_OUT/details.json`.
8. If feedback is enabled, writes `accepted` or `rejected` to
   `/var/odyssey/feedback/<position>.txt`.
9. Writes `/logs/verifier/reward.json` = `{"reward": 0|1, "error": 0|1}` (error = the
   grader itself failed: base mismatch, missing `run.sh`, timeout, ...) and `grade.json`
   with the human-readable outcome, then removes the scratch copies.

`run.sh` should not trust anything in the grading copy beyond the source under test.
Nothing in compile, validate or the step scripts assumes a language: `run.sh` is any
executable bash script. `protected_names` defaults to Python's import-hook files; a
sequence in another language sets its own list (httrack-2026 protects `.gitignore`,
because its grader deletes git-ignored build products before rebuilding). A compiled
language's `run.sh` must rebuild from the grading copy, never reuse build outputs the
agent's tree or the image could carry.
The toy-ledger graders run `python -I -B -m pytest -c pytest.ini --rootdir .` (no
repo directory on `sys.path`, no `PYTHON*` variables) and count a group as passed only
if pytest exits 0 **and** its junit report exists, ran at least one test and has no
failures or errors, so code that ends the process early with status 0 does not pass.

The next step's `setup.sh` deletes `/tests/*` before the agent starts (Harbor does not).

## 6. Compile-time options (`odyssey compile`)

- `--ordering T1,T3,T2` or `--design williams|latin|random|cold` (cold = one task per ticket)
- `--code-state forward-merge|all-defects|single-defect|carry` (`--allow-leak` to
  compile single-defect orderings longer than one ticket; see section 4)
- `--framing informed|natural`: informed prepends a header telling the agent that more
  tickets follow, that the tree is reset between tickets, and where memory persists.
- `--feedback none|binary`
- `--seed-notes <file>`: copy expert notes into `memory_dir/NOTES.md` before segment 1
  (onboarding-notes arm). The arm id gets `-notes-<first 6 hex of the file's sha256>`,
  so two notes files are two arms.
- `--egress open|allowlist` (default `open`): `allowlist` confines the agent container's
  network to the model APIs (section 11). The arm id gets `-egress`; with `open` nothing in
  the compiled task changes.
- `--egress-allow-host <host>` (repeatable, needs `--egress allowlist`): add a model
  endpoint to the allowlist, e.g. `bedrock-runtime.us-east-1.amazonaws.com`; wildcards
  and code or package hosts are refused. `--agent-cli kimi-cli|mini-swe-agent`
  (repeatable, needs `--egress allowlist`): also bake that harness into the image. Neither
  changes the arm id; the manifest records them under `arm_options.egress`. Unused, the
  compiled task is byte-identical to one compiled before they existed.
- `--out <dir>`

Output: `<out>/<sequence>__<arm>__<ordering-id>/` (a Harbor task dir) with an
`odyssey.json` manifest: sequence id, arm options, ordering, and for each step its
name, position, ticket id, cluster and role. Ordering ids are content-derived:
`x<hash>` for `--ordering`, `<w|l|r|c><row>-<hash>` for designs, where the hash is of the
row's tickets. A recompile into the same `--out` can therefore only replace a task dir
with one that runs the same tickets in the same order.

Run-time conditions are Harbor flags, not compile options: `-a claude-code|codex|...`,
`-m <model>`, `--resume-trajectory` (conversation carried) or not (fresh session per
segment, only files carry).

## 7. Accounting (`odyssey account <jobs-dir>`)

Reads each trial's `result.json`, `odyssey.json` (copied into the task) and
`steps/<name>/agent/trajectory.json` (ATIF) and emits one row per segment:

`job, trial, sequence, arm, ordering_id, position, ticket, cluster, role, agent, model,
resumed, reward, n_llm_calls, n_tool_calls, prompt_tokens, cached_tokens,
cache_write_tokens, completion_tokens, incremental_tokens, sdc_usd, reported_cost_usd,
agent_seconds, exception`, followed by `step_name, ordering_length, it_delta, it_content,
it_startup_tokens, sdc_neutral_usd, compactions, reported_cost_scope, warnings,
it_startup_main_tokens, it_delta_net, grader_error, egress_allowed, egress_denied,
egress_denied_targets` (the last three only for `--egress allowlist` tasks, section 11).

- Labels come from the manifest **by step name only**. Step names carry the ticket id,
  so a step whose name is not in the task dir's current manifest (recompiled since the
  run) gets an empty ticket and a warning instead of whatever now sits at that position.
- A planned segment the trial never reached (Harbor stops after a failed setup) still
  gets a row, with `exception = not_run` and no reward.
- `grader_error` is test.sh's `error` flag: the reward of such a row says nothing
  about the agent.

- **Incremental tokens (IT)**: completion tokens plus context that enters for the first
  time in this segment (history that is merely re-sent is excluded). `it_delta` counts
  the whole first prompt of a fresh session, which is mostly the harness's system
  prompt and tool schemas (about 17.8k tokens for Claude Code); `it_startup_main_tokens`
  is that part and `it_delta_net = it_delta - it_startup_main_tokens`. A subagent's first
  prompt is real new context and stays in both. Subagent (sidechain) steps arrive from
  Harbor 0.22 without an agent id; each sidechain user step (the Task prompt) starts a
  new chain and later calls are matched to the chain their cache read continues.
- **Standardized dollar cost (SDC)**: tokens priced from the frozen `prices.toml`,
  separating fresh input, cache write, cache read and output.
- Each ATIF call is attributed to the first segment whose trajectory contains it
  (copied-context steps and calls already seen in an earlier segment's trajectory are
  skipped). The step's execution window only produces warnings: Harbor stamps it with
  the host clock and ATIF uses the container's, so filtering on it would drop real calls
  whenever the clocks disagree. Under resume the segments must add up to the session's
  last trajectory, and a warning says when they do not. Harness-reported per-step totals
  are not trusted.
  Measured: under `--resume-trajectory` both Claude Code and Codex
  append to one session file, so Harbor's per-step tokens **and** `cost_usd` are
  session-cumulative. `reported_cost_usd` is therefore differenced against the previous
  step when the harness token totals show a cumulative scope; the extra column
  `reported_cost_scope` (`segment` | `cumulative` | `unknown`) records which applied.

## 8. Analysis (`odyssey analyze segments.csv`)

- Default metric `it_delta_net` (section 7); the report also shows the pooled ratios on
  gross `it_delta`, so the share that is harness start-up overhead is visible.
- Per ticket: cold success rate, cold geometric-mean IT and SDC.
- Warm/cold ratio per ticket and pooled (geometric), with a bootstrap over sessions.
  The cold baseline of a warm condition comes from **its own arm**: position-1 segments
  of that arm (`--cold exposure0`, default; resume and fresh sessions pooled, since
  their first segments are the same run), or one-ticket orderings whose arm starts the
  same way (`--cold singleton`: same single-defect-or-not and same notes file). Each
  bootstrap replicate averages over the point estimate's tickets; a replicate missing
  one of them is invalid, and the invalid share is reported.
- Position curve; related-predecessor contrast (same cluster seen before or not).
- Failures: reported both ways (complete-case and PAR-2 penalised). `not_run` rows count
  as failures; `grader_error` rows and rows without a ticket are excluded and counted.
- single-defect arms are flagged: their warm ratios must not be pooled with other arms.

## 9. Non-goals for v0

Harness memory-directory persistence across fresh sessions, the in-container ticket
dispenser, separate-container verification and the censored mixed model are designed
for but not built in v0.

## 10. Known limitations

Real problems found in review that v0 deliberately does not fix, and why.

1. **The agent runs as root, and an in-container grader cannot be made safe against a
   root agent.** The fixes in sections 4 and 5 close the routes that were demonstrated
   (agent test config, conftest/pytest shadowing, deleted visible tests, a doctored base
   tarball, git config hooks, left-behind processes), but a root agent can still change
   the toolchain the grader runs: the Python interpreter, pytest in site-packages, `git`,
   coreutils, or the grader's own state under `/var/lib/odyssey-grader`. Not fixed
   because both remedies need Docker to build and test, which was unavailable: (a)
   separate-container verification (Harbor `environment_mode = "separate"`, the agent's
   tree collected as an artifact and graded in a fresh container), the real fix; (b) a
   non-root agent via `[agent] user`, which in Harbor 0.22 also runs `setup.sh` as that
   user while Harbor uploads the step's workdir as root, so `setup.sh` could not remove
   its own payload. Treat v0 rewards as "not gamed by accident or by easy tricks", and
   review `segment.patch` for runs that matter.
2. **Code under test runs inside the grader's test process.** A fix that imports and
   then subverts pytest (for example by writing a fake junit report and exiting 0) is
   indistinguishable from a pass in-process. The exit-code-plus-junit rule stops the
   simple form. Separate verification does not change this; log review does.
3. **The process sweep is untested inside a real container.** Its Linux (`/proc`) branch
   has not run; only the host fallback (ps, restricted to a test token) is covered by the
   tests. It also kills anything started more than 5 s after boot, so a harness that
   keeps an in-container process across steps (terminus-2's tmux session) or an image
   that starts services lazily does not work with these step scripts.
4. **The setting is still recognisable as an evaluation.** `/opt/odyssey/base.tar.gz`,
   `/tests`, `/logs` and (under binary feedback) `/var/odyssey/feedback` exist under
   natural framing too. The position and the experiment name are no longer written
   anywhere the agent can read.
5. **single-defect leaks by construction** (section 4). It is refused for warm
   orderings unless `--allow-leak`, and flagged in analysis, not made safe.
6. **Accounting attribution limits.** Calls a timed-out agent makes after its step
   ended surface in the next segment's trajectory and are charged to that segment (with
   a warning), not to the segment that made them. Interleaved subagents whose cache
   reads do not identify them can still be put on the wrong chain. Harbor's converter
   copies `agent_id` but Claude Code writes `agentId`; that should be reported upstream.
   `sdc_usd` still includes the fresh-session start-up prompt (only `it_delta_net`
   removes it), so compare fresh and resume on `it_delta_net` or `sdc_neutral_usd`.
7. **Labels still come from the task dir on disk.** Strict step-name matching and
   content-derived ordering ids prevent relabelling a trial with other tickets, but
   editing a ticket's cluster or role and recompiling changes the labels of old trials.
   Comparing Harbor's `task_checksum` would catch it but needs Harbor's `dirhash`
   dependency; not done.
8. **validate's clean-failure rule only reads the `details.json` shape of section 3**;
   other graders are judged on exit codes and the check says so. The subset sweep is
   opt-in because it costs 2^(T-1) grader runs per ticket; for real sequences, where one
   grader run takes a minute, `--subset-sample` checks a stated sample, so start states
   outside the sample are unchecked.
9. **The onboarding-notes arm is "onboarded", not "no hints".** `ORACLE_NOTES.md` no
   longer states any fix or points at a fix site, but some conventions it describes are
   relevant to open tickets, as real onboarding notes would be.

## 11. Network allowlist for the agent container (`--egress allowlist`)

Real sequences are reverted merged fixes of public repositories, and the agent's
container needs the internet to reach its model, so with open egress an agent can fetch
the upstream fix. `odyssey compile --egress allowlist` (opt-in; default `open`, which
leaves every compiled file byte-identical to a compile without the option) confines the
agent container to the model APIs.

### Mechanism: Harbor 0.22's own egress control

Harbor 0.22 already implements per-task network policy for the docker environment; the
option only turns it on and adds logging. Facts from the installed source
(`harbor/` = `site-packages/harbor`, version 0.22.0):

- `[environment] network_mode` is `public | no-network | allowlist` with
  `allowed_hosts` (`models/task/config.py:36`, `:246`); `[agent]` and `[verifier]` can
  override it per phase (`:218`, resolved in `trial/network_policy.py:147`).
  `allow_internet` is a deprecated alias mapped to `public`/`no-network` (`:470`, `:851`).
- Any non-public policy makes the docker environment add a sidecar,
  `harbor-docker-egress-control-sidecar` (`environments/docker/docker.py:266`), after a
  kernel probe for nftables `fib` support; without it, an allowlist task is rejected,
  not run open (`environments/base.py:768`).
- Compose files are applied as: Harbor's build file, the task's
  `environment/docker-compose.yaml`, mounts/env overlays, then the egress overlay last
  (`docker.py:316`); `--project-directory` is `environment/` (`:620`). Every service that
  declares no `network_mode`/`networks`, including `main`, is put into the sidecar's
  network namespace (`network_mode: service:<sidecar>`, `:381`, `:422`). Harbor execs
  into `main` with `docker compose exec main bash -c` (`:1141`).
- The sidecar (`environments/docker/harbor-docker-egress-control-sidecar/`, image
  `gogost/gost:3.2.7-nightly.20260602@sha256:afc01377…`) installs nftables rules in that
  shared namespace (`bin/network-policy`): every TCP connection not to a local address is
  redirected to a transparent gost proxy, which reads the TLS SNI or HTTP Host and admits
  only allowlisted names (`gost.yaml`, red handler with sniffing, whitelist bypass); all
  other non-loopback traffic is rejected except ICMP. The agent's container has no
  `NET_ADMIN`, so it cannot change the rules.
- Phase switches go through `network-policy` in the sidecar (`docker.py:1186`,
  `trial/trial.py:262`); a policy equal to the baseline is never switched.
- Agent installs happen in `setup()` before step 1 under the `[environment]` baseline.
  Claude Code's installer is skipped when `claude` is on PATH, else it downloads from
  `downloads.claude.ai` (or npm) (`agents/installed/claude_code.py:422-462`); Codex's is
  skipped when `codex` is on PATH, else it fetches nvm from `raw.githubusercontent.com`,
  Node from `nodejs.org` and the package from npm (`agents/installed/codex.py:330-384`).
  Harbor sets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` for Claude Code
  (`claude_code.py:1830`). Both CLIs also honour a base-URL setting
  (`ANTHROPIC_BASE_URL`; Codex `openai_base_url`), which the allowlist does not need: the
  proxy is transparent, so no `HTTPS_PROXY` support is required of the harness.
- Multi-step order per step: upload `workdir/`, `setup.sh`, agent phase, artifact
  collection, verifier, archive into `steps/<name>/` (`trial/multi_step.py:91`, `:402`).
  An artifact entry with `service = "<sidecar>"` is copied out of that service's own
  filesystem (`models/task/config.py:639`, `trial/trial.py:1246`).

What `--egress allowlist` writes into the task:

| file | change |
|---|---|
| `task.toml` | `[environment] network_mode = "allowlist"`, `allowed_hosts = ["api.anthropic.com", "api.openai.com"]`; `[verifier] network_mode = "no-network"` (grading needs no network; validate `--no-network` proves it per sequence); top-level `artifacts` entry collecting `/var/log/odyssey-egress.jsonl` from the sidecar as `odyssey-egress.jsonl` |
| `environment/Dockerfile` | the sequence's Dockerfile plus an appended block (`odyssey/templates/egress/Dockerfile.agent-cli`) that copies Node 22.23.4 from `node:22.23.4-bookworm-slim@sha256:5ec23b7c…` into `/opt/agent-cli` and installs `@anthropic-ai/claude-code@2.1.322` and `@openai/codex@0.167.0` there (build args `AGENT_CLI_*_VERSION`), with `claude` and `codex` on PATH. The trial therefore installs nothing; a project's own `node` is untouched |
| `environment/docker-compose.yaml` | `main`: `cap_drop: [NET_RAW]` (an `AF_PACKET` socket would bypass the netfilter hooks). Sidecar: `net.ipv4.ping_group_range = "1 0"` (no unprivileged ICMP sockets in the shared namespace, so with `NET_RAW` dropped the agent cannot send ICMP, which Harbor's rules let through), and a bind mount of our `gost.yaml` |
| `environment/odyssey-egress/gost.yaml` | Harbor's `gost.yaml` verbatim (a test checks it against the installed Harbor) plus `log: {output: /var/log/odyssey-egress.jsonl, level: debug, format: json}` |
| `environment/.dockerignore` | the two additions are excluded from the image's build context |
| `odyssey.json` | arm `…-egress`; `arm_options.egress = {mode, allowed_hosts}` |

Compile refuses (and writes nothing) when the sequence sets `allow_internet = false`, uses
a prebuilt `docker_image` (no Dockerfile to extend), has a `USER` instruction, or already
ships a compose file. Installing a different CLI version at run time
(`--ak version=...`) fails closed: the install hosts are blocked.

The allowlist is the two API hosts the CLIs call with an API key, nothing else: no
wildcard, no code host, package index or search engine. Harbor's run flags
`--allow-environment-host` / `--allow-agent-host` widen it for one run;
`odyssey account` warns on every row of such a trial.

### Connection log and accounting

gost logs every connection decision in the sidecar's filesystem (the agent shares only
the network namespace, not the mount namespace): `proxy: <host:port>` for an admitted
connection, `bypass: <host:port>` for a refused one. Harbor copies the cumulative log
after each step's agent phase to `steps/<name>/artifacts/odyssey-egress.jsonl`
(`artifacts/` for a one-step trial; the agent's own `/logs/artifacts` mount is a
different subdirectory, so it cannot plant this file). `odyssey account` differences
consecutive snapshots and adds per segment `egress_allowed`, `egress_denied` (each
connection once, by gost's session id) and `egress_denied_targets`
(`github.com:443=2 pypi.org:443=1`). A segment covers what was logged after the previous
step's collection: its own `setup.sh` and agent phase, and the previous step's verifier
(which has no network). Denials by the harness itself (for example an update check) show
up here too; read the targets before calling a denial an agent's attempt.

### Measured on the build box (httrack-2026, `scripts/egress/box_check.sh`)

Run on 2026-11-10 (Harbor 0.22.0, Docker 29.9.3, x86_64); evidence pulled to
`jobs/egress-box/` (gitignored). No API key was read or passed and no model was called;
provider reachability is an unauthenticated request answered with 401.

| check | result |
|---|---|
| oracle, cold HTT-01 and ordering HTT-01,HTT-02,HTT-03 (forward-merge) | reward 1, error 0 on every step: grading works with the verifier at `no-network` |
| nop, same tasks | reward 0, error 0 on every step |
| `harbor run -a claude-code --install-only` / `-a codex --install-only` | "Claude Code is already available" / "Codex is already available"; agent setup took 0.3 s / 0.7 s, nothing downloaded |
| `probe_agent.py` in the agent phase (cold, and each of the 3 steps) | 33/33 probes as expected in all four: api.anthropic.com and api.openai.com answer 401; github.com, a repo page, raw.githubusercontent.com, codeload, api.github.com, pypi.org, registry.npmjs.org, google.com blocked over HTTPS and HTTP; `git ls-remote` over https and git:// blocked; raw IPs (TCP 22/80/443, HTTPS to a GitHub address) blocked; UDP and TCP DNS to 8.8.8.8 / 1.1.1.1 and DNS over HTTPS (by name and by IP) blocked; UDP NTP blocked; SNI and Host spoofing and `--connect-to` through api.anthropic.com return no GitHub content; AF_PACKET and raw IP sockets refused; ICMP echo refused; `claude --version` 2.1.322 and `codex --version` 0.167.0 run |
| control: the same probe on the default open compile | 23 of the `deny_*` probes got through (including `git ls-remote` of the upstream httrack repo, raw.githubusercontent.com 200, and GitHub content through the SNI front), and the CLIs are absent |
| `odyssey account` on those jobs | probe segments: `egress_allowed` 3, `egress_denied` 22 each, per step of the 3-step trial (snapshots differenced correctly); oracle and nop segments: 0 and 0 (setup, grading and Harbor itself make no connections) |

Why the two compose additions: in an experiment with Harbor's sidecar but without the
`ping_group_range` sysctl (`jobs/egress-box/experiments/exp1`), the agent container got
ICMP echo replies from 8.8.8.8; with Docker's default capabilities (the open control)
root in the container opens AF_PACKET and raw IP sockets. The allowlist was not run
without `cap_drop: [NET_RAW]`, so a link-layer bypass of the sidecar's rules is reasoned
(AF_PACKET frames skip netfilter's IP hooks), not demonstrated.

### What it does not stop

- **Knowledge in the model's weights.** A model that saw the upstream fix in training can
  reproduce it; httrack-2026's fixes postdate the models' cutoffs, which is the defence
  against that, not this option.
- **An allowed host used as a relay.** Anything reachable through api.anthropic.com or
  api.openai.com stays reachable: a provider-side tool that fetches URLs (web fetch or
  search tools enabled by the harness or requested in the API call), or files the agent
  sends to and gets back from the API. The allowlist checks names, not content. Disable
  server-side tools in the harness configuration for runs that matter, and review
  trajectories.
- **SNI and Host spoofing do not help**: the proxy dials the name it sniffed, not the
  address the client chose (measured: TLS with SNI `api.anthropic.com` sent to a GitHub
  address reached Anthropic, not GitHub). A client that sends no SNI is judged by its
  destination IP, which is never allowlisted.
- **DNS answers.** Name resolution goes through Docker's embedded resolver, which
  resolves any name (measured: `github.com` resolves, a random name gets NXDOMAIN).
  Resolved addresses are useless without a connection, and other resolvers (UDP/TCP 53,
  DNS over HTTPS) are blocked, but a recursive resolver is a narrow data channel: an
  agent could exchange data with an authoritative DNS server someone else controls
  (a DNS tunnel). It cannot fetch GitHub or PyPI content that way.
- **Logged denials are attempts, not proof of intent**, and an agent that never tries
  leaves no trace.
- **Harbor's own sidecar is trusted**: the rules and proxy are Harbor 0.22's, and the
  `gost.yaml` override must be re-derived if Harbor changes that file (the test fails).
  The image build itself has network access (it fetches the repository snapshot and the
  CLIs); only the running container is confined.
