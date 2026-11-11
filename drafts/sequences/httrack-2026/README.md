# httrack-2026

Ten real, already-merged HTTrack bug fixes from late July 2026, reverted together
on one snapshot ("stacked reverts", [REAL_SEQUENCE_GUIDE.md](../../docs/REAL_SEQUENCE_GUIDE.md)). C, autotools,
automake test harness. The first real-repo SWE-Odyssey sequence.

## Provenance

| item | value |
|---|---|
| upstream repo | https://github.com/xroche/httrack (GPL-3.0) |
| snapshot S | `fac60dff4a539c6cced6f814da11873efc714981` (2026-07-30), submodule `src/coucal` at `93ec411c55667050965c7bb8826c22adc3521dec` |
| source dataset | SWE-bench-Live/MultiLang, split `c`, revision `eb09c2e5db6e4c5a95e01c903c8fd1d5da33c4f3` (dataset licence MIT) |
| upstream image | `starryzhang/sweb.eval.x86_64.xroche_1776_httrack-893@sha256:9bf86e8ed8f060ccf811357b548033db3cbac856504928c132f47588df30cab7` (amd64 only) |
| stacking | `scripts/real_sequence/stack_reverts.py`, ids below, `--fixup source/fixups/01-release-notes.patch` |
| image build | `environment/Dockerfile`; fetches S and coucal from GitHub (the only network access; Harbor builds each task's image from it, so the task build needs GitHub); the base is source only |

Licence note: the image and `environment/base.patch` contain GPL-3.0 code. Running
it internally is fine; redistributing a built image carries GPL source duties
(the source is the upstream commit plus `base.patch`).

## Tickets

This table is the only place the benchmark ids appear.

| ticket | upstream instance | merged | gold (files, changed lines) | F2P (hidden) | cluster / role |
|---|---|---|---|---|---|
| HTT-01 | xroche__httrack-809 | 2026-07-27 | `src/htsback.c`, 52 | `110_local-ftp-parallel` | ftp / core |
| HTT-02 | xroche__httrack-830 | 2026-07-27 | `src/htsftp.c`, `src/htslib.c`, `src/htslib.h`, 95 | `121_local-ftp-nocache-splice` | ftp / core |
| HTT-03 | xroche__httrack-851 | 2026-07-27 | `src/htsback.c`, 57 | `130_local-chunked-truncated` | transfer / core |
| HTT-04 | xroche__httrack-849 | 2026-07-27 | `src/htsback.c`, `src/htsopt.h`, `src/htswarc.c`, `src/htswarc.h`, 58 | `134_local-warc-revisit-ip`, `135_local-warc-forced-notmodified`, `136_local-warc-cache-priority` | transfer / trap |
| HTT-05 | xroche__httrack-811 | 2026-07-27 | `src/proxy/store.c`, 13 | `117_local-proxytrack-ndx-cursor` | proxytrack / core |
| HTT-06 | xroche__httrack-835 | 2026-07-27 | `src/proxy/store.c`, 87 | `119_local-proxytrack-ndx-fields2` | proxytrack / core |
| HTT-07 | xroche__httrack-829 | 2026-07-27 | `src/proxy/proxytrack.c`, 6 | `120_local-proxytrack-webdav-default` | proxytrack / trap |
| HTT-08 | xroche__httrack-876 | 2026-07-29 | `src/htsserver.c`, 87 | `142_webhttrack-content-type` | N-server / isolated |
| HTT-09 | xroche__httrack-856 | 2026-07-27 | `src/htscharset.c`, `.h`, `src/htsencoding.c`, `.h`, `src/htsparse.c`, 138 | `139_local-query-charref` | N-charset / isolated |
| HTT-10 | xroche__httrack-817 | 2026-07-27 | `src/htscache.c`, `src/htscache.h`, `src/htscoremain.c`, 90 | `106_engine-repair-rename` | N-cache / isolated |

All ten are dated after every target model's training cutoff (July 2026).
Pairs: HTT-05/HTT-06 share `src/proxy/store.c` (same `.ndx` reader); HTT-01/HTT-02
are both FTP but in different layers; HTT-03/HTT-04 share `src/htsback.c` with
different mechanisms (HTT-04 marked trap); HTT-07 is proxytrack's WebDAV code
(trap for the `.ndx` pair). HTT-08, HTT-09, HTT-10 touch code no other ticket
touches.

P2P per ticket (in each `run.sh`): a common core (`00_runnable`,
`01_engine-mime`, `01_engine-parse`, `01_engine-savename`, `15_local-types`,
`18_local-update`) plus 7 to 14 tests of the ticket's area; 13 to 20 in all.
The list per ticket is in `source/assemble.sh`.

## How the tickets were edited

Every upstream issue in this set reads like an agent-written audit report
(function names, file:line, the fix's shape). Each `ticket.md` is a rewrite as
a user's report: what was run, what happened, what should happen, with a
reproduction, and an "expected" section stating every observable the hidden
tests assert (log wording the tests grep for, exit codes, sizes, protocol
bytes, content types, boundary cases). No ticket names a source file, function
or line, the fix, a hidden test, an upstream issue/PR number or an instance id.
The originals are kept verbatim in `source/<ticket>/original_issue.md` (never
compiled into a task). Per ticket:

- HTT-01: symptom only (empty FTP files under load); keeps the user-visible
  debug message `slots ready moved to background` the original mentions.
- HTT-02: original explains the missing cache check; rewrite states the
  resume rule the tests check (SIZE larger, MDTM equal, stamp FTP files and
  partials with MDTM, no MDTM means no resume).
- HTT-03: adds the warning and error wording the test greps, the `-%B`
  behaviour, the reset-vs-truncation distinction and the hostile chunk-size case
  (not in the original issue, but asserted by the test).
- HTT-04: merges the two upstream issues (missing IP, forced not-modified) and
  states the archive-keeping rule the third test checks.
- HTT-05: the original gave the parser internals; the rewrite gives the field
  limits as observed behaviour and a table of index files with expected item
  counts.
- HTT-06: the original was two lines naming variables; rewritten as two user
  scenarios with expected conversions.
- HTT-07: crash reproduction with curl; expected 207 body elements.
- HTT-08: full extension-to-type table and the CRLF/header requirements.
- HTT-09: expected request queries per charset, including the iso-2022-jp case.
- HTT-10: both failure modes, the exit codes and the log/stderr wording.

Later edits by the independent verification (2026-11-10): HTT-07 lost the
sentence that diagnosed the crash (the empty default-document name); HTT-06's
second section no longer says how the copy is bounded (source instead of
destination length) and now states that `--convert` must exit 0 on a path it
refuses; HTT-03 no longer claims that the old cache entry stays valid on
`--update` (the test asserts the opposite for the hostile chunk case) and says
that the partial bytes of a direct-to-disk file must stay (the test asserts
they are there).

## Base tree edits beyond the reverts

- Names of the hidden tests were scrubbed from `tests/tests-list.mk` and
  `.github/workflows/windows-build.yml` (`stack.json` `scrubbed_lines`).
- `history.txt` (release notes of the 3.49-15 release, which shipped all ten
  fixes with their issue numbers) was edited to drop those clauses
  (`source/fixups/01-release-notes.patch`).
- Kept on purpose: `tests/102_local-ftp-refetch.test` has a comment, written
  upstream while HTT-01's bug was open, saying parallel FTP crawls lose files to
  a backlog slot swap bug. It was in the tree for the real fixer too. The
  shared test helpers `tests/Makefile.am` (two tickets' test registrations had
  already moved elsewhere) stay at S.
- Dockerfile (verification fix): `src/coucal/.git` (the depth-1 fetch of the
  coucal submodule) is deleted before the tree is copied. Before, it was copied
  into `/app` and `base.tar.gz`, so the agent's repository held coucal as a
  bare gitlink inside a nested repository, and the grader's `git clean -X`
  did not descend into it. The upstream image's `/root/.gitconfig`
  (identity "haha <example@gmail.com>", which overrode the neutral system
  identity, and `safe.directory=/testbed`) is deleted too. The base content,
  and therefore `base.manifest`, is unchanged (checked byte for byte).
- `sequence.toml` (verification fix): the build system (`configure.ac`,
  `acinclude.m4`, `m4/`, every `Makefile.am`) is now a protected path. Before,
  a one-line `AC_CONFIG_COMMANDS` in `configure.ac` that sets
  `TEST_LOG_COMPILER = true` scored reward 1 on HTT-07 and HTT-10 with no fix.
  No gold patch touches these files; a fix that needs a new compiled source
  file cannot be graded.

## Dropped candidates

From the 23 tasks that stacked textually at S:

- 444 (generated 12,911-line charset table), 850 (P2P 0: whole suite failed in
  the dataset's environment): defective.
- 395, 454, 599, 618 (June, or tiny one-file changes), 763 (macOS-only symptom),
  803 (help text and docs), 807 (no observable bug on Linux; its test needs an
  extra interposer library whose Makefile rules the revert removes), 789 and 893
  (tests live in `src/htsselftest.c` or need `configure.ac` changes).
- 584: its fix is required by HTT-03's test (measured: with every other gold
  applied and 584's reverted, `130_local-chunked-truncated` fails). Dropping 584
  keeps its fix in the base and removes the dependency.
- 530: behaviour change under discussion upstream (what `-M` measures), weak as
  a bug report.
- The other 15 of the 38 July/June tasks do not reverse-apply at S.

## Validation (16-vCPU x86_64 Docker host shared with other jobs, 2026-11-10)

| check | result |
|---|---|
| `odyssey validate --image httrack-2026:v2 --no-network --jobs 4 --repeat 3 --subsets --subset-sample 20 --seed 0` | **70/70 checks passed** in 75 min |
| image contract (8 items, incl. base tarball vs `base.manifest`) | all OK |
| gold patches pairwise disjoint; apply in 92 orders | OK |
| per ticket, the four conditions x 3 repeats | identical exit codes in every repeat (no `(flaky)` marker); every must-fail is a clean failure (F2P `FAIL`, P2P all `PASS`) |
| subsets | sampled, 20 of 510 per ticket: the 9 single other golds, the 9 "all others but one", and 2 seeded random subsets (each check's detail lists them) |
| base manifest determinism | `docker build --no-cache` rebuild gives a byte-identical manifest |
| Harbor 0.22 `-a oracle -n 1`, ordering HTT-05,HTT-06,HTT-08 under forward-merge and carry | reward 1, error 0 on all 6 steps |
| Harbor `-a nop`, same tasks | reward 0, error 0 on all 6 steps |
| Harbor `--design cold`, all 10 tickets, oracle / nop | 10/10 reward 1 / 10/10 reward 0, error 0 throughout; nop failures are F2P `FAIL` with P2P all `PASS` |
| earlier pass on the first image (before the release-notes fixup; same tickets) | 60/60 |

Verifier wall time per grade (run.sh `seconds`, inside Harbor, cold oracle / cold
nop; build = clean bootstrap + configure + make, 4 CPUs, host shared with other
jobs):

| ticket | oracle total (build) | nop total (build) |
|---|---|---|
| HTT-01 | 74 s (35) | 65 s (27) |
| HTT-02 | 66 s (23) | 80 s (40) |
| HTT-03 | 63 s (24) | 54 s (28) |
| HTT-04 | 40 s (25) | 43 s (30) |
| HTT-05 | 41 s (31) | 42 s (33) |
| HTT-06 | 35 s (27) | 32 s (24) |
| HTT-07 | 35 s (26) | 44 s (35) |
| HTT-08 | 39 s (30) | 40 s (29) |
| HTT-09 | 56 s (41) | 52 s (38) |
| HTT-10 | 32 s (24) | 34 s (26) |

All well inside the 720 s run.sh deadline (80% of `verifier_timeout_sec = 900`).
A full rebuild per grade is used because it is this cheap; there is no
incremental build cache.

### Independent verification (same host, 2026-11-10)

Re-run from a fresh copy of the repository and a fresh `docker build --no-cache` under the tag
`verify-httrack-2026:v1` (the builder's Dockerfile), then again on
`verify-httrack-2026:v2` (the Dockerfile and `sequence.toml` fixes above). The
host was heavily loaded by other jobs throughout (load average 13 to 37 on 16
vCPUs).

| check | result |
|---|---|
| `odyssey base-manifest` of v1 and of v2 vs the checked-in `base.manifest` | byte-identical, both |
| `odyssey validate --image verify-httrack-2026:v1 --no-network --jobs 5 --repeat 3 --subsets --subset-sample 20 --seed 11` | **70/70** in 89 min; identical exit codes in all 3 repeats |
| `odyssey validate --image verify-httrack-2026:v2 --no-network --jobs 5 --repeat 1` (final image and sequence) | **60/60** in 15 min |
| Harbor 0.22 `-a oracle -n 1`, ordering HTT-04,HTT-03,HTT-10, forward-merge and carry, v1 and final | reward 1, error 0 on all 6 steps, both times |
| Harbor `-a nop`, same tasks | reward 0, error 0 on all 6 steps, both times; P2P all PASS |
| Harbor `--design cold`, all 10, oracle / nop, v1 and final | 10/10 reward 1 / 10/10 reward 0, error 0; every nop has P2P all PASS |
| grader, HTT-07 and HTT-10: gold / do-nothing / tests-only patch (F2P test replaced by `exit 0`, `testlib.sh` and `local-crawl.sh` gutted) | 1 / 0 / 0 |
| grader, `configure.ac` patch setting `TEST_LOG_COMPILER = true` | **1 on HTT-07 and HTT-10 before the fix**, 0 after (gold still 1) |
| HTT-01 F2P test alone, 10x at load 18-23 and 15x at load 12, base / gold | 0 and 0 PASS / 10 and 15 PASS; the swap happened in every run |
| leak audit of the v1 image (whole filesystem, `grep -a`, git objects, refs, reflog) | no hidden test name or content, no gold-only line; one commit, no packs, no reflog beyond it; no httrack objects or binaries anywhere; findings: the nested `src/coucal/.git` and `/root/.gitconfig` (fixed) and the `#797` comment (kept) |

Verifier wall time per grade under that load (Harbor, final sequence, run.sh
`seconds`, build in brackets): oracle 66-113 s (43-97), nop 37-107 s (25-70);
per ticket, oracle cold: HTT-01 100 s, HTT-02 89, HTT-03 113, HTT-04 66,
HTT-05 71, HTT-06 70, HTT-07 84, HTT-08 110, HTT-09 99, HTT-10 89.

Not covered: the 490 unsampled subsets per ticket; flakiness beyond 3 repeats
(HTT-01's test depends on a race under load and passed in every run here, but
the host was loaded by other jobs during all of them); `all-defects` and
`single-defect` policies under Harbor (compile and setup are shared with the
two policies run).

## Known limitations

- Upstream httrack at a later commit contains all ten fixes. With the default
  open egress the agent container has full internet access and nothing stops an
  agent from fetching it; compile with `--egress allowlist` (docs/SPEC.md
  section 11), which limits the agent to the model APIs and logs denied
  connections. Same for every real-repo sequence.
- The upstream repo's own `AGENTS.md`/`CLAUDE.md` are in the tree, and the
  sequence also lists them as repo memory files, so an agent's notes in them
  persist across segments.
- `tests/102_local-ftp-refetch.test` names HTT-01's bug area in a comment (see
  above), with the upstream issue number of HTT-01 (`#797`), which an agent
  with open internet access could look up.

