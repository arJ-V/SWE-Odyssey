"""Compile tests: generated structure, payload safety, and a host-side run of the step scripts.

The step scripts are executed for real by `fakecontainer.FakeContainer` on the
mini-seq fixture (shell-only graders). tests/test_integrity.py runs the same
scripts on toy-ledger against adversarial agents.
"""

import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from fakecontainer import (
    BASH, FakeContainer, host_compile, manifest, noop_agent, oracle_agent, run_steps,
)
from odyssey import cli
from odyssey.compile import CODE_STATES, ArmOptions, compile_task, tree_manifest
from odyssey.design import cold, explicit, williams
from odyssey.spec import load_sequence

FIXTURE = Path(__file__).parent / "fixtures" / "mini-seq"
BASE = FIXTURE / "environment" / "base"
ORDER = ("MINI-2", "MINI-1", "MINI-3")


@pytest.fixture(scope="module")
def seq():
    return load_sequence(FIXTURE)


def gold(seq, ticket_id: str) -> str:
    return seq.ticket(ticket_id).gold_patch.read_text()


# --- Harbor's own validation ---------------------------------------------------


def _harbor_python() -> str | None:
    """A Python that can import harbor: this one, or the one behind the `harbor` CLI."""
    try:
        import harbor  # noqa: F401
        return sys.executable
    except ImportError:
        pass
    exe = shutil.which("harbor")
    if exe is None:
        return None
    first = Path(exe).read_text(errors="ignore").splitlines()[:1]
    if first and first[0].startswith("#!") and "python" in first[0]:
        return first[0][2:].strip()
    return None


HARBOR_CHECK = """
import json, sys
from harbor.models.task.task import Task
task = Task(sys.argv[1])
assert Task.is_valid_dir(sys.argv[1]), "Task.is_valid_dir is False"
cfg = task.config
print(json.dumps({
    "name": task.name,
    "steps": [s.name for s in cfg.steps],
    "strategy": cfg.multi_step_reward_strategy.value,
    "step_verifier_timeouts": [s.verifier.timeout_sec for s in cfg.steps],
    "step_agent_timeouts": [s.agent.timeout_sec for s in cfg.steps],
    "min_rewards": [s.min_reward for s in cfg.steps],
    "workdir": cfg.environment.workdir,
    "metadata_keys": sorted(cfg.metadata),
}))
"""


def harbor_validate(task_dir: Path) -> dict | None:
    python = _harbor_python()
    if python is None:
        return None
    proc = subprocess.run([python, "-c", HARBOR_CHECK, str(task_dir)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# --- structure -------------------------------------------------------------------


@pytest.mark.parametrize("code_state", CODE_STATES)
@pytest.mark.parametrize("framing", ["informed", "natural"])
def test_generated_structure(seq, tmp_path, code_state, framing):
    arm = ArmOptions(code_state=code_state, framing=framing)
    task_dir = compile_task(seq, explicit(ORDER), arm, tmp_path, allow_leak=True)
    assert task_dir.name == f"mini-seq__{arm.arm_id}__{explicit(ORDER).id}"

    names = [f"s0{k}-{t.lower()}" for k, t in enumerate(ORDER, start=1)]
    for name, ticket in zip(names, ORDER, strict=True):
        step = task_dir / "steps" / name
        for rel in ("instruction.md", "workdir/setup.sh", "tests/test.sh", "tests/ticket/run.sh",
                    "solution/solve.sh", "solution/gold.patch"):
            assert (step / rel).is_file(), f"{name}/{rel} missing"
        for script in ("workdir/setup.sh", "tests/test.sh", "solution/solve.sh"):
            assert os.access(step / script, os.X_OK)
            assert "@@" not in (step / script).read_text()
            subprocess.run([BASH, "-n", str(step / script)], check=True)
        # The grader gets the start state and the base manifest from its own upload.
        assert (step / "tests" / "start" / "base.manifest").read_text() == tree_manifest(BASE)
        assert (step / "workdir" / ".odyssey_step" / "base.manifest").read_text() == tree_manifest(BASE)
        assert (step / "instruction.md").read_text().endswith(seq.ticket(ticket).ticket_md.read_text())
    assert (task_dir / "environment" / "Dockerfile").is_file()

    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["multi_step_reward_strategy"] == "mean"
    assert [s["name"] for s in config["steps"]] == names
    assert all("min_reward" not in s for s in config["steps"])
    assert config["task"]["name"] == f"odyssey/{task_dir.name}"
    assert config["environment"]["workdir"] == "/app"
    assert json.loads(config["metadata"]["odyssey_manifest"]) == manifest(task_dir)

    m = manifest(task_dir)
    assert m["arm_options"]["code_state"] == code_state and m["arm"] == arm.arm_id
    assert m["ordering"] == list(ORDER)
    assert [(s["position"], s["ticket"], s["cluster"], s["role"]) for s in m["steps"]] == [
        (1, "MINI-2", "math", "core"), (2, "MINI-1", "math", "core"), (3, "MINI-3", "text", "isolated")]

    checked = harbor_validate(task_dir)
    if checked is not None:
        assert checked["steps"] == names
        assert checked["strategy"] == "mean"
        assert checked["step_verifier_timeouts"] == [120.0] * 3
        assert checked["step_agent_timeouts"] == [600.0] * 3
        assert checked["min_rewards"] == [None] * 3
        assert "odyssey_manifest" in checked["metadata_keys"]


def test_harbor_is_available_for_validation():
    """Not a hard requirement, but say so loudly when the Harbor check above was skipped."""
    if _harbor_python() is None:
        pytest.skip("harbor not importable; task.toml was checked with tomllib only")


@pytest.mark.parametrize("code_state", CODE_STATES)
def test_payload_never_leaks_current_or_future_gold(seq, tmp_path, code_state):
    for ordering in williams(seq.ticket_ids) + cold(seq.ticket_ids):
        task_dir = compile_task(seq, ordering, ArmOptions(code_state=code_state), tmp_path, allow_leak=True)
        for step in manifest(task_dir)["steps"]:
            step_dir = task_dir / "steps" / step["name"]
            position, current = step["position"], step["ticket"]
            payload_text = "".join(p.read_text() for p in (step_dir / "workdir").rglob("*") if p.is_file())
            tests_text = "".join(p.read_text() for p in (step_dir / "tests").rglob("*") if p.is_file())
            future = ordering.tickets[position:]
            past = ordering.tickets[: position - 1]

            state = step_dir / "workdir" / ".odyssey_step" / "state.patch"
            assert gold(seq, current) not in payload_text
            assert gold(seq, current) not in tests_text
            # The grader's copy of the start state is exactly setup.sh's.
            assert (step_dir / "tests" / "start" / "state.patch").read_text() == (
                state.read_text() if state.exists() else "")
            assert (step_dir / "solution" / "gold.patch").read_text() == gold(seq, current)
            if code_state == "forward-merge":
                assert all(gold(seq, t) not in payload_text for t in future)
                assert state.read_text() == "".join(gold(seq, t) for t in past)
                assert step["state_tickets"] == list(past)
            elif code_state == "all-defects":
                assert state.read_text() == "" and step["state_tickets"] == []
            elif code_state == "single-defect":
                others = [t for t in seq.ticket_ids if t != current]
                assert step["state_tickets"] == others
                assert state.read_text() == "".join(gold(seq, t) for t in others)
            else:
                assert not state.exists() and step["state_tickets"] == []


def test_framing_headers(seq, tmp_path):
    notes = FIXTURE / "notes" / "ORACLE_NOTES.md"
    ordering = explicit(ORDER)

    def instructions(**arm):
        task_dir = compile_task(seq, ordering, ArmOptions(**arm), tmp_path)
        return [(task_dir / "steps" / s["name"] / "instruction.md").read_text() for s in manifest(task_dir)["steps"]]

    informed = instructions(framing="informed", code_state="carry")
    assert informed[1].startswith("You will work through 3 tickets")
    assert "This is ticket 2 of 3." in informed[1]
    assert "carry over" in informed[1] and "/memory/" in informed[1] and "CLAUDE.md" in informed[1]

    natural = instructions(framing="natural")
    assert natural[1] == seq.ticket("MINI-1").ticket_md.read_text()

    with_extras = instructions(framing="natural", feedback="binary", seed_notes=notes)
    assert "/var/odyssey/feedback" not in with_extras[0]
    assert "/var/odyssey/feedback" in with_extras[1]
    assert all("/memory/NOTES.md" in text for text in with_extras)
    assert "ticket 2 of 3" not in with_extras[1]

    cold_dir = compile_task(seq, cold(["MINI-1"])[0], ArmOptions(framing="informed"), tmp_path)
    cold_text = (cold_dir / "steps" / "s01-mini-1" / "instruction.md").read_text()
    assert cold_text == seq.ticket("MINI-1").ticket_md.read_text()


def test_seed_notes_only_in_first_payload(seq, tmp_path):
    notes = FIXTURE / "notes" / "ORACLE_NOTES.md"
    task_dir = compile_task(seq, explicit(ORDER), ArmOptions(seed_notes=notes), tmp_path)
    payloads = sorted((task_dir / "steps").glob("*/workdir/.odyssey_step/NOTES.md"))
    assert [p.parts[-4] for p in payloads] == ["s01-mini-2"]
    m = manifest(task_dir)
    assert m["arm_options"]["seed_notes"]["file"] == "ORACLE_NOTES.md"
    # Different notes files must never share an arm (or a task dir).
    assert m["arm"] == "fm-inf-nofb-notes-" + m["arm_options"]["seed_notes"]["sha256"][:6]
    other = tmp_path / "OTHER.md"
    other.write_text("different notes\n")
    assert ArmOptions(seed_notes=other).arm_id != m["arm"]


def test_single_defect_needs_allow_leak_beyond_cold_runs(seq, tmp_path):
    arm = ArmOptions(code_state="single-defect")
    with pytest.raises(ValueError, match="allow-leak"):
        compile_task(seq, explicit(ORDER), arm, tmp_path)
    assert compile_task(seq, cold(["MINI-1"])[0], arm, tmp_path).is_dir()
    assert cli.main(["compile", str(FIXTURE), "--out", str(tmp_path / "o"), "--ordering", "MINI-1,MINI-2",
                     "--code-state", "single-defect"]) == 2
    assert cli.main(["compile", str(FIXTURE), "--out", str(tmp_path / "o"), "--ordering", "MINI-1,MINI-2",
                     "--code-state", "single-defect", "--allow-leak"]) == 0


def test_recompile_replaces_but_never_clobbers_foreign_dirs(seq, tmp_path):
    ordering, arm = explicit(ORDER), ArmOptions()
    first = compile_task(seq, ordering, arm, tmp_path)
    (first / "stale.txt").write_text("x")
    second = compile_task(seq, ordering, arm, tmp_path)
    assert first == second and not (second / "stale.txt").exists()

    (second / "odyssey.json").unlink()
    with pytest.raises(FileExistsError):
        compile_task(seq, ordering, arm, tmp_path)


def test_arm_option_validation(tmp_path):
    with pytest.raises(ValueError, match="--code-state"):
        ArmOptions(code_state="keep")
    with pytest.raises(ValueError, match="--seed-notes"):
        ArmOptions(seed_notes=tmp_path / "missing.md")
    assert ArmOptions().arm_id == "fm-inf-nofb"
    assert ArmOptions("carry", "natural", "binary").arm_id == "carry-nat-fb"


def test_unknown_or_repeated_tickets_rejected(seq, tmp_path):
    with pytest.raises(KeyError):
        compile_task(seq, explicit(["MINI-1", "NOPE"]), ArmOptions(), tmp_path)
    repeated = dataclasses.replace(explicit(["MINI-1", "MINI-2"]), tickets=("MINI-1", "MINI-1"))
    with pytest.raises(ValueError, match="repeats"):
        compile_task(seq, repeated, ArmOptions(), tmp_path)


# --- CLI -------------------------------------------------------------------------


def test_cli_compile_design_and_subset(tmp_path, capsys):
    out = tmp_path / "tasks"
    code = cli.main(["compile", str(FIXTURE), "--out", str(out), "--design", "williams",
                     "--subset", "MINI-1,MINI-3", "--code-state", "carry", "--feedback", "binary"])
    assert code == 0
    printed = capsys.readouterr().out
    dirs = sorted(p.name for p in out.iterdir())
    assert [d.rsplit("-", 1)[0] for d in dirs] == ["mini-seq__carry-inf-fb__w01", "mini-seq__carry-inf-fb__w02"]
    assert printed.count("harbor run -p") == 4 and "-a oracle" in printed

    assert cli.main(["design", str(FIXTURE), "--design", "latin"]) == 0
    assert "  MINI-3 MINI-1 MINI-2" in capsys.readouterr().out

    assert cli.main(["compile", str(FIXTURE), "--out", str(out), "--ordering", "MINI-1,NOPE"]) == 2
    assert "unknown ticket id" in capsys.readouterr().err


def test_cli_delegates_account_and_analyze(monkeypatch, capsys):
    calls = []
    fake = type(sys)("odyssey.accounting")
    fake.main = lambda argv: calls.append(argv) or 0
    monkeypatch.setitem(sys.modules, "odyssey.accounting", fake)
    assert cli.main(["account", "jobs/", "--out", "x.csv"]) == 0
    assert calls == [["jobs/", "--out", "x.csv"]]

    monkeypatch.setitem(sys.modules, "odyssey.analyze", None)  # import now fails as if absent
    assert cli.main(["analyze", "segments.csv"]) == 2
    assert "not available yet" in capsys.readouterr().err


# --- running the step scripts ------------------------------------------------------


def run_trial(seq, tmp_path, arm, agent, ordering=None, inspect=None):
    """Compile, then run every step; returns per-step (reward, grade, segment.patch)."""
    container = FakeContainer(tmp_path / "c", BASE)
    task_dir = host_compile(seq, tmp_path / "c", ordering or explicit(ORDER), arm, allow_leak=True)
    results = run_steps(container, task_dir, agent, inspect)
    assert all(r[0] != "setup failed" for r in results), results
    return container, results


@pytest.mark.parametrize("code_state", CODE_STATES)
def test_oracle_resolves_every_segment(seq, tmp_path, code_state):
    container, results = run_trial(seq, tmp_path, ArmOptions(code_state=code_state, feedback="binary"),
                                   oracle_agent)
    for (reward, grade, patch), ticket in zip(results, ORDER, strict=True):
        assert reward == {"reward": 1, "error": 0}
        assert grade["ticket"] == ticket and grade["exit_code"] == 0 and grade["segment_patch"] == "ok"
        # The segment diff is exactly the gold patch's change.
        assert gold(seq, ticket).splitlines()[-3:] == patch.splitlines()[-3:]
    feedback = sorted(p.name for p in (container.state / "feedback").iterdir())
    assert feedback == ["1.txt", "2.txt", "3.txt"]
    assert (container.state / "feedback" / "2.txt").read_text() == "accepted\n"


@pytest.mark.parametrize("code_state", CODE_STATES)
def test_doing_nothing_resolves_nothing(seq, tmp_path, code_state):
    _, results = run_trial(seq, tmp_path, ArmOptions(code_state=code_state), noop_agent)
    assert [r for r, _, _ in results] == [{"reward": 0, "error": 0}] * 3
    assert all(patch == "" for _, _, patch in results)


def memory_writing_agent(container, step_dir, step):
    """Leaves notes in both memory locations and a stray edit that is not a fix."""
    (container.app / "NOTES.md").write_text(f"note from segment {step['position']}\n")
    (container.memory / f"seg{step['position']}.txt").write_text("kept\n")
    with open(container.app / "README.md", "a") as f:
        f.write(f"stray edit {step['position']}\n")


def neutral_git_history(container) -> None:
    assert container.git("rev-list", "--count", "HEAD").strip() == "1"
    log = container.git("log", "--format=%an <%ae> %s")
    assert "odyssey" not in log.lower(), log


@pytest.mark.parametrize("code_state", ["forward-merge", "all-defects", "single-defect"])
def test_reset_policies_keep_memory_and_discard_edits(seq, tmp_path, code_state):
    seen = []

    def inspect(container, step):
        app = container.app
        seen.append(step["position"])
        assert not (app / "setup.sh").exists() and not (app / ".odyssey_step").exists()
        assert not any(container.tests.iterdir()), "/tests not scrubbed"
        neutral_git_history(container)
        assert container.git("status", "--porcelain") == ""
        assert "stray edit" not in (app / "README.md").read_text()
        if step["position"] > 1:
            assert (app / "NOTES.md").read_text() == f"note from segment {step['position'] - 1}\n"
            assert (container.memory / f"seg{step['position'] - 1}.txt").exists()
        math = (app / "math.sh").read_text()
        add_fixed, mul_fixed = "$1 + $2 ))\n}\n\n# sub" in math, "$1 * $2" in math
        expected = {t: t in step["state_tickets"] for t in ("MINI-1", "MINI-2")}
        assert (add_fixed, mul_fixed) == (expected["MINI-1"], expected["MINI-2"])

    _, results = run_trial(seq, tmp_path, ArmOptions(code_state=code_state), memory_writing_agent,
                           inspect=inspect)
    assert seen == [1, 2, 3]
    for _, _, patch in results:
        # The agent's work is in the diff; carried memory files are not.
        assert "+stray edit" in patch and "NOTES.md" not in patch


def test_carry_keeps_edits_and_diffs_per_segment(seq, tmp_path):
    def inspect(container, step):
        readme = (container.app / "README.md").read_text()
        for k in range(1, step["position"]):
            assert f"stray edit {k}" in readme
        assert container.git("status", "--porcelain") == ""
        neutral_git_history(container)

    _, results = run_trial(seq, tmp_path, ArmOptions(code_state="carry", framing="natural"),
                           memory_writing_agent, inspect=inspect)
    for k, (_, _, patch) in enumerate(results, start=1):
        assert f"+stray edit {k}" in patch
        assert f"+stray edit {k - 1}" not in patch


def test_seed_notes_land_in_memory_dir(seq, tmp_path):
    notes = FIXTURE / "notes" / "ORACLE_NOTES.md"
    container, _ = run_trial(seq, tmp_path, ArmOptions(seed_notes=notes), noop_agent)
    assert (container.memory / "NOTES.md").read_text() == notes.read_text()


@pytest.mark.parametrize("code_state", CODE_STATES)
def test_no_experiment_traces_without_feedback(seq, tmp_path, code_state):
    """Nothing in the container tells the agent its position or that it is in a series."""
    def inspect(container, step):
        assert not container.state.exists() or not any(container.state.rglob("*"))
        neutral_git_history(container)

    run_trial(seq, tmp_path, ArmOptions(code_state=code_state, framing="natural"), noop_agent, inspect=inspect)


def test_state_patch_that_does_not_apply_fails_setup(seq, tmp_path):
    container = FakeContainer(tmp_path / "c", BASE)
    task_dir = host_compile(seq, tmp_path / "c", explicit(ORDER), ArmOptions())
    step_dir = task_dir / "steps" / "s02-mini-1"
    broken = gold(seq, "MINI-2").replace(" mul() {", " mux() {")  # context no longer matches
    (step_dir / "workdir" / ".odyssey_step" / "state.patch").write_text(broken)
    proc = container.prepare_step(step_dir, check=False)
    assert proc.returncode != 0 and "state.patch does not apply" in proc.stderr


def test_grader_always_writes_a_reward(seq, tmp_path):
    container = FakeContainer(tmp_path / "c", BASE)
    task_dir = host_compile(seq, tmp_path / "c", explicit(ORDER), ArmOptions())
    step_dir = task_dir / "steps" / "s01-mini-2"
    container.prepare_step(step_dir)

    (step_dir / "tests" / "ticket" / "run.sh").unlink()
    assert container.verify(step_dir) == {"reward": 0, "error": 1}
    assert "run.sh missing" in container.grade()["error"]

    # Killed mid-run (as Harbor does on verifier timeout): the provisional reward remains.
    (step_dir / "tests" / "ticket" / "run.sh").write_text("sleep 30\n")
    shutil.rmtree(container.tests)
    shutil.copytree(step_dir / "tests", container.tests)
    proc = subprocess.Popen([BASH, str(container.tests / "test.sh")], cwd=container.app,
                            env=container.env, start_new_session=True)
    deadline = time.time() + 10
    while not (container.verifier / "reward.json").exists() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()
    assert json.loads((container.verifier / "reward.json").read_text()) == {"reward": 0, "error": 1}


TIMEOUT_SHIM = """\
import os, signal, subprocess, sys
args = sys.argv[1:]
if args[0] == "-k":
    args = args[2:]
proc = subprocess.Popen(args[1:], start_new_session=True)
try:
    sys.exit(proc.wait(timeout=float(args[0])))
except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)  # like coreutils, signal the whole group
    sys.exit(124)
"""


def test_grader_times_out_run_sh(seq, tmp_path, monkeypatch):
    if shutil.which("timeout") is None:
        # Stand-in for coreutils `timeout` (absent on macOS); same exit code 124.
        shim_dir = tmp_path / "bin"
        shim_dir.mkdir()
        (shim_dir / "timeout").write_text(f"#!{sys.executable}\n{TIMEOUT_SHIM}")
        (shim_dir / "timeout").chmod(0o755)
        monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")
    slow = dataclasses.replace(seq, verifier_timeout_sec=2)
    container = FakeContainer(tmp_path / "c", BASE)
    task_dir = host_compile(slow, tmp_path / "c", explicit(ORDER), ArmOptions())
    step_dir = task_dir / "steps" / "s01-mini-2"
    container.prepare_step(step_dir)
    (step_dir / "tests" / "ticket" / "run.sh").write_text("sleep 30\n")
    assert container.verify(step_dir) == {"reward": 0, "error": 1}
    assert container.grade()["timed_out"] is True


def test_bash_and_python_base_manifests_agree(tmp_path):
    """setup.sh/test.sh compare their own manifest with the compiled one byte for byte."""
    from odyssey.compile import shell_template

    tree = tmp_path / "tree"
    (tree / "pkg" / "__pycache__").mkdir(parents=True)
    (tree / "pkg" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (tree / "pkg" / "a file.py").write_text("x = 1\n")
    (tree / "pkg" / "B.py").write_text("y = 2\n")
    (tree / ".pytest_cache").mkdir()
    (tree / ".hidden").write_text("h\n")
    (tree / ".DS_Store").write_bytes(b"\0")
    (tree / "pkg" / "stray.pyc").write_bytes(b"\0")
    (tree / "link").symlink_to("pkg/B.py")
    (tree / "dirlink").symlink_to("pkg")
    script = shell_template("lib_manifest.sh") + '\ntree_manifest "$1"\n'
    out = subprocess.run([BASH, "-c", script, "_", str(tree)], capture_output=True, text=True, check=True).stdout
    assert out == tree_manifest(tree)
    assert "__pycache__" not in out and ".pytest_cache" not in out
    assert ".DS_Store" not in out and ".pyc" not in out and "./.hidden" in out
    assert "link pkg/B.py  ./link\n" in out and "link pkg  ./dirlink\n" in out
