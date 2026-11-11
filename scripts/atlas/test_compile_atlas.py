"""Offline tests of the atlas-commerce generator (no Docker).

    uv run --python 3.12 python -m pytest -q scripts/atlas/test_compile_atlas.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import compile_atlas as ca  # noqa: E402
from odyssey import accounting, design, spec  # noqa: E402

SEQ = ROOT / "sequences" / "atlas-commerce"
EXTRA = SEQ / "testing" / "tickets"
PLACEHOLDER_MARK = "REFONLY-c4d2e1"  # only in ATL-PH's gold.patch
ATL2_MARK = "acknowledged without change"


@pytest.fixture(scope="module")
def atlas():
    return ca.load(SEQ, [EXTRA])


def _text_under(path: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in sorted(path.rglob("*")) if p.is_file())


def test_sequence_loads_with_odyssey_spec():
    seq = spec.load_sequence(SEQ)
    assert seq.id == "atlas-commerce"
    assert seq.environment.base_tree == (SEQ / "environment" / "repo").resolve()


def test_static_checks_pass(atlas):
    assert ca.check(atlas) == []
    assert set(atlas.tickets) >= {"ATL-2", "ATL-PH"}


def test_ordering_layout_state_and_no_lookahead(atlas, tmp_path):
    task = ca.compile_ordering(atlas, design.explicit(["ATL-2", "ATL-PH"]), tmp_path)
    assert task.name == "atlas-commerce__fml-inf-nofb__xa715b8"
    s1, s2 = task / "steps" / "s01-atl-2", task / "steps" / "s02-atl-ph"
    gold2 = (SEQ / "tickets" / "ATL-2" / "gold.patch").read_text()
    assert (s1 / "workdir" / ".odyssey_step" / "state.patch").read_text() == ""
    assert (s2 / "workdir" / ".odyssey_step" / "state.patch").read_text() == gold2
    assert (s2 / "tests" / "start" / "state.patch").read_text() == gold2
    # What reaches the container before or during the agent's turn of step k (workdir and
    # instruction) holds no reference of step k or later; tests/ arrive after the turn but
    # must not hold another ticket's reference either.
    for path in (s1 / "workdir", s1 / "instruction.md", s1 / "tests"):
        text = _text_under(path) if path.is_dir() else path.read_text()
        assert PLACEHOLDER_MARK not in text and ATL2_MARK not in text, path
    for path in (s2 / "workdir", s2 / "instruction.md", s2 / "tests"):
        text = _text_under(path) if path.is_dir() else path.read_text()
        assert PLACEHOLDER_MARK not in text, path
    # The references live only in solution/ (oracle) and in later steps' state.
    assert ATL2_MARK in (s1 / "solution" / "change.patch").read_text()
    assert PLACEHOLDER_MARK in (s2 / "solution" / "change.patch").read_text()
    assert "ATL-PH" not in (s1 / "instruction.md").read_text()
    assert "PLACEHOLDER-REF-7f3a9c" not in _text_under(s1)  # not even ATL-PH's ticket text
    assert "ticket 1 of 2" in (s1 / "instruction.md").read_text()


def test_tokens_are_per_step_and_only_hashed_in_environment(atlas, tmp_path):
    task = ca.compile_ordering(atlas, design.explicit(["ATL-2", "ATL-PH"]), tmp_path)
    tokens = json.loads((task / "environment" / "controller" / "tokens.json").read_text())["tokens"]
    import hashlib
    env_text = _text_under(task / "environment")
    for step in ("s01-atl-2", "s02-atl-ph"):
        setup = (task / "steps" / step / "workdir" / "setup.sh").read_text()
        test = (task / "steps" / step / "tests" / "test.sh").read_text()
        setup_token = re.search(r"^ATLAS_SETUP_TOKEN=(\w+)$", setup, re.M).group(1)
        verify_token = re.search(r"^ATLAS_VERIFY_TOKEN=(\w+)$", test, re.M).group(1)
        assert tokens[hashlib.sha256(setup_token.encode()).hexdigest()] == {"step": step, "kind": "setup"}
        assert tokens[hashlib.sha256(verify_token.encode()).hexdigest()] == {"step": step, "kind": "verify"}
        assert setup_token not in env_text and verify_token not in env_text
        assert verify_token not in _text_under(task / "steps" / step / "workdir")
        assert setup_token not in _text_under(task / "steps" / step / "tests")
    ignore = (task / "environment" / ".dockerignore").read_text()
    assert "controller/tokens.json" in ignore.splitlines()


def test_manifest_is_readable_by_odyssey_account(atlas, tmp_path):
    task = ca.compile_ordering(atlas, design.explicit(["ATL-2", "ATL-PH"]), tmp_path)
    manifest = accounting.load_manifest({"config": {"task": {"path": str(task)}}}, tmp_path, [tmp_path])
    steps = accounting.manifest_steps(manifest, ["s01-atl-2", "s02-atl-ph"])
    assert [(s.ticket, s.position, s.cluster) for s in steps] == [
        ("ATL-2", 1, "orders-consumers"), ("ATL-PH", 2, "placeholder")]
    assert manifest["arm"] == "fml-inf-nofb"
    assert manifest["arm_options"]["code_state"] == "forward-merge-live"


def test_task_toml_per_step_timeouts(atlas, tmp_path):
    task = ca.compile_ordering(atlas, design.explicit(["ATL-PH", "ATL-2"]), tmp_path)
    cfg = tomllib.loads((task / "task.toml").read_text())
    assert [(s["name"], s["agent"]["timeout_sec"], s["verifier"]["timeout_sec"]) for s in cfg["steps"]] == [
        ("s01-atl-ph", 600.0, 300.0), ("s02-atl-2", 3600.0, 600.0)]
    assert cfg["environment"]["network_mode"] == "public"
    test = (task / "steps" / "s02-atl-2" / "tests" / "test.sh").read_text()
    assert "ODYSSEY_RUN_TIMEOUT_SEC=480" in test


def test_generated_scripts_are_valid_bash(atlas, tmp_path):
    task = ca.compile_ordering(atlas, design.explicit(["ATL-2"]), tmp_path)
    for script in task.rglob("*.sh"):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script
        assert "@@" not in script.read_text(), script


def test_offline_variant_makes_agent_net_internal(atlas, tmp_path):
    task = ca.compile_ordering(atlas, design.explicit(["ATL-2"]), tmp_path, offline=True)
    compose = (task / "environment" / "docker-compose.yaml").read_text()
    assert "internal: true" in compose
    assert task.name.startswith("atlas-commerce__fml-inf-nofb-offline__")


def test_base_manifest_matches_repo(atlas, tmp_path):
    from odyssey import compile as oc
    task = ca.compile_ordering(atlas, design.explicit(["ATL-2"]), tmp_path)
    compiled = (task / "steps" / "s01-atl-2" / "workdir" / ".odyssey_step" / "base.manifest").read_text()
    assert compiled == oc.tree_manifest(SEQ / "environment" / "repo")


def test_validation_plan_conditions(atlas):
    # The subset pool is pinned to the placeholder, so the expectation does not depend on how
    # many real tickets sequence.toml lists.
    steps = ca.validation_plan(atlas, "ATL-2", subset_k=None, seed=0, repeat_reference=2, others=["ATL-PH"])
    conditions = [(s["action"], tuple(s["state_tickets"]), s["expect"]["reward"]) for s in steps]
    assert conditions[:3] == [("nothing", (), 0), ("reference", (), 1), ("reference", (), 1)]
    assert [c[0] for c in conditions[3:7]] == [
        "known-bad:no-created-at", "known-bad:failed-shape", "known-bad:no-report-batch-item-failures",
        "known-bad:unconditional-put"]
    assert conditions[7:] == [("nothing", ("ATL-PH",), 0), ("reference", ("ATL-PH",), 1)]
    assert steps[0]["expect"]["clean_failure"] is True
    assert steps[3]["expect"]["must_fail"] == ["test_end_to_end_fifo_group_ordering",
                                               "test_end_to_end_duplicate_post_is_idempotent"]


def test_subset_sample_is_stated_and_deterministic():
    others = ["A", "B", "C", "D"]
    sample = ca.subset_sample(others, 6, seed=1)
    assert sample[:4] == [("A",), ("B",), ("C",), ("D",)]
    assert sample[4:6] == [("B", "C", "D"), ("A", "C", "D")]
    assert ca.subset_sample(others, 6, seed=1) == sample
    assert len(ca.subset_sample(others, None, seed=0)) == 15


def test_known_bad_solution_is_the_variant(atlas, tmp_path):
    task = ca.compile_validation(atlas, "ATL-2", tmp_path, subset_k=0, seed=0, repeat_reference=1, offline=False)
    manifest = json.loads((task / "odyssey.json").read_text())
    for step in manifest["steps"]:
        solve = (task / "steps" / step["name"] / "solution" / "solve.sh").read_text()
        change = (task / "steps" / step["name"] / "solution" / "change.patch").read_text()
        if step["action"] == "nothing":
            assert change == "" and "ATLAS_DEPLOY=''" in solve
        elif step["action"] == "reference":
            assert change == (SEQ / "tickets" / "ATL-2" / "gold.patch").read_text()
        else:
            name = step["action"].split(":", 1)[1]
            assert change == (SEQ / "tickets" / "ATL-2" / "known_bad" / f"{name}.patch").read_text()
            assert "ATLAS_DEPLOY=orders_core" in solve


def test_disjoint_gold_patches_are_enforced(atlas, tmp_path):
    clash = ca.Atlas(atlas.seq, dict(atlas.tickets))
    fake_dir = tmp_path / "ATL-X"
    (fake_dir / "tests").mkdir(parents=True)
    (fake_dir / "ticket.md").write_text("x\n")
    (fake_dir / "tests" / "run.sh").write_text("#!/bin/sh\n")
    (fake_dir / "tests" / "run.sh").chmod(0o755)
    (fake_dir / "gold.patch").write_text((SEQ / "tickets" / "ATL-2" / "gold.patch").read_text())
    ticket = spec.Ticket("ATL-X", "x", "x", "core", fake_dir / "ticket.md", fake_dir / "gold.patch", fake_dir / "tests")
    clash.tickets["ATL-X"] = ca.AtlasTicket(ticket, {"ticket": {"agent_timeout_sec": 1, "verifier_timeout_sec": 1},
                                                     "grading": {"fail_to_pass": ["t"]}}, fake_dir)
    problems = ca.check(clash)
    assert any("both touch" in p and "ATL-X" in p for p in problems)


def _verdict(tmp_path, exit_code, junit=None):
    results = tmp_path / "results"
    results.mkdir(parents=True, exist_ok=True)
    if junit is not None:
        (results / "junit-main.xml").write_text(junit)
    out = subprocess.run([sys.executable, str(HERE / "grader_lib" / "verdict.py"), "--results", str(results),
                          "--exit", str(exit_code)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


PASS = '<testsuites><testsuite tests="2"><testcase name="a"/><testcase name="b"/></testsuite></testsuites>'
FAIL = '<testsuites><testsuite tests="2"><testcase name="a"/><testcase name="b"><failure/></testcase></testsuite></testsuites>'
SKIP = '<testsuites><testsuite tests="1"><testcase name="a"><skipped/></testcase></testsuite></testsuites>'


def test_verdict_needs_exit_zero_and_passing_reports(tmp_path):
    assert _verdict(tmp_path, 0, PASS)["reward"] == 1
    assert _verdict(tmp_path / "x", 1, PASS) == {**_verdict(tmp_path / "x", 1, PASS), "reward": 0, "tampered": 0}
    assert _verdict(tmp_path / "y", 1, FAIL)["reward"] == 0


def test_verdict_flags_exit_zero_that_reports_contradict(tmp_path):
    for case, junit in (("none", None), ("failing", FAIL), ("skipped", SKIP), ("garbage", "<not xml")):
        verdict = _verdict(tmp_path / case, 0, junit)
        assert verdict["reward"] == 0 and verdict["tampered"] == 1, (case, verdict)


def test_validation_plan_covers_every_other_ticket(atlas):
    real = [t for t in atlas.tickets if t != "ATL-PH"]
    steps = ca.validation_plan(atlas, "ATL-2", subset_k=None, seed=0, repeat_reference=1,
                               others=[t for t in real if t != "ATL-2"])
    subsets = {tuple(s["state_tickets"]) for s in steps if s["state_tickets"]}
    assert len(subsets) == 2 ** (len(real) - 1) - 1
    for step in steps:
        assert "ATL-2" not in step["state_tickets"]
        assert step["condition"].endswith("+".join(step["state_tickets"]) or "the base")


def test_ticket_toml_labels_must_agree_with_sequence_toml(atlas, tmp_path):
    import dataclasses
    at = atlas.tickets["ATL-6"]
    wrong = ca.AtlasTicket(at.ticket, {**at.config, "ticket": {**at.config["ticket"], "role": "isolated"}}, at.directory)
    problems = ca.check(ca.Atlas(atlas.seq, {**atlas.tickets, "ATL-6": wrong}))
    assert any("ATL-6" in p and "disagrees with sequence.toml" in p for p in problems)
    assert dataclasses.replace(at.ticket).role == "core"


def test_setup_pytest_fail_is_reported_as_failure(tmp_path):
    """atlas_run's pytest entry point: pytest.fail() in a fixture -> <failure>; other setup errors stay <error>."""
    tests = tmp_path / "test_fixture_fail.py"
    tests.write_text(
        "import pytest\n"
        "@pytest.fixture(scope='session')\n"
        "def converged():\n"
        "    pytest.fail('pipeline did not converge')\n"
        "@pytest.fixture\n"
        "def broken():\n"
        "    raise RuntimeError('grader bug')\n"
        "def test_a(converged):\n    pass\n"
        "def test_b(converged):\n    pass\n"
        "def test_c(broken):\n    pass\n"
        "def test_d():\n    pass\n")
    out = tmp_path / "out"
    env = {"ATLAS_OUT": str(out), "PATH": "/usr/bin:/bin"}
    rc = subprocess.run([sys.executable, str(HERE / "grader_lib" / "atlas_run.py"), "pytest", "--name", "t",
                         str(tests)], env=env, capture_output=True, text=True).returncode
    assert rc != 0
    sys.path.insert(0, str(HERE / "grader_lib"))
    import atlas_run
    outcomes = atlas_run.parse_junit(out / "junit-t.xml")
    assert outcomes == {"test_a": "failed", "test_b": "failed", "test_c": "error", "test_d": "passed"}
