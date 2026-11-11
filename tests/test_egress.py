"""`odyssey compile --egress allowlist` (SPEC section 11) and the egress accounting columns.

Off (the default) must leave every compiled file as it was before the option existed;
on, the task must carry Harbor 0.22's allowlist policy, the sidecar overlay and the
agent-CLI install. Where the `harbor` package is importable (here or behind the
`harbor` CLI), the generated task is also loaded by Harbor itself.
"""

import filecmp
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from odyssey import accounting as acc
from odyssey import cli
from odyssey.compile import (
    AGENT_CLI_CLAUDE_CODE_VERSION, AGENT_CLI_CODEX_VERSION, AGENT_CLI_NODE_IMAGE,
    EGRESS_ALLOWED_HOSTS, EGRESS_LOG_ARTIFACT, EGRESS_LOG_PATH, EGRESS_SIDECAR_SERVICE,
    ArmOptions, compile_task,
)
from odyssey.design import explicit
from odyssey.spec import load_sequence
from test_accounting import PRICES, StepSpec, TaskSpec, W1, W2, make_task, make_trial
from test_compile import _harbor_python

FIXTURE = Path(__file__).parent / "fixtures" / "mini-seq"
ORDER = ("MINI-2", "MINI-1", "MINI-3")
TEMPLATES = Path(__file__).parents[1] / "odyssey" / "templates" / "egress"


@pytest.fixture(scope="module")
def seq():
    return load_sequence(FIXTURE)


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- off: nothing changes ----------------------------------------------------------


def test_open_is_the_default_and_leaves_no_trace(seq, tmp_path):
    default = compile_task(seq, explicit(ORDER), ArmOptions(), tmp_path / "a")
    explicit_open = compile_task(seq, explicit(ORDER), ArmOptions(egress="open"), tmp_path / "b")
    assert default.name == explicit_open.name == f"mini-seq__fm-inf-nofb__{explicit(ORDER).id}"
    assert _tree(default) == _tree(explicit_open)

    config = tomllib.loads((default / "task.toml").read_text())
    assert "artifacts" not in config
    assert "network_mode" not in config["verifier"]
    assert config["environment"]["network_mode"] == "public"
    assert "allowed_hosts" not in config["environment"]
    manifest = json.loads((default / "odyssey.json").read_text())
    assert set(manifest["arm_options"]) == {"code_state", "framing", "feedback", "seed_notes"}
    # environment/ is the sequence's build context, copied verbatim.
    cmp = filecmp.dircmp(FIXTURE / "environment", default / "environment")
    assert not cmp.left_only and not cmp.right_only and not cmp.diff_files


# --- on: task.toml and manifest ------------------------------------------------------


def test_allowlist_task_toml_and_manifest(seq, tmp_path):
    arm = ArmOptions(egress="allowlist")
    assert arm.arm_id == "fm-inf-nofb-egress"
    task_dir = compile_task(seq, explicit(ORDER), arm, tmp_path)
    assert task_dir.name == f"mini-seq__fm-inf-nofb-egress__{explicit(ORDER).id}"

    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["environment"]["network_mode"] == "allowlist"
    assert config["environment"]["allowed_hosts"] == ["api.anthropic.com", "api.openai.com"]
    assert config["verifier"]["network_mode"] == "no-network"
    assert config["artifacts"] == [{
        "source": EGRESS_LOG_PATH, "service": EGRESS_SIDECAR_SERVICE, "destination": EGRESS_LOG_ARTIFACT}]
    # Steps set only timeouts, so every step inherits the task-level policies.
    assert all(set(s["verifier"]) == {"timeout_sec"} for s in config["steps"])

    manifest = json.loads((task_dir / "odyssey.json").read_text())
    assert manifest["arm"] == "fm-inf-nofb-egress"
    assert manifest["arm_options"]["egress"] == {
        "mode": "allowlist", "allowed_hosts": ["api.anthropic.com", "api.openai.com"]}


def test_allowlist_is_short_and_has_no_code_hosts():
    assert EGRESS_ALLOWED_HOSTS == ("api.anthropic.com", "api.openai.com")
    for host in EGRESS_ALLOWED_HOSTS:
        assert "*" not in host
        assert not any(bad in host for bad in ("github", "gitlab", "pypi", "npm", "google", "bing"))


def test_arm_id_with_notes_and_egress(seq, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("hello\n")
    arm = ArmOptions(code_state="carry", framing="natural", feedback="binary", seed_notes=notes,
                     egress="allowlist")
    assert arm.arm_id.startswith("carry-nat-fb-notes-") and arm.arm_id.endswith("-egress")


# --- on: environment overlay ----------------------------------------------------------


def test_allowlist_environment_overlay(seq, tmp_path):
    task_dir = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist"), tmp_path)
    env = task_dir / "environment"
    source = FIXTURE / "environment"

    # Every file of the sequence's context is unchanged except the Dockerfile, which only grows.
    for rel, data in _tree(source).items():
        if rel != "Dockerfile":
            assert (env / rel).read_bytes() == data, rel
    original = (source / "Dockerfile").read_text()
    dockerfile = (env / "Dockerfile").read_text()
    assert dockerfile.startswith(original)
    block = dockerfile[len(original):]
    assert f"COPY --from={AGENT_CLI_NODE_IMAGE} /usr/local/bin/node /opt/agent-cli/bin/node" in block
    assert "@sha256:" in AGENT_CLI_NODE_IMAGE
    assert f"ARG AGENT_CLI_CLAUDE_CODE_VERSION={AGENT_CLI_CLAUDE_CODE_VERSION}" in block
    assert f"ARG AGENT_CLI_CODEX_VERSION={AGENT_CLI_CODEX_VERSION}" in block
    assert '"@anthropic-ai/claude-code@${AGENT_CLI_CLAUDE_CODE_VERSION}"' in block
    assert '"@openai/codex@${AGENT_CLI_CODEX_VERSION}"' in block
    assert "/usr/local/bin/claude --version" in block and "/usr/local/bin/codex --version" in block
    assert "@@" not in block and "FROM " not in block  # appended to the final stage, not a new one

    compose = (env / "docker-compose.yaml").read_text()
    assert compose == (TEMPLATES / "docker-compose.yaml").read_text()
    for needle in ("  main:\n", "      - NET_RAW\n", f"  {EGRESS_SIDECAR_SERVICE}:\n",
                   'net.ipv4.ping_group_range: "1 0"',
                   "./odyssey-egress/gost.yaml:/opt/egress-sidecar/gost.yaml:ro"):
        assert needle in compose
    # main must not declare networking, or Harbor would leave it outside the sidecar.
    assert "network_mode" not in compose.split("harbor-docker")[0].split("#")[0]
    assert "networks:" not in compose

    gost = (env / "odyssey-egress" / "gost.yaml").read_text()
    assert f"output: {EGRESS_LOG_PATH}" in gost and "level: debug" in gost and "format: json" in gost
    assert (env / ".dockerignore").read_text().splitlines()[-2:] == ["docker-compose.yaml", "odyssey-egress"]


def _harbor_package_dir() -> Path | None:
    python = _harbor_python()
    if python is None:
        return None
    proc = subprocess.run([python, "-c", "import harbor, os; print(os.path.dirname(harbor.__file__))"],
                          capture_output=True, text=True)
    return Path(proc.stdout.strip()) if proc.returncode == 0 else None


def test_gost_config_is_harbors_plus_a_log_block():
    harbor_dir = _harbor_package_dir()
    if harbor_dir is None:
        pytest.skip("harbor not importable")
    harbor_gost = (harbor_dir / "environments" / "docker" / "harbor-docker-egress-control-sidecar"
                   / "gost.yaml").read_text()
    ours = (TEMPLATES / "gost.yaml").read_text()
    assert harbor_gost in ours, "Harbor's sidecar gost.yaml changed; re-derive templates/egress/gost.yaml"
    extra = ours.replace(harbor_gost, "")
    assert [line for line in extra.splitlines() if line and not line.startswith("#")] == [
        "log:", f"  output: {EGRESS_LOG_PATH}", "  level: debug", "  format: json"]


HARBOR_EGRESS_CHECK = """
import json, sys
from pathlib import Path
from harbor.models.task.task import Task
from harbor.models.task.config import NetworkMode
from harbor.environments.docker.docker import DockerEnvironment
task = Task(sys.argv[1])
cfg = task.config

class Probe:  # just what DockerEnvironment._egress_controlled_service_names reads
    _environment_docker_compose_path = Path(sys.argv[1]) / "environment" / "docker-compose.yaml"
    extra_docker_compose_paths = []
    _EGRESS_CONTROL_SERVICE_NAME = DockerEnvironment._EGRESS_CONTROL_SERVICE_NAME

print(json.dumps({
    "env_mode": cfg.environment.network_mode.value,
    "allowed": cfg.environment.allowed_hosts,
    "verifier": cfg.verifier.explicit_phase_policy().network_mode.value,
    "step_verifier": [s.verifier.explicit_phase_policy() for s in cfg.steps],
    "artifacts": [[a.source, a.service, a.destination] for a in cfg.artifacts],
    "routed_services": DockerEnvironment._egress_controlled_service_names(Probe()),
    "sidecar": DockerEnvironment._EGRESS_CONTROL_SERVICE_NAME,
}))
"""


def test_harbor_loads_the_allowlist_task(seq, tmp_path):
    python = _harbor_python()
    if python is None:
        pytest.skip("harbor not importable")
    task_dir = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist"), tmp_path)
    proc = subprocess.run([python, "-c", HARBOR_EGRESS_CHECK, str(task_dir)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["env_mode"] == "allowlist"
    assert got["allowed"] == list(EGRESS_ALLOWED_HOSTS)
    assert got["verifier"] == "no-network"
    assert got["step_verifier"] == [None, None, None]
    assert got["artifacts"] == [[EGRESS_LOG_PATH, EGRESS_SIDECAR_SERVICE, EGRESS_LOG_ARTIFACT]]
    assert got["sidecar"] == EGRESS_SIDECAR_SERVICE
    # Harbor puts `main` into the sidecar's network namespace (our compose adds no networking).
    assert got["routed_services"] == ["main"]


# --- refusals ----------------------------------------------------------------------


def _variant(tmp_path: Path, *, toml_edit=None, dockerfile_extra: str = "", extra_file: str | None = None):
    root = tmp_path / "seq"
    import shutil
    shutil.copytree(FIXTURE, root)
    if toml_edit:
        path = root / "sequence.toml"
        path.write_text(toml_edit(path.read_text()))
    if dockerfile_extra:
        with open(root / "environment" / "Dockerfile", "a") as handle:
            handle.write(dockerfile_extra)
    if extra_file:
        (root / "environment" / extra_file).write_text("services: {}\n")
    return load_sequence(root)


@pytest.mark.parametrize("case,message", [
    ("no-internet", "allow_internet = false"),
    ("user", "USER instruction"),
    ("compose", "already has docker-compose.yaml"),
])
def test_allowlist_refusals_write_nothing(tmp_path, case, message):
    if case == "no-internet":
        s = _variant(tmp_path, toml_edit=lambda t: t.replace("allow_internet = true", "allow_internet = false"))
    elif case == "user":
        s = _variant(tmp_path, dockerfile_extra="USER nobody\n")
    else:
        s = _variant(tmp_path, extra_file="docker-compose.yaml")
    out = tmp_path / "out"
    with pytest.raises(ValueError, match=message):
        compile_task(s, explicit(ORDER), ArmOptions(egress="allowlist"), out)
    assert not out.exists()
    # The same sequence still compiles with the default.
    compile_task(s, explicit(ORDER), ArmOptions(), out)


def test_allowlist_refuses_prebuilt_image(tmp_path):
    s = _variant(tmp_path, toml_edit=lambda t: t.replace('context = "environment"', 'docker_image = "x/y:z"')
                 .replace('base_tree = "environment/base"\n', ""))
    with pytest.raises(ValueError, match="build context"):
        compile_task(s, explicit(ORDER), ArmOptions(egress="allowlist"), tmp_path / "out")


def test_unknown_egress_mode():
    with pytest.raises(ValueError, match="--egress"):
        ArmOptions(egress="proxy")


def test_cli_egress_flag(tmp_path, capsys):
    assert cli.main(["compile", str(FIXTURE), "--out", str(tmp_path), "--ordering", "MINI-1",
                     "--egress", "allowlist"]) == 0
    assert "arm fm-inf-nofb-egress" in capsys.readouterr().out
    (task_dir,) = tmp_path.iterdir()
    assert (task_dir / "environment" / "docker-compose.yaml").is_file()
    with pytest.raises(SystemExit):
        cli.main(["compile", str(FIXTURE), "--out", str(tmp_path), "--ordering", "MINI-1",
                  "--egress", "wide-open"])


# --- accounting ----------------------------------------------------------------------

# Real gost lines (exp2 on the build box), one connection per sid.
ALLOW = '{"bypass":"allowlist","kind":"bypass","level":"debug","msg":"proxy: api.anthropic.com:443, whitelist: true","sid":"%s"}'
DENY = '{"bypass":"allowlist","kind":"bypass","level":"debug","msg":"bypass: %s, whitelist: true","sid":"%s"}'
DENY_HANDLER = ('{"handler":"red","kind":"handler","level":"debug","msg":"bypass: %s","sid":"%s",'
                '"dst":"%s"}')
ERROR = '{"kind":"service","level":"error","msg":"bypass","sid":"%s"}'
NOISE = '{"bypass":"allowlist","kind":"bypass","level":"debug","msg":"load items 2"}'


def test_egress_connections_counts_each_connection_once():
    lines = [NOISE, ALLOW % "a", DENY % ("8.8.8.8:53", "b"), DENY_HANDLER % ("8.8.8.8:53", "b", "8.8.8.8:53"),
             ERROR % "b", "not json"]
    assert acc.egress_connections(lines) == [
        ("a", "allowed", "api.anthropic.com:443"), ("b", "denied", "8.8.8.8:53"), ("b", "denied", "8.8.8.8:53")]


def _egress_task(tmp_path: Path) -> Path:
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core"), ("LED-2", "A", "core")]))
    manifest = json.loads((task / "odyssey.json").read_text())
    manifest["arm"] = "fm-inf-nofb-egress"
    manifest["arm_options"]["egress"] = {"mode": "allowlist", "allowed_hosts": list(EGRESS_ALLOWED_HOSTS)}
    (task / "odyssey.json").write_text(json.dumps(manifest))
    return task


def test_account_counts_denied_requests_per_segment(tmp_path):
    task = _egress_task(tmp_path)
    trial = make_trial(tmp_path, task, [StepSpec("s01-led-1", *W1, None), StepSpec("s02-led-2", *W2, None)])
    step1 = [NOISE, ALLOW % "a1", ALLOW % "a2", DENY % ("github.com:443", "d1"), ERROR % "d1"]
    # Step 2's snapshot is step 1's log plus what came after (the log is cumulative).
    step2 = step1 + [DENY % ("raw.githubusercontent.com:443", "d2"), DENY % ("github.com:443", "d3"),
                     DENY % ("github.com:443", "d4"), DENY_HANDLER % ("1.1.1.1:53", "d5", "1.1.1.1:53"),
                     DENY % ("1.1.1.1:53", "d5"), ALLOW % "a3"]
    for name, lines in (("s01-led-1", step1), ("s02-led-2", step2)):
        path = trial / "steps" / name / "artifacts" / acc.EGRESS_LOG
        path.parent.mkdir(parents=True)
        path.write_text("\n".join(lines) + "\n")

    rows = {r["position"]: r for r in acc.account_trial(trial, PRICES)}
    assert (rows[1]["egress_allowed"], rows[1]["egress_denied"]) == (2, 1)
    assert rows[1]["egress_denied_targets"] == "github.com:443=1"
    assert (rows[2]["egress_allowed"], rows[2]["egress_denied"]) == (1, 4)
    assert rows[2]["egress_denied_targets"] == "github.com:443=2 1.1.1.1:53=1 raw.githubusercontent.com:443=1"
    assert "egress" not in rows[1]["warnings"] and "egress" not in rows[2]["warnings"]

    out = tmp_path / "segments.csv"
    acc.write_csv(list(rows.values()), out)
    header = out.read_text().splitlines()[0].split(",")
    assert header[-3:] == ["egress_allowed", "egress_denied", "egress_denied_targets"]


def test_account_single_step_and_missing_log(tmp_path):
    task = _egress_task(tmp_path)
    trial = make_trial(tmp_path, task, [StepSpec("s01-led-1", *W1, None)], single_step=True)
    rows = acc.account_trial(trial, PRICES)
    assert rows[0].get("egress_denied") is None
    assert "egress log missing" in rows[0]["warnings"]

    path = trial / "artifacts" / acc.EGRESS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DENY % ("pypi.org:443", "x") + "\n")
    rows = acc.account_trial(trial, PRICES)
    assert rows[0]["egress_denied"] == 1 and rows[0]["egress_denied_targets"] == "pypi.org:443=1"


def test_account_open_runs_have_empty_egress_columns(tmp_path):
    task = make_task(tmp_path, TaskSpec(tickets=[("LED-1", "A", "core")]))
    trial = make_trial(tmp_path, task, [StepSpec("s01-led-1", *W1, None)])
    (row,) = acc.account_trial(trial, PRICES)
    assert "egress_denied" not in row and "egress" not in row["warnings"]


def test_account_warns_when_a_run_widened_the_allowlist(tmp_path):
    task = _egress_task(tmp_path)
    trial = make_trial(tmp_path, task, [StepSpec("s01-led-1", *W1, None)])
    result = json.loads((trial / "result.json").read_text())
    result["config"]["environment"] = {"extra_allowed_hosts": ["github.com"]}
    (trial / "result.json").write_text(json.dumps(result))
    (row, _) = acc.account_trial(trial, PRICES)
    assert "run widened the egress allowlist with github.com" in row["warnings"]


# --- --egress-allow-host and --agent-cli -----------------------------------------------------

BEDROCK = "bedrock-runtime.us-east-1.amazonaws.com"


def test_extra_options_unset_change_nothing(seq, tmp_path):
    plain = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist"), tmp_path / "a")
    empty = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist", egress_allow_hosts=(),
                                                          agent_clis=()), tmp_path / "b")
    assert _tree(plain) == _tree(empty)


def test_extra_allowed_host(seq, tmp_path):
    arm = ArmOptions(egress="allowlist", egress_allow_hosts=(BEDROCK, BEDROCK, "api.openai.com"))
    assert arm.arm_id == "fm-inf-nofb-egress"
    assert arm.allowed_hosts == ("api.anthropic.com", "api.openai.com", BEDROCK)
    task_dir = compile_task(seq, explicit(ORDER), arm, tmp_path)
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["environment"]["allowed_hosts"] == ["api.anthropic.com", "api.openai.com", BEDROCK]
    manifest = json.loads((task_dir / "odyssey.json").read_text())
    assert manifest["arm_options"]["egress"] == {
        "mode": "allowlist", "allowed_hosts": ["api.anthropic.com", "api.openai.com", BEDROCK]}
    # The image is the same as without the host: only the policy changes.
    plain = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist"), tmp_path / "plain")
    assert _tree(task_dir / "environment") == _tree(plain / "environment")


@pytest.mark.parametrize("kwargs,message", [
    ({"egress_allow_hosts": (BEDROCK,)}, "need --egress allowlist"),
    ({"agent_clis": ("kimi-cli",)}, "need --egress allowlist"),
    ({"egress": "allowlist", "egress_allow_hosts": ("*.amazonaws.com",)}, "without wildcards"),
    ({"egress": "allowlist", "egress_allow_hosts": ("Bedrock.example.com",)}, "lowercase hostname"),
    ({"egress": "allowlist", "egress_allow_hosts": ("localhost",)}, "hostname"),
    ({"egress": "allowlist", "egress_allow_hosts": ("raw.githubusercontent.com",)}, "code or package host"),
    ({"egress": "allowlist", "egress_allow_hosts": ("pypi.org",)}, "code or package host"),
    ({"egress": "allowlist", "agent_clis": ("aider",)}, "--agent-cli must be one of"),
])
def test_extra_option_refusals(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ArmOptions(**kwargs)


def test_agent_cli_block(seq, tmp_path):
    from odyssey.compile import AGENT_CLI_EXTRAS, AGENT_CLI_UV_IMAGE
    arm = ArmOptions(egress="allowlist", agent_clis=("mini-swe-agent", "kimi-cli", "kimi-cli"))
    assert arm.agent_clis == ("kimi-cli", "mini-swe-agent")
    task_dir = compile_task(seq, explicit(ORDER), arm, tmp_path / "x")
    plain = compile_task(seq, explicit(ORDER), ArmOptions(egress="allowlist"), tmp_path / "plain")
    base = (plain / "environment" / "Dockerfile").read_text()
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    assert dockerfile.startswith(base)  # the Node CLIs first, unchanged
    block = dockerfile[len(base):]
    assert f"COPY --from={AGENT_CLI_UV_IMAGE} /uv /uvx /root/.local/bin/" in block
    assert "@sha256:" in AGENT_CLI_UV_IMAGE
    assert "uv tool install --python 3.13 kimi-cli==1.51.0; kimi --version; \\\n" in block
    assert "uv tool install --python 3.13 mini-swe-agent==2.4.6 --with litellm==1.104.7" in block
    assert block.index("kimi-cli==") < block.index("mini-swe-agent==")
    for spec, _ in AGENT_CLI_EXTRAS.values():
        assert "==" in spec.split()[0]  # every harness pinned
    assert "@@" not in block and "FROM " not in block
    # Each RUN line continues to the next: the block is one valid instruction.
    run = block[block.index("RUN "):]
    lines = run.rstrip("\n").splitlines()
    assert all(line.endswith("\\") for line in lines[:-1]) and not lines[-1].endswith("\\")
    manifest = json.loads((task_dir / "odyssey.json").read_text())
    assert manifest["arm_options"]["egress"]["agent_clis"] == ["kimi-cli", "mini-swe-agent"]
    assert manifest["arm"] == "fm-inf-nofb-egress"


def test_cli_extra_flags(tmp_path, capsys):
    assert cli.main(["compile", str(FIXTURE), "--out", str(tmp_path), "--ordering", "MINI-1",
                     "--egress", "allowlist", "--egress-allow-host", BEDROCK, "--agent-cli", "kimi-cli"]) == 0
    (task_dir,) = tmp_path.iterdir()
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["environment"]["allowed_hosts"][-1] == BEDROCK
    assert "kimi-cli==" in (task_dir / "environment" / "Dockerfile").read_text()
    capsys.readouterr()
    assert cli.main(["compile", str(FIXTURE), "--out", str(tmp_path / "o"), "--ordering", "MINI-1",
                     "--egress-allow-host", BEDROCK]) == 2
    assert "need --egress allowlist" in capsys.readouterr().err
