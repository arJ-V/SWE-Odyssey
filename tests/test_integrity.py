"""Grading integrity on toy-ledger: oracle/nop controls and adversarial agents.

Each test compiles toy-ledger with host paths and drives the real step scripts
through `fakecontainer.FakeContainer` (as Harbor's MultiStepTrial would). The
graders run pytest with this interpreter, which has pytest installed.

An attack passes the test when it does not earn a reward it did not work for
(or, for the git and process attacks, when its code never runs).
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

import pytest

from fakecontainer import FakeContainer, host_compile, noop_agent, oracle_agent, run_steps
from odyssey.compile import CODE_STATES, RESET_POLICIES, ArmOptions
from odyssey.design import explicit
from odyssey.spec import load_sequence

SEQ_DIR = Path(__file__).resolve().parents[1] / "drafts" / "sequences" / "toy-ledger"
BASE = SEQ_DIR / "environment" / "repo"
ORDER = ("LED-1", "LED-3", "LED-4")
ONE = ("LED-1",)
ZERO = {"reward": 0, "error": 0}
ONE_REWARD = {"reward": 1, "error": 0}


@pytest.fixture(scope="module")
def seq():
    return load_sequence(SEQ_DIR)


def trial(seq, tmp_path, agent, ordering=ORDER, code_state="forward-merge", env=None, **kwargs):
    container = FakeContainer(tmp_path / "c", BASE, extra_env={"ODYSSEY_PYTHON": sys.executable, **(env or {})})
    task_dir = host_compile(seq, tmp_path / "c", explicit(ordering), ArmOptions(code_state=code_state),
                            allow_leak=True)
    return container, run_steps(container, task_dir, agent, **kwargs)


def rewards(results) -> list:
    return [r[0] for r in results]


# --- controls: the end-to-end oracle/nop runs for every policy ---------------------


@pytest.mark.parametrize("code_state", CODE_STATES)
def test_oracle_scores_one_and_nop_scores_zero(seq, tmp_path, code_state):
    _, oracle = trial(seq, tmp_path / "oracle", oracle_agent, code_state=code_state)
    assert rewards(oracle) == [ONE_REWARD] * 3
    assert all(grade["segment_patch"] == "ok" for _, grade, _ in oracle)
    _, nop = trial(seq, tmp_path / "nop", noop_agent, code_state=code_state)
    assert rewards(nop) == [ZERO] * 3


# --- the grading copy uses trusted test infrastructure ----------------------------


def ini_collect_only(c, step_dir, step):
    ini = c.app / "pytest.ini"
    ini.write_text(ini.read_text().replace("addopts = -q -p no:cacheprovider",
                                           "addopts = -q -p no:cacheprovider --co"))


def root_conftest(c, step_dir, step):
    (c.app / "conftest.py").write_text("import os\nos._exit(0)\n")


def edit_visible_conftest(c, step_dir, step):
    with open(c.app / "tests" / "conftest.py", "a") as f:
        f.write("\nimport os\nos._exit(0)\n")


def shadow_pytest_at_root(c, step_dir, step):
    (c.app / "pytest.py").write_text("raise SystemExit(0)\n")


def shadow_pytest_in_lib(c, step_dir, step):
    (c.app / "lib" / "pytest.py").write_text("raise SystemExit(0)\n")


def sitecustomize_in_lib(c, step_dir, step):
    (c.app / "lib" / "sitecustomize.py").write_text("import os\nos._exit(0)\n")


def exit_zero_at_import(c, step_dir, step):
    init = c.app / "lib" / "ledgerly" / "__init__.py"
    init.write_text("import os\nos._exit(0)\n" + init.read_text())


def preplant_hidden_dir(c, step_dir, step):
    hidden = c.app / "tests_hidden"
    hidden.mkdir()
    (hidden / "conftest.py").write_text("import os\nos._exit(0)\n")
    (hidden / "pytest.ini").write_text("[pytest]\naddopts = --co\n")


@pytest.mark.parametrize("attack", [
    ini_collect_only, root_conftest, edit_visible_conftest, shadow_pytest_at_root, shadow_pytest_in_lib,
    sitecustomize_in_lib, exit_zero_at_import, preplant_hidden_dir,
], ids=lambda f: f.__name__)
def test_grader_ignores_the_agents_test_infrastructure(seq, tmp_path, attack):
    _, results = trial(seq, tmp_path, attack, ordering=ONE)
    assert rewards(results) == [ZERO]


def test_deleting_visible_tests_does_not_shrink_the_graded_suite(seq, tmp_path):
    def p2p_count(container):
        return json.loads((container.verifier / "details.json").read_text())["pass_to_pass"]["tests"]

    honest, results = trial(seq, tmp_path / "honest", oracle_agent, ordering=ONE)
    assert rewards(results) == [ONE_REWARD]

    def oracle_then_gut_tests(c, step_dir, step):
        c.oracle(step_dir)
        for test_file in (c.app / "tests").glob("test_*.py"):
            test_file.write_text("")

    gutted, results = trial(seq, tmp_path / "gutted", oracle_then_gut_tests, ordering=ONE)
    assert rewards(results) == [ONE_REWARD]
    assert p2p_count(gutted) == p2p_count(honest) > 0


def test_symlinked_test_dirs_cannot_redirect_the_grader(seq, tmp_path):
    outside = tmp_path / "outside"

    def symlink_tests(c, step_dir, step):
        outside.mkdir()
        (outside / "test_money.py").write_text("def test_ok():\n    pass\n")
        shutil.rmtree(c.app / "tests")
        (c.app / "tests").symlink_to(outside)
        (c.app / "tests_hidden").symlink_to(outside)

    _, results = trial(seq, tmp_path, symlink_tests, ordering=ONE)
    assert rewards(results) == [ZERO]
    assert sorted(p.name for p in outside.iterdir()) == ["test_money.py"], "grader wrote outside its copy"


# --- the base snapshot cannot be doctored --------------------------------------------


def poison_base_tarball(c, step_dir, step):
    """Segment 1: add an always-pass conftest.py to the base tarball (a root agent can)."""
    if step["position"] != 1:
        return
    raw = gzip.decompress(c.tarball.read_bytes())
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw)) as src, tarfile.open(fileobj=buf, mode="w") as dst:
        for member in src.getmembers():
            dst.addfile(member, src.extractfile(member) if member.isfile() else None)
        data = b"import os\nos._exit(0)\n"
        info = tarfile.TarInfo("./conftest.py")
        info.size = len(data)
        dst.addfile(info, io.BytesIO(data))
    c.tarball.write_bytes(gzip.compress(buf.getvalue()))


@pytest.mark.parametrize("code_state", CODE_STATES)
def test_doctored_base_tarball_is_detected(seq, tmp_path, code_state):
    _, results = trial(seq, tmp_path, poison_base_tarball, code_state=code_state)
    first_reward, first_grade, _ = results[0]
    assert first_reward == {"reward": 0, "error": 1}
    assert "manifest" in first_grade["error"]
    if code_state in RESET_POLICIES:
        # The next setup refuses to build a tree from it; Harbor stops the trial there.
        assert results[1][0] == "setup failed" and "manifest" in results[1][1]
    else:
        # carry never rebuilds from the tarball, but every grade still checks it.
        assert all(r == {"reward": 0, "error": 1} for r in rewards(results))


# --- git config the agent controls never runs ------------------------------------------


@pytest.mark.parametrize("code_state", ["forward-merge", "carry"])
def test_agent_git_config_never_runs(seq, tmp_path, code_state):
    home = tmp_path / "home"
    home.mkdir()
    markers = tmp_path / "markers"
    markers.mkdir()

    def plant(c, step_dir, step):
        hook = home / "hook.sh"
        hook.write_text(f"#!/bin/sh\ntouch {markers}/ran-$$\nexit 1\n")
        hook.chmod(0o755)
        # Global config (root's ~/.gitconfig in the container) and the repo's own config.
        (home / ".gitconfig").write_text(f"[core]\n\tfsmonitor = {hook}\n\thooksPath = {home}\n")
        shutil.copy(hook, home / "pre-commit")
        subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=c.app, check=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"})
        (c.app / ".gitattributes").write_text("*.py filter=evil\n")
        subprocess.run(["git", "config", "filter.evil.clean", str(hook)], cwd=c.app, check=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"})

    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
    _, results = trial(seq, tmp_path, plant, code_state=code_state, env=env)
    assert all(r != "setup failed" for r in rewards(results)), results
    assert list(markers.iterdir()) == [], "agent-controlled git config ran inside setup.sh or test.sh"


def test_carry_survives_broken_git_state(seq, tmp_path):
    def oracle_then_break_git(c, step_dir, step):
        c.oracle(step_dir)
        (c.app / ".git" / "index.lock").write_text("")
        (c.app / ".git" / "rebase-merge").mkdir()

    _, results = trial(seq, tmp_path, oracle_then_break_git, code_state="carry")
    assert rewards(results) == [ONE_REWARD] * 3


# --- processes the agent leaves behind ---------------------------------------------------


def test_left_behind_process_cannot_rewrite_the_reward(seq, tmp_path):
    token = f"odyssey-daemon-{uuid.uuid4().hex}"
    daemons: list[subprocess.Popen] = []

    def spawn(c, step_dir, step):
        out = c.verifier / "reward.json"
        loop = (f"# {token}\nend=$(( $(date +%s) + 30 )); while [ $(date +%s) -lt $end ]; do "
                f"printf '{{\"reward\": 1, \"error\": 0}}\\n' > {out} 2>/dev/null; sleep 0.02; done")
        daemons.append(subprocess.Popen(["sh", "-c", loop], start_new_session=True))

    env = {"ODYSSEY_STOP_AGENTS": "1", "ODYSSEY_SWEEP_MATCH": token}
    try:
        container, results = trial(seq, tmp_path, spawn, ordering=("LED-1", "LED-3"), env=env)
        time.sleep(0.5)
        assert json.loads((container.verifier / "reward.json").read_text()) == ZERO
        assert rewards(results) == [ZERO, ZERO]
        assert all(d.poll() is not None for d in daemons), "the sweep left a daemon running"
    finally:
        for d in daemons:
            if d.poll() is None:
                d.kill()
