"""Compile sequence + ordering + arm into a Harbor 0.22 multi-step task dir (SPEC sections 2, 4-6).

Layout written for one ordering x one arm:

    <out>/<sequence>__<arm>__<ordering-id>/
      task.toml                      [[steps]] in ordering order; manifest under [metadata]
      odyssey.json                   manifest read by `odyssey account`
      environment/                   copy of the sequence's build context
      steps/sNN-<ticket>/
        instruction.md               framing header + ticket text
        workdir/setup.sh             code-state policy (templates/setup.sh)
        workdir/.odyssey_step/       payload moved out of the repo by setup.sh:
                                     state.patch (reset policies), NOTES.md (step 1, --seed-notes),
                                     base.manifest (when the sequence declares base_tree or base_manifest)
        tests/test.sh                grader (templates/test.sh)
        tests/start/                 the segment's start state for the grader: state.patch,
                                     base.manifest (uploaded only after the agent's turn)
        tests/ticket/run.sh, files/  the ticket's hidden grader
        solution/solve.sh, gold.patch  oracle only

Harbor's docker backend uploads workdir/ with `docker compose cp <dir>/.`, which
includes dot-directories, so the payload can live in a hidden dir.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from odyssey import __version__
from odyssey.design import Ordering
from odyssey.spec import Sequence, Ticket

CODE_STATES = ("forward-merge", "all-defects", "single-defect", "carry")
RESET_POLICIES = ("forward-merge", "all-defects", "single-defect")
FRAMINGS = ("informed", "natural")
FEEDBACKS = ("none", "binary")
EGRESS_MODES = ("open", "allowlist")
MANIFEST_NAME = "odyssey.json"
PAYLOAD_DIR = ".odyssey_step"
FEEDBACK_DIR = "/var/odyssey/feedback"

# --egress allowlist (SPEC section 11). The hosts Claude Code and Codex call with an
# API key; nothing else. Harbor's run flags --allow-environment-host and
# --allow-agent-host can widen it at run time; `odyssey account` warns when a run did.
EGRESS_ALLOWED_HOSTS = ("api.anthropic.com", "api.openai.com")
# Harbor 0.22's egress-control sidecar (environments/docker/docker.py).
EGRESS_SIDECAR_SERVICE = "harbor-docker-egress-control-sidecar"
# Where our gost.yaml makes the sidecar log every connection, and where Harbor puts
# the copy it collects after each step (under the trial's or step's artifacts/).
EGRESS_LOG_PATH = "/var/log/odyssey-egress.jsonl"
EGRESS_LOG_ARTIFACT = "odyssey-egress.jsonl"
EGRESS_DIR = "odyssey-egress"
# Agent CLIs baked into the image at build time (the trial cannot download them).
AGENT_CLI_NODE_IMAGE = (
    "node:22.23.4-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d")
AGENT_CLI_CLAUDE_CODE_VERSION = "2.1.322"
AGENT_CLI_CODEX_VERSION = "0.167.0"
# Optional Python harnesses baked in with `--agent-cli`, installed
# with uv into root's home. Versions are pinned and were at least two weeks old when
# chosen; litellm is mini-swe-agent's model client. kimi-cli 1.52.0, its last release,
# only prints "kimi-cli is no longer maintained" and exits (measured 2026-11-10), so
# 1.51.0 is the last working one.
AGENT_CLI_UV_IMAGE = (
    "ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff")
AGENT_CLI_UV_PYTHON = "3.13"
AGENT_CLI_EXTRAS = {
    # name -> (uv tool install arguments, command that proves the install)
    "kimi-cli": ("kimi-cli==1.51.0", "kimi --version"),
    "mini-swe-agent": ("mini-swe-agent==2.4.6 --with litellm==1.104.7 --with orjson --with fastapi",
                       "mini-swe-agent --help >/dev/null"),
}
# Node harnesses baked in with `--agent-cli`, installed with `npm ci` from a lockfile
# shipped in templates/egress/<name>/ and copied into the build context as
# AGENT_CLI_NPM_DIR. dsh: the DeepSeek Harness, @deepseek-ai/dsh 0.2.1-rc.4 (published
# 2026-10-26; every dsh release is a prerelease, and this was the newest one at least two
# weeks old when chosen on 2026-11-10).
AGENT_CLI_NPM_EXTRAS = {
    # name -> (template dir and Dockerfile.<name> block, package@version pinned in its lockfile)
    "dsh": ("dsh", "@deepseek-ai/dsh@0.2.1-rc.4"),
}
AGENT_CLI_NPM_DIR = "odyssey-dsh"
AGENT_CLI_NAMES = tuple(sorted({*AGENT_CLI_EXTRAS, *AGENT_CLI_NPM_EXTRAS}))
# Hosts --egress-allow-host refuses: code and package hosts, the point of the allowlist.
_CODE_HOSTS = ("github", "gitlab", "bitbucket", "sourceforge", "pypi", "pythonhosted", "npmjs",
               "googlesource", "savannah")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_COMPOSE_NAMES = ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml")

_CODE_STATE_SHORT = {"forward-merge": "fm", "all-defects": "ad", "single-defect": "sd", "carry": "carry"}
_FRAMING_SHORT = {"informed": "inf", "natural": "nat"}

# Ignored on both sides of the base-snapshot manifest (templates/lib_manifest.sh);
# image builds drop them too (see drafts/sequences/toy-ledger/environment/.dockerignore).
_MANIFEST_IGNORED = (".git", "__pycache__", ".pytest_cache", ".DS_Store", "*.pyc")

# run.sh gets this share of the verifier timeout; the rest covers the scratch
# copy and the diff, so test.sh can still write its reward before Harbor kills it.
_RUN_TIMEOUT_SHARE = 0.8


@dataclass(frozen=True)
class ArmOptions:
    """The compile-time half of an arm (the run-time half is Harbor flags)."""

    code_state: str = "forward-merge"
    framing: str = "informed"
    feedback: str = "none"
    seed_notes: Path | None = None
    egress: str = "open"
    # --egress-allow-host: hosts added to EGRESS_ALLOWED_HOSTS (e.g. a Bedrock endpoint).
    egress_allow_hosts: tuple[str, ...] = ()
    # --agent-cli: extra harnesses baked into the image (keys of AGENT_CLI_EXTRAS).
    agent_clis: tuple[str, ...] = ()

    def __post_init__(self):
        for value, allowed, flag in (
            (self.code_state, CODE_STATES, "--code-state"),
            (self.framing, FRAMINGS, "--framing"),
            (self.feedback, FEEDBACKS, "--feedback"),
            (self.egress, EGRESS_MODES, "--egress"),
        ):
            if value not in allowed:
                raise ValueError(f"{flag} must be one of {', '.join(allowed)}, got {value!r}")
        if self.seed_notes is not None and not Path(self.seed_notes).is_file():
            raise ValueError(f"--seed-notes file not found: {self.seed_notes}")
        if (self.egress_allow_hosts or self.agent_clis) and self.egress != "allowlist":
            raise ValueError("--egress-allow-host and --agent-cli need --egress allowlist "
                             "(with open egress the agent reaches every host and Harbor installs its harness)")
        for host in self.egress_allow_hosts:
            if not _HOSTNAME.match(host):
                raise ValueError(f"--egress-allow-host must be a lowercase hostname without wildcards, got {host!r}")
            if any(bad in host for bad in _CODE_HOSTS):
                raise ValueError(f"--egress-allow-host {host!r} is a code or package host; "
                                 "the allowlist exists to keep those out")
        for name in self.agent_clis:
            if name not in AGENT_CLI_NAMES:
                raise ValueError(f"--agent-cli must be one of {', '.join(AGENT_CLI_NAMES)}, got {name!r}")
        # Order-independent and duplicate-free, so equal options compile to equal bytes.
        object.__setattr__(self, "egress_allow_hosts", tuple(sorted(set(self.egress_allow_hosts))))
        object.__setattr__(self, "agent_clis", tuple(sorted(set(self.agent_clis))))

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """The allowlist: the two CLI hosts, then any --egress-allow-host not already in it."""
        return EGRESS_ALLOWED_HOSTS + tuple(h for h in self.egress_allow_hosts if h not in EGRESS_ALLOWED_HOSTS)

    @property
    def arm_id(self) -> str:
        """Short arm label used in directory names, e.g. 'fm-inf-nofb' or 'carry-nat-fb-notes-3fa2c1'.

        The notes part carries a hash of the notes file, so two different notes
        files never share a task directory (or an arm in the analysis).
        """
        parts = [
            _CODE_STATE_SHORT[self.code_state],
            _FRAMING_SHORT[self.framing],
            "fb" if self.feedback == "binary" else "nofb",
        ]
        if self.seed_notes is not None:
            parts += ["notes", self._notes_sha256()[:6]]
        if self.egress != "open":
            parts.append("egress")
        return "-".join(parts)

    def _notes_sha256(self) -> str:
        return hashlib.sha256(Path(self.seed_notes).read_bytes()).hexdigest()

    def as_dict(self) -> dict:
        notes = None
        if self.seed_notes is not None:
            notes = {"file": Path(self.seed_notes).name, "sha256": self._notes_sha256()}
        options = {
            "code_state": self.code_state,
            "framing": self.framing,
            "feedback": self.feedback,
            "seed_notes": notes,
        }
        if self.egress != "open":
            # Only when set, so open-egress manifests stay byte-identical to earlier compiles.
            options["egress"] = {"mode": self.egress, "allowed_hosts": list(self.allowed_hosts)}
            if self.agent_clis:  # only when set, so earlier allowlist manifests stay byte-identical too
                options["egress"]["agent_clis"] = list(self.agent_clis)
        return options


def step_name(position: int, ticket_id: str, total: int) -> str:
    width = max(2, len(str(total)))
    return f"s{position:0{width}d}-{ticket_id.lower()}"


def task_dir_name(seq: Sequence, arm: ArmOptions, ordering: Ordering) -> str:
    return f"{seq.id}__{arm.arm_id}__{ordering.id}"


def state_tickets(seq: Sequence, arm: ArmOptions, ordering: Ordering, position: int) -> list[str]:
    """Whose gold patches make up the start state of the segment at `position` (1-based).

    This is the only place gold patches enter a step's payload, so it decides
    what the agent could ever see: never the current ticket, and under
    forward-merge never a later one.
    """
    current = ordering.tickets[position - 1]
    if arm.code_state == "forward-merge":
        return list(ordering.tickets[: position - 1])
    if arm.code_state == "single-defect":
        # Every other ticket of the whole pool, so the start state depends only
        # on the ticket (not on the ordering or a --subset).
        return [t for t in seq.ticket_ids if t != current]
    return []


def compile_task(
    seq: Sequence, ordering: Ordering, arm: ArmOptions, out_root: Path, *, allow_leak: bool = False,
) -> Path:
    """Write one task dir and return its path. Replaces an earlier compile of the same name.

    single-defect puts every other ticket's gold patch into each start tree, so
    in segment 1 the agent can copy the fixes for every later ticket into
    memory. That makes warm single-defect runs measure memory use, not
    learning; such orderings are refused unless `allow_leak` is set.
    """
    for ticket_id in ordering.tickets:
        seq.ticket(ticket_id)  # raises KeyError for unknown ids
    if len(set(ordering.tickets)) != len(ordering.tickets):
        raise ValueError(f"ordering {ordering.id} repeats a ticket: {ordering.tickets}")
    if arm.code_state == "single-defect" and len(ordering.tickets) > 1 and not allow_leak:
        raise ValueError(
            "--code-state single-defect leaks every later ticket's fix into segment 1 "
            "(the agent can copy it to memory), so it is only valid for cold runs "
            "(--design cold); pass --allow-leak to compile it anyway")
    if arm.egress != "open":
        check_egress_support(seq)

    task_dir = Path(out_root) / task_dir_name(seq, arm, ordering)
    _prepare_dir(task_dir)
    manifest = build_manifest(seq, ordering, arm)
    base_manifest = base_manifest_text(seq)

    _write_environment(seq, task_dir / "environment")
    if arm.egress != "open":
        write_egress_environment(task_dir / "environment", arm.agent_clis)
    for step in manifest["steps"]:
        _write_step(seq, ordering, arm, step, task_dir / "steps" / step["name"], base_manifest)
    (task_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (task_dir / "task.toml").write_text(task_toml(seq, arm, ordering, manifest))
    return task_dir


def compile_many(
    seq: Sequence, orderings: list[Ordering], arm: ArmOptions, out_root: Path, *, allow_leak: bool = False,
) -> list[Path]:
    return [compile_task(seq, o, arm, out_root, allow_leak=allow_leak) for o in orderings]


def build_manifest(seq: Sequence, ordering: Ordering, arm: ArmOptions) -> dict:
    total = len(ordering.tickets)
    steps = []
    for position, ticket_id in enumerate(ordering.tickets, start=1):
        ticket = seq.ticket(ticket_id)
        steps.append({
            "name": step_name(position, ticket_id, total),
            "position": position,
            "ticket": ticket_id,
            "title": ticket.title,
            "cluster": ticket.cluster,
            "role": ticket.role,
            "state_tickets": state_tickets(seq, arm, ordering, position),
        })
    return {
        "odyssey_manifest_version": 1,
        "odyssey_version": __version__,
        "sequence": seq.id,
        "task_name": f"odyssey/{task_dir_name(seq, arm, ordering)}",
        "arm": arm.arm_id,
        "arm_options": arm.as_dict(),
        "ordering_id": ordering.id,
        "design": ordering.design,
        "ordering": list(ordering.tickets),
        "workdir": seq.workdir,
        "memory_dir": seq.memory_dir,
        "repo_memory_files": list(seq.repo_memory_files),
        "steps": steps,
    }


# --- task.toml ---------------------------------------------------------------


def _q(value: str) -> str:
    """A TOML basic string. JSON's escapes (\\", \\\\, \\n, \\uXXXX) are all valid TOML."""
    return json.dumps(value, ensure_ascii=True)


def task_toml(seq: Sequence, arm: ArmOptions, ordering: Ordering, manifest: dict) -> str:
    env = seq.environment
    egress = arm.egress != "open"
    lines = [
        "# Generated by `odyssey compile`; edit the sequence or the arm and recompile instead.",
        'schema_version = "1.4"',
        'multi_step_reward_strategy = "mean"',
    ]
    if egress:
        # The sidecar's connection log, collected after every step's agent phase from
        # the sidecar's own filesystem (Harbor 0.22 ArtifactConfig.service).
        lines.append(
            f"artifacts = [{{ source = {_q(EGRESS_LOG_PATH)}, service = {_q(EGRESS_SIDECAR_SERVICE)}, "
            f"destination = {_q(EGRESS_LOG_ARTIFACT)} }}]")
    lines += [
        "",
        "[task]",
        f"name = {_q(manifest['task_name'])}",
        f"description = {_q(f'SWE-Odyssey {seq.id}, arm {arm.arm_id}, ordering {ordering.id}')}",
        f"keywords = {json.dumps(['swe-odyssey', seq.id, arm.arm_id])}",
        "",
        "[metadata]",
        f"odyssey_sequence = {_q(seq.id)}",
        f"odyssey_arm = {_q(arm.arm_id)}",
        f"odyssey_ordering_id = {_q(ordering.id)}",
        f"odyssey_ordering = {json.dumps(list(ordering.tickets))}",
        f"odyssey_manifest = {_q(json.dumps(manifest, sort_keys=True))}",
        "",
        "[agent]",
        f"timeout_sec = {seq.agent_timeout_sec!r}",
        "",
        "[verifier]",
        f"timeout_sec = {seq.verifier_timeout_sec!r}",
    ]
    if egress:
        # Grading needs no network (validate --no-network proves it per sequence).
        lines.append('network_mode = "no-network"')
    lines += [
        "",
        "[environment]",
        f"workdir = {_q(seq.workdir)}",
    ]
    if egress:
        lines += [
            'network_mode = "allowlist"',
            f"allowed_hosts = {json.dumps(list(arm.allowed_hosts))}",
        ]
    else:
        lines.append(f"network_mode = {_q('public' if env.allow_internet else 'no-network')}")
    if env.docker_image:
        lines.append(f"docker_image = {_q(env.docker_image)}")
    if env.build_timeout_sec is not None:
        lines.append(f"build_timeout_sec = {env.build_timeout_sec!r}")
    for key in ("cpus", "memory_mb", "storage_mb"):
        value = getattr(env, key)
        if value is not None:
            lines.append(f"{key} = {value}")
    for step in manifest["steps"]:
        # Step-level timeouts must be explicit: Harbor's StepConfig defaults the
        # step verifier timeout to 600 s, which would override [verifier].
        lines += [
            "",
            "[[steps]]",
            f"name = {_q(step['name'])}",
            "",
            "[steps.agent]",
            f"timeout_sec = {seq.agent_timeout_sec!r}",
            "",
            "[steps.verifier]",
            f"timeout_sec = {seq.verifier_timeout_sec!r}",
        ]
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)  # fail here, not inside Harbor, if the writer ever emits bad TOML
    return text


# --- per-step files ----------------------------------------------------------


def _prepare_dir(task_dir: Path) -> None:
    """Replace an earlier compile, but never delete a directory we did not create."""
    if task_dir.exists():
        if not (task_dir / MANIFEST_NAME).is_file():
            raise FileExistsError(f"{task_dir} exists and was not generated by odyssey compile; not overwriting")
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)


def _write_environment(seq: Sequence, dest: Path) -> None:
    # Harbor requires environment/ to exist even when a prebuilt docker_image is used.
    if seq.environment.context is not None:
        shutil.copytree(seq.environment.context, dest, symlinks=True)
    else:
        dest.mkdir(parents=True)


# --- --egress allowlist (SPEC section 11) -------------------------------------

_USER_INSTRUCTION = re.compile(r"^\s*USER\s", re.IGNORECASE | re.MULTILINE)


def check_egress_support(seq: Sequence) -> None:
    """Refuse sequences the egress overlay cannot be added to safely (before anything is written)."""
    env = seq.environment
    if not env.allow_internet:
        raise ValueError(
            "--egress allowlist: the sequence sets allow_internet = false, which already denies "
            "all network access (the agent then cannot reach its model either)")
    if env.context is None:
        raise ValueError(
            "--egress allowlist needs the sequence's build context: the agent CLIs are installed "
            "into the image at build time, which a prebuilt docker_image does not allow")
    dockerfile = Path(env.context) / "Dockerfile"
    if not dockerfile.is_file():
        raise ValueError(f"--egress allowlist: {dockerfile} not found")
    if _USER_INSTRUCTION.search(dockerfile.read_text()):
        raise ValueError(
            f"--egress allowlist: {dockerfile} has a USER instruction; the appended agent-CLI "
            "install runs as the image's final user and needs root")
    for name in (*_COMPOSE_NAMES, EGRESS_DIR, AGENT_CLI_NPM_DIR):
        if (Path(env.context) / name).exists():
            raise ValueError(f"--egress allowlist: the build context already has {name}; "
                             "merging it with the egress overlay is not supported")


def egress_dockerfile_block() -> str:
    """The Dockerfile lines appended to the sequence's (final stage) to pre-install the agent CLIs."""
    return (_template("egress/Dockerfile.agent-cli")
            .replace("@@NODE_IMAGE@@", AGENT_CLI_NODE_IMAGE)
            .replace("@@CLAUDE_CODE_VERSION@@", AGENT_CLI_CLAUDE_CODE_VERSION)
            .replace("@@CODEX_VERSION@@", AGENT_CLI_CODEX_VERSION))


def agent_cli_extras_block(names: tuple[str, ...]) -> str:
    """Dockerfile lines that install the --agent-cli harnesses (after the Claude Code/Codex block).

    The uv-installed Python harnesses come first (unchanged when no Node harness is
    named), then one block per Node harness (AGENT_CLI_NPM_EXTRAS).
    """
    uv_names = tuple(n for n in names if n in AGENT_CLI_EXTRAS)
    npm_blocks = "".join(_template(f"egress/Dockerfile.{AGENT_CLI_NPM_EXTRAS[n][0]}")
                         for n in names if n in AGENT_CLI_NPM_EXTRAS)
    return (_uv_tools_block(uv_names) if uv_names else "") + npm_blocks


def _uv_tools_block(names: tuple[str, ...]) -> str:
    installs = "".join(
        f"    uv tool install --python {AGENT_CLI_UV_PYTHON} {AGENT_CLI_EXTRAS[name][0]}; "
        f"{AGENT_CLI_EXTRAS[name][1]}; \\\n"
        for name in names)
    return (_template("egress/Dockerfile.uv-tools")
            .replace("@@UV_IMAGE@@", AGENT_CLI_UV_IMAGE)
            .replace("@@UV_PYTHON@@", AGENT_CLI_UV_PYTHON)
            .replace("@@UV_TOOL_INSTALLS@@\n", installs))


def write_egress_environment(dest: Path, agent_clis: tuple[str, ...] = ()) -> None:
    """Add the allowlist overlay to a copied build context.

    - Dockerfile: the agent-CLI install block is appended (the last stage is the image).
    - docker-compose.yaml: merged by Harbor before its own egress-control overlay; drops
      NET_RAW from `main`, disables unprivileged ICMP in the shared network namespace and
      bind-mounts our gost.yaml (Harbor's plus a connection log) into the sidecar.
    - odyssey-egress/gost.yaml: that config.
    - .dockerignore: the two additions stay out of the image's build context.
    - odyssey-dsh/ (only with `--agent-cli dsh`): package.json and package-lock.json for the
      Dockerfile's `npm ci`; it is part of the build context and is copied into the image.
    """
    dockerfile = dest / "Dockerfile"
    text = dockerfile.read_text()
    if not text.endswith("\n"):
        text += "\n"
    block = egress_dockerfile_block()
    if agent_clis:
        block += agent_cli_extras_block(agent_clis)
    dockerfile.write_text(text + block)
    for name in agent_clis:
        if name in AGENT_CLI_NPM_EXTRAS:  # the lockfile `npm ci` installs from
            npm_dir = dest / AGENT_CLI_NPM_DIR
            npm_dir.mkdir(exist_ok=True)
            for file in ("package.json", "package-lock.json"):
                (npm_dir / file).write_text(_template(f"egress/{AGENT_CLI_NPM_EXTRAS[name][0]}/{file}"))
    (dest / "docker-compose.yaml").write_text(_template("egress/docker-compose.yaml"))
    (dest / EGRESS_DIR).mkdir()
    (dest / EGRESS_DIR / "gost.yaml").write_text(_template("egress/gost.yaml"))
    ignore = dest / ".dockerignore"
    existing = ignore.read_text() if ignore.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    ignore.write_text(existing + "docker-compose.yaml\n" + EGRESS_DIR + "\n")


def _write_step(
    seq: Sequence, ordering: Ordering, arm: ArmOptions, step: dict, step_dir: Path, base_manifest: str | None,
) -> None:
    ticket = seq.ticket(step["ticket"])
    position, total = step["position"], len(ordering.tickets)

    step_dir.mkdir(parents=True)
    (step_dir / "instruction.md").write_text(instruction(seq, arm, ticket, position, total))

    # The same start state goes to setup.sh (before the agent) and to test.sh
    # (after it), so the grader never has to trust what setup.sh left behind.
    state_patch = ""
    if arm.code_state in RESET_POLICIES:
        state_patch = _concat_patches([seq.ticket(t).gold_patch for t in step["state_tickets"]])

    workdir = step_dir / "workdir"
    payload = workdir / PAYLOAD_DIR
    payload.mkdir(parents=True)
    if arm.code_state in RESET_POLICIES:
        (payload / "state.patch").write_text(state_patch)
    if base_manifest is not None:
        (payload / "base.manifest").write_text(base_manifest)
    if position == 1 and arm.seed_notes is not None:
        shutil.copyfile(arm.seed_notes, payload / "NOTES.md")
    _write_script(workdir / "setup.sh", "setup.sh", {
        "ODYSSEY_STEP": step["name"],
        "ODYSSEY_CODE_STATE": arm.code_state,
        "ODYSSEY_STATE_TICKETS": " ".join(step["state_tickets"]),
        "ODYSSEY_WORKDIR": seq.workdir,
        "ODYSSEY_MEMORY_DIR": seq.memory_dir,
        "ODYSSEY_MEMORY_FILES": "\n".join(seq.repo_memory_files),
    })

    tests = step_dir / "tests"
    ticket_payload = tests / "ticket"
    shutil.copytree(ticket.tests_dir, ticket_payload, symlinks=True)
    start = tests / "start"
    start.mkdir()
    (start / "state.patch").write_text(state_patch)
    if base_manifest is not None:
        (start / "base.manifest").write_text(base_manifest)
    _write_script(tests / "test.sh", "test.sh", {
        "ODYSSEY_STEP": step["name"],
        "ODYSSEY_POSITION": position,
        "ODYSSEY_TICKET": ticket.id,
        "ODYSSEY_CODE_STATE": arm.code_state,
        "ODYSSEY_FEEDBACK": arm.feedback,
        "ODYSSEY_WORKDIR": seq.workdir,
        "ODYSSEY_MEMORY_FILES": "\n".join(seq.repo_memory_files),
        "ODYSSEY_PROTECTED_PATHS": "\n".join(seq.grading.protected_paths),
        "ODYSSEY_PROTECTED_NAMES": "\n".join(seq.grading.protected_names),
        "ODYSSEY_RUN_TIMEOUT_SEC": max(1, int(seq.verifier_timeout_sec * _RUN_TIMEOUT_SHARE)),
    })

    solution = step_dir / "solution"
    solution.mkdir()
    shutil.copyfile(ticket.gold_patch, solution / "gold.patch")
    _write_script(solution / "solve.sh", "solve.sh", {"ODYSSEY_WORKDIR": seq.workdir})


def instruction(seq: Sequence, arm: ArmOptions, ticket: Ticket, position: int, total: int) -> str:
    """Framing header (from templates/framing.toml) followed by the ticket text verbatim.

    A one-ticket ordering (a cold run) gets no sequence header even when
    informed: there is no series to describe.
    """
    framing = tomllib.loads(_template("framing.toml"))
    parts = []
    if arm.framing == "informed" and total > 1:
        memory_files = ", ".join(seq.repo_memory_files)
        memory_key = "with_files" if memory_files else "dir_only"
        memory = framing["memory"][memory_key].format(memory_dir=seq.memory_dir, memory_files=memory_files)
        parts.append(framing["informed"].format(
            position=position,
            total=total,
            tree_policy=framing["tree_policy"][arm.code_state],
            memory=memory,
        ).strip())
    if arm.feedback == "binary" and position > 1:
        parts.append(framing["pointers"]["feedback"].format(feedback_dir=FEEDBACK_DIR))
    if arm.seed_notes is not None:
        parts.append(framing["pointers"]["notes"].format(notes_path=f"{seq.memory_dir}/NOTES.md"))

    text = ticket.ticket_md.read_text()
    if not text.endswith("\n"):
        text += "\n"
    if not parts:
        return text
    return "\n".join(parts) + "\n\n---\n\n" + text


def base_manifest_text(seq: Sequence) -> str | None:
    """The base snapshot's hash manifest: hashed from base_tree, or the checked-in base_manifest.

    None when the sequence declares neither; the step scripts then cannot tell
    a doctored base tarball from the real one (they log that and go on).
    """
    env = seq.environment
    if env.base_tree is not None:
        return tree_manifest(env.base_tree)
    if env.base_manifest is not None:
        return env.base_manifest.read_text()
    return None


def tree_manifest(tree: Path) -> str:
    """Hash manifest of a base tree, byte-identical to `tree_manifest` in the step scripts.

    One "<sha256>  ./path" line per regular file, then one "link <target>  ./path"
    line per symlink, each block sorted bytewise; names in _MANIFEST_IGNORED are
    skipped wherever they occur.
    """
    tree = Path(tree)
    files: list[str] = []
    links: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(tree):
        subdirs = []
        for name in dirnames + filenames:
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in _MANIFEST_IGNORED):
                continue
            path = Path(dirpath) / name
            rel = "./" + path.relative_to(tree).as_posix()
            if path.is_symlink():
                links.append((rel, os.readlink(path)))
            elif path.is_dir():
                subdirs.append(name)
            elif path.is_file():
                files.append(rel)
        dirnames[:] = subdirs
    lines = [f"{hashlib.sha256((tree / rel).read_bytes()).hexdigest()}  {rel}"
             for rel in sorted(files, key=lambda r: r.encode())]
    lines += [f"link {target}  {rel}" for rel, target in sorted(links, key=lambda x: x[0].encode())]
    return "".join(line + "\n" for line in lines)


def _concat_patches(paths: list[Path]) -> str:
    chunks = []
    for path in paths:
        text = path.read_text()
        chunks.append(text if text.endswith("\n") else text + "\n")
    return "".join(chunks)


def _template(name: str) -> str:
    return resources.files("odyssey").joinpath("templates", name).read_text()


_INCLUDE = re.compile(r"^@@INCLUDE ([A-Za-z0-9_.]+)@@$", re.MULTILINE)


def shell_template(name: str) -> str:
    """A shell template with its `@@INCLUDE <file>@@` lines replaced by that template's text.

    Shared helpers (process sweep, base manifest) live in one file each, so the
    step scripts and `odyssey validate` cannot drift apart.
    """
    return _INCLUDE.sub(lambda m: _template(m.group(1)).rstrip("\n"), _template(name))


def _write_script(dest: Path, template: str, config: dict) -> None:
    """Fill a shell template's @@CONFIG@@ block with shell-quoted assignments."""
    block = "\n".join(f"{key}={shlex.quote(str(value))}" for key, value in config.items())
    text = shell_template(template)
    if text.count("@@CONFIG@@") != 1:
        raise RuntimeError(f"template {template} must contain exactly one @@CONFIG@@ marker")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text.replace("@@CONFIG@@", block))
    dest.chmod(0o755)


def harbor_commands(task_dir: Path) -> list[str]:
    """Suggested commands: an oracle smoke run, then a real agent run."""
    path = shlex.quote(str(task_dir))
    return [
        f"harbor run -p {path} -a oracle",
        f"harbor run -p {path} -a claude-code -m anthropic/<model> [--resume-trajectory]",
    ]
