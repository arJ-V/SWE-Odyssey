# atlas-commerce: one growing AWS product as a SWE-Odyssey sequence

atlas-commerce is one repository and one LocalStack Community 4.14.0 account: order intake,
bulk fulfilment and merchant payments. Its tickets are deploy, code, repair and policy changes to
that same product, all on token-free LocalStack. This directory holds the merged base system, the
per-step reset and redeploy, the one-trial Harbor compilation, verdict integrity, the ticket
contract (AUTHORING.md) and the seven tickets.

**Status (2026-11-11):** all seven tickets are built and pass the bar on the box with
the final foundation (Harbor 0.22.0, no model calls; section "Measured: integration"): every
ticket's reference scores 1 and do-nothing 0 from 20 distinct start states each (the base, all
six single other tickets, all six "all but one", all six together, and the prefixes of the 14
Williams rows), every known-bad (25 in all, 6 of them reproducing failures real models made) scores 0
on the checks it targets, all 14 Williams rows score oracle 1 and nop 0 on every step, the 7 cold
tasks likewise, a scripted probe over two full 7-ticket orderings passes 206/206 checks each
(no look-ahead, reset of leftovers, forgery, session resume), and `odyssey account` gives one
correctly labelled row per segment carrying the trusted verdict. No ticket was dropped; the
sibling pair, the trap pair and the isolated ticket all survive. Paid model runs were made on
11 Nov 2026 (Claude Code with Opus 5.5 and Codex with GPT-6.1 Sol: two cold runs per ticket and
warm sequences with session resume; smaller models are being run); results are in the top-level
`README.md`.

An independent validity re-check the same day (section "Measured: validity re-check") reproduced
these verdicts from fresh images and new start states, ran every known-bad with all six other
references merged too, and found two grader gaps that let an over-broad IAM grant score 1 (ATL-1's
API role, ATL-3's consumer role); both now have a resource-scope check, re-validated in full. It
also judged the trap relations weak (section "Tickets").

`testing/tickets/ATL-PH` is a placeholder used only to test the machinery; it is not in the pool
and is loaded only with `--extra-tickets`.

## Tickets

| id | title | kind | cluster | role | graded by (F2P / P2P) | known-bads | agent / verifier timeout |
|---|---|---|---|---|---|---|---|
| ATL-1 | Public order intake: `orders-api` POST /orders straight to `orders.fifo` | deploy | order-intake-api | trap (with ATL-4) | 13 / 6 + wildcard guard | stale-stage, constant-group-id, no-content-type, api-role-star | 3600 / 600 s |
| ATL-2 | Orders consumer: partial-batch failures, idempotent writes, exact amounts | code | orders-consumers | core (sibling of ATL-4) | 5 / 3 + guard | no-created-at, failed-shape, no-report-batch-item-failures, unconditional-put | 3600 / 600 s |
| ATL-3 | Least privilege for `orders-consumer-role` | policy | iam-scope | trap (source of ATL-3 -> ATL-7) | 2 / 2 + guard | adds-cmv, sqs-wildcard, not-deployed, policy-in-code, managed-sqs-exec-role | 1800 / 300 s |
| ATL-4 | Bulk fulfilment is broken in production | repair | orders-consumers | trap (with ATL-1; sibling of ATL-2) | 2 / 9 + guard | opus46-hand-serializer, stringify-collections, counter-not-idempotent, hotfix-outside-repo | 1800 / 600 s |
| ATL-5 | Payments ledger and merchant rollups to the cent | code | payments-ledger | core | 14 / 2 + guard | floor-per-payment, float-running-total, ledger-drops-payer | 2400 / 600 s |
| ATL-6 | Order workflow: `NewOrderRule` starts `OrderProcessor` | deploy | order-workflow | core | 10 / 1 + guard | raw-message-delivery, events-role-states-star, target-input-path, always-default-price | 1800 / 600 s |
| ATL-7 | Merchant document index: CloudFormation `pipeline-stack` | deploy | document-index | isolated | 12 / 3 + guard | no-url-decode, no-kms-grant, extra-dynamodb-actions | 1800 / 600 s |

Designed relations: the **sibling pair** ATL-2/ATL-4
(SQS-driven Lambdas writing order rows, idempotent by `order_id`; same cluster), the **trap pair**
ATL-1/ATL-4 (both "POST /orders on API Gateway": ATL-1 must be a direct `AWS` integration to SQS,
ATL-4 must keep `OrdersAPI` a Lambda integration), the secondary trap ATL-3 -> ATL-7 (strip
unlisted IAM actions, versus KMS grants that never fail at runtime on Community), and the
**isolated** ATL-7 (CloudFormation, S3 notifications, KMS: used by no other ticket). The Williams
design for these seven tickets (14 rows; checked on the compiled rows) has every ordered pair of
tickets adjacent exactly twice and before/after each other in 7 rows each, so each pair, the
sibling and trap pairs included, is seen in both relative orders.

How strong these relations are was judged by reading the texts (validity verifier, 2026-11-11;
nothing here is measured): the sibling pair is real (conditional writes keyed by `order_id`, field
and type fidelity, FIFO deduplication by order id). The **trap pair is weak**: ATL-4's text
says outright that `OrdersAPI` stays a Lambda integration and must not become a direct service
integration, and ATL-4's start state has no stale `prod` stage (the base component redeploys
it), so neither the "integration style" lesson nor the "redeploy the stage" lesson from ATL-1 is
in play. The **ATL-3 -> ATL-7 trap is also weak**: ATL-7's text lists the required
`kms:Decrypt`/`kms:GenerateDataKey` grants, and the two are in different clusters. ATL-7 is
isolated in what the agent builds (no other ticket uses CloudFormation), although the base's
fulfilment pipeline already shows S3 notifications and KMS-encrypted queues. Analyses that rely on
the `trap` role should treat it as a label, not as a measured interference.

Decisions taken at integration (each ticket author flagged them):
- **ATL-3 keeps its repository-parity check** (`test_consumer_role_policy_matches_repository`:
  the role's one inline policy equals `iam/orders-consumer-role.json`). It is the policy analogue
  of ATL-2's and ATL-4's code-parity checks and the ticket states it; it is why the `policy-in-code`
  known-bad scores 0.
- **ATL-6 keeps the SNS-envelope check** (`test_review_alert_arrives_in_standard_sns_envelope`).
  It requires the standard SNS envelope and does not accept a raw body; Community 4.14.0 honours `RawMessageDelivery`
  (measured by the ATL-6 author), so it needs no paid feature, and the ticket text states the format.
- **ATL-4's `api_url` fixture selects `OrdersAPI` by name** (falling back to the first API
  listed, `apis[0]`), because the account may also hold ATL-1's `orders-api`.
- ATL-1 and ATL-2 both have tests named `test_end_to_end_fifo_group_ordering` and
  `test_end_to_end_duplicate_post_is_idempotent` (API-driven in ATL-1, queue-driven in ATL-2).
  Test names are per ticket; anything that pools results by test name must key on ticket + name.
- **ATL-1 and ATL-3 gained a resource-scope check** after integration (validity verifier,
  2026-11-11). ATL-1: `test_api_role_sqs_grants_name_the_queue` (fail-to-pass, as the other API
  checks) fails an integration role with any SQS grant on `"Resource": "*"`; the reference with its
  SQS statement on `*` scored 1 before (measured), although the ticket asks to keep the role scoped
  to the queue; known-bad `api-role-star`. ATL-3:
  `test_consumer_role_queue_and_table_grants_name_their_resources` (pass-to-pass) fails a role
  whose SQS or DynamoDB grants cover `"Resource": "*"`, inline or attached. Without it, attaching
  the AWS-managed `AWSLambdaSQSQueueExecutionRole` (Receive/Delete/GetQueueAttributes on `*`, no
  ChangeMessageVisibility) beside a table-only `iam/orders-consumer-role.json` scored 1 (measured),
  although the ticket asks for the queue and table by ARN. The ticket text states it; known-bad
  `managed-sqs-exec-role` covers it.
- **ATL-5's text no longer carries the worked example** "three payments of 0.125 give
  `total_cents` = 37" (validity verifier): it was the grader's own test vector for a known
  failure (flooring per payment). The formula
  `floor(100 * sum(...))`, which the grader checks, is still stated. Text only; no grader change.

## How it works

One ordering compiles (`scripts/atlas/compile_atlas.py`) to one Harbor 0.22 multi-step task. All
steps share one set of containers, so `--resume-trajectory` carries the agent's session exactly
as in the codebase sequences.

| service | role | networks |
|---|---|---|
| `main` | the agent's workstation; `/app` is the repository; PID 1 is `platform/guard.py` | `agent_net` |
| `localstack-proxy` | credential proxy (`platform/localstack_proxy.py`): only allowlisted keys and attested Lambda sessions; `/_localstack/*` blocked except health | `agent_net`, `backend_net` |
| `deny-guard` | applies the Deny statements of the builder policy to the builder key | `backend_net` |
| `localstack-backend` | `localstack/localstack:4.14.0`, every service the components use | `backend_net` (Lambda containers too) |
| `atlas-controller` | trusted reset controller; holds the Docker socket | **none** (`network_mode: none`) |

**Per step** (code state `forward-merge-live`, arm prefix `fml`):

1. `setup.sh` (in main, before the agent): kill everything the previous agent left running; scrub
   `/tests`, `/solution`, `/logs/verifier`, the grader's scratch and cached AWS credentials; ask
   the controller to reset (a request file on the `ctl` volume carrying this step's setup token);
   the controller revokes every key, restarts LocalStack, the proxy and deny-guard (the proxy's 30 s
   session cache goes with it), removes Lambda containers, empties `/shared`, creates
   `ProvisioningOperator` with the builder policy and writes its new key; setup then rebuilds
   `/app` from `/opt/odyssey/base.tar.gz` (checked against the compiled manifest) plus the
   references of the tickets at earlier positions, renews `.git` (one neutral commit), keeps
   `/memory` and the repository memory files, and runs `deploy/deploy.py --all` as the new
   `ProvisioningOperator`.
2. The agent's turn.
3. Harbor's verifier phase: main's PID-1 guard sees Harbor's `chmod 777 /logs/verifier` (the
   start of every verifier phase, before `/tests` is uploaded) and kills every process the agent
   left; `test.sh` then sweeps again, rebuilds the trusted start tree, copies the agent's tree,
   saves `segment.patch`, obtains a grader-only key from the controller with the step's verify
   token (uploaded only now), runs the ticket's `run.sh` with results in a root-only directory,
   sweeps again, revokes the key, and writes `reward.json = {"reward", "error", "tampered"}` last.
   The verdict needs run.sh's exit code 0 **and** its JUnit reports showing a full pass; a
   disagreement, or any file in `/logs/verifier` the grader did not write, is `tampered = 1`
   with reward 0.

**Choice: a generator, not an `odyssey compile` extension.** The atlas step scripts (controller
requests, live redeploy, grader key, compose environment, per-ticket timeouts) are different
scripts, not options of the existing ones, so `odyssey/` is unchanged and every existing
sequence compiles byte for byte as before. The generator imports from odyssey the sequence loader,
the ordering designs and ids, step names, the manifest builder, the base-tree manifest and the
shell helpers `lib_sweep.sh` and `lib_manifest.sh`; it writes the same layout and `odyssey.json`,
so `odyssey account` reads atlas trials unchanged (checked, below).

**Network policy.** Read before any paid run.
- `main` is on `agent_net`, which is a normal Docker bridge with internet (`network_mode = "public"`
  in task.toml): the agent needs its model API, and Harbor installs Claude Code / Codex into the
  container at trial start. Nothing else of the trial is reachable from `main` except the
  credential proxy (`localstack:4566`); the controller has no network, and LocalStack and
  deny-guard are only on `backend_net` (the probe checks that they do not resolve from `main`).
- `--egress allowlist` (SPEC section 11) is **not available** for atlas: `odyssey compile` refuses
  a sequence that ships a compose file, and `compile_atlas.py` has no egress option. By SPEC
  section 11's reading of Harbor's `docker.py`, its egress sidecar would not cover `main` here
  anyway, because `main` declares its own network (not run).
- What open internet allows: the agent can install packages and read the web. General AWS
  documentation is fair use for these tickets. This repository, every `gold.patch` included, is
  public, so with open egress nothing stops an agent from fetching the reference solutions; runs
  that must exclude that need an egress control first.
- Setup and grading need no internet: the grader toolchain is baked into the image
  (`/opt/atlas-grader`, the IAM-simulate engine for ATL-6), and every validation below ran with
  `--offline` (`agent_net` internal). The one remaining network need is the host's Docker pulling
  LocalStack's Lambda runtime images (`public.ecr.aws/lambda/python:3.9`, `:3.11`) on first use;
  pre-pull them on a fresh host.

## Running trials

The commands are the ones the oracle/nop validation runs used, with the agent swapped; results of
the model runs are in the top-level `README.md`.
Run on a Docker host with Harbor 0.22.0 (`harbor` on PATH), from the repository root, with the
API key in the environment Harbor runs in (`set -a; . ./.env; set +a`; never print it).

```sh
# Compile once (online network, informed framing): 14 Williams rows of 7 steps, and 7 cold one-step tasks.
uv run -q --python 3.12 python scripts/atlas/compile_atlas.py compile sequences/atlas-commerce \
    --out build/atlas/williams --design williams
uv run -q --python 3.12 python scripts/atlas/compile_atlas.py compile sequences/atlas-commerce \
    --out build/atlas/cold --design cold
# Rows are named atlas-commerce__fml-inf-nofb__w01-3fec01 ... w14-b7a248; cold tasks
# atlas-commerce__fml-inf-nofb__c01-af8089 (ATL-1) ... c07-6d5034 (ATL-7).
W=build/atlas/williams/atlas-commerce__fml-inf-nofb__w01-3fec01
C=build/atlas/cold/atlas-commerce__fml-inf-nofb__c01-af8089

# Cold (one ticket, one session): Claude Code, then Codex.
harbor run -p $C -a claude-code -m anthropic/<model> -n 1 -o jobs/atlas --job-name cold-c01-claude -y
harbor run -p $C -a codex -m openai/<model> -n 1 -o jobs/atlas --job-name cold-c01-codex -y

# Warm, fresh session per ticket (only files carry: /memory and the repo memory files).
harbor run -p $W -a claude-code -m anthropic/<model> -n 1 -o jobs/atlas --job-name w01-claude-fresh -y
harbor run -p $W -a codex -m openai/<model> -n 1 -o jobs/atlas --job-name w01-codex-fresh -y

# Warm with session resume (the conversation carries across the 7 tickets).
harbor run -p $W -a claude-code -m anthropic/<model> --resume-trajectory -n 1 -o jobs/atlas --job-name w01-claude-resume -y
harbor run -p $W -a codex -m openai/<model> --resume-trajectory -n 1 -o jobs/atlas --job-name w01-codex-resume -y

# After every run: remove the trial's Docker leftovers (LocalStack's Lambda containers outlive a trial).
python3 scripts/atlas/cleanup_job.py jobs/atlas/<job name>

# Accounting: one row per segment, then the cross-check of labels and trusted verdicts.
uv run -q --python 3.12 python -m odyssey.cli account jobs/atlas --out segments.csv
python3 scripts/atlas/check_account.py segments.csv jobs/atlas
python3 scripts/atlas/timings.py jobs/atlas
```

- **Concurrency.** A trial is five containers plus LocalStack's Lambda containers, and several
  graders have live deadlines (30-120 s). Run at most two trials at once on a 16-vCPU host
  (`-n 1` per `harbor run`, two runs side by side), check `uptime`, and re-grade nothing by hand:
  a step whose reward is 0 with `error = 1` in its `reward.json` is a grader failure, which
  `odyssey account` reports as `grader_error` and analysis excludes.
- **Wall time.** Setup (reset + redeploy) adds 15-60 s per step outside the agent's budget, and
  grading 3-130 s (tables below). The agent budgets sum to 17,000 s for the seven tickets
  (3600 + 3600 + 1800 + 1800 + 2400 + 1800 + 1800), so a 7-step trial is bounded by about
  4 h 50 min of agent time plus about 10 min of setup and grading; the expected wall time is
  the agents' real working time plus about 6 (measured: a 7-step oracle trial takes 5.5-6.2 min end to end) min.
- **What to look at afterwards.** Each step's `verifier/grade.json` (the verdict, exit code,
  tampering reasons), `verifier/segment.patch` (what the agent changed) and
  `verifier/guard-sweeps.jsonl` (what was still running when the verifier started).
- **Codex and Claude Code installation** in this image was checked with `--install-only` (no model
  call; results below).

## Images, pins and licences

| what | pin | licence / note |
|---|---|---|
| main and controller base | `public.ecr.aws/ubuntu/ubuntu:24.04@sha256:c7961894220d8080d1cfe3e885299049fe90eed4715f78ff9a9a784e5d800359` (pulled 2026-11-11) | Ubuntu |
| LocalStack | `localstack/localstack:4.14.0@sha256:3ebc37595918b8accb852f8048fef2aff047d465167edd655528065b07bc364a`, Community, no auth token | Apache 2.0 for this pinned image (the EULA text was not read on an official domain) |
| proxy, deny-guard | `python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535` | PSF / Debian |
| AWS CLI v2 | 2.27.50 | Apache 2.0 |
| Docker CLI (controller only) | static 27.5.1 | Apache 2.0 |
| workstation Python (`/opt/venv`) | boto3 1.35.99, botocore 1.35.99, awscli-local 0.22.0, requests 2.32.3, pyyaml 6.0.2, uv 0.9.7 | |
| grader Python (`/opt/atlas-grader`) | pytest 8.4.1, pytest-json-ctrf 0.3.5, boto3/botocore 1.35.99, requests 2.32.3, pyyaml 6.0.2 (transitive dependencies resolved at build time) | |
| IAM gate engine (`/opt/iam-gate`) | `@cloud-copilot/iam-simulate@0.1.154`, `@cloud-copilot/iam-data@0.19.202606021`, Ubuntu's Node 18 | |

The build needs network (apt, AWS CLI, PyPI, npm, Docker CLI). Nothing in a trial downloads
anything except LocalStack's Lambda runtime images, pulled by the host's Docker.

## Measured: integration (box, 2026-11-11, Harbor 0.22.0, no model calls)

Final foundation and tickets, as in this directory. Two lanes of Harbor runs at a time on the
dedicated 16-vCPU box (load average under 1.6 throughout); summaries in `results/integration/` (validation reports, `sequence_report.txt`,
`segments.csv`, `check_account.txt`, `timings.txt`, `check_probe_seq__*.txt`, `lane_log.txt`).
Everything was run once; nothing needed a re-run (no failed reference condition occurred).

**Foundation changes made at integration** (requested by the ticket authors), then everything
below was re-run on them:
- `grader_lib/atlas_pytest.py`: atlas_run.py runs pytest through it, so a `pytest.fail()` in a
  fixture (ATL-5's "did not converge") is the test's failure, not an error, for every ticket;
  ATL-5's private conftest.py is gone. Offline test: a fixture's `pytest.fail` gives `failed`, a
  fixture's `RuntimeError` stays `error`.
- `sequence.toml` is owned by the integrator, lists the tickets in id order, and `check` refuses
  a `ticket.toml` whose title, cluster or role disagree with it.
- `test_compile_atlas.py`'s plan test no longer depends on the size of the pool (it failed as soon
  as a second real ticket existed); two tests added (full subset coverage, label agreement).
- `validate_ticket.py` prints whole condition labels (the "reference with all six" condition was
  printed cut to five tickets; the report's `state` was always right, checked in the ATL-3 report).
- New tools: `probe_marks.py` + `check_probe_seq.py` (the probe over any real ordering),
  `sequence_report.py` (oracle/nop trial verdicts and pooled independence), `check_account.py`,
  `cleanup_job.py`; `timings.py` now reports setup by start state and grader time per ticket.
- Offline: `compile_atlas.py check` 7 tickets OK; `test_compile_atlas.py` 19 passed; the core suite
  213 passed.

**Single-ticket validation** (`validate_ticket.py <ID> --offline --subset-sample 13 --seed 0`: nop
and reference at the base, every known-bad at the base, and nop + reference with each of the 13
subsets "each single other ticket, each all-but-one, all six"; every condition starts with a
full reset and redeploy, setup and grading with no internet):

| ticket | conditions | result | wall time | known-bads (all reward 0; failed checks) |
|---|---|---|---|---|
| ATL-1 | 31 | PASS | 23.2 min | stale-stage: the 3 end-to-end tests; constant-group-id: `..._identifiers_are_request_derived`; no-content-type: the Content-Type test and the 3 end-to-end tests |
| ATL-2 | 32 | PASS | 24.7 min | no-created-at: ordering and duplicate tests; failed-shape: partial-batch and redelivery tests; no-report-batch-item-failures: the mapping test; unconditional-put: redelivery and duplicate tests |
| ATL-3 | 32 | PASS | 14.6 min | adds-cmv, sqs-wildcard: the role test; not-deployed: role and parity tests; policy-in-code: the parity test |
| ATL-4 | 32 | PASS | 39.9 min | opus46-hand-serializer: processed + exactly-once; stringify-collections: processed; counter-not-idempotent: exactly-once; hotfix-outside-repo: the repository-parity test |
| ATL-5 | 31 | PASS | 49.7 min | floor-per-payment and float-running-total: the 13 fixture tests; ledger-drops-payer: the processor-fields test |
| ATL-6 | 32 | PASS | 21.5 min | raw-message-delivery: the envelope test; events-role-states-star: the two gate probes; target-input-path: e2e + envelope; always-default-price: e2e |
| ATL-7 | 31 | PASS | 15.4 min | no-url-decode: the end-to-end test; no-kms-grant: the CMK test; extra-dynamodb-actions: the write-only test |

In every nop condition exactly the ticket's fail-to-pass tests failed (none errored) and every
pass-to-pass test passed; no condition had a grader error or a tampering flag.

**Whole-sequence runs** (online compile, as a paid run would use; `sequence_report.py`):

| run | trials | result |
|---|---|---|
| Williams design, oracle | 14 rows x 7 steps | reward 1 on all 98 steps |
| Williams design, nop | 14 rows x 7 steps | reward 0 on all 98 steps, each a clean failure (F2P all failed, P2P all passed) |
| cold (`--design cold`), oracle / nop | 7 + 7 one-step tasks | 1 on every ticket / 0 on every ticket, clean |
| probe, resumable (`AtlasResumeProbe`, `--resume-trajectory`) | rows w02 and w09 | `check_probe_seq.py` 206/206 checks each; every step reward 0 despite the planted forger |
| `--install-only` (no model call), cold ATL-3 | claude-code, codex | Claude Code 2.1.322 installed in 9.5 s, Codex 0.167.1 in 15.9 s, no exception |

No step had a grader error, a tampering flag or a Harbor exception, and after every run no
container, network or volume of the trial was left on the box.

**Independence** (validation conditions and Williams prefixes pooled, `sequence_report.py`): for
every ticket the reference scored 1 and do-nothing scored 0 (clean) in each of **20 distinct
start states out of the 64 possible** (the base, all 6 single other tickets, all 6 all-but-one,
all six others, and 6 more from the Williams prefixes), 29 gradings per condition per ticket. The
other 44 start states were not run. Because every row's start state is built only from
references, this is also the check that no later ticket passes because of an earlier reference
(do-nothing scored 0 everywhere) and that no grader needs anything an agent deployed in another
ticket (the reference scored 1 from every state, with nothing carried from earlier agents).

**Probe over full orderings** (`check_probe_seq.py`, marks from `probe_marks.py`: two lines unique
to each ticket's reference, found nowhere in the base, other references, ticket texts or tests).
At every step k of w02 (ATL-2,3,1,4,7,5,6) and w09 (ATL-6,5,7,4,1,3,2):
- no file anywhere in `main` (image, `/app`, `/tmp`, `/root`, `/memory`, ...; `/proc`, `/sys`,
  `/dev`, `/logs` excluded) held a mark of the current or any later ticket, and the live
  signature of every not-yet-merged reference was absent (no `orders-api`, no
  `ReportBatchItemFailures`, no inline policy on `orders-consumer-role`, base fulfilment code, no
  `payments-ingest`, no `OrderProcessor`, no `pipeline-stack`);
- the marks of every earlier ticket were in `/app` and their signatures were live;
- `/tests`, `/solution`, `/logs/verifier` were empty, no grader directory or tokens file existed,
  the controller, LocalStack and deny-guard did not resolve, a forged controller request was
  refused, the builder key could neither send to `orders.fifo` nor assume a role, PID 1 survived
  `kill -9`, and `/app` was one "Maintainers" commit;
- from step 2 on: step 1's left-running forger, copier and ticker were gone (the guard killed them
  at step 1's verifier start; the ticker's last tick was before it), its `/app` file, queue and
  function were gone, nothing of the grader reached `/memory`, `/memory` itself carried, and the
  builder key was new at every step;
- every step scored 0 with no tampering flag, although step 1 left a forger running that writes
  reward 1 into `/logs/verifier` and replaces `/tests/test.sh` as soon as it appears (the guard
  killed it before `/tests` was uploaded);
- session resume: Harbor called the agent's `run` at step 1 and `resume` at steps 2-7, and the
  session file under `/logs/agent/sessions` seen at step k held exactly the entries of steps 1..k-1.

**Accounting** (`odyssey account` over the 14 + 14 Williams, 14 cold and 2 probe trials, then
`check_account.py`): 224 rows for 224 planned segments; ordering id, position, ticket, cluster,
role, arm and `ordering_length` match each task's `odyssey.json` and `sequence.toml` on every row;
every row's reward equals the reward test.sh recorded in `grade.json` and `reward.json` (including
the probe rows, where a forger had planted reward 1), and `grader_error` equals test.sh's error
flag. Probe steps 2-7 are `resumed = true`. Every row warns "no trajectory.json", as expected for
agents without a model. `odyssey account` has no `tampered` column; a tampered step shows as
reward 0 and its reason is in `grade.json`.

**Timing** (`timings.py` over 445 steps of these runs; seconds):

| phase | min | median | max |
|---|---|---|---|
| controller reset (LocalStack restart, proxy + deny-guard restart, new key) | 5.5 | 8.7 | 9.6 |
| redeploy, base only (position 1) | 9.7 | 10.2 | 10.3 |
| redeploy, base + 6 references (position 7) | 13.4 | 16.9 | 17.2 |
| whole setup.sh, position 1 | 15.8 | 17.1 | 19.1 |
| whole setup.sh, position 7 | 22.4 | 25.8 | 26.4 |

| ticket | grader, reward 1 (median / max) | grader, reward 0 (median / max) | verifier timeout |
|---|---|---|---|
| ATL-1 | 23 / 33 | 3 / 126 (end-to-end deadlines) | 600 |
| ATL-2 | 22 / 26 | 21 / 29 | 600 |
| ATL-3 | 3 / 3 | 2 / 2 | 300 |
| ATL-4 | 44 / 54 | 45 / 94 | 600 |
| ATL-5 | 20 / 27 | 123 / 123 (the fixture's 120 s wait) | 600 |
| ATL-6 | 19 / 19 | 4 / 138 | 600 |
| ATL-7 | 4 / 5 | 3 / 63 | 600 |

Step timeouts were left as the ticket authors set them: the slowest grading seen (138 s) is under
a third of the 480 s that run.sh gets from a 600 s verifier timeout (ATL-3: 3 s against 240 s),
and setup is not limited by Harbor (setup.sh's own caps are 420 s for the reset and 900 s for the
deploy, against a measured maximum of 26 s). A whole 7-step trial took 5.5-6.2 min with the oracle
(median 5.7) and 6.3-7.0 min with nop, so the platform's overhead per 7-step sequence is about
6 min; the expected wall time of a paid sequence is the agents' working time plus that, bounded by
the 17,000 s sum of the agent timeouts.

## Measured: validity re-check (independent verifier, box, 2026-11-11)

An independent re-run, not the integrator's: a fresh copy of the repository, images
built fresh by Harbor (no trial image was on the box), online compile (not `--offline`), Harbor
0.22.0, only the `oracle` and `nop` agents, at most two trials at a time (load average under
2.1). Start states were chosen to avoid the integrator's 20 per ticket. The runs drove
`validate_ticket.py` with an explicit list of subsets or conditions and changed nothing in
`scripts/atlas/`. Summaries are in `results/validity-recheck/`
(every validation report, the condition specs, the probe patches, and `sequence_report` before and
after the two grader changes).

| ticket | conditions | result | other-ticket subsets (new start states) |
|---|---|---|---|
| ATL-1 | 11 | PASS, 10.8 min | 2+3+6, 4+7, 2+6+7 |
| ATL-2 | 12 | PASS, 9.2 min | 1+4+6, 1+3, 4+6+7 |
| ATL-3 | 12 | PASS, 5.5 min | 4+7, 1+4+6, 2+4+7 |
| ATL-4 | 12 | PASS, 15.5 min | 3+5, 2+6, 2+5+7 |
| ATL-5 | 11 | PASS, 18.2 min | 1+3+6+7, 1+3+6, 1+2+6 |
| ATL-6 | 12 | PASS, 10.0 min | 2+4+5, 1+3+7, 1+2+4+5 |
| ATL-7 | 11 | PASS, 6.4 min | 1+2+5+6, 2+4, 4+5 |

Each run: nop at the base 0 with exactly the fail-to-pass tests failing, reference at the base 1,
every known-bad at the base 0 with the same failed tests as in "Measured: integration", and nop 0
(clean) / reference 1 with each listed subset. No grader error, tampering flag or Harbor exception.

- **Known-bads from another start state** (not run before): all 25 known-bads re-run with the
  references of all six other tickets merged and deployed (ATL-3's `policy-in-code` with all but
  ATL-2, whose reference edits the same file): every one scored 0 and failed exactly the tests
  it fails at the base.
- **Orderings the integrator did not use** (neither is a Williams row): `ATL-4,1,2,6,3,7,5` and
  `ATL-7,3,5,2,6,1,4`. Oracle 1 on all 7 steps of each, nop 0 on all 7 with clean failures;
  `sequence_report.py`: PASS, 4 trials, 0 problems.
- **Independence**: 3 to 5 new start states per ticket (the subsets above plus ordering prefixes
  not in the integrator's sample), so 23 to 25 of the 64 per ticket have now been graded.
- **Probes of the graders' gaps** (wrong answers run as extra known-bads; expectations written
  before the runs):
  - ATL-3: `AWSLambdaSQSQueueExecutionRole` attached plus a table-only
    `iam/orders-consumer-role.json` scored **1**. Fixed: the new resource-scope check; afterwards a
    full `validate_ticket.py ATL-3 --subset-sample 13 --seed 0` (33 conditions, online) passed in
    14.9 min with this variant (`managed-sqs-exec-role`) scoring 0 on the new check.
  - ATL-1: the reference with the API role's SQS statement on `"Resource": "*"` scored **1**.
    Fixed: the new resource-scope check; afterwards a
    full `validate_ticket.py ATL-1 --subset-sample 13 --seed 0` (32 conditions, online) passed in
    23.9 min, with `api-role-star` scoring 0 on the new check and the new check failing cleanly in
    every nop condition.
  - ATL-1: `MessageDeduplicationId=$context.requestId` scored 0 at the base and **1 with ATL-2's
    reference merged** (see Known limits). Not fixed.
- **After the two grader changes**, the ordering `ATL-4,1,2,6,3,7,5` was compiled again and run:
  oracle 1 on all 7 steps, nop 0 on all 7 with clean failures
  (`sequence_report.py`: PASS, 2 trials, 0 problems). The integrator's Williams, cold and probe
  runs above predate these two checks; their ATL-1 and ATL-3 verdicts were not re-run on them.
  After every run no container of the trial was left on the box.

## Measured: foundation (box, 2026-11-11, before the other tickets existed)


Everything below ran on the dedicated box (16 vCPUs, load average under 1 throughout) with
Harbor's `oracle` and `nop` agents and the scripted probes in `scripts/atlas/`; no model was
called. Summaries are in `results/pre-integration/`.

**ATL-2 single-ticket validation** (`validate_ticket.py`, one Harbor task per run, every condition
starting with a full reset and redeploy; the first run compiled `--offline`, i.e. `agent_net`
internal, so setup and grading had no internet):

| condition | run 1 (offline) | run 2 (online, reference twice) | failed checks |
|---|---|---|---|
| nop at the base | 0 | 0 | exactly the 5 fail-to-pass tests; the 3 pass-to-pass tests and the wildcard guard pass |
| reference at the base | 1 | 1, 1 | none |
| known-bad `no-created-at` | 0 | 0 | `test_end_to_end_fifo_group_ordering`, `test_end_to_end_duplicate_post_is_idempotent` |
| known-bad `failed-shape` | 0 | 0 | `test_partial_batch_response_reports_only_unprocessable_messages`, `test_redelivered_order_is_acknowledged_without_change` |
| known-bad `no-report-batch-item-failures` | 0 | 0 | `test_event_source_mapping_declares_report_batch_item_failures` |
| known-bad `unconditional-put` | 0 | 0 | `test_redelivered_order_is_acknowledged_without_change`, `test_end_to_end_duplicate_post_is_idempotent` |
| nop with ATL-PH's reference merged and deployed | 0 | 0 | the same 5 fail-to-pass tests |
| reference with ATL-PH's reference | 1 | 1 | none |
| wall time | 6.0 min, 8 conditions | 6.9 min, 9 conditions | |

No condition had a grader error or a tampering flag. Independence was checked only against the
placeholder: no other real ticket existed yet (superseded by "Measured: integration").

**Two-step trials** (ordering `ATL-2,ATL-PH` unless noted; reward per step):

| run | rewards | notes |
|---|---|---|
| oracle (first attempt) | 1, (aborted) | Harbor could not archive a root-owned `grader/` subdirectory of `/logs/verifier` (`PermissionError`); test.sh now makes its outputs world-writable |
| oracle | 1, 1 | |
| nop | 0, 0 | |
| oracle, reverse order `ATL-PH,ATL-2` | 1, 1 | ATL-2 graded from a start state that holds ATL-PH's reference |
| nop, reverse order | 0, 0 | |
| probe (twice) | 0, 0 | `check_probe.py`: 27 of 27 checks (second run; the first used an older checker), below |
| probe declared resumable, `--resume-trajectory` | 0, 0 | Harbor called the agent's `resume()` at step 2, and the session file written at step 1 under `/logs/agent/sessions` was intact; `check_probe.py` 27 of 27 |
| probe with the guard disabled (`--unsafe-no-guard`, experiment) | **1**, 0 | the left-behind forger replaced the uploaded `/tests/test.sh`; the forged script wrote reward 1. This is the hole the guard closes |
| guard disabled, forger only plants reward files | 0 (`tampered` 1), 0 | test.sh's own check: "files in /logs/verifier not written by the grader: reward.txt" |

What the probe established (`results/pre-integration/check_probe__*.txt`): at step 1 no file anywhere in main
contained text unique to ATL-PH's reference or to ATL-2's own reference; `/tests`, `/solution` and
`/logs/verifier` were empty and no grader directory existed; no tokens file existed in main;
`atlas-controller`, `localstack-backend` and `deny-guard` did not resolve from main; a forged
controller request was refused ("invalid token"); the builder key was denied SendMessage to
`orders.fifo` and `sts:AssumeRole`; PID 1 survived `kill -9 1`. When Harbor started step 1's
verifier the guard killed the three left-behind loops (forger, copier, ticker), the generated
test.sh ran and step 1 scored 0. At step 2 the loops were gone, the ticker's last tick was before
that kill, `/app/PROBE_LEFTOVER.txt`, the queue `probe-leftover-q` and the function
`probe-leftover-fn` were gone, the reset reported removing that function's Lambda container, the
builder key was new, `/memory` had carried (by design) and held no grader copy, `/app` was one
"Maintainers" commit containing ATL-2's reference, the orders mapping had
`ReportBatchItemFailures` (the reference was deployed), and nothing of ATL-PH's own reference was
visible. `/tmp`, `/root` and `/var/tmp` markers carried (by design, see Known limits).

**Reset and redeploy time** (`scripts/atlas/timings.py` over 35 steps of the runs above):

| phase | min | median | max |
|---|---|---|---|
| controller reset total | 5.3 s | 8.7 s | 8.8 s |
| of which LocalStack restart to healthy | 2.1 s | 5.3 s | 5.4 s |
| of which proxy + deny-guard restart | 2.4 s | 2.5 s | 2.6 s |
| of which credential bootstrap | 0.6 s | 0.6 s | 0.6 s |
| tree rebuild + state.patch + git | 0.02 s | 0.03 s | 0.03 s |
| `deploy.py --all` (base, plus ATL-2 or ATL-PH when merged) | 9.7 s | 9.8 s | 20.7 s (first trial, cold) |
| whole setup.sh | 15.8 s | 18.7 s | 27.4 s |

Lambda containers removed per reset: 0 to 3. After every run, no container, network or volume of
the trial was left on the box (validate_ticket.py's sweep found none; `docker ps -a` showed none).
ATL-2's grader took about 27 s (pytest) plus the wildcard guard.

**Accounting.** `odyssey account` over the oracle, nop and probe jobs produced 6 segment rows with
the right sequence, arm (`fml-inf-nofb`), ordering id, position, ticket, cluster, role, reward
and `ordering_length` 2, and `grader_error` false (warning: "no trajectory.json", expected for
agents without a model).

**Offline checks** (at that time; 19 now). `scripts/atlas/test_compile_atlas.py`: 16 passed (layout, no look-ahead in
any step's workdir, instruction or tests, token hashing and placement, manifest read by
`odyssey.accounting`, per-step timeouts, `bash -n` of every generated script, the offline variant,
the base manifest, validation plans and subset samples, known-bad solutions, disjoint gold patches,
the verdict rule). The core suite (`uv run --python 3.12 python -m pytest -q`) passed 213 tests;
`odyssey/` was not changed.

**Harbor's per-step timeouts** (read in Harbor 0.22.0's `trial/multi_step.py`,
`_step_agent_timeout_sec` / `_step_verifier_timeout_sec`, and `models/task/config.py`): a step's
`[steps.agent] timeout_sec` overrides `[agent]`; `[steps.verifier] timeout_sec` is always used and
defaults to 600 s. The generator writes both for every step from the ticket's `ticket.toml`.

## Known limits

- **Root in main (persists across the reset; `segment.patch` does not reveal it).** The agent is
  root in the container that grades it (SPEC section 10.1): it can replace the toolchain the grader
  runs (bash, python, git). The guard stops leftover *processes*, not a trojaned binary. Two points
  sharpen this for the shared-container design, both verified by the integrity probe
  `scripts/atlas/forge_probe.py` (a do-nothing agent that at step 1 replaced
  `/opt/atlas-grader/bin/python` with a 300-byte shim scored reward 1 on all 7 steps of a Williams
  row with `tampered` never set): (1) `setup.sh` rebuilds `/app` and the live platform but **never
  restores `/opt`**, so a grader interpreter (or `/opt/odyssey/base.tar.gz`, or any `/opt/atlas-*`)
  tampered at one step forges every later step's verdict in a resumed trajectory — one compromised
  step taints the rest; (2) `review segment.patch` is **not** a sufficient check here, because
  `segment.patch` is built only from the `/app` working tree (test.sh's `copy_agent_tree`), so a
  trojan under `/opt` or `/bin` leaves `segment.patch` empty. Corrupting `base.tar.gz` is caught
  (the manifest check in `setup.sh`/`test.sh` fails and the step errors rather than passing), but a
  trojaned grader interpreter is not. This is the accepted threat model of every SWE-Odyssey code
  sequence, not atlas-specific; a robust fix would run the grader under an interpreter the agent
  cannot modify (e.g. supplied by the trusted controller or a read-only verifier mount).
- **The guard's trigger is Harbor 0.22's verifier reset command** (`chmod 777 /logs/verifier`). An
  agent that runs that chmod itself kills its own processes, harmlessly. A Harbor version that
  stops chmodding the directory needs the trigger re-derived (`guard.py` falls back to polling the
  directory's ctime only when inotify is unavailable). Harbor also chmods it once at trial start
  (seen as an extra "verifier-start" with nothing killed).
- **What carries besides /memory:** `/tmp`, `/root`, `/var/tmp`, `/opt` (the grader toolchain and
  `/opt/odyssey/base.tar.gz`) and installed packages are not wiped between tickets, because
  Harbor's agents keep their own state there (Claude Code's settings, Codex's home) and the image's
  toolchain lives there. The probe confirmed they carry (including two `/opt` markers across every
  reset). Nothing of a later ticket ever reaches them; but `/opt` being writable by root in `main`
  and never reset is the channel behind the "Root in main" limit above.
- **No IAM enforcement on Community**: deny-guard restores the
  explicit Deny statements for the builder key only. A Lambda the agent creates can still write
  protected data with its role; graders drive fresh inputs, so this is no shortcut.
- **LocalStack's root credentials** stay reachable from Lambda containers on `backend_net`
  (`localstack-backend:4566`, `test`/`test`), as on Pro.
- **Grader copies.** The probe's copier (a loop copying `/tests` and `/logs/verifier` into
  `/memory`) was killed by the guard before `/tests` was uploaded and copied nothing; after a step,
  Harbor moves `/logs/verifier` out of the container. A copy made by something that survives the
  guard (a trojaned binary run by test.sh) is not detected.
- **Toolchain drift.** Transitive Python dependencies of the two venvs are resolved at build time;
  a rebuild months later may differ.
- **Grader gaps**: in ATL-1 and ATL-3, extra
  non-universal actions on the named queue other than `sqs:ChangeMessageVisibility` are not
  rejected (grants on `"Resource": "*"` now are, see the decisions above); ATL-1's template check
  accepts any identifier expression containing `$` (see the ATL-1 bullet below); ATL-6's
  `test_final_state.py` uses fixed order ids (ORD-1001, ORD-2002), which an agent could
  in principle pre-seed into the queues (its new envelope check uses fresh uuids); ATL-5's global
  invariants count every row the agent created while testing (the ticket says so). ATL-5, ATL-6
  and ATL-7 grade the live platform only: an agent that deploys correctly by hand, without a
  repository component, scores 1 (their texts ask for the component as the team's convention).
- **ATL-1's duplicate-POST test** is strongest at the base: there, only SQS FIFO deduplication
  keeps the row unchanged; with ATL-2's reference merged the consumer is idempotent itself, so a
  weaker API (a deduplication id that is not the order id) would also pass that test. Measured by
  the validity verifier: a reference with `MessageDeduplicationId=$context.requestId` scored 0 at
  the base (the duplicate test) and **1 with ATL-2's reference merged**, and the template check
  accepts it because it only requires a `$`. So for this wrong answer ATL-1's verdict depends on
  the ordering. Gold, nop and
  the documented known-bads do not depend on the start state (validated).

## What remains unverified

- **Difficulty**: the validation runs above show only that the graders separate right from wrong
  answers; six of the known-bads reproduce failures real models made. Model results are in the
  top-level `README.md`. ATL-3 may take a capable model only minutes. Whether the 1800 s agent
  budgets of ATL-4 and ATL-6 make them easier than a tighter budget would is not measured.
- **Paid-run mechanics**: Claude Code and Codex install in this image (`--install-only`), the
  scripted resumable probe shows Harbor's resume path and session-file carry-over across 7 steps,
  and the model runs resumed real `claude-code` / `codex` sessions across atlas steps. Their
  behaviour when the guard kills a background process they started was not checked here.
- **Independence**: 23 to 25 of the 64 start states per ticket were graded (integration plus the
  validity re-check); the rest were not. Known-bads were run at the base and with all six other
  references, not in between.
- **Difficulty after the texts were written** (validity verifier, by reading): to state every
  checked behaviour, several texts name the very property a known model failure got
  wrong: ATL-2 requires `created_at` kept as epoch seconds (the failure dropped it), ATL-4 lists
  "true/false stay booleans, lists stay lists, nested objects stay maps" (the failure broke
  exactly those), ATL-6 requires the standard SNS envelope (the failure turned on raw delivery).
  ATL-3's text all but dictates the policy document. The known-bads prove the graders catch these
  failures, not that a current model still makes them.
- **Flakiness**: every reference grading passed (29 per ticket, at most two trials at a time,
  load average under 1.6); behaviour under heavier load or more parallel trials is unmeasured.
- **The online variant of the single-ticket validations**: the integrator's validations ran
  `--offline`; the validity re-check ran them online. Both gave the same verdicts.
- **A fresh host**: Lambda runtime images (`public.ecr.aws/lambda/python:3.9`, `:3.11`) were
  already cached on the box; a fresh host pulls them on first use and needs network for that.
- **Egress control**: none (see "Network policy"); the reference solutions are public in this
  repository.
- **Harbor upgrades**: the guard's trigger (Harbor 0.22's `chmod 777 /logs/verifier` at verifier
  start) and the per-step timeout semantics were read in Harbor 0.22.0 only.
