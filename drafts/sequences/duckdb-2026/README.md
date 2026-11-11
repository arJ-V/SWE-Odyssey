# duckdb-2026

Eleven real, already-merged DuckDB bug fixes from July 2026, reverted together
on one August 2026 snapshot ("stacked reverts", [REAL_SEQUENCE_GUIDE.md](../../docs/REAL_SEQUENCE_GUIDE.md)).
C++17, CMake/Ninja, sqllogictest suite driven by `test/unittest`.

## Provenance

| item | value |
|---|---|
| upstream repo | https://github.com/duckdb/duckdb (MIT) |
| snapshot S | `dce07451cb10fdf75b721183333c4a1c08cfe87d` (2026-08-04), no submodules |
| source dataset | SWE-bench-Live/MultiLang, split `cpp`, revision `eb09c2e5db6e4c5a95e01c903c8fd1d5da33c4f3` (dataset licence MIT) |
| upstream image | `starryzhang/sweb.eval.x86_64.duckdb_1776_duckdb-24318@sha256:01a191813fa2dfa34a4995fb169707504575b7d67123d44dad019b1b264bdab9` (amd64 only; the newest task's image) |
| stacking | `scripts/real_sequence/stack_reverts.py`, ids below, `--fixup source/fixups/01-bloom-filters-visible-test.patch` |
| image build | `environment/Dockerfile`: fetches S from GitHub and installs `ccache` with apt (the only network access; Harbor builds each task's image from it, so the task build needs GitHub and the Ubuntu mirror) |

## Tickets

This table is the only place the benchmark ids appear.

| ticket | upstream instance | merged | gold (files, changed lines) | F2P (hidden) | cluster / role |
|---|---|---|---|---|---|
| DDB-01 | duckdb__duckdb-24011 | 2026-07-21 | `src/optimizer/filter_combiner.cpp`, 24 | `test/sql/copy/parquet/bloom_filters.test` | filter / core |
| DDB-02 | duckdb__duckdb-23604 | 2026-07-04 | `src/optimizer/filter_combiner.cpp`, 3 | `test/optimizer/pushdown/starts_with_pushdown.test` | filter / trap |
| DDB-03 | duckdb__duckdb-24290 | 2026-07-29 | `src/optimizer/statistics/expression/propagate_cast.cpp`, 22 | `test/sql/optimizer/array_extract_row_group_pruning.test` | stats / core |
| DDB-04 | duckdb__duckdb-24074 | 2026-07-23 | `src/optimizer/statistics/operator/propagate_aggregate.cpp`, 90 | `test/sql/copy/parquet/parquet_stats.test` | stats / core |
| DDB-05 | duckdb__duckdb-23618 | 2026-07-05 | `extension/core_functions/scalar/generic/least.cpp`, 69 | `test/optimizer/statistics/statistics_least_greatest.test` | stats / core |
| DDB-06 | duckdb__duckdb-24100 | 2026-07-24 | `src/storage/table/row_group.cpp`, 5 | `test/sql/optimizer/test_rowid_pruning.test` | rowgroup / core |
| DDB-07 | duckdb__duckdb-24181 | 2026-07-27 | `src/storage/table/column_data.cpp`, `standard_column_data.cpp`, 11 | `test/sql/storage/wal/wal_null_update_row_group_pruning.test` | rowgroup / core |
| DDB-08 | duckdb__duckdb-24105 | 2026-07-24 | `src/execution/index/fixed_size_allocator.cpp`, `fixed_size_buffer.cpp`, `.hpp`, `src/include/duckdb/storage/index_storage_info.hpp`, `src/storage/wal_replay.cpp`, 39 | `test/sql/storage/test_read_only_wal_replay.test` | N-wal-index / isolated |
| DDB-09 | duckdb__duckdb-24318 | 2026-07-30 | `src/common/hive_partitioning.cpp`, `.hpp`, 10 | `test/sql/copy/parquet/parquet_hive_null.test`, `parquet_hive_empty.test` | N-hive / isolated |
| DDB-10 | duckdb__duckdb-24211 | 2026-07-28 | `src/catalog/catalog_entry/duck_table_entry.cpp`, `.hpp`, 42 | `test/sql/alter/struct/rename_col_struct.test`, `rename_col_nested_struct.test` | N-alter / isolated |
| DDB-11 | duckdb__duckdb-23637 | 2026-07-06 | `src/function/window/window_value_function.cpp`, `src/include/duckdb/function/window/window_aggregator.hpp`, 6 | `test/sql/window/test_empty_frames.test` | N-window / isolated |

"Merged" is the dataset's `created_at`. All are July 2026, after every target
model's training cutoff (Claude Opus 5.5 and Fable 5.2: June 2026; GPT-6.1 Sol:
April 2026). Pairs: DDB-01/DDB-02 share `filter_combiner.cpp` but are different
mechanisms (bound merging vs prefix-function pushdown; DDB-02 marked trap);
DDB-03/DDB-04 are both statistics through a cast; DDB-05 is statistics
propagation of a function in the `core_functions` extension; DDB-06/DDB-07 are
both "a scan keeps or skips the wrong row groups" in table storage. DDB-08 to
DDB-11 touch code no other ticket touches.

Most hidden tests are the upstream test file at S (the fix's new assertions
appended to an existing file, so the overlay replaces a visible file); DDB-02,
DDB-03, DDB-05 and DDB-07 add a new file. P2P per ticket (in each `run.sh`): a
common core (`case_condition`, `constant_folding`, `attach_schema`,
`parquet_count_star`) plus 9 to 12 tests of the ticket's area, 13 to 16 in all;
every one passed both at the base and with all eleven gold patches applied
(interaction experiment below). The lists are in `source/assemble.sh`.

## How the tickets were edited

The upstream issues are user bug reports with SQL reproductions that name no
source file. Each `ticket.md` keeps the report's reproduction and symptom,
drops the GitHub template (OS, version hashes, names, affiliations), and adds a
"What should happen" section that states every observable the hidden test
asserts (plan lines such as `Row Groups Scanned: 1 / 3` or `Empty Result`,
exact query results, boundary rowids, the NULL cases), so a careful engineer can
infer the whole test from the text. No ticket names a source file, function or
line, the fix, a hidden test, an upstream issue/PR number or an instance id.
The originals are kept verbatim in `source/<ticket>/original_issue.md` (never
compiled into a task). Per ticket:

- DDB-01: adds the `ROW_GROUP_SIZE 10000` BIGINT case of the test and the rule
  (only equal inclusive bounds).
- DDB-02: adds `^@` and the expected range-filter text. (Verification removed
  the builder's added comparison with `prefix(s, ...)`, which the original does
  not make and which points straight at the fix site; the repro now contrasts
  with `LIKE`, as the original does. Base output checked in the image.)
- DDB-03: the test's attached database with 3 row groups, `a[-1]`, and the NULL
  array/element counts.
- DDB-04: `TRY_CAST`, the result types, and "no row group is read".
- DDB-05: the original showed one query; the rewrite states the nullable-input
  rules and the two tables the test builds, with the expected ranges.
- DDB-06: the table of boundary rowids, including past-the-end and empty table.
- DDB-07: the original pasted the upstream test file verbatim (with its path);
  rewritten as a two-session CLI reproduction with the four expected counts.
  (Verification removed an added diagnosis, "the scan decides from the
  checkpointed column statistics ... and skips the row group", which the
  original does not contain and which names the mechanism of the fix.)
- DDB-08: the original showed one index case; the rewrite adds the three other
  WAL shapes the test replays (large PK table, index plus ADD COLUMN in one
  transaction, ADD PRIMARY KEY) and the index-scan settings it uses.
- DDB-09: states the read rule for string vs typed partition columns and the
  type-detection behaviour the test relies on.
- DDB-10: adds the nested, intermediate-struct and cast-default cases.
- DDB-11: the original diagnosed nothing; the rewrite adds `EXCLUDE TIES`,
  `lead`, and `rank` (which must ignore the frame).

## Base tree edits beyond the reverts

- DDB-01's test change could not be reverted textually (later upstream edits
  to `bloom_filters.test`), so `source/fixups/01-bloom-filters-visible-test.patch`
  removes its two new queries from the visible file; the hidden overlay is the
  file at S.
- The base changed `test/configs/verify_aggregate_state_export.json` (DDB-04's
  test registration reverted). Those configs only drive CI's config runs, not
  the plain `unittest` run the grader does, so they are not in any overlay.
- Leak scan: no issue or PR number of the eleven tasks appears anywhere in the
  base tree (`git grep` for `#<n>`, `issues/<n>`, `pull/<n>` outside
  `third_party`/`data`), and none of the symbols the gold patches introduce
  exists in the base.
- The upstream tree ships `AGENTS.md` (build and test instructions for coding
  agents; it recommends `make reldebug`) and an empty `CLAUDE.md` that imports
  it. They are realistic and stay; both are also listed as repo memory files.

## Image and build (what an agent and the grader see)

- `/app` is the base tree (15,066 files) with one neutral commit;
  `/opt/odyssey/base.tar.gz` is its tarball; `base.manifest` hashes it. The
  upstream `/testbed` (history to 2026-07-30 and a build of it) and the upstream
  `/root/.gitconfig` (identity "haha", `safe.directory=/testbed`) are deleted.
- No compiled DuckDB is in the image. A full build takes ~14 minutes on the
  16-vCPU Docker host (`make release`, 551 compile steps, measured inside the image
  build at load ~30), so the image carries a **ccache** filled by building the
  **bugged** base: `make release` and `make reldebug` at `/app`, and the
  grader's exact CMake command at `/check` (the test runner's sources embed
  their source and build paths). Every build directory is deleted afterwards and
  the Dockerfile proves `/app` equals the tarball again. ccache runs with
  `base_dir=/` and `hash_dir=false`, so a build of the same tree at any
  directory one level below `/` hits it. Cache size: 51 MB release-only,
  759 MB with reldebug. Nothing compiled from fixed code is in the image.
- `run.sh` copies the grading copy (without any `build/`) to `/check`,
  configures with the `make release` CMake command, builds `unittest`, and runs
  each F2P and P2P test file in its own `unittest -f <list>` process with a
  JUnit report (a crash is a failure, a timeout or missing report an error, a
  `require` skip is not a pass). `details.json` carries per-test results, build
  seconds and the ccache hit count.
- Protected paths (restored from the trusted start tree before `run.sh`):
  `test/` (sqllogictests and the C++ runner), `data/`, `Makefile`, `scripts/`,
  `third_party/catch` (the Catch2 framework the runner is compiled with) and
  `third_party/sqlite` (absent, but on the runner's include path), and every
  `CMakeLists.txt`, `*.cmake`, `*.cmake.in`. No gold patch touches them; a fix
  that needs a new source file cannot be graded. (Added in verification: before
  `third_party/catch` was protected, a patch that only made Catch2 treat every
  assertion as OK scored reward 1 on DDB-02 with the bug unfixed.)

## Measurements (16-vCPU x86_64 Docker host, other jobs running, load 15-40)

| what | result |
|---|---|
| image size | 8.32 GB (`docker image inspect`; upstream image 5.74 GB) |
| cold `make release` inside the image build (16 vCPU) | 840 s, 551 compile steps |
| cold `make reldebug` inside the image build | 1258 s |
| agent's first `make release` in `/app` (`--cpus 4`, warm cache) | 22-24 s (551/551 ccache hits) |
| agent's first `make reldebug` (`--cpus 4`, warm cache) | 265 s (debug-info link) |
| one-line code change in one `.cpp`, `ninja -C build/release unittest`, `--cpus 4` | 70 s (1 unity file recompiled, then link) |
| one-line change in a widely included header (`row_group.hpp`), same | 329 s (42 unity files) |
| grader `run.sh`, base + own gold, Harbor cold oracle trials (`cpus = 4`) | 28-313 s (build 24-309 s, tests 2-5 s); DDB-08 is the slowest (its gold edits two headers: 39 cache misses) |
| Harbor verifier step wall time (test.sh: copies, diff, run.sh) | oracle cold 61-344 s, nop cold 44-88 s, oracle sequence steps 79-110 s |

Per ticket, Harbor cold oracle trial (verifier wall / run.sh / build seconds):
DDB-01 102/82/77, DDB-02 98/72/70, DDB-03 61/28/24, DDB-04 67/41/38,
DDB-05 64/33/30, DDB-06 89/61/59, DDB-07 89/63/59, DDB-08 344/313/309,
DDB-09 127/97/93, DDB-10 160/139/136, DDB-11 72/40/37. `verifier_timeout_sec`
is 1800 (run.sh deadline 1440 s). Under forward-merge a later segment also
recompiles the files earlier gold patches changed (validate's "base + all
gold" grades did this for all eleven; their individual times are not
recorded by validate).

### Interaction experiment (before choosing the pool)

One container, the stacked base built once, states switched with git and
rebuilt incrementally:

- 1031 sqllogictests of the pool's areas (`test/optimizer`, `test/sql/optimizer`,
  `window`, `alter`, `copy/parquet`, `copy/partitioned`, `storage/wal`,
  `storage/compression`, `storage/update`, `function/generic`, `index`,
  `attach`) at the base and with all eleven golds: every candidate P2P passed in
  both; four unrelated tests failed in both (`test_art_duckdb_versions`,
  `patas_corrupted_file`, `rle_corrupted_count_offset`, `test_wal_bc`) and are
  used nowhere; 40 skip for missing extensions (json, tpch, tpcds).
- All F2P files at: base (all fail; the batch process segfaulted at exit, see
  Flakiness), all golds (all
  pass), each single gold (exactly its own F2P flips), each all-but-one (exactly
  the left-out ticket's F2P fails; recorded as failure counts only, because a
  script bug overwrote the per-state reports).

### Validation

`odyssey validate drafts/sequences/duckdb-2026 --image duckdb-2026:base --no-network
--jobs 4 --repeat 3 --subsets --subset-sample 5 --seed 0` on the Docker host: **76/76
checks passed** in 6791 s.
Every check ran with `--network none`, so no build or test needs the network.

- Gold patches touch disjoint hunks; all 112 apply orders work; image contract
  (manifest, tarball, one-commit clean repo, tools, `/memory`, no hidden tests,
  tarball equals `/app`) holds.
- Each ticket: base fails, base + own gold passes, base + all gold passes,
  base + all other gold fails, each **three times with identical exit codes**;
  every failure was a clean one (F2P failed, P2P all passed, no errors).
- Subsets: the sample is the first five single-other-gold subsets per ticket
  (DDB-01: DDB-02..DDB-06; DDB-02: DDB-01, DDB-03..DDB-06; ...; DDB-06 to
  DDB-11: DDB-01..DDB-05), 5 of 1022 each; all leave the ticket failing. The
  interaction experiment above covers every single-gold and every
  all-but-one state at the F2P level.
- The validated image is `duckdb-2026:base`; the final Dockerfile adds one
  layer that deletes `/root/.gitconfig`. That image passed the contract and
  DDB-03's four conditions again (25/25),
  and is what the Harbor smoke built.

### Harbor smoke (Harbor 0.22.0, `scripts/real_sequence/harbor_smoke.sh`, cold tasks 3 at a time)

Ordering DDB-01, DDB-02, DDB-06 under forward-merge and carry (informed
framing, no feedback), and `--design cold` for all eleven tickets: `oracle`
scored 1 with error 0 on every step of every task (2 x 3 sequence steps, 11
cold tasks), `nop` scored 0 with error 0 on every step; every nop grade was a
clean failure (F2P failed, P2P all passed, no errors). Harbor built each task's
environment from `environment/Dockerfile` (the final one, including the
`/root/.gitconfig` layer). Wall time 2291 s for the 26 trials.

## Flakiness

- `--repeat 3` in validate (see above).
- `test/sql/settings/enable_caching_operators.test_slow` (not used) failed in
  2 of 13 interaction states unrelated to the stack.
- The F2P batch run at the base (14 files in one `unittest` process)
  segfaulted after all 14 had run and before the JUnit report was complete;
  the cause was not investigated. The grader runs one process per test file,
  so a crash is charged to one test and counts as its failure.
- The runner sometimes garbles the file name in a failure message (raw
  control bytes in the JUnit XML, ~3% of failing runs); see Independent
  verification. The grader now strips them before parsing.


## Independent verification (2026-11-11)

Re-run by a separate verifier from a fresh copy of the repository and a fresh
`docker build --no-cache` (tag `verify-duckdb-2026:base`, 15 min on the
16-vCPU host, cold `make release` 300 s, `make reldebug` 504 s).

- The image's base manifest is byte-identical to `base.manifest` (15,066 entries).
- `validate --no-network --jobs 4 --repeat 3 --subsets --subset-sample 20 --seed 1`
  (all 10 single-other-gold and all 10 all-but-one subsets per ticket):
  69/76 in 13,706 s. Every exit code was right (no wrong pass); the 7 failed
  checks were unclean failures, an F2P reported `ERROR` instead of `FAIL` in 9
  of ~286 failing grades. Cause (reproduced, 2 of 56 stress runs): the test
  runner's failure message sometimes prints a garbled file name (`at
  \xB0\xBAA\xD2<0x0c>Z:31`) whose raw control byte Catch2 writes into the JUnit
  XML unescaped, so the report does not parse. Fixed in every `run.sh`
  (control bytes other than tab/newline are replaced before parsing); 48/48
  stress runs then classified as `FAIL`, two of them with garbled names.
  Re-validate of the affected tickets with the fixed graders (`--only
  DDB-03,DDB-06,DDB-08,DDB-09,DDB-11`, same options, `--jobs 5`): 46/46 in
  5,500 s. The other six tickets' checks passed in the first run; the fix only
  changes the result when the XML does not parse.
- Harbor 0.22, ordering DDB-08,DDB-05,DDB-07 under forward-merge: oracle 1/1/1,
  nop 0/0/0, error 0 (run twice, before and after the run.sh fix); `--design
  cold`, all 11: oracle 1 and nop 0 on every task, error 0, every nop grade a
  clean F2P failure with all P2P passing.
- Verifier step wall time, cold oracle (run.sh / build): DDB-01 93 s (75/72),
  DDB-02 100 (79/78), DDB-03 50 (26/23), DDB-04 49 (28/26), DDB-05 47 (27/25),
  DDB-06 70 (52/50), DDB-07 83 (63/59), DDB-08 336 (311/307), DDB-09 113
  (90/85), DDB-10 151 (128/127), DDB-11 59 (40/37); nop 43-61 s. Under
  forward-merge, every segment after DDB-08 recompiles DDB-08's headers once
  per container (nop step 2 after DDB-08: 263-277 s).
- Grader robustness (real `setup.sh` + `test.sh` of compiled cold tasks in the
  image): edits to only the F2P test files, `Makefile` and `CMakeLists.txt`
  score 0 (DDB-02, DDB-06, DDB-09); gold plus F2P files rigged to fail scores 1
  (DDB-06). Before `third_party/catch` was protected, making Catch2's `isOk`
  return true (no source change) scored **1** on DDB-02 and DDB-06; after,
  0 on both, and gold still 1.
- Leak audit inside the image: one commit by "Maintainers", no reflog, no
  unreachable objects; no file outside `/app` has any gold-added line or
  hidden-test line (279 patterns, binaries included); the 1,107 ccache results,
  decompressed, contain none of the eight identifiers the gold patches add
  (controls from the same files found); no duckdb package or source elsewhere;
  `/testbed` is an empty directory. The upstream `/testbed` (history to
  2026-07-30) still exists in a lower image layer: invisible in the container,
  but present in an exported image.
- Every ticket's reproduction was run in the base build and with all golds;
  each shows the reported symptom at the base and the
  expected behaviour with the golds.

## Dropped candidates

The textual stack at S held 19 tasks dated June/July 2026. Chosen: the
July ones with a substantial or multi-file fix and a hidden test that is fast and
deterministic, plus the pairs above. Not taken:

| instance | why |
|---|---|
| duckdb__duckdb-23634 (dictionary-compression string stats, 6 files) | Stacked, but the base did not compile: a later upstream commit at S (FSST dictionary compression) calls the helper this fix added to `stats_writer.hpp`. Keeping the helper would mean hand-editing the gold patch, so the ticket was dropped. |
| duckdb__duckdb-23280, -23413, -23422, -23491, -23538 | June 2026 (borderline for the Claude cutoffs); not needed for an 11-ticket pool. |
| duckdb__duckdb-23569 (PEG window clause) | The issue text names the transformer function; the pool already has a window ticket. |
| duckdb__duckdb-23615 (string length stats) | Small; the stats cluster already has three tickets. |
| duckdb__duckdb-24018, -24101, -24266, -23628 | Not in the textual stacked set at S; three of them share `bloom_filters.test` with DDB-01. |
| duckdb__duckdb-23624 | Its files are absent at S. |
| duckdb__duckdb-24150 | A 120k-line diff of committed binaries. |
| duckdb__duckdb-22152 | April 2026. |

Upstream F2P lists were not used as given: duckdb__duckdb-24211 lists
`test/sql/settings/enable_caching_operators.test_slow`, which its fix does not
touch, which passes at the base, and which failed in 2 of 13 states of the
interaction experiment with nothing to do with the stack (flaky); it is in no
grader list.

## Known limitations

- Upstream DuckDB contains all eleven fixes. With the default open egress the
  agent has full internet access and could fetch it; compile with
  `--egress allowlist` (docs/SPEC.md section 11), which limits the agent to the
  model APIs and logs denied connections. Applies to every real-repo sequence.
- Difficulty is unscreened. The "What should happen" sections state exact
  acceptance criteria, which may make tickets easier than the upstream issues.
- The grader trusts the image's ccache and toolchain. A root agent could
  poison or wipe `/var/cache/ccache`; a wiped cache makes the next grade a cold
  build (not measured on 4 cores; 14 min on the loaded 16-vCPU host), which
  would likely exceed the 1440 s run.sh deadline, so such a segment would score
  0 with a grader timeout (SPEC limitation 1).
- Code under test runs inside the test runner process (SPEC limitation 2).
- `make debug`, `relassert` and sanitizer configurations are not cached (release and reldebug are); an agent
  that picks them waits for a cold build.
