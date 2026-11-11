"""Support for sequences whose base tree lives only in the image (drafts/docs/REAL_SEQUENCE_GUIDE.md).

A real repo is built by the sequence's Dockerfile, so there is no host copy of
the base tree. Such a sequence checks in `base_manifest` (written by
`odyssey base-manifest`), and compile, validate and the step scripts use that
manifest exactly as they use one hashed from `base_tree`.
"""

import dataclasses
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from fakecontainer import FakeContainer, host_compile, noop_agent, oracle_agent, run_steps
from odyssey import cli
from odyssey import validate as v
from odyssey.compile import ArmOptions, base_manifest_text, compile_task, tree_manifest
from odyssey.design import explicit
from odyssey.spec import SpecError, load_sequence, manifest_problem

FIXTURE = Path(__file__).parent / "fixtures" / "mini-seq"
BASE = FIXTURE / "environment" / "base"
ORDER = ("MINI-2", "MINI-1", "MINI-3")


@pytest.fixture
def image_only_seq(tmp_path):
    """mini-seq with the host base tree replaced by a checked-in manifest of it."""
    dest = tmp_path / "mini-seq"
    shutil.copytree(FIXTURE, dest)
    (dest / "base.manifest").write_text(tree_manifest(BASE))
    shutil.rmtree(dest / "environment" / "base")
    toml = dest / "sequence.toml"
    toml.write_text(toml.read_text().replace('base_tree = "environment/base"', 'base_manifest = "base.manifest"'))
    return dest


def test_base_manifest_loads_and_is_used_by_compile(image_only_seq, tmp_path):
    seq = load_sequence(image_only_seq)
    assert seq.environment.base_tree is None
    assert seq.environment.base_manifest == (image_only_seq / "base.manifest").resolve()
    assert base_manifest_text(seq) == tree_manifest(BASE)
    task = compile_task(seq, explicit(list(ORDER)), ArmOptions(), tmp_path / "out")
    for step in sorted((task / "steps").iterdir()):
        assert (step / "tests" / "start" / "base.manifest").read_text() == tree_manifest(BASE)
        assert (step / "workdir" / ".odyssey_step" / "base.manifest").read_text() == tree_manifest(BASE)


def test_base_manifest_and_base_tree_are_exclusive(image_only_seq):
    toml = image_only_seq / "sequence.toml"
    toml.write_text(toml.read_text().replace('base_manifest = "base.manifest"',
                                             'base_manifest = "base.manifest"\nbase_tree = "environment"'))
    with pytest.raises(SpecError, match="base_tree or base_manifest, not both"):
        load_sequence(image_only_seq)


def test_bad_base_manifest_is_reported(image_only_seq):
    path = image_only_seq / "base.manifest"
    path.write_text("not a manifest\n")
    with pytest.raises(SpecError, match="line 1 is not"):
        load_sequence(image_only_seq)
    path.unlink()
    with pytest.raises(SpecError, match="base_manifest file not found"):
        load_sequence(image_only_seq)


def test_manifest_problem_rules():
    good = tree_manifest(BASE)
    assert manifest_problem(good) is None
    assert manifest_problem("") == "is empty"
    assert "newline" in manifest_problem(good.rstrip("\n"))
    assert manifest_problem("link ../x  ./a\n") is None


def test_step_scripts_check_the_base_against_a_checked_in_manifest(image_only_seq, tmp_path):
    """Oracle 1 / nop 0 with the manifest coming from the file; a doctored tarball fails setup."""
    seq = load_sequence(image_only_seq)
    for agent, expected in ((oracle_agent, 1), (noop_agent, 0)):
        root = tmp_path / f"c-{expected}"
        container = FakeContainer(root, BASE)
        task = host_compile(seq, root, explicit(list(ORDER)), ArmOptions(), allow_leak=False)
        results = run_steps(container, task, agent)
        assert [r[0] for r in results] == [{"reward": expected, "error": 0}] * 3, results

    root = tmp_path / "c-doctored"
    container = FakeContainer(root, BASE)
    with tarfile.open(container.tarball, "w:gz") as tar:
        tar.add(BASE, arcname=".")
        tar.add(FIXTURE / "sequence.toml", arcname="./extra.txt")
    task = host_compile(seq, root, explicit(list(ORDER)), ArmOptions(), allow_leak=False)
    assert run_steps(container, task, oracle_agent)[0][0] == "setup failed"


def test_contract_script_uses_the_checked_in_manifest(image_only_seq, tmp_path):
    seq = load_sequence(image_only_seq)
    tarball = tmp_path / "base.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(BASE, arcname=".")
    script = v.contract_script(dataclasses.replace(seq, workdir=str(tmp_path / "app")))
    script = script.replace("/opt/odyssey/base.tar.gz", str(tarball))
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert "CONTRACT base tarball matches base manifest OK" in out


def test_contract_compares_symlinks_as_links(tmp_path):
    """A repo may track a symlink that points at its own parent (DuckDB's glob tests do)."""
    import os
    app = tmp_path / "app"
    shutil.copytree(BASE, app)
    loop = app / "data" / "mydir"
    loop.mkdir(parents=True)
    os.symlink("../..", loop / "link_to_upper_dir")
    os.symlink("missing-target", loop / "dangling")
    tarball = tmp_path / "base.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(app, arcname=".")
    seq = load_sequence(FIXTURE)
    script = v.contract_script(dataclasses.replace(seq, workdir=str(app)))
    script = script.replace("/opt/odyssey/base.tar.gz", str(tarball))
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert "CONTRACT base tarball matches workdir OK" in out
    (loop / "dangling").unlink()
    os.symlink("other-target", loop / "dangling")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert "CONTRACT base tarball matches workdir FAIL" in out


def test_local_validate_with_a_tree_checks_it_against_the_manifest(image_only_seq, tmp_path):
    seq = load_sequence(image_only_seq)
    runner = v.LocalRunner(seq, BASE)
    try:
        checks = v.validate(seq, runner)
    finally:
        runner.close()
    assert [c for c in checks if not c.ok] == []
    assert any(c.name == "local tree matches base manifest" and c.ok for c in checks)

    other = tmp_path / "other"
    shutil.copytree(BASE, other)
    (other / "README.md").write_text("changed\n")
    runner = v.LocalRunner(seq, other)
    try:
        assert [c.ok for c in runner.tree_checks()] == [False]
    finally:
        runner.close()


def test_cli_base_manifest_from_a_tree(image_only_seq, tmp_path, capsys):
    # The manifest the sequence names does not exist yet: that is what the command writes.
    (image_only_seq / "base.manifest").unlink()
    out = image_only_seq / "base.manifest"
    assert cli.main(["base-manifest", str(image_only_seq), "--tree", str(BASE), "--out", str(out)]) == 0
    assert out.read_text() == tree_manifest(BASE)
    # --no-docker on such a sequence needs an explicit tree
    assert cli.main(["validate", str(image_only_seq), "--no-docker"]) == 2
    assert "needs --tree" in capsys.readouterr().err
    assert cli.main(["validate", str(image_only_seq), "--no-docker", "--tree", str(BASE), "--jobs", "3",
                     "--subsets", "--subset-sample", "1"]) == 0
    assert "sampled 1 of 2 subsets" in capsys.readouterr().out


def test_parallel_validate_reports_the_same_checks():
    seq = load_sequence(FIXTURE)
    runs = []
    for jobs in (1, 4):
        runner = v.LocalRunner(seq, BASE)
        try:
            runs.append([(c.ticket, c.name, c.ok) for c in v.validate(seq, runner, repeat=2, subsets=True, jobs=jobs)])
        finally:
            runner.close()
    assert runs[0] == runs[1]
    assert all(ok for _, _, ok in runs[0])


def test_sample_subsets_order_and_determinism():
    others = list("abcde")
    every = v.sample_subsets(others, None)
    assert len(every) == 2 ** 5 - 2
    sample = v.sample_subsets(others, 12, seed=3, key="T")
    assert sample[:5] == [[x] for x in others]                      # every single other gold first
    assert sample[5:10] == [[o for o in others if o != x] for x in others]  # then all-but-one
    assert len(sample) == 12 and len({tuple(s) for s in sample}) == 12
    assert all(0 < len(s) < 5 for s in sample)
    assert sample == v.sample_subsets(others, 12, seed=3, key="T")
    assert v.sample_subsets(others, 100) == every


def test_cli_rejects_bad_validate_options(capsys):
    assert cli.main(["validate", str(FIXTURE), "--no-docker", "--jobs", "0"]) == 2
    assert cli.main(["validate", str(FIXTURE), "--no-docker", "--subset-sample", "3"]) == 2
    assert "--subset-sample needs --subsets" in capsys.readouterr().err


def test_only_restricts_grader_checks(capsys):
    assert cli.main(["validate", str(FIXTURE), "--no-docker", "--only", "MINI-2"]) == 0
    out = capsys.readouterr().out
    assert "MINI-2" in out and "MINI-1       base fails" not in out
    assert "gold patches apply together" in out
    assert cli.main(["validate", str(FIXTURE), "--no-docker", "--only", "NOPE"]) == 2


def test_docker_runner_passes_large_scripts_on_stdin(image_only_seq, tmp_path, monkeypatch):
    """A check script larger than the argument-size limit (a big repo's manifest) still runs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    # `docker info` succeeds; `docker run ... IMAGE -s` runs the script from stdin.
    fake.write_text('#!/usr/bin/env bash\n'
                    'case "$1" in info) exit 0;; run) [ "${!#}" = "-s" ] || exit 9; exec bash -s;; esac\n'
                    'exit 1\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    runner = v.DockerRunner(load_sequence(image_only_seq), image="fake:latest")
    payload = "x" * 4_000_000  # well above ARG_MAX on Linux and macOS
    result = runner.run(f"p='{payload}'\necho ${{#p}}")
    assert result.exit_code == 0
    assert result.output.strip() == str(len(payload))
