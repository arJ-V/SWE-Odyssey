# Building a real-repo sequence ("stacked reverts")

This is the recipe used for `drafts/sequences/httrack-2026` (C, autotools, automake test
harness), written so the next builder can repeat it for another repo and
language. Read `docs/SPEC.md` first; this guide only adds what a real repository
needs. Times are what httrack took on a remote 16-vCPU x86_64 Docker host that
other jobs were also using.

## 0. The construction in one paragraph

Take one commit **S** of the upstream repo, shortly after the tasks you want.
Reverse-apply the source fixes of N already-merged tasks so all N bugs are
present at once; revert their test changes; that tree is the **base**. Each
task's fix becomes that ticket's `gold.patch` (regenerated against the base),
and its tests (S versions) become the ticket's hidden overlay. The image fetches
S, applies `base.patch`, deletes the tickets' own test files, and keeps only the
resulting tree. Everything is then proven with `odyssey validate` in Docker.

## 1. Pick the repo, S and candidates (30 min)

- Source rows: SWE-bench-Live/MultiLang parquet files (one per language split).
  Check per-repo density (enough tasks in a short window) and whether the
  candidates' fixes reverse-apply textually at a single commit.
- Prefer tasks dated after every model's cutoff; multi-file fixes; tests that
  are one new test file each (cheap to hide) over edits to shared test files or
  to test code compiled into the product (httrack's `src/htsselftest.c`).
- Drop rows whose P2P is 0 and F2P is the whole suite (broken environment), and
  generated-file diffs.
- S must be within days of the tasks when the repo churns; check that every
  source file the tasks touch exists at S.

```sh
cd /tmp   # never run python from inside the untrusted data directory
uv run --python 3.12 --with pyarrow python -I \
    <odyssey>/scripts/real_sequence/extract_instances.py \
    --parquet <data>/c.parquet --repo xroche/httrack --since 2026-06-01 --out <work>/instances
```

This writes `<work>/instances/<id>/{meta.json,patch.diff,test_patch.diff,problem.md}`
and an `index.tsv` (date, files, lines, F2P/P2P counts, image). Read the file
lists (`grep '^diff --git'`) to see which tasks only add a test file.

## 2. Docker host setup (5 min)

The upstream images are amd64; Docker work here ran on a remote x86_64 Docker
host reached over ssh (`<host>` below). Keep everything under
`~/work/<sequence-id>/`, tag images `<sequence-id>:<tag>`, and never prune or
touch other images/containers if the host is shared. Long commands:
`setsid nohup sh -c '...; echo $? > x.done' < /dev/null > /dev/null 2>&1 &`
then poll `x.done` with short ssh calls (a plain `nohup ... &` without
`setsid`/`< /dev/null` can keep the ssh session open).

```sh
ssh <host> 'mkdir -p ~/work/<seq> && cd ~/work/<seq> && git clone -q <repo-url> repo \
  && cd repo && git checkout -q <S> && git submodule update --init'
rsync -a <work>/instances/ <host>:work/<seq>/instances/
rsync -a <odyssey>/scripts/real_sequence/ <host>:work/<seq>/tools/
ssh <host> 'docker pull <upstream image of the newest task>'      # httrack: 0.8 GB, 1 min
```

Inspect the upstream image: where the repo lives (`/testbed`), whether a build
tree or an installed copy exists (`find / -name 'lib<proj>*'`), which tools are
present (`git bash tar patch sha256sum cmp find xargs` are required by SPEC 3).

## 3. Measure the build and the suite at S (10 min)

Run the dataset's `rebuild_cmds` / `test_cmds` by hand in the upstream image on
an export of S, offline (`--network none`), and time them. httrack: bootstrap
5 s, configure 4 s, `make -j4` 14 s, full `make check -j8` 85 s with 203 PASS
and 10 SKIP (network tests). That decides two things:

- whether the grader can do a full clean rebuild per grade (yes for httrack;
  for a 10-minute C++ build, keep a build directory in the image *built from the
  bugged base* outside the workdir and rebuild incrementally from it, and record
  the wall time);
- which tests skip or need the network (never use those as P2P).

## 4. Stack the reverts (1 min per attempt)

```sh
ssh <host> 'cd /tmp && python3 -I ~/work/<seq>/tools/stack_reverts.py \
  --repo ~/work/<seq>/repo --instances ~/work/<seq>/instances \
  --ids 809,830,851,... [--fixup <patch>]... --out ~/work/<seq>/stackN'
```

`stack_reverts.py` (stdlib only):

- splits every task's patch per file into **src** (gold), **test** (hidden) and
  **drop** (CI config such as `.github/`; reverted, neither gold nor hidden)
  using `--test-regex` / `--drop-regex` (defaults cover `tests/`, `*selftest*`,
  `*_test.*`, `*.test`);
- reverts newest first; a src part that does not reverse drops the task (it is
  never hand-merged); test parts fall back to `git apply -C1`, then `patch -F3`,
  then "delete if the task created the file", else "kept at S" (reported);
- scrubs the names of deleted own tests from test lists, Makefiles and CI
  config (`TESTS += x.test` lines, workflow lists);
- applies `--fixup` patches you write (see pitfalls), then writes
  `base.patch` (modifications only), `base.deleted` (removed files, by name, so
  hidden test content never sits in the build context), `gold/<id>.patch`
  (regenerated against the base), `hidden/<id>/...` (S versions of the task's
  own tests plus every shared test file the base changed) and `stack.json`
  (what happened to each file).

Read `stack.json`: every "NOT reverted; kept at S" on a shared helper means the
visible tree still holds that task's test infrastructure (a hint, sometimes a
broken build); every "kept at S" on an own test means the visible test still
contains the new assertions: fix it with a fixup or drop the task.

## 5. Interaction experiment before choosing the pool (20-30 min, optional but cheap)

Before committing to a pool, run the **whole suite** on: base, base+all gold,
and for each candidate base+own gold and base+all-other gold (one container
each, 4 in parallel). For httrack this showed each gold flips exactly its own
tests, except that one task's test (chunked truncation) needed another task's
fix (update purge) to pass: dropping the latter from the pool, so that its fix
stays in the base, removed the dependency. Script:
`scripts/real_sequence/full_suite.sh <name> <gold ids...>` (copies the base,
applies golds, overlays S's test dir, runs the suite, records one result line
per test; build, check and result commands are overridable for other
harnesses). Drive it with `xargs -P4 -L1` over a job file whose lines have no
trailing spaces (a trailing blank makes xargs join the next line).

## 6. Author the sequence directory

```
sequences/<seq>/
  sequence.toml           base_manifest = "base.manifest", [grading] for the language
  base.manifest           written in step 7
  environment/Dockerfile  multi-stage, see below
  environment/base.patch, base.deleted   from stack_reverts.py
  tickets/<id>/ticket.md  hand-written (step 8)
  tickets/<id>/gold.patch, tests/run.sh, tests/files/...   from source/assemble.sh
  notes/ORACLE_NOTES.md   30-60 lines of orientation, nothing ticket-specific
  source/<id>/original_issue.md   upstream text, unmodified (never compiled)
  source/assemble.sh      ticket table: id, instance, F2P, P2P
  source/fixups/*.patch   hand fixes applied by stack_reverts.py --fixup
  README.md               provenance, mapping, drops, timings, flakiness
```

**Dockerfile** (`drafts/sequences/httrack-2026/environment/Dockerfile` is the
template): `FROM <upstream>@sha256:<digest> AS source`, `git fetch --depth 1`
S (and submodules at their pinned commits), `git apply --index base.patch`,
`git rm` the `base.deleted` files, export `git ls-files --recurse-submodules`
to `/base`. Final stage `FROM` the same digest: delete the upstream repo
(`rm -rf /testbed`; it holds later history and a build of fixed code), set a
neutral system git identity, `COPY --from=source /base/ /app/`, tar it to
`/opt/odyssey/base.tar.gz`, one neutral commit, `/memory`. The build needs
network for the fetch only; say so in the README. Pin the upstream image by the
digest `docker image inspect --format '{{json .RepoDigests}}'` prints.

Keep the base **source only**. Generated build files and objects embed dates
and paths, so the manifest would not survive a rebuild.

**run.sh** (`scripts/real_sequence/templates/run_automake.sh`, rendered per
ticket by `render_run_sh.py`): the grading copy has `tests/`, `.gitignore`s and
self-test sources restored from the trusted tree and the hidden files overlaid;
the script deletes git-ignored files using a git dir of its own outside the
tree (never a `.git` found in the copy), runs the system `autoreconf`, builds
out of tree, runs `make check TESTS="..."` for the F2P group and the P2P group,
and decides from the automake `.trs` files, never from make's exit code. It
writes `details.json` in the SPEC 3 shape (`tests/passed/failed/errors/skipped`
per group, plus build status and seconds). For another harness, copy the
template and change only the build and the result reader (CTest: `--output-junit`;
cargo: `--message-format json`; jest/vitest: `--json`), keeping the rules: rebuild
from source, trust only restored test files, a missing result is an error, a
skip is not a pass.

**P2P**: about 15 tests per ticket: a common core of fast tests that touch
nothing in the pool, plus the area's tests. Never list another ticket's F2P
(it is not in the visible tree) and never a test that skips offline.

**[grading]**: `protected_paths` = the test directory plus any test code compiled
into the product; `protected_names` = the files the grader relies on that may
live anywhere (httrack: `.gitignore`). The Python defaults do not apply.

## 7. Build the image and write the manifest (3-4 min)

```sh
rsync -a <odyssey>/ <host>:work/<seq>/odyssey/
ssh <host> 'cd ~/work/<seq>/odyssey/sequences/<seq>/environment && docker build -t <seq>:base .'
ssh <host> 'cd ~/work/<seq>/odyssey && uv run --python 3.12 python -m odyssey.cli \
  base-manifest sequences/<seq> --image <seq>:base --out sequences/<seq>/base.manifest'
rsync -a <host>:work/<seq>/odyssey/sequences/<seq>/base.manifest <odyssey>/sequences/<seq>/
```

Rebuild once with `--no-cache` and re-run `base-manifest`: the two must be
identical, or Harbor's own build of the environment will fail `setup.sh`.

## 8. Write the tickets (the slow part: 15-30 min each)

Upstream issues written by agents name the function and line; rewrite them as
a user's report: what they ran, what happened, what should happen, with a
reproduction. Then read the hidden tests line by line and make sure every
observable they assert can be inferred from the text: exact log or error
wording the tests grep for, exit codes, file sizes, protocol bytes, content
types, edge cases (boundary sizes, empty input, platform fallbacks). Put those
in an "expected" section as the maintainer's acceptance criteria. Never name a
source file, function or line, never include the fix, the hidden test names,
benchmark ids or upstream issue/PR numbers. Keep the original text unmodified
in `source/<id>/original_issue.md`.

Leak scan of the base: grep the base tree for each task's upstream issue
numbers and for phrases of its fix. httrack's `history.txt` release notes for
the snapshot's release described every fix with its issue number: a fixup patch
(`source/fixups/01-release-notes.patch`) removes those clauses. Comments in
other tests that mention an open bug (one did) are realistic and stay; list them
in the README.

## 9. Validate (Docker, on the host)

```sh
# iteration
odyssey validate sequences/<seq> --image <seq>:base --no-network --jobs 4 --only HTT-01
# full (httrack: 10 tickets, 4 conditions x 3 repeats + 20 sampled subsets each)
odyssey validate sequences/<seq> --image <seq>:base --no-network --jobs 4 \
    --repeat 3 --subsets --subset-sample 20 --seed 0
```

Time on httrack (one grade 35-80 s): a single pass (40 grader runs plus the
image contract and 92 apply orders) 12 min; the full run above (320 grader
runs) 75 min at `--jobs 4`, with other jobs on the host. Start long runs with
`setsid nohup ... < /dev/null &` and poll a done-file.

`--no-network` proves no check needs the network. `--repeat 3` covers the
flakiness requirement (any disagreement between repeats is marked `(flaky)`).
`--subset-sample` states exactly which subsets ran in each check's detail.

## 10. Harbor smoke (oracle 1, nop 0)

Compile one 3-ticket ordering under `forward-merge` and `carry`, plus
`--design cold` for the same tickets, then on the host:

```sh
harbor run -p <task> -a oracle -n 1     # every step reward 1
harbor run -p <task> -a nop -n 1        # every step reward 0
```

Harbor builds the environment from each task's `environment/` (the same
Dockerfile), so the base-manifest determinism of step 7 matters here.
`scripts/real_sequence/harbor_smoke.sh <seq-dir> <T1,T2,T3> <jobs-dir> [cold ids]`
does all of it and prints one line per trial (httrack: 2 sequence tasks and 10
cold tasks, oracle and nop, 33 min). The per-ticket grader wall time for the
README comes from `details.json` (`seconds`, `build.seconds`) in each cold
trial's verifier output.

## 11. Pull the evidence

Copy the validate report, the Harbor job dirs and the experiment runs back
into `jobs/real-build/<seq>/` (gitignored), check them for key material
(`grep -r 'sk-ant\|AKIA'`), and write the sequence README from them: only
claims you ran.

## Pitfalls met on httrack

- The upstream repo had a submodule: fetch it at the commit S pins, and export
  with `--recurse-submodules`, or the base silently lacks it.
- Test lists outlived the reverts (`TESTS += <hidden>.test`, CI workflow lists):
  the stacker scrubs names; check `stack.json` `scrubbed_lines`.
- A helper library a reverted test introduced was still referenced by the
  visible `Makefile.am`: write a fixup, or drop the task.
- Two tasks interacted (one test needed another task's fix): found only by the
  full-suite experiment; drop one of them from the pool.
- The test harness reported a test as F2P that passes at the stacked base
  (`84_webhttrack-mirror-verbatim` for one task): use the measured base, not the
  dataset's F2P list.
- In validate, the grader runs in the image's `/app`, which has a `.git` owned
  by root; a grader that calls `git` in the tree hits "dubious ownership".
  Use a git dir of your own (`--git-dir=$work/x --work-tree=$src`).
- On a shared host, other jobs' containers change CPU load and therefore the
  timing of race-sensitive tests; repeat runs are the only flakiness evidence.
- Agents get internet in the agent container (they need their model API), so
  with the default open egress nothing stops an agent from fetching upstream's
  fixed source. Compile real-repo runs with `--egress allowlist` (docs/SPEC.md
  section 11), which confines the agent to the model APIs and logs denied
  attempts; the sequence's Dockerfile must then have no `USER` instruction and
  the context no compose file of its own.
