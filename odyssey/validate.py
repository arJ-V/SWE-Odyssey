"""Check a sequence against the ticket contract (SPEC section 3).

Per ticket, with run.sh as the judge:
  1. base                      -> fails
  2. base + own gold           -> passes
  3. base + every gold         -> passes
  4. base + every other gold   -> fails
  (--subsets) base + every other subset of the other tickets' gold -> fails,
  which covers every start state forward-merge can produce. That is 2^(T-1)
  grader runs per ticket; --subset-sample K checks a stated sample instead
  (every single other gold, every "all others but one", then random fill).
A "fails" must be the right kind of failure when run.sh writes details.json:
the fail-to-pass tests fail on assertions (no collection or import errors) while
the pass-to-pass tests all pass. Otherwise a hidden test that merely cannot be
imported without the gold patch, or a regression another ticket's gold patch
causes, would count as "fails correctly".
Across tickets: the gold patches touch disjoint hunks (static), and apply
together pairwise in both orders and all-forward / all-reversed (dynamic).

Two runners execute the same bash check scripts:
  - DockerRunner builds (or uses) the sequence image and runs each check in a
    fresh container, in the image's workdir. It also checks the image contract.
  - LocalRunner (--no-docker) runs each check in a fresh temp copy of a tree
    given by --tree (a directory or a tarball). Quick, but only as faithful as
    the host toolchain.
Checks are independent, so --jobs N runs N of them at once (each Docker check
is its own container).
"""

from __future__ import annotations

import json
import random
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import combinations, permutations
from pathlib import Path

from odyssey.compile import base_manifest_text, shell_template, tree_manifest
from odyssey.spec import Sequence, Ticket

APPLY_FAILED = "ODYSSEY_APPLY_FAILED"
DETAILS_BEGIN, DETAILS_END = "ODYSSEY_DETAILS_BEGIN", "ODYSSEY_DETAILS_END"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
_WHOLE_FILE = (0, 10**12)


@dataclass
class Check:
    ticket: str  # "*" for checks that span tickets
    name: str
    ok: bool
    detail: str


# --- static checks -----------------------------------------------------------


def patch_hunks(text: str) -> dict[str, list[tuple[int, int]]]:
    """Map each file a unified/git patch touches to the old-side line ranges of its hunks.

    New, deleted and binary files count as touching the whole file.
    """
    files: dict[str, list[tuple[int, int]]] = {}
    old = current = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            old = current = None
        elif line.startswith("--- "):
            old = line[4:].split("\t")[0].strip()
        elif line.startswith("+++ ") and old is not None:
            new = line[4:].split("\t")[0].strip()
            current = _strip_prefix(new if old == "/dev/null" else old)
            ranges = files.setdefault(current, [])
            if "/dev/null" in (old, new):
                ranges.append(_WHOLE_FILE)
        elif line.startswith(("GIT binary patch", "Binary files ")):
            match = re.search(r" b/(\S+)", line)
            path = current or (match.group(1) if match else "<binary>")
            files.setdefault(path, []).append(_WHOLE_FILE)
        elif current is not None and (m := _HUNK.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or 1)
            files[current].append((start, start + max(count, 1) - 1))
    return files


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def disjoint_hunk_checks(seq: Sequence) -> list[Check]:
    """Hunks (including their context lines) of different tickets must not overlap."""
    hunks = {t.id: patch_hunks(t.gold_patch.read_text()) for t in seq.tickets}
    checks = [
        Check(tid, "gold patch parses", bool(files), f"{len(files)} file(s)")
        for tid, files in hunks.items()
    ]
    clashes = []
    tickets = list(hunks)
    for i, a in enumerate(tickets):
        for b in tickets[i + 1:]:
            for path in set(hunks[a]) & set(hunks[b]):
                if any(_overlaps(x, y) for x in hunks[a][path] for y in hunks[b][path]):
                    clashes.append(f"{a} and {b} overlap in {path}")
    checks.append(Check("*", "gold patches touch disjoint hunks", not clashes, "; ".join(clashes) or "ok"))
    return checks


# --- check scripts (shared by both runners) ----------------------------------


def _git_apply(patch: str, label: str) -> str:
    return (
        f"git -c safe.directory='*' apply --whitespace=nowarn {shlex.quote(patch)} "
        f"|| {{ echo '{APPLY_FAILED} {label}'; exit 98; }}"
    )


def condition_script(tree: str, patches: list[tuple[str, str]], tests_dir: str) -> str:
    """Apply (label, path) patches to `tree`, overlay the hidden files and run run.sh there."""
    q = shlex.quote
    lines = ["set -uo pipefail", f"cd {q(tree)} || exit 97"]
    lines += [_git_apply(path, label) for label, path in patches]
    lines += [
        f"if [ -d {q(tests_dir + '/files')} ]; then cp -a {q(tests_dir + '/files')}/. . || exit 97; fi",
        'ODYSSEY_OUT="$(mktemp -d)"; export ODYSSEY_OUT',
        f"bash {q(tests_dir + '/run.sh')}",
        "status=$?",
        # details.json (if run.sh writes one) tells a clean failure from a crash.
        f'echo {DETAILS_BEGIN}; cat "$ODYSSEY_OUT/details.json" 2>/dev/null; echo; echo {DETAILS_END}',
        "exit $status",
    ]
    return "\n".join(lines)


def apply_orders_script(tree: str, orders: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """For each named order, copy `tree` and apply the patches in sequence; print OK/FAIL lines."""
    q = shlex.quote
    lines = ["set -uo pipefail", 'scratch="$(mktemp -d)"']
    for name, patches in orders:
        applies = " && ".join(
            f"git -c safe.directory='*' apply --whitespace=nowarn {q(path)}" for _, path in patches
        )
        lines += [
            'rm -rf "$scratch/t"; mkdir "$scratch/t"',
            f'cp -a {q(tree)}/. "$scratch/t/"',
            # A repo of its own keeps `git apply` from resolving paths against an enclosing repo.
            '[ -d "$scratch/t/.git" ] || git init -q "$scratch/t"',
            f'if (cd "$scratch/t" && {applies}) >/dev/null 2>&1; then echo "ORDER {name} OK"; '
            f'else echo "ORDER {name} FAIL"; fi',
        ]
    lines.append('rm -rf "$scratch"')
    return "\n".join(lines)


def contract_script(seq: Sequence) -> str:
    """Image contract (SPEC section 3), one CONTRACT line per item."""
    q = shlex.quote
    w, m = q(seq.workdir), q(seq.memory_dir)
    items = []
    lines = ["set -uo pipefail"]
    manifest = base_manifest_text(seq)
    if manifest is not None:
        # The step scripts refuse a base tarball that differs from the compiled
        # manifest (hashed from base_tree, or the checked-in base_manifest).
        lines += [shell_template("lib_manifest.sh"), f"expected_manifest={q(manifest)}"]
        items.append(("base tarball matches base manifest",
                      'd=$(mktemp -d) && tar -xzf /opt/odyssey/base.tar.gz -C "$d" && '
                      '[ "$(tree_manifest "$d")" = "${expected_manifest%$\'\\n\'}" ]'))
    items += [
        ("base tarball present", "test -f /opt/odyssey/base.tar.gz"),
        ("workdir is a git repo with one commit",
         f"test \"$(git -c safe.directory='*' -C {w} rev-list --count HEAD)\" = 1"),
        ("workdir is clean", f"test -z \"$(git -c safe.directory='*' -C {w} status --porcelain)\""),
        ("tools installed", "for t in git bash tar patch sha256sum cmp xargs find; do command -v $t || exit 1; done"),
        ("memory_dir writable", f"test -d {m} && test -w {m}"),
        ("no hidden tests in image", "test ! -d /tests || test -z \"$(ls -A /tests)\""),
        ("base tarball matches workdir",
         "d=$(mktemp -d) && tar -xzf /opt/odyssey/base.tar.gz -C \"$d\" && "
         # --no-dereference: compare symlinks as links, so a repository's
         # deliberately looping test symlink is not a spurious difference.
         f"diff -rq --no-dereference --exclude=.git \"$d\" {w}"),
    ]
    for name, cmd in items:
        lines.append(f"if ( {cmd} ) >/dev/null 2>&1; then echo 'CONTRACT {name} OK'; "
                     f"else echo 'CONTRACT {name} FAIL'; fi")
    return "\n".join(lines)


# --- runners -----------------------------------------------------------------


@dataclass
class RunResult:
    exit_code: int
    output: str


class LocalRunner:
    """Runs checks in temp copies of a pristine tree on the host (--no-docker)."""

    def __init__(self, seq: Sequence, tree: Path):
        self.seq = seq
        self._tmp = Path(tempfile.mkdtemp(prefix="odyssey-validate-"))
        self.pristine = self._tmp / "pristine"
        _extract_tree(Path(tree), self.pristine)

    def tree_checks(self) -> list[Check]:
        """A --tree given for a sequence with a base manifest must be that base snapshot."""
        manifest = base_manifest_text(self.seq)
        if manifest is None:
            return []
        same = tree_manifest(self.pristine) == manifest
        detail = "ok" if same else "the tree differs from the sequence's base manifest"
        return [Check("*", "local tree matches base manifest", same, detail)]

    def path(self, host_path: Path) -> str:
        return str(host_path)

    @property
    def tree(self) -> str:
        return str(self.pristine)

    def run_condition(self, patches: list[tuple[str, str]], tests_dir: str) -> RunResult:
        work = Path(tempfile.mkdtemp(dir=self._tmp, prefix="check-"))
        try:
            tree = work / "tree"
            shutil.copytree(self.pristine, tree, symlinks=True)
            # A repo of its own keeps `git apply` from resolving paths against an enclosing repo.
            subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
            return self.run(condition_script(str(tree), patches, tests_dir))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def run(self, script: str) -> RunResult:
        try:
            proc = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True,
                timeout=self.seq.verifier_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "timed out")
        return RunResult(proc.returncode, proc.stdout + proc.stderr)

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class DockerRunner:
    """Runs each check in a fresh container of the sequence image, sequence dir mounted read-only."""

    MOUNT = "/odyssey_seq"

    def __init__(self, seq: Sequence, image: str | None = None, *, network: bool | None = None):
        self.seq = seq
        # None: follow [environment].allow_internet; False: --network none regardless.
        self.network = seq.environment.allow_internet if network is None else network
        if shutil.which("docker") is None:
            raise RuntimeError("docker not found; use --no-docker (and --tree <path> if the sequence has no base_tree)")
        info = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if info.returncode != 0:
            raise RuntimeError("docker daemon is not reachable; start it or use --no-docker (and --tree <path> if the sequence has no base_tree)")
        self.image = image or self._build()

    def _build(self) -> str:
        env = self.seq.environment
        if env.docker_image:
            return env.docker_image
        if not (env.context / "Dockerfile").is_file():
            raise RuntimeError(f"{env.context} has no Dockerfile; pass --image")
        tag = f"odyssey-validate/{self.seq.id.lower()}:latest"
        print(f"building {tag} from {env.context} ...", flush=True)
        subprocess.run(["docker", "build", "-q", "--label", f"odyssey.sequence={self.seq.id}",
                        "-t", tag, str(env.context)], check=True)
        return tag

    def path(self, host_path: Path) -> str:
        try:
            rel = host_path.resolve().relative_to(self.seq.root)
        except ValueError as exc:
            raise RuntimeError(f"{host_path} is outside the sequence dir; docker mode cannot mount it") from exc
        return f"{self.MOUNT}/{rel.as_posix()}"

    @property
    def tree(self) -> str:
        return self.seq.workdir

    def run_condition(self, patches: list[tuple[str, str]], tests_dir: str) -> RunResult:
        return self.run(condition_script(self.seq.workdir, patches, tests_dir))

    def run(self, script: str) -> RunResult:
        env = self.seq.environment
        # Named and labelled after the sequence, so a shared Docker host can tell
        # whose containers these are, and a timed-out one can be removed.
        name = f"odyssey-validate-{self.seq.id.lower()}-{uuid.uuid4().hex[:10]}"
        cmd = ["docker", "run", "--rm", "--name", name, "--label", f"odyssey.sequence={self.seq.id}",
               "-v", f"{self.seq.root}:{self.MOUNT}:ro", "-w", self.seq.workdir]
        if not self.network:
            cmd += ["--network", "none"]
        if env.cpus:
            cmd += ["--cpus", str(env.cpus)]
        if env.memory_mb:
            cmd += ["--memory", f"{env.memory_mb}m"]
        # The script goes in on stdin, not as an argument: the contract script
        # embeds the base manifest, which for a large repository (DuckDB: 15k
        # files) exceeds the kernel's argument-size limit.
        cmd += ["-i", "--entrypoint", "bash", self.image, "-s"]
        try:
            proc = subprocess.run(cmd, input=script, capture_output=True, text=True,
                                  timeout=self.seq.verifier_timeout_sec)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return RunResult(124, "timed out (container removed)")
        return RunResult(proc.returncode, proc.stdout + proc.stderr)

    def manifest(self) -> str:
        """The hash manifest of the image's base tarball (what `odyssey base-manifest` writes)."""
        script = "\n".join([
            "set -euo pipefail",
            shell_template("lib_manifest.sh"),
            'd="$(mktemp -d)"; tar -xzf /opt/odyssey/base.tar.gz -C "$d"; tree_manifest "$d"',
        ])
        result = self.run(script)
        if result.exit_code != 0:
            raise RuntimeError(f"could not hash /opt/odyssey/base.tar.gz in {self.image}: {result.output[-500:]}")
        return result.output

    def close(self) -> None:
        pass


def _extract_tree(source: Path, dest: Path) -> None:
    """--tree is a directory (copied without .git) or a tarball such as base.tar.gz."""
    if source.is_dir():
        shutil.copytree(source, dest, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    elif tarfile.is_tarfile(source):
        dest.mkdir(parents=True)
        with tarfile.open(source) as tar:
            if hasattr(tarfile, "data_filter"):  # Python >= 3.11.4
                tar.extractall(dest, filter="data")
            else:
                tar.extractall(dest)
    else:
        raise ValueError(f"--tree must be a directory or a tarball: {source}")


# --- the validation itself ---------------------------------------------------


def validate(seq: Sequence, runner, repeat: int = 1, subsets: bool = False, *,
             jobs: int = 1, subset_sample: int | None = None, seed: int = 0,
             only: list[str] | None = None) -> list[Check]:
    """All checks, in a fixed order whatever `jobs` is.

    only: run the per-ticket grader checks for these tickets alone (the other
    tickets' gold patches still take part); the cross-ticket checks always run.

    subsets: also check base + subsets of the other tickets' gold; every proper
    non-empty subset, or with `subset_sample` a sample of that many per ticket
    (see sample_subsets). jobs: grader runs executed concurrently.
    """
    checks = disjoint_hunk_checks(seq)
    if isinstance(runner, DockerRunner):
        checks += _contract_checks(seq, runner)
    if isinstance(runner, LocalRunner):
        checks += runner.tree_checks()
    checks += _apply_order_checks(seq, runner)

    # Plan every grader run first, run them (concurrently), then judge in order.
    plans = []
    for ticket in seq.tickets:
        if only is not None and ticket.id not in only:
            continue
        plans.append(_condition_plan(seq, ticket, repeat))
        if subsets:
            plans.append(_subset_plan(seq, ticket, subset_sample, seed))
    runs = [(ticket, gold) for plan in plans for ticket, gold in plan.runs]
    results = _run_many(seq, runner, runs, jobs)
    offset = 0
    for plan in plans:
        checks += plan.judge(results[offset:offset + len(plan.runs)])
        offset += len(plan.runs)
    return checks


@dataclass
class _Plan:
    runs: list           # (ticket whose grader runs, tickets whose gold is applied)
    judge: object        # results -> list[Check]


def _run_many(seq: Sequence, runner, runs: list, jobs: int) -> list[RunResult]:
    def one(run):
        ticket, gold = run
        return runner.run_condition(_gold(seq, runner, gold), runner.path(ticket.tests_dir))

    if jobs <= 1 or len(runs) <= 1:
        return [one(r) for r in runs]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(one, runs))


def _condition_plan(seq: Sequence, ticket: Ticket, repeat: int) -> _Plan:
    others = [t for t in seq.tickets if t.id != ticket.id]
    conditions = [
        ("base fails", [], False),
        ("base + own gold passes", [ticket], True),
        ("base + all gold passes", list(seq.tickets), True),
        ("base + other gold fails", others, False),
    ]

    def judge(results):
        return [_judge(ticket.id, name, results[i * repeat:(i + 1) * repeat], should_pass)
                for i, (name, _, should_pass) in enumerate(conditions)]

    return _Plan([(ticket, gold) for _, gold, _ in conditions for _ in range(repeat)], judge)


def _condition_checks(seq: Sequence, runner, ticket: Ticket, repeat: int) -> list[Check]:
    plan = _condition_plan(seq, ticket, repeat)
    return plan.judge(_run_many(seq, runner, plan.runs, 1))


def sample_subsets(others: list, sample: int | None, seed: int = 0, key: str = "") -> list[list]:
    """Proper, non-empty subsets of `others` to check, in a deterministic order.

    sample=None: all 2^n - 2 of them. Otherwise at most `sample`: first every
    single other ticket, then every "all others but one" (the states just
    before and just after most of a forward-merge ordering), then a seeded
    random fill from the rest.
    """
    n = len(others)
    every = [list(c) for k in range(1, n) for c in combinations(range(n), k)]
    if sample is None or sample >= len(every):
        return [[others[i] for i in s] for s in every]
    picked: list[tuple] = []
    candidates = [(i,) for i in range(n)]
    candidates += [tuple(j for j in range(n) if j != i) for i in range(n)]
    rest = [tuple(s) for s in every]
    random.Random(f"{seed}:{key}").shuffle(rest)
    for subset in candidates + rest:
        if len(picked) >= sample:
            break
        if 0 < len(subset) < n and subset not in picked:
            picked.append(subset)
    return [[others[i] for i in s] for s in picked]


def _subset_plan(seq: Sequence, ticket: Ticket, sample: int | None, seed: int) -> _Plan:
    """Subsets of the other tickets' gold must still leave this ticket failing.

    Conditions 1 and 4 cover the empty set and the full complement; forward-merge
    starts segments from every prefix of an ordering, i.e. from these subsets.
    With `sample` only a stated sample is checked; the detail names every subset run.
    """
    others = [t for t in seq.tickets if t.id != ticket.id]
    total = 2 ** len(others) - 2
    subsets = sample_subsets(others, sample, seed, key=ticket.id)

    def judge(results):
        bad = []
        for subset, result in zip(subsets, results, strict=True):
            check = _judge(ticket.id, "subset", [result], should_pass=False)
            if not check.ok:
                bad.append("+".join(t.id for t in subset) + f" ({check.detail.split(' | ')[0]})")
        if len(subsets) == total:
            name, detail = "base + any subset of other gold fails", f"{total} subsets"
        else:
            name = "base + sampled subsets of other gold fail"
            detail = (f"sampled {len(subsets)} of {total} subsets (seed {seed}): "
                      + ", ".join("+".join(t.id for t in s) for s in subsets))
        if bad:
            detail += f"; wrong at: {'; '.join(bad)}"
        return [Check(ticket.id, name, not bad, detail)]

    return _Plan([(ticket, s) for s in subsets], judge)


def _subset_check(seq: Sequence, runner, ticket: Ticket, *, sample: int | None = None, seed: int = 0) -> Check:
    plan = _subset_plan(seq, ticket, sample, seed)
    return plan.judge(_run_many(seq, runner, plan.runs, 1))[0]


def _gold(seq: Sequence, runner, tickets) -> list[tuple[str, str]]:
    return [(t.id, runner.path(t.gold_patch)) for t in tickets]


def _contract_checks(seq: Sequence, runner) -> list[Check]:
    output = runner.run(contract_script(seq)).output
    checks = []
    for line in output.splitlines():
        if line.startswith("CONTRACT "):
            name, _, verdict = line[len("CONTRACT "):].rpartition(" ")
            checks.append(Check("*", f"image: {name}", verdict == "OK", verdict))
    if not checks:
        checks.append(Check("*", "image contract", False, output.strip()[-500:]))
    return checks


def _apply_order_checks(seq: Sequence, runner) -> list[Check]:
    tickets = list(seq.tickets)
    orders = [("all forward", _gold(seq, runner, tickets)),
              ("all reversed", _gold(seq, runner, tickets[::-1]))]
    orders += [(f"{a.id} then {b.id}", _gold(seq, runner, [a, b])) for a, b in permutations(tickets, 2)]
    result = runner.run(apply_orders_script(runner.tree, orders))
    failed = [line[len("ORDER "):-len(" FAIL")] for line in result.output.splitlines()
              if line.startswith("ORDER ") and line.endswith(" FAIL")]
    seen = sum(1 for line in result.output.splitlines() if line.startswith("ORDER "))
    ok = seen == len(orders) and not failed
    detail = "ok" if ok else (f"failed: {', '.join(failed)}" if failed else result.output.strip()[-500:])
    return [Check("*", f"gold patches apply together in any order ({len(orders)} orders)", ok, detail)]


def parse_details(output: str) -> dict | None:
    """The details.json that condition_script echoed between markers, if any."""
    if DETAILS_BEGIN not in output:
        return None
    body = output.split(DETAILS_BEGIN, 1)[1].split(DETAILS_END, 1)[0].strip()
    try:
        details = json.loads(body) if body else None
    except json.JSONDecodeError:
        return None
    return details if isinstance(details, dict) else None


def clean_failure(details: dict) -> str | None:
    """Why a must-fail result is the wrong kind of failure, or None if it is a clean one.

    Reads the toy-ledger details.json shape: fail_to_pass / pass_to_pass groups
    with tests, failed and errors counts.
    """
    f2p, p2p = details.get("fail_to_pass"), details.get("pass_to_pass")
    if not isinstance(f2p, dict) or not isinstance(p2p, dict):
        return None  # another grader's format: exit code only
    if f2p.get("errors", 0):
        return f"fail-to-pass errored ({f2p['errors']} collection/import errors) instead of failing"
    if not f2p.get("failed", 0):
        return "fail-to-pass tests did not fail"
    if p2p.get("failed", 0) or p2p.get("errors", 0) or not p2p.get("tests", 0):
        return "pass-to-pass tests did not all pass"
    return None


def _judge(ticket_id: str, name: str, results: list[RunResult], should_pass: bool) -> Check:
    codes = [r.exit_code for r in results]
    apply_failure = next((r.output for r in results if APPLY_FAILED in r.output), None)
    if apply_failure is not None:
        line = next(l for l in apply_failure.splitlines() if APPLY_FAILED in l)
        return Check(ticket_id, name, False, f"patch did not apply: {line.split(APPLY_FAILED)[-1].strip()}")
    passed = [c == 0 for c in codes]
    ok = all(p == should_pass for p in passed)
    detail = f"exit codes {codes}"
    if len(set(passed)) > 1:
        detail += " (flaky)"
    if ok and not should_pass:
        details = [parse_details(r.output) for r in results]
        if any(d is None for d in details):
            detail += " (no details.json: exit code only)"
        else:
            reasons = {reason for d in details if (reason := clean_failure(d))}
            if reasons:
                ok = False
                detail += " but " + "; ".join(sorted(reasons))
    if not ok:
        tail = [l for l in results[-1].output.strip().splitlines()
                if l not in (DETAILS_BEGIN, DETAILS_END)][-5:]
        detail += " | " + " / ".join(tail)
    return Check(ticket_id, name, ok, detail)


def format_report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    rows = [f"{'OK  ' if c.ok else 'FAIL'}  {c.ticket:<12} {c.name:<{width}}  {c.detail}" for c in checks]
    failed = sum(not c.ok for c in checks)
    rows.append(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return "\n".join(rows)
