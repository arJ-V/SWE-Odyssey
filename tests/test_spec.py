import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from odyssey import cli
from odyssey import validate as v
from odyssey.spec import SpecError, load_sequence

FIXTURE = Path(__file__).parent / "fixtures" / "mini-seq"


@pytest.fixture
def seq_dir(tmp_path):
    dest = tmp_path / "mini-seq"
    shutil.copytree(FIXTURE, dest)
    return dest


def edit_toml(seq_dir: Path, old: str, new: str) -> None:
    path = seq_dir / "sequence.toml"
    text = path.read_text()
    assert old in text, f"fixture no longer contains {old!r}"
    path.write_text(text.replace(old, new, 1))


def problems(seq_dir: Path) -> str:
    with pytest.raises(SpecError) as info:
        load_sequence(seq_dir)
    return str(info.value)


def test_loads_fixture():
    seq = load_sequence(FIXTURE)
    assert seq.id == "mini-seq"
    assert seq.ticket_ids == ["MINI-1", "MINI-2", "MINI-3"]
    assert seq.workdir == "/app" and seq.memory_dir == "/memory"
    assert seq.repo_memory_files == ("CLAUDE.md", "AGENTS.md", "NOTES.md")
    assert seq.verifier_timeout_sec == 120.0
    assert seq.environment.context == (FIXTURE / "environment").resolve()
    assert seq.environment.docker_image is None
    assert seq.notes_path is not None and seq.notes_path.name == "ORACLE_NOTES.md"
    one = seq.ticket("MINI-1")
    assert (one.cluster, one.role, one.title) == ("math", "core", "add() subtracts")
    assert one.run_sh.is_file() and one.files_dir.is_dir() and one.gold_patch.is_file()


def test_unknown_ticket_lookup_names_known_ids():
    with pytest.raises(KeyError, match="MINI-1"):
        load_sequence(FIXTURE).ticket("NOPE")


def test_missing_directory_and_toml(tmp_path):
    with pytest.raises(SpecError, match="does not exist"):
        load_sequence(tmp_path / "absent")
    with pytest.raises(SpecError, match="missing sequence.toml"):
        load_sequence(tmp_path)


def test_invalid_toml(seq_dir):
    (seq_dir / "sequence.toml").write_text("schema = = 1\n")
    assert "not valid TOML" in problems(seq_dir)


def test_duplicate_ids_including_case(seq_dir):
    edit_toml(seq_dir, 'id = "MINI-2"', 'id = "MINI-1"')
    assert "duplicate ticket id 'MINI-1'" in problems(seq_dir)
    shutil.copytree(FIXTURE, seq_dir, dirs_exist_ok=True)
    edit_toml(seq_dir, 'id = "MINI-2"', 'id = "mini-1"')
    assert "duplicate ticket id 'mini-1'" in problems(seq_dir)


def test_missing_ticket_files_are_all_reported(seq_dir):
    (seq_dir / "tickets" / "MINI-2" / "gold.patch").unlink()
    (seq_dir / "tickets" / "MINI-3" / "tests" / "run.sh").unlink()
    (seq_dir / "tickets" / "MINI-1" / "ticket.md").write_text("  \n")
    text = problems(seq_dir)
    assert "ticket MINI-2: missing gold patch" in text
    assert "ticket MINI-3: missing grader entry point" in text
    assert "ticket MINI-1: ticket text" in text and "is empty" in text


@pytest.mark.parametrize("old, new, message", [
    ("schema = 1", "schema = 2", "schema must be 1"),
    ('workdir = "/app"', 'workdir = "app"', "must be an absolute container path"),
    ('workdir = "/app"', 'workdir = "/"', "is a system directory"),
    ('memory_dir = "/memory"', 'memory_dir = "/app/memory"', "must be outside workdir"),
    ('id = "mini-seq"', 'id = "mini seq"', "[sequence].id"),
    ('id = "MINI-3"', 'id = "MINI/3"', "id must match"),
    ("cpus = 1", "cpus = 0", "[environment].cpus must be a positive integer"),
    ("cpus = 1", "cpu = 1", "unknown key 'cpu' in [environment]"),
    ('role = "isolated"', 'roll = "isolated"', "unknown key 'roll'"),
    ("verifier_timeout_sec = 120", "verifier_timeout_sec = -5", "verifier_timeout_sec must be a positive number"),
    ('context = "environment"', 'context = "environment"\ndocker_image = "x:1"', "exactly one of"),
    ('context = "environment"', 'context = "nope"', "context directory not found"),
    ('repo_memory_files = ["CLAUDE.md", "AGENTS.md", "NOTES.md"]', 'repo_memory_files = ["../x"]',
     "repo-relative path"),
    ("allow_internet = true", 'allow_internet = "yes"', "allow_internet must be true or false"),
])
def test_bad_fields(seq_dir, old, new, message):
    edit_toml(seq_dir, old, new)
    assert message in problems(seq_dir)


def test_several_problems_reported_together(seq_dir):
    edit_toml(seq_dir, "schema = 1", "schema = 3")
    edit_toml(seq_dir, "cpus = 1", "cpus = -1")
    error = problems(seq_dir)
    assert "schema must be 1" in error and "cpus must be a positive integer" in error


def test_docker_image_instead_of_context(seq_dir):
    edit_toml(seq_dir, 'context = "environment"', 'docker_image = "example/mini:1"')
    seq = load_sequence(seq_dir)
    assert seq.environment.docker_image == "example/mini:1"
    assert seq.environment.context is None


def test_ticket_path_overrides(seq_dir):
    moved = seq_dir / "elsewhere"
    shutil.move(seq_dir / "tickets" / "MINI-1" / "ticket.md", moved)
    edit_toml(seq_dir, 'title = "add() subtracts"', 'title = "add() subtracts"\nticket = "elsewhere"')
    assert load_sequence(seq_dir).ticket("MINI-1").ticket_md == moved.resolve()


def test_defaults_for_optional_ticket_fields(seq_dir):
    edit_toml(seq_dir, 'cluster = "text"\nrole = "isolated"\n', "")
    ticket = load_sequence(seq_dir).ticket("MINI-3")
    assert ticket.cluster == "MINI-3" and ticket.role == "core"


# --- ticket contract (odyssey validate, --no-docker) ------------------------------

BASE_TREE = FIXTURE / "environment" / "base"


def run_validate(seq_dir: Path, tree: Path = BASE_TREE, repeat: int = 1) -> list[v.Check]:
    seq = load_sequence(seq_dir)
    runner = v.LocalRunner(seq, tree)
    try:
        return v.validate(seq, runner, repeat=repeat)
    finally:
        runner.close()


def failing(checks: list[v.Check]) -> list[tuple[str, str]]:
    return [(c.ticket, c.name) for c in checks if not c.ok]


def test_fixture_satisfies_ticket_contract():
    checks = run_validate(FIXTURE, repeat=2)
    assert failing(checks) == []
    names = {c.name for c in checks}
    assert {"base fails", "base + own gold passes", "base + all gold passes", "base + other gold fails",
            "gold patches touch disjoint hunks"} <= names
    assert any(c.name.startswith("gold patches apply together in any order (8 orders)") for c in checks)


def test_contract_from_a_tarball_tree(tmp_path):
    tarball = tmp_path / "base.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(BASE_TREE, arcname=".")
    assert failing(run_validate(FIXTURE, tree=tarball)) == []


def test_grader_that_always_passes_is_caught(seq_dir):
    (seq_dir / "tickets" / "MINI-2" / "tests" / "run.sh").write_text("exit 0\n")
    assert failing(run_validate(seq_dir)) == [("MINI-2", "base fails"), ("MINI-2", "base + other gold fails")]


def test_overlapping_gold_patches_are_caught(seq_dir):
    # MINI-2's gold now also rewrites add(), the line MINI-1 fixes.
    mini1 = (seq_dir / "tickets" / "MINI-1" / "gold.patch").read_text()
    (seq_dir / "tickets" / "MINI-2" / "gold.patch").write_text(mini1.replace("$1 + $2", "$2 + $1"))
    failures = failing(run_validate(seq_dir))
    assert ("*", "gold patches touch disjoint hunks") in failures
    assert any(name.startswith("gold patches apply together") for _, name in failures)


def test_patch_hunks_parses_ranges_and_new_files():
    text = (FIXTURE / "tickets" / "MINI-2" / "gold.patch").read_text()
    assert v.patch_hunks(text) == {"math.sh": [(15, 19)]}
    new_file = "diff --git a/n.txt b/n.txt\nnew file mode 100644\n--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1 @@\n+x\n"
    assert v.patch_hunks(new_file)["n.txt"][0] == v._WHOLE_FILE


def test_cli_validate_no_docker(capsys, seq_dir):
    assert cli.main(["validate", str(FIXTURE), "--no-docker", "--tree", str(BASE_TREE)]) == 0
    assert "checks passed" in capsys.readouterr().out
    # Without --tree the sequence's base_tree is used.
    assert cli.main(["validate", str(FIXTURE), "--no-docker", "--subsets"]) == 0
    assert "base + any subset of other gold fails" in capsys.readouterr().out
    edit_toml(seq_dir, 'base_tree = "environment/base"\n', "")
    assert cli.main(["validate", str(seq_dir), "--no-docker"]) == 2
    assert "needs --tree" in capsys.readouterr().err


def test_must_fail_checks_reject_errors_and_p2p_breakage(seq_dir):
    """A grader that fails for the wrong reason (collection error, broken P2P) is not a clean failure."""
    details = ('{"fail_to_pass": {"tests": 1, "failed": 0, "errors": 1}, '
               '"pass_to_pass": {"tests": 3, "failed": 0, "errors": 0}}')
    (seq_dir / "tickets" / "MINI-2" / "tests" / "run.sh").write_text(
        "bash tests/check_mul.sh && exit 0\n"
        f"printf '%s' '{details}' > \"$ODYSSEY_OUT/details.json\"\nexit 1\n")
    checks = {(c.ticket, c.name): c for c in run_validate(seq_dir)}
    base = checks[("MINI-2", "base fails")]
    assert not base.ok and "errored" in base.detail
    assert checks[("MINI-2", "base + own gold passes")].ok


def test_clean_failure_rules():
    ok = {"fail_to_pass": {"tests": 2, "failed": 2, "errors": 0},
          "pass_to_pass": {"tests": 5, "failed": 0, "errors": 0}}
    assert v.clean_failure(ok) is None
    assert "did not fail" in v.clean_failure({**ok, "fail_to_pass": {"tests": 2, "failed": 0, "errors": 0}})
    assert "pass-to-pass" in v.clean_failure({**ok, "pass_to_pass": {"tests": 5, "failed": 1, "errors": 0}})
    assert v.clean_failure({"resolved": False}) is None  # unknown format: exit code only
    assert v.parse_details("x\nODYSSEY_DETAILS_BEGIN\n{\"a\": 1}\nODYSSEY_DETAILS_END\n") == {"a": 1}
    assert v.parse_details("ODYSSEY_DETAILS_BEGIN\n\nODYSSEY_DETAILS_END") is None


def test_subset_sweep_finds_a_vacuous_start_state(seq_dir):
    # MINI-3's grader now passes once MINI-1's fix is present: a forward-merge
    # segment for MINI-3 after MINI-1 would be solved before the agent starts.
    run = seq_dir / "tickets" / "MINI-3" / "tests" / "run.sh"
    run.write_text("grep -q '\\$1 + \\$2' math.sh && exit 0\n" + run.read_text())
    seq = load_sequence(seq_dir)
    runner = v.LocalRunner(seq, BASE_TREE)
    try:
        check = v._subset_check(seq, runner, seq.ticket("MINI-3"))
    finally:
        runner.close()
    assert not check.ok and "MINI-1" in check.detail


def test_contract_script_checks_base_tarball_against_manifest(tmp_path):
    import dataclasses
    seq = load_sequence(FIXTURE)
    tarball = tmp_path / "base.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(BASE_TREE, arcname=".")
    script = v.contract_script(dataclasses.replace(seq, workdir=str(tmp_path / "app")))
    script = script.replace("/opt/odyssey/base.tar.gz", str(tarball))
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert "CONTRACT base tarball matches base manifest OK" in out
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(BASE_TREE, arcname=".")
        tar.add(FIXTURE / "sequence.toml", arcname="./conftest.py")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout
    assert "CONTRACT base tarball matches base manifest FAIL" in out


# --- [grading] and base_tree --------------------------------------------------------


def test_grading_defaults_and_overrides(seq_dir):
    seq = load_sequence(FIXTURE)
    assert seq.grading.protected_paths == ()
    assert "conftest.py" in seq.grading.protected_names and "*.pth" in seq.grading.protected_names
    assert seq.environment.base_tree == (FIXTURE / "environment" / "base").resolve()
    edit_toml(seq_dir, "[[tickets]]", '[grading]\nprotected_paths = ["tests/"]\nprotected_names = []\n\n[[tickets]]')
    seq = load_sequence(seq_dir)
    assert seq.grading.protected_paths == ("tests",) and seq.grading.protected_names == ()


@pytest.mark.parametrize("table, message", [
    ('[grading]\nprotected_paths = ["../x"]', "repo-relative path"),
    ('[grading]\nprotected_names = ["a/conftest.py"]', "basename glob"),
    ('[grading]\nprotected_paths = "tests"', "list of non-empty strings"),
    ('[grading]\nprotect = []', "unknown key 'protect'"),
])
def test_bad_grading_table(seq_dir, table, message):
    edit_toml(seq_dir, "[[tickets]]", table + "\n\n[[tickets]]")
    assert message in problems(seq_dir)


def test_missing_base_tree(seq_dir):
    edit_toml(seq_dir, 'base_tree = "environment/base"', 'base_tree = "environment/nope"')
    assert "base_tree directory not found" in problems(seq_dir)
