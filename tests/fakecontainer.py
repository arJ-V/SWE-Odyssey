"""Host-side stand-in for one Harbor multi-step trial's container (used by the compile and integrity tests).

`FakeContainer` lays out a container filesystem under a temp dir and drives the
steps the way Harbor's MultiStepTrial does (upload workdir -> setup.sh -> agent
-> upload tests -> test.sh). Container paths are pointed into the temp dir
through the scripts' ODYSSEY_* layout variables, and the sequence is compiled
with host paths for workdir and memory_dir.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from odyssey.compile import compile_task

BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"


class FakeContainer:
    """One trial's container filesystem under a host temp dir, driven like MultiStepTrial."""

    def __init__(self, root: Path, base_tree: Path, extra_env: dict | None = None):
        self.root = root
        self.app = root / "app"
        self.memory = root / "memory"
        self.state = root / "var-odyssey"
        self.grader = root / "var-lib-grader"
        self.tests = root / "tests"
        self.solution = root / "solution"
        self.verifier = root / "logs-verifier"
        self.scratch = root / "tmp"
        self.extra_env = dict(extra_env or {})
        for d in (self.app, self.memory, self.tests, self.solution, self.verifier, self.scratch, root / "opt"):
            d.mkdir(parents=True)
        self.tarball = root / "opt" / "base.tar.gz"
        subprocess.run(["tar", "--exclude", "__pycache__", "--exclude", ".pytest_cache",
                        "-czf", str(self.tarball), "-C", str(base_tree), "."], check=True)
        subprocess.run(["tar", "-xzf", str(self.tarball), "-C", str(self.app)], check=True)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "snapshot")

    def git(self, *args) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=self.app, check=True, capture_output=True, text=True).stdout

    @property
    def env(self) -> dict:
        return {
            **os.environ,
            "ODYSSEY_BASE_TARBALL": str(self.tarball),
            "ODYSSEY_STATE_DIR": str(self.state),
            "ODYSSEY_GRADER_DIR": str(self.grader),
            "ODYSSEY_TESTS_DIR": str(self.tests),
            "ODYSSEY_SOLUTION_DIR": str(self.solution),
            "ODYSSEY_VERIFIER_DIR": str(self.verifier),
            "ODYSSEY_SCRATCH_ROOT": str(self.scratch),
            "ODYSSEY_STOP_AGENTS": "0",  # never sweep processes on the host running these tests
            **self.extra_env,
        }

    def run(self, *cmd, cwd=None, check=True):
        return subprocess.run([BASH, *cmd], cwd=cwd or self.app, env=self.env,
                              capture_output=True, text=True, check=check)

    def prepare_step(self, step_dir: Path, check=True):
        shutil.copytree(step_dir / "workdir", self.app, dirs_exist_ok=True, symlinks=True)
        return self.run(str(self.app / "setup.sh"), check=check)

    def oracle(self, step_dir: Path) -> None:
        shutil.copytree(step_dir / "solution", self.solution, dirs_exist_ok=True)
        self.run(str(self.solution / "solve.sh"), cwd=self.root)

    def verify(self, step_dir: Path) -> dict:
        for d in (self.tests, self.verifier):
            shutil.rmtree(d)
            d.mkdir()
        shutil.copytree(step_dir / "tests", self.tests, dirs_exist_ok=True, symlinks=True)
        self.run(str(self.tests / "test.sh"))
        return json.loads((self.verifier / "reward.json").read_text())

    def grade(self) -> dict:
        return json.loads((self.verifier / "grade.json").read_text())

    def snapshot(self) -> dict:
        """Content hash of every file under the workdir, .git included."""
        return {str(p.relative_to(self.app)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.app.rglob("*")) if p.is_file() and not p.is_symlink()}


def host_compile(seq, root: Path, ordering, arm, **kwargs) -> Path:
    host_seq = dataclasses.replace(seq, workdir=str(root / "app"), memory_dir=str(root / "memory"))
    return compile_task(host_seq, ordering, arm, root / "tasks", **kwargs)


def manifest(task_dir: Path) -> dict:
    return json.loads((task_dir / "odyssey.json").read_text())


def run_steps(container: FakeContainer, task_dir: Path, agent, inspect=None, check_untouched=True):
    """Run every step; returns per-step (reward, grade, segment.patch), or stops at a failed setup.

    A failed setup.sh is recorded as ("setup failed", stderr, "") and ends the
    trial, as it does in Harbor.
    """
    results = []
    for step in manifest(task_dir)["steps"]:
        step_dir = task_dir / "steps" / step["name"]
        proc = container.prepare_step(step_dir, check=False)
        if proc.returncode != 0:
            results.append(("setup failed", proc.stderr, ""))
            break
        if inspect:
            inspect(container, step)
        agent(container, step_dir, step)
        before = container.snapshot()
        reward = container.verify(step_dir)
        if check_untouched:
            assert container.snapshot() == before, "grading modified the agent's tree"
        assert not list(container.scratch.glob("odyssey-grade-*")), "scratch copy left behind"
        patch = container.verifier / "segment.patch"
        results.append((reward, container.grade(), patch.read_text() if patch.exists() else ""))
    return results


def oracle_agent(container, step_dir, step):
    container.oracle(step_dir)


def noop_agent(container, step_dir, step):
    pass
