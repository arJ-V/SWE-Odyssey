#!/usr/bin/env python3
"""Build a "stacked reverts" base snapshot from already-merged fixes (drafts/docs/REAL_SEQUENCE_GUIDE.md).

Given a clean git checkout of the repo at snapshot S and the per-instance
directories written by extract_instances.py, this reverse-applies the fixes of
the chosen instances, newest first, so that every chosen bug is present at
once. Each instance's diff is split per file into three classes:

  src   the fix proper; reverted in the base, and becomes the ticket's gold.patch
  test  test code (hidden tests, test helpers); reverted in the base where it
        reverses, and the S version goes into the hidden overlay
  drop  reverted in the base only (CI config that names hidden tests, ...);
        neither gold nor hidden

An instance whose src part does not reverse is dropped (reported, not
hand-merged). Test files that do not reverse are kept at S when they are
shared helpers, and deleted when the instance created them.

Writes into --out:
  base.patch             S -> base without deletions (git diff --binary); the image build applies it
  base.deleted           files S has and the base does not (one path per line)
  base.files             the base tree's file list (sanity)
  gold/<id>.patch        src fix, regenerated against the base (clean context)
  hidden/<id>/...        S versions of the instance's own test files plus every
                         shared test file the base changed (repo-relative)
  stack.json             what happened per instance and file
The working repository stays in --out/work (HEAD = base).

Run with the system python3 (stdlib only), from outside the repo:
    python3 -I stack_reverts.py --repo <checkout at S> --instances <dir> \
        --ids 809,851,... --out <dir> [--test-regex ...] [--drop-regex ...] [--own-glob '*.test']
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TEST_REGEX = r"^(tests?/|testing/|.*/tests?/|.*selftest[^/]*$|.*_test\.[a-z]+$|.*\.test$)"
DEFAULT_DROP_REGEX = r"^(\.github/|\.gitlab-ci|\.circleci/|appveyor)"


def sh(args, cwd, check=True, input_text=None):
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, input=input_text)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args))} failed in {cwd}:\n{proc.stdout}{proc.stderr}")
    return proc


def git(work, *args, check=True, input_text=None):
    return sh(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
              cwd=work, check=check, input_text=input_text)


def split_diff(text: str) -> list[tuple[str, str, bool]]:
    """[(path, chunk, created)] for each file of a git diff (path relative to the repo)."""
    chunks = re.split(r"(?m)^(?=diff --git )", text)
    out = []
    for chunk in chunks:
        if not chunk.startswith("diff --git "):
            continue
        m = re.match(r"diff --git a/(\S+) b/(\S+)", chunk)
        path = m.group(2) if m else chunk.split()[2][2:]
        created = bool(re.search(r"(?m)^new file mode ", chunk))
        out.append((path, chunk if chunk.endswith("\n") else chunk + "\n", created))
    return out


def try_reverse(work: Path, chunk: str, fuzz: bool = False) -> str | None:
    """Reverse-apply `chunk` in `work`; return the method that worked, or None."""
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
        f.write(chunk)
        name = f.name
    try:
        for label, args in (("strict", []), ("context1", ["-C1"])):
            if git(work, "apply", "-R", "--check", *args, name, check=False).returncode == 0:
                git(work, "apply", "-R", *args, name)
                return label
        # GNU patch with fuzz, as a last resort; refuse anything that leaves rejects.
        if fuzz and sh(["patch", "-R", "-p1", "--dry-run", "-s", "-f", "-F3", "-i", name],
                       cwd=work, check=False).returncode == 0:
            sh(["patch", "-R", "-p1", "-s", "-f", "-F3", "--no-backup-if-mismatch", "-i", name], cwd=work)
            return "fuzz3"
        return None
    finally:
        Path(name).unlink()


def load_instance(instances: Path, ident: str) -> dict:
    cands = [d for d in instances.iterdir() if d.is_dir() and (d.name == ident or d.name.endswith(f"-{ident}"))]
    if len(cands) != 1:
        raise SystemExit(f"instance {ident!r}: found {len(cands)} matches in {instances}")
    d = cands[0]
    meta = json.loads((d / "meta.json").read_text())
    return {"dir": d, "id": d.name, "meta": meta,
            "patch": (d / "patch.diff").read_text(), "test_patch": (d / "test_patch.diff").read_text()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--instances", required=True, type=Path)
    ap.add_argument("--ids", required=True, help="comma-separated instance ids or numeric suffixes")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--test-regex", default=DEFAULT_TEST_REGEX)
    ap.add_argument("--drop-regex", default=DEFAULT_DROP_REGEX)
    ap.add_argument("--own-glob", default="*.test",
                    help="basename glob of a test file that belongs to one instance (its F2P files always do)")
    ap.add_argument("--fixup", action="append", default=[],
                    help="patch applied to the base after the reverts and the name scrub (repeatable)")
    args = ap.parse_args()
    test_re, drop_re = re.compile(args.test_regex), re.compile(args.drop_regex)

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    work = out / "work"
    work.mkdir(parents=True)
    # Snapshot S as a fresh repository (submodule contents included as plain files).
    sh(["bash", "-c", f"cd {args.repo} && git ls-files -z --recurse-submodules | "
                      f"tar --null -T - -cf - | tar -xf - -C {work}"], cwd=out)
    git(work, "init", "-q")
    git(work, "add", "-A")
    git(work, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-q", "-m", "S")
    s_rev = git(work, "rev-parse", "HEAD").stdout.strip()

    insts = [load_instance(args.instances, i.strip()) for i in args.ids.split(",") if i.strip()]
    insts.sort(key=lambda x: (x["meta"]["created_at"], int(x["meta"].get("pull_number") or 0)), reverse=True)

    report = {"snapshot_tree_commit": s_rev, "instances": {}}
    src_marks: dict[str, tuple[str, str]] = {}
    own: dict[str, set[str]] = {}
    for inst in insts:
        iid = inst["id"]
        rec = {"created_at": inst["meta"]["created_at"], "files": {}, "status": "kept"}
        files = split_diff(inst["patch"]) + split_diff(inst["test_patch"])
        src = [(p, c) for p, c, _ in files if not test_re.search(p) and not drop_re.search(p)]
        before = git(work, "rev-parse", "HEAD").stdout.strip()
        if not src:
            rec["status"] = "dropped: no source change"
        else:
            how = try_reverse(work, "".join(c for _, c in src))
            if how is None:
                rec["status"] = "dropped: source fix does not reverse at S (after newer reverts)"
                git(work, "checkout", "-q", "--", ".")
            else:
                for p, _ in src:
                    rec["files"][p] = f"src reverted ({how})"
        if rec["status"] != "kept":
            report["instances"][iid] = rec
            print(f"{iid}: {rec['status']}", file=sys.stderr)
            continue
        git(work, "add", "-A")
        git(work, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-q", "-m", f"revert src {iid}")
        after = git(work, "rev-parse", "HEAD").stdout.strip()
        src_marks[iid] = (after, before)

        f2p = {Path(t).name for t in inst["meta"].get("FAIL_TO_PASS") or []}
        own[iid] = set()
        for p, chunk, created in files:
            if not (test_re.search(p) or drop_re.search(p)):
                continue
            kind = "drop" if drop_re.search(p) else "test"
            if kind == "test" and (Path(p).name in f2p or (created and fnmatch.fnmatch(Path(p).name, args.own_glob))):
                own[iid].add(p)
            how = try_reverse(work, chunk, fuzz=True)
            if how is not None:
                rec["files"][p] = f"{kind} reverted ({how})"
            elif created and (work / p).exists():
                (work / p).unlink()
                rec["files"][p] = f"{kind} created by the instance; deleted (later edits made it irreversible)"
            else:
                rec["files"][p] = f"{kind} NOT reverted; kept at S"
        # F2P tests the test patch did not touch but that exist (e.g. an older test the fix flips)
        for name in f2p:
            for cand in work.glob(f"**/{name}"):
                rel = cand.relative_to(work).as_posix()
                if test_re.search(rel):
                    own[iid].add(rel)
        git(work, "add", "-A")
        git(work, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-q", "--allow-empty", "-m", f"revert tests {iid}")
        report["instances"][iid] = rec
        print(f"{iid}: kept; own tests {sorted(own[iid])}", file=sys.stderr)

    # An instance's own test that the base no longer has must not stay named in
    # test lists, Makefiles or CI config (that would leak the hidden test's name
    # and break the visible suite).
    kept_ids = [i for i in src_marks]
    gone = sorted({Path(p).name for i in kept_ids for p in own[i] if not (work / p).exists()})
    scrubbed = []
    if gone:
        token_re = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(g) for g in gone) + r")(?![\w.-])")
        for rel in git(work, "ls-files").stdout.split("\n"):
            if not rel or not (test_re.search(rel) or drop_re.search(rel) or Path(rel).name.startswith("Makefile")):
                continue
            path = work / rel
            try:
                text = path.read_text()
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            if not token_re.search(text):
                continue
            lines = []
            for line in text.splitlines(keepends=True):
                if token_re.search(line):
                    stripped = token_re.sub("", line)
                    scrubbed.append(f"{rel}: {line.strip()}")
                    # a line that only named the test (e.g. "TESTS += x.test") goes entirely
                    if re.fullmatch(r"\s*([A-Za-z_]+\s*\+?=)?\s*\\?\s*", stripped):
                        continue
                    line = re.sub(r"[ \t]{2,}", " ", stripped)
                lines.append(line)
            path.write_text("".join(lines))
        git(work, "add", "-A")
        git(work, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-q", "--allow-empty", "-m", "scrub names")
    report["scrubbed_lines"] = scrubbed

    # Hand-written fixups the author checked in (e.g. build rules for a helper
    # the revert removed); each must apply cleanly to the scrubbed base.
    for fix in args.fixup:
        git(work, "apply", "--whitespace=nowarn", str(Path(fix).resolve()))
        git(work, "add", "-A")
        git(work, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-q", "-m", f"fixup {Path(fix).name}")
    report["fixups"] = [Path(f).name for f in args.fixup]

    base_rev = git(work, "rev-parse", "HEAD").stdout.strip()
    # base.patch carries modifications and additions only; deleted files (the
    # hidden tests the instances added) are listed by name in base.deleted, so
    # their content never sits in the image build context.
    (out / "base.patch").write_text(git(work, "diff", "--binary", "--no-renames", "--diff-filter=d",
                                        s_rev, base_rev).stdout)
    (out / "base.deleted").write_text(git(work, "diff", "--name-only", "--no-renames", "--diff-filter=D",
                                          s_rev, base_rev).stdout)
    (out / "base.files").write_text(git(work, "ls-files").stdout)

    # Gold patches, regenerated against the base so their context is the base's.
    gold_dir = out / "gold"
    gold_dir.mkdir()
    for iid, (after, before) in src_marks.items():
        raw = git(work, "diff", "--binary", after, before).stdout
        scratch = out / "scratch"
        if scratch.exists():
            shutil.rmtree(scratch)
        git(work, "worktree", "add", "-q", "--detach", str(scratch), base_rev)
        try:
            ok = git(scratch, "apply", "--check", "-", input_text=raw, check=False).returncode == 0
            if not ok:
                report["instances"][iid]["status"] = "dropped: gold does not apply to the stacked base"
                print(f"{iid}: gold does not apply to the base", file=sys.stderr)
                continue
            git(scratch, "apply", "-", input_text=raw)
            git(scratch, "add", "-A")
            gold = git(scratch, "diff", "--cached", "--binary", base_rev).stdout
            (gold_dir / f"{iid}.patch").write_text(gold)
        finally:
            git(work, "worktree", "remove", "--force", str(scratch))

    # Hidden overlays: S versions of every test file the base changed, minus other
    # instances' own tests, plus this instance's own tests.
    changed = [p for p in git(work, "diff", "--name-only", s_rev, base_rev).stdout.split("\n")
               if p and test_re.search(p)]
    kept = [i for i in src_marks if report["instances"][i]["status"] == "kept"]
    all_own = set().union(*(own[i] for i in kept)) if kept else set()
    for iid in kept:
        files = (set(changed) - (all_own - own[iid])) | own[iid]
        dest = out / "hidden" / iid
        for p in sorted(files):
            blob = git(work, "cat-file", "-e", f"{s_rev}:{p}", check=False)
            if blob.returncode != 0:
                continue  # not at S (deleted later upstream): nothing to restore
            target = dest / p
            target.parent.mkdir(parents=True, exist_ok=True)
            content = subprocess.run(["git", "show", f"{s_rev}:{p}"], cwd=work, capture_output=True, check=True).stdout
            target.write_bytes(content)
            mode = git(work, "ls-tree", s_rev, "--", p).stdout.split()[0]
            if mode == "100755":
                target.chmod(0o755)
        report["instances"][iid]["own_tests"] = sorted(own[iid])
        report["instances"][iid]["hidden_files"] = sorted(files)
    report["base_tree_commit"] = base_rev
    report["shared_test_files_changed"] = sorted(set(changed) - all_own)
    (out / "stack.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({i: r["status"] for i, r in report["instances"].items()}, indent=1))


if __name__ == "__main__":
    main()
