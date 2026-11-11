# Adding a ticket to atlas-commerce

This is the contract for a ticket directory, the commands that check one ticket on its own, and
the pitfalls met while building the base and ATL-2. Read README.md first for what the sequence is;
you should not need to read the step scripts or the controller to add a ticket.

## 1. The model in one paragraph

atlas-commerce is one repository (`/app`, host copy `environment/repo/`) and one LocalStack
Community 4.14.0 account. A ticket is a change to that repository plus its deployment. Before the
ticket at position k, the step's setup resets the platform (LocalStack, proxy and deny-guard
restarted, Lambda containers removed, new agent credentials), rebuilds `/app` as the base snapshot
plus the **reference changes of the tickets at positions 1..k-1** (`gold.patch` of each), and runs
`python3 deploy/deploy.py --all` from that tree as the agent's identity. So the start state of a
ticket depends only on which other tickets came before it, never on what an agent did, and never
contains anything of a ticket not yet given (code state `forward-merge-live`). The ticket's grader
then runs against the live platform after the agent's turn.

## 2. Sequence layout

```
sequences/atlas-commerce/
  sequence.toml                 ids, titles, cluster (relatedness), role; loadable by odyssey.spec
  environment/                  the Harbor build context, copied into every compiled task
    Dockerfile                  targets `agent` (main) and `controller`
    docker-compose.yaml         main, atlas-controller, localstack-proxy, deny-guard, localstack-backend
    repo/                       /app at the start: the base snapshot (also base_tree for the manifest)
    platform/                   proxy, deny-guard, builder policy, the PID-1 guard of main
    controller/controller.py    the trusted reset controller
  tickets/<ID>/                 one directory per ticket (section 3)
  testing/tickets/ATL-PH/       a placeholder ticket, only for testing the machinery (--extra-tickets)
  AUTHORING.md, README.md
scripts/atlas/
  compile_atlas.py              compile orderings / validation plans; `check` = static checks
  validate_ticket.py            one ticket's validation through Harbor (box)
  probe_agent.py, check_probe.py  the reset / leak / forgery probe (box; check_probe.py: ATL-2,ATL-PH)
  probe_marks.py, check_probe_seq.py  the same probe over any ordering of the real tickets
  sequence_report.py            oracle/nop ordering trials + validation reports -> verdicts, independence
  timings.py, cleanup_job.py    timings by start state and ticket; Docker sweep after a harbor run
  templates/                    setup.sh, test.sh, solve.sh, lib_ctl.sh (+ odyssey's lib_sweep.sh, lib_manifest.sh)
  grader_lib/                   shipped to /tests/atlas/ in every step: atlas_grader.py, atlas_run.py,
                                atlas_pytest.py, verdict.py
  test_compile_atlas.py         offline tests
```

## 3. The ticket directory contract

```
tickets/<ID>/
  ticket.md            the work request shown to the agent
  gold.patch           the reference change to /app (git diff against environment/repo)
  ticket.toml          timeouts, reference deploy, grading lists, known-bad variants
  tests/run.sh         the grader's entry point (executable)
  tests/...            everything else the grader needs (pytest files, scope files, helpers)
  known_bad/<name>.patch   one file per known-bad variant
```

Plus one `[[tickets]]` entry in `sequence.toml`, which is the only place title, cluster and role
are read from (designs and `odyssey account` use it). The sequence integrator owns that file: a new
ticket's author proposes the entry and the integrator adds it, so parallel authors never edit it
at once. A `ticket.toml` may repeat `title`, `cluster` and `role` under `[ticket]` (needed when a
ticket is loaded with `--extra-tickets`); `compile_atlas.py check` refuses any disagreement.

```toml
[[tickets]]
id = "ATL-4"                       # [A-Za-z0-9._-], the directory name
title = "Bulk fulfilment is broken in production"
cluster = "orders-consumers"       # same label = related (siblings); unique label = unrelated
role = "trap"                      # core | trap | isolated (carried to analysis)
```

### ticket.md

A realistic work request: what is wrong or missing, why it matters, what "done" looks like. Write
it as the platform team would. It **must state every behaviour the grader checks**: resource names,
response shapes, field types and exact values, time limits, what must not change. Read the grader
line by line and make sure each assertion can be inferred from the text. Do not name the fix,
the grader's file or test names or benchmark ids. Point at the repository
conventions the agent needs (`deploy/deploy.py --component <name>`, `iam/<role>.json`). See
`tickets/ATL-2/ticket.md`.

### gold.patch

- `git diff` against `environment/repo/` (paths `a/...`, `b/...`), applied with `git apply` in `/app`.
- It must apply to the base alone **and** together with every other ticket's gold patch in any
  order: two gold patches may not touch the same file (`compile_atlas.py check` refuses it). If two
  tickets need to change one place, give one of them its own file: e.g. ATL-3's role policy is
  `iam/orders-consumer-role.json`, which `deploy/components/orders_core.py` already applies when
  present, so ATL-2 (handler) and ATL-3 (policy) touch disjoint files.
- New infrastructure goes in a new component, `deploy/components/<name>.py`, with a module
  docstring (first line = summary), an integer `ORDER` (base components use 10, 20, 30; use 40-90)
  and an idempotent `deploy()` built from `deploy/support.py` (`ensure_role`, `ensure_queue`,
  `ensure_table`, `ensure_function`, `ensure_sqs_mapping`, `package_dir`, `package_files`, `client`).
  Once the gold patch is merged, every later step's setup deploys it with `--all`.
- Deploy-only: no self-tests, no convergence waits beyond "the resource is ready", no direct writes
  to data the platform protects (section 6). Lambda code is packaged by `support.package_dir` from
  the repository, so graders can compare `CodeSha256` with what the repository builds.
- Keep the resource names the ticket states: the graders hard-code them.

### ticket.toml

```toml
[ticket]
kind = "code"                    # deploy | code | repair | policy (documentation)
agent_timeout_sec = 3600         # written per step into task.toml ([steps.agent])
verifier_timeout_sec = 600       # [steps.verifier]; run.sh gets 80% of it as a hard deadline

[reference]
deploy = ["orders_core"]         # oracle: after gold.patch, deploy.py --component <each>

[grading]                        # JUnit test names (pytest function names, with [param] ids)
fail_to_pass = ["test_..."]      # must fail cleanly when the agent does nothing, from every start state
pass_to_pass = ["test_..."]      # must pass when the agent does nothing

[[known_bad]]                    # zero or more; each must score 0
name = "no-created-at"           # [a-z0-9][a-z0-9_-]*
patch = "known_bad/no-created-at.patch"   # a gold.patch-style patch against the base, used INSTEAD of gold
evidence = "where this failure was seen"
must_fail = ["test_..."]         # the checks this variant is meant to fail (each must be in [grading])
# deploy = [...]                 # optional; defaults to [reference].deploy
```

Checks of a test runner that is not pytest (the wildcard guard) appear under `other` in
`details.json`; listing them in `[grading]` is optional.

A `pytest.fail()` raised while a test's fixtures are set up (a fixture's way of saying "the
pipeline never converged", e.g. ATL-5's `pipeline_state`) is recorded as that test's failure,
not an error: atlas_run.py runs pytest through `grader_lib/atlas_pytest.py`, which relabels exactly
that case. Every other setup exception stays an error, which the validator treats as a broken
grader. No ticket needs a conftest.py for this.

### tests/run.sh

Harbor uploads the step's `tests/` after the agent's turn; the generated `test.sh` copies the
agent's `/app` to a grading copy and runs `bash /tests/ticket/run.sh` with:

| variable | value |
|---|---|
| cwd, `ATLAS_REPO` | the grading copy of the agent's `/app` (read it as data) |
| `ATLAS_TICKET_DIR` | `/tests/ticket` (this `tests/` directory) |
| `ATLAS_LIB` | `/tests/atlas` (`atlas_grader.py`, `atlas_run.py`) |
| `ATLAS_PYTHON` | `/opt/atlas-grader/bin/python`: pytest 8.4.1, pytest-json-ctrf 0.3.5, boto3/botocore 1.35.99, requests 2.32.3, pyyaml 6.0.2 |
| `ATLAS_OUT` | a root-only results directory; write reports here, never to `/logs/verifier` |
| `ATLAS_GRADING` | the `[grading]` lists as JSON |
| `AWS_SHARED_CREDENTIALS_FILE` | profile `default` = the agent's builder key (`ProvisioningOperator`); profile `verifier` = a grader-only key |
| `ATLAS_VERIFIER_PROFILE` | `verifier` |
| `AWS_ENDPOINT_URL`, `AWS_DEFAULT_REGION` | `http://localstack:4566` (the proxy), `us-east-1` |

Exit 0 means pass. Record every check through `atlas_run.py`, because the verdict needs **both**
exit 0 and JUnit reports in `$ATLAS_OUT` showing at least one test and no failure, error or skip:

```bash
#!/usr/bin/env bash
set -u
run() { "$ATLAS_PYTHON" -I -B "$ATLAS_LIB/atlas_run.py" "$@"; }
run pytest --name main "$ATLAS_TICKET_DIR/test_outputs.py"          # -> junit-main.xml
status=$?
if [ "$status" -eq 0 ]; then                                          # guard only after pytest passes
    run check --name iam_wildcard_role_guard -- "$ATLAS_PYTHON" -I -B "$ATLAS_TICKET_DIR/iam_wildcard_role_guard.py"
    status=$?
fi
run details                                                           # -> details.json (F2P/P2P)
exit "$status"
```

Rules for graders:

- **No network at grade time.** Use `$ATLAS_PYTHON`; never `uvx`, `pip install` or `curl`. A
  missing package goes into the Dockerfile's grader venv.
- **Two identities.** Tests that write no protected data use the default profile, which is the builder, so
  their identity assertions (`...:user/ProvisioningOperator`) and builder-bypass assertions hold.
  Anything that must write protected data (send to `orders.fifo`, put to a protected table, invoke a
  protected function) uses `atlas_grader.client(service, verifier=True)`; deny-guard does not
  restrict that key. The key exists only during the verifier and is revoked afterwards.
- **Drive fresh inputs.** Name every input with a new uuid and wait for its effect; never read
  state the agent could have pre-written. Drain queues you assert to be empty before the checks
  (as `test_atl2.py`'s `queues` fixture), so the agent's own experiments do not count against it.
- **Do not import the agent's code into the grader process.** Check behaviour on the deployed
  function (invoke it with the verifier key, as `test_atl2.py` does) and check that what is
  deployed is the repository's code (`atlas_grader.code_sha256(atlas_grader.package_dir(...))`
  against `CodeSha256`). If you ever must import repository code, run it as `nobody` in a
  subprocess with a copy outside `ATLAS_OUT`.
- **Account-wide checks are pass-to-pass invariants.** The wildcard-role guard lists every role;
  ATL-6's checks walk every Lambda; ATL-5 expects exactly one mapping on `payments-inbound`; ATL-6
  matches queue names by substring (`'FulfillmentQueue' in q`). Every reference and the base must
  keep satisfying all of them; a new reference that creates a universal-allow role, a second
  mapping on `payments-inbound` or a queue whose name contains another ticket's substring breaks
  other tickets.
- **Later checks must not pass because of earlier references.** Every fail-to-pass check must
  name resources of its own ticket, so that no other ticket's gold makes it pass (validated over
  subsets, section 5).
- A test that exercises another ticket's resource may be re-driven (as ATL-2 sends its end-to-end
  messages straight to the queue instead of through the API that belongs to ATL-1), but never
  weakened. Say what a test checks in the test file's docstring.

### known_bad/*.patch

A complete alternative to `gold.patch` (against the base), deployed the same way. Where a model
failure on the same work is documented, reproduce it as one of the variants. Make one by applying gold.patch to a copy of
`environment/repo`, introducing the defect, and diffing against the base:

```sh
T=$(mktemp -d); cp -R sequences/atlas-commerce/environment/repo/. $T; cd $T
git init -q && git add -A && git -c user.name=x -c user.email=x@x commit -qm base
git apply <ticket>/gold.patch && <edit> && git diff > <ticket>/known_bad/<name>.patch
```

### Start-state requirements

A ticket may rely only on the base (components `orders_core`, `fulfillment`, `payments_core`; see
`environment/repo/README.md`) and on nothing any other ticket adds: every ticket must pass from the
base alone. If a ticket needs more starting infrastructure (for example a pre-seeded queue), add it
to the base in `environment/repo/` (and say so in README.md), then re-run the static check and
**every** existing ticket's validation, because the base is everyone's start state.

## 4. What the agent sees and what it does not

- `/app` (base + merged references, one commit by "Maintainers"), `/memory` and the repository's
  `CLAUDE.md`/`AGENTS.md`/`NOTES.md` (both kept across tickets), the AWS endpoint as
  `ProvisioningOperator`, and the instruction (framing header + `ticket.md`).
- Never: other tickets' text, gold patches, graders or known-bads (they reach a step only in that
  step's `solution/` for the oracle, `tests/` after the agent's turn, or a later step's merged
  state); controller tokens (hashed, in the controller only); the grader key; LocalStack's root
  credentials (they never leave the LocalStack container); the controller, LocalStack and
  deny-guard hostnames (main is on `agent_net` only).
- Carries across tickets (by design or as a known limit): `/memory`, repository memory files,
  `/tmp`, `/root`, `/var/tmp` and anything installed with apt/pip/npm (Harbor's agents keep their own
  state under `/tmp` and `$HOME`, so setup does not wipe them).

## 5. Commands

Locally (no Docker):

```sh
uv run --python 3.12 python scripts/atlas/compile_atlas.py check sequences/atlas-commerce
uv run --python 3.12 python -m pytest -q scripts/atlas/test_compile_atlas.py
```

On a Docker host with Harbor installed (the repository copied to `~/atlas/<label>/repo`), **one ticket alone**:

```sh
cd ~/atlas/<label>/repo && export PATH=$HOME/.local/bin:$PATH
uv run -q --python 3.12 python scripts/atlas/validate_ticket.py ATL-4 \
    --work ~/atlas/<label>/validate --offline [--subset-sample 8 --seed 0] [--repeat-reference 2]
```

This compiles one Harbor task whose steps are the conditions and runs it with the oracle agent:
nop at the base (reward 0; every fail-to-pass test fails, every pass-to-pass passes), the
reference at the base (1), each known-bad at the base (0, with its `must_fail` checks failing),
and for every subset of the other tickets (or the stated sample of K, chosen as: each single other
ticket, each "all but one", all, then a seeded random fill) nop (0, the same clean failure) and the
reference (1). Every condition starts with a full reset and redeploy, and every step must also have
no grader error and no tampering flag. `--offline` makes `agent_net` internal, which proves setup
and grading need no internet. It prints one line per condition, writes
`<work>/<job>.report.json`, removes the trial's Docker leftovers, and exits 0 only if every
condition holds. Cost for ATL-2: 6.0 min for 8 conditions including the environment start (a
grader with longer live waits costs more per condition). Use `--others` to restrict the subset
pool while other tickets are unfinished. Keep at most two Harbor runs at a time on the box and run
a failed reference condition once more before concluding (live checks are load-sensitive).

A sequence run (one ordering = one task; `--design williams` for the full rotation):

```sh
uv run -q --python 3.12 python scripts/atlas/compile_atlas.py compile sequences/atlas-commerce \
    --out ~/atlas/<label>/tasks --ordering ATL-2,ATL-4,ATL-1
harbor run -p <task> -a oracle -n 1 -o <jobs>              # every step 1
harbor run -p <task> -a nop -n 1 -o <jobs>                 # every step 0
harbor run -p <task> -a claude-code -m anthropic/<model> --resume-trajectory -o <jobs>
uv run odyssey account <jobs> --out segments.csv           # one row per segment, unchanged tooling
```

The reset/leak/forgery probe (ordering `ATL-2,ATL-PH`, needs `--extra-tickets
sequences/atlas-commerce/testing/tickets`):

```sh
PYTHONPATH=scripts/atlas harbor run -p <task> --agent-import-path probe_agent:AtlasProbe -n 1 -o <jobs>
# resumable variant: --agent-import-path probe_agent:AtlasResumeProbe --resume-trajectory
# (Harbor 0.22 warns that --agent-import-path is deprecated in favour of --agent; it still works)
python3 scripts/atlas/check_probe.py <jobs>/<job>/<trial>
```

## 6. Pitfalls

- **Protected data paths.** The builder policy (`environment/platform/builder-policy.json`)
  denies the builder key: SendMessage to
  `orders.fifo`; Put/Update/BatchWrite on `orders`, `FulfillmentTable`, `pipeline-events`;
  Put/Update/Delete/BatchWrite/TransactWrite on `payments` and `merchant_rollups`; Invoke of
  `orders-consumer` and `pipeline-processor`; and (deny-guard) `sts:AssumeRole*`. Deployments and
  oracles run as the builder, so they cannot do any of that either; graders use the verifier key.
- **Restart is the only reset.** Community has no state reset endpoint; the controller restarts
  LocalStack (about 3-5 s to healthy) and removes Lambda containers, whose names are LocalStack's
  container name with `_` replaced by `-` plus `-lambda-<function>-<hash>`.
- **The first REST API.** With ATL-1's `orders-api` in the account, `get_rest_apis()["items"][0]`
  is not guaranteed to be `OrdersAPI`, so ATL-4's `api_url` fixture selects `OrdersAPI` by name (falling back to
  `apis[0]`); select by name in any new grader too.
- **CodeSha256.** LocalStack 4.14 reports the base64 SHA-256 of the uploaded zip: ATL-2's
  parity check passed in every validation condition (base, reference, every known-bad), all
  deployed with `deploy.py`. A deterministic zip (`support._zip`) is what makes repository-parity
  checks possible; do not package with the `zip` tool.
- **Timing.** A whole setup (reset + tree + deploy of the base) took 15.8-27.4 s (median 18.7 s
  over 35 steps; the reset itself 5.3-8.8 s); each merged component adds its deploy time. Graders with 45-60 s live deadlines flake under load; re-run a
  failed reference once before drawing a conclusion, and keep box load low.
- **`/logs/verifier` belongs to test.sh.** Anything else found there is treated as a planted
  verdict (tampered, reward 0). run.sh writes to `$ATLAS_OUT` only.
- **Background processes do not survive into grading.** main's PID 1 kills every process the
  agent left running when Harbor starts the verifier; a ticket whose grader needs a process the
  agent started (for example a local server on port 3000) cannot be graded here.
- **Harbor needs the verifier outputs writable.** test.sh makes everything it writes under
  `/logs/verifier` world-writable; a root-owned subdirectory aborted a trial once
  (`PermissionError` while Harbor archived the step).
- **Per-step timeouts are always written.** Harbor 0.22 uses `[steps.verifier] timeout_sec`
  (default 600) for every step and falls back to `[agent]` only when `[steps.agent]` is unset.
- **Lambda runtimes are pulled by the host's Docker** on first use (`public.ecr.aws/lambda/python:*`),
  so a cold box needs network for the first trial even with `--offline`.
