#!/usr/bin/env python3
"""Compile atlas-commerce orderings, and single-ticket validation plans, into Harbor 0.22 multi-step tasks.

    # an ordering (or a design) x one arm -> one task dir per ordering, like `odyssey compile`
    python3 scripts/atlas/compile_atlas.py compile sequences/atlas-commerce --out build/atlas \\
        --ordering ATL-2,ATL-4 [--framing informed|natural] [--offline] [--extra-tickets DIR]
    python3 scripts/atlas/compile_atlas.py compile sequences/atlas-commerce --out build/atlas --design williams

    # one ticket's validation plan as ONE task whose steps are the conditions (run with -a oracle)
    python3 scripts/atlas/compile_atlas.py validate-plan sequences/atlas-commerce ATL-2 --out build/atlas \\
        [--subset-sample K] [--seed S] [--repeat-reference N] [--offline] [--extra-tickets DIR]

    # static checks of every ticket directory (no Docker)
    python3 scripts/atlas/compile_atlas.py check sequences/atlas-commerce [--extra-tickets DIR]

Why a generator and not `odyssey compile`: an atlas step's setup asks a trusted controller to
reset LocalStack, rebuilds /app and redeploys the platform from it, and its verifier needs a
grader-only AWS key, a compose environment with five services and per-ticket timeouts. Those are
different step scripts, not options of the existing ones, so they live here; everything that can
be shared is imported from odyssey unchanged (sequence loading, orderings and their ids, step
names, the manifest, the base-tree manifest, the process sweep and manifest shell helpers). The
output has the same layout and odyssey.json manifest as `odyssey compile`, so `odyssey account`
reads atlas trials unchanged.

Code state "forward-merge-live" (arm prefix `fml`): segment k starts from the base snapshot plus
the reference changes of the tickets at positions 1..k-1, deployed live; nothing of a later
ticket is ever in the container.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import itertools
import json
import random
import re
import secrets
import shlex
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from odyssey import compile as ocompile  # noqa: E402
from odyssey import design as odesign  # noqa: E402
from odyssey import spec as ospec  # noqa: E402
from odyssey.spec import Sequence, Ticket  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
GRADER_LIB = HERE / "grader_lib"
ODYSSEY_TEMPLATES = ROOT / "odyssey" / "templates"

CODE_STATE = "forward-merge-live"
CODE_STATE_SHORT = "fml"
FRAMING_SHORT = {"informed": "inf", "natural": "nat"}
RUN_TIMEOUT_SHARE = 0.8
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
COMPONENT = re.compile(r"^[a-z0-9_]+$")

INFORMED_HEADER = """\
You will work through {total} tickets on the atlas-commerce platform, one at a time. This is ticket {position} of {total}.
Between tickets the maintainers merge the reference change for every earlier ticket and redeploy the platform from the repository: the AWS environment is reset and rebuilt, the working tree is reset to that state, and your other changes, deployments and running processes are discarded.
Anything you write under {memory_dir}/ or to the repository files {memory_files} is kept across tickets.
"""


@dataclasses.dataclass(frozen=True)
class AtlasTicket:
    ticket: Ticket
    config: dict
    directory: Path

    @property
    def id(self) -> str:
        return self.ticket.id

    @property
    def agent_timeout(self) -> float:
        return float(self.config["ticket"]["agent_timeout_sec"])

    @property
    def verifier_timeout(self) -> float:
        return float(self.config["ticket"]["verifier_timeout_sec"])

    @property
    def deploy(self) -> list[str]:
        return list(self.config.get("reference", {}).get("deploy", []))

    @property
    def grading(self) -> dict:
        grading = self.config.get("grading", {})
        return {"fail_to_pass": list(grading.get("fail_to_pass", [])),
                "pass_to_pass": list(grading.get("pass_to_pass", []))}

    @property
    def known_bad(self) -> list[dict]:
        return list(self.config.get("known_bad", []))


@dataclasses.dataclass(frozen=True)
class Atlas:
    seq: Sequence
    tickets: dict[str, AtlasTicket]


# --- loading and static checks ------------------------------------------------------------------

def load(seq_dir: Path, extra_ticket_dirs: list[Path] = ()) -> Atlas:
    seq = ospec.load_sequence(seq_dir)
    tickets: dict[str, AtlasTicket] = {}
    for ticket in seq.tickets:
        directory = ticket.ticket_md.parent
        tickets[ticket.id] = AtlasTicket(ticket, _toml(directory / "ticket.toml"), directory)
    extras = []
    for root in extra_ticket_dirs:
        for directory in sorted(Path(root).resolve().iterdir()):
            if not (directory / "ticket.toml").is_file():
                continue
            config = _toml(directory / "ticket.toml")
            meta = config.get("ticket", {})
            ticket = Ticket(id=directory.name, title=str(meta.get("title", "")),
                            cluster=str(meta.get("cluster", directory.name)), role=str(meta.get("role", "core")),
                            ticket_md=directory / "ticket.md", gold_patch=directory / "gold.patch",
                            tests_dir=directory / "tests")
            if ticket.id in tickets:
                raise SystemExit(f"extra ticket {ticket.id} clashes with a sequence ticket")
            extras.append(ticket)
            tickets[ticket.id] = AtlasTicket(ticket, config, directory)
    if extras:
        seq = dataclasses.replace(seq, tickets=seq.tickets + tuple(extras))
    atlas = Atlas(seq, tickets)
    problems = check(atlas)
    if problems:
        raise SystemExit("atlas ticket problems:\n" + "\n".join(f"  - {p}" for p in problems))
    return atlas


def _toml(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    return tomllib.loads(path.read_text())


def base_components(seq: Sequence) -> set[str]:
    return {p.stem for p in (seq.environment.base_tree / "deploy" / "components").glob("*.py")
            if not p.name.startswith("_")}


def patch_components(patch: Path) -> set[str]:
    return set(re.findall(r"^\+\+\+ b/deploy/components/([a-z0-9_]+)\.py$", patch.read_text(), re.M))


def patch_files(patch: Path) -> set[str]:
    return set(re.findall(r"^\+\+\+ b/(\S+)$", patch.read_text(), re.M)) | \
        set(re.findall(r"^--- a/(\S+)$", patch.read_text(), re.M))


def check(atlas: Atlas) -> list[str]:
    problems: list[str] = []
    known = base_components(atlas.seq)
    for tid, at in atlas.tickets.items():
        where = f"ticket {tid}"
        cfg = at.config
        # sequence.toml is the one place for title, cluster and role (odyssey.spec, designs and
        # accounting read them there); a ticket.toml may repeat them (needed with --extra-tickets),
        # but then they must agree, so a label cannot drift between the two files.
        for key in ("title", "cluster", "role"):
            value = cfg.get("ticket", {}).get(key)
            if value is not None and value != getattr(at.ticket, key):
                problems.append(f"{where}: ticket.toml [ticket].{key} = {value!r} disagrees with sequence.toml "
                                f"({getattr(at.ticket, key)!r})")
        for key in ("agent_timeout_sec", "verifier_timeout_sec"):
            value = cfg.get("ticket", {}).get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                problems.append(f"{where}: [ticket].{key} must be a positive number")
        if not at.ticket.ticket_md.is_file() or not at.ticket.ticket_md.read_text().strip():
            problems.append(f"{where}: ticket.md missing or empty")
        if not at.ticket.gold_patch.is_file() or not at.ticket.gold_patch.read_text().strip():
            problems.append(f"{where}: gold.patch missing or empty")
            continue
        run_sh = at.ticket.tests_dir / "run.sh"
        if not run_sh.is_file():
            problems.append(f"{where}: tests/run.sh missing")
        elif not run_sh.stat().st_mode & 0o111:
            problems.append(f"{where}: tests/run.sh is not executable")
        available = known | patch_components(at.ticket.gold_patch)
        for component in at.deploy:
            if not COMPONENT.match(component) or component not in available:
                problems.append(f"{where}: [reference].deploy names {component!r}, which is neither a base "
                                f"component nor added by gold.patch")
        grading = at.grading
        names = grading["fail_to_pass"] + grading["pass_to_pass"]
        if not grading["fail_to_pass"]:
            problems.append(f"{where}: [grading].fail_to_pass is empty")
        if len(set(names)) != len(names):
            problems.append(f"{where}: a test is listed twice in [grading]")
        seen = set()
        for bad in at.known_bad:
            name = bad.get("name", "")
            if not NAME.match(name) or name in seen:
                problems.append(f"{where}: known_bad name {name!r} must be unique and match {NAME.pattern}")
            seen.add(name)
            patch = at.directory / str(bad.get("patch", ""))
            if not patch.is_file() or not patch.read_text().strip():
                problems.append(f"{where}: known_bad {name}: patch {patch} missing or empty")
            for test in bad.get("must_fail", []):
                if test not in names:
                    problems.append(f"{where}: known_bad {name}: must_fail {test!r} is not in [grading]")
            for component in bad.get("deploy", at.deploy):
                if component not in known | (patch_components(patch) if patch.is_file() else set()):
                    problems.append(f"{where}: known_bad {name}: deploys unknown component {component!r}")
    # Disjoint references: every pair of gold patches must touch different files, so all of
    # them apply together in any order (SPEC section 3).
    for a, b in itertools.combinations(sorted(atlas.tickets), 2):
        pa, pb = atlas.tickets[a].ticket.gold_patch, atlas.tickets[b].ticket.gold_patch
        if pa.is_file() and pb.is_file():
            shared = (patch_files(pa) & patch_files(pb)) - {"/dev/null"}
            if shared:
                problems.append(f"gold patches of {a} and {b} both touch {sorted(shared)}")
    return problems


# --- plans --------------------------------------------------------------------------------------

def ordering_plan(atlas: Atlas, ordering: odesign.Ordering) -> list[dict]:
    total = len(ordering.tickets)
    steps = []
    for position, tid in enumerate(ordering.tickets, start=1):
        steps.append({"name": ocompile.step_name(position, tid, total), "position": position, "ticket": tid,
                      "state_tickets": list(ordering.tickets[:position - 1]), "action": "reference"})
    return steps


def subset_sample(others: list[str], k: int | None, seed: int) -> list[tuple[str, ...]]:
    """Every subset of the other tickets, or a stated sample of K (SPEC section 3 order: singles,
    all-but-one, all, then seeded random fill)."""
    full = [tuple(c) for r in range(1, len(others) + 1) for c in itertools.combinations(others, r)]
    if k is None or k >= len(full):
        return full
    chosen: list[tuple[str, ...]] = []
    for group in ([(o,) for o in others], [tuple(x for x in others if x != o) for o in others], [tuple(others)]):
        for subset in group:
            if subset and subset not in chosen and len(chosen) < k:
                chosen.append(subset)
    rest = [s for s in full if s not in chosen]
    random.Random(seed).shuffle(rest)
    chosen += rest[:max(0, k - len(chosen))]
    return chosen


def validation_plan(atlas: Atlas, tid: str, *, subset_k: int | None, seed: int, repeat_reference: int,
                    others: list[str] | None = None) -> list[dict]:
    """The four-way contract, known-bads and independence for one ticket, as one step list."""
    at = atlas.tickets[tid]
    others = [t for t in (others if others is not None else atlas.tickets) if t != tid]
    conditions: list[tuple[list[str], str, str]] = [([], "nothing", "nop at the base")]
    conditions += [([], "reference", "reference at the base")] * max(1, repeat_reference)
    conditions += [([], f"known-bad:{bad['name']}", f"known-bad {bad['name']} at the base") for bad in at.known_bad]
    for subset in subset_sample(others, subset_k, seed):
        label = "+".join(subset)
        conditions.append((list(subset), "nothing", f"nop with {label}"))
        conditions.append((list(subset), "reference", f"reference with {label}"))
    total = len(conditions)
    steps = []
    for position, (state, action, label) in enumerate(conditions, start=1):
        expect: dict = {"reward": 1 if action == "reference" else 0}
        if action == "nothing":
            expect["clean_failure"] = True
        if action.startswith("known-bad:"):
            bad = next(b for b in at.known_bad if b["name"] == action.split(":", 1)[1])
            expect["must_fail"] = list(bad.get("must_fail", []))
        steps.append({"name": ocompile.step_name(position, tid, total), "position": position, "ticket": tid,
                      "state_tickets": state, "action": action, "condition": label, "expect": expect})
    return steps


# --- writing ------------------------------------------------------------------------------------

def _include(name: str) -> str:
    for directory in (TEMPLATES, ODYSSEY_TEMPLATES):
        if (directory / name).is_file():
            return (directory / name).read_text()
    raise FileNotFoundError(name)


def shell_template(name: str) -> str:
    return re.sub(r"^@@INCLUDE ([A-Za-z0-9_.]+)@@$", lambda m: _include(m.group(1)).rstrip("\n"),
                  _include(name), flags=re.M)


def write_script(dest: Path, template: str, config: dict) -> None:
    block = "\n".join(f"{key}={shlex.quote(str(value))}" for key, value in config.items())
    text = shell_template(template)
    if text.count("@@CONFIG@@") != 1:
        raise RuntimeError(f"template {template} must contain exactly one @@CONFIG@@")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text.replace("@@CONFIG@@", block))
    dest.chmod(0o755)


def instruction(atlas: Atlas, tid: str, position: int, total: int, framing: str) -> str:
    text = atlas.tickets[tid].ticket.ticket_md.read_text()
    if not text.endswith("\n"):
        text += "\n"
    if framing != "informed" or total < 2:
        return text
    seq = atlas.seq
    header = INFORMED_HEADER.format(total=total, position=position, memory_dir=seq.memory_dir,
                                    memory_files=", ".join(seq.repo_memory_files))
    return header + "\n---\n\n" + text


def change_patch(atlas: Atlas, tid: str, action: str) -> tuple[str, list[str]]:
    at = atlas.tickets[tid]
    if action == "nothing":
        return "", []
    if action == "reference":
        return at.ticket.gold_patch.read_text(), at.deploy
    name = action.split(":", 1)[1]
    bad = next(b for b in at.known_bad if b["name"] == name)
    return (at.directory / bad["patch"]).read_text(), list(bad.get("deploy", at.deploy))


def write_task(atlas: Atlas, steps: list[dict], *, out_root: Path, task_dir_name: str, manifest: dict,
               offline: bool, guard: bool = True) -> Path:
    seq = atlas.seq
    task_dir = Path(out_root) / task_dir_name
    if task_dir.exists():
        if not (task_dir / ocompile.MANIFEST_NAME).is_file():
            raise FileExistsError(f"{task_dir} exists and was not generated by a compiler; not overwriting")
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)
    base_manifest = ocompile.base_manifest_text(seq)

    env_dir = task_dir / "environment"
    shutil.copytree(seq.environment.context, env_dir, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "tokens.json"))
    if offline:
        compose = env_dir / "docker-compose.yaml"
        text = compose.read_text()
        marker = "  agent_net: {}  # atlas:agent_net\n"
        if marker not in text:
            raise RuntimeError("compose file lacks the agent_net marker needed for --offline")
        compose.write_text(text.replace(marker, "  agent_net:  # atlas:agent_net (--offline: no internet)\n"
                                                "    internal: true\n"))
    if not guard:
        # EXPERIMENT ONLY (--unsafe-no-guard): main's PID 1 just sleeps, to show what the guard prevents.
        compose = env_dir / "docker-compose.yaml"
        text = compose.read_text()
        line = '    command: ["python3", "-I", "/opt/atlas/guard.py"]\n'
        if line not in text:
            raise RuntimeError("compose file lacks the guard command line")
        compose.write_text(text.replace(line, '    command: ["sleep", "infinity"]  # --unsafe-no-guard\n'))

    tokens: dict[str, dict] = {}
    total = len(steps)
    for step in steps:
        tid = step["ticket"]
        at = atlas.tickets[tid]
        setup_token, verify_token = secrets.token_hex(32), secrets.token_hex(32)
        tokens[hashlib.sha256(setup_token.encode()).hexdigest()] = {"step": step["name"], "kind": "setup"}
        tokens[hashlib.sha256(verify_token.encode()).hexdigest()] = {"step": step["name"], "kind": "verify"}
        step_dir = task_dir / "steps" / step["name"]
        step_dir.mkdir(parents=True)
        (step_dir / "instruction.md").write_text(
            instruction(atlas, tid, step["position"], total, manifest.get("arm_options", {}).get("framing", "natural")))

        state_patch = ocompile._concat_patches([atlas.tickets[t].ticket.gold_patch for t in step["state_tickets"]])
        payload = step_dir / "workdir" / ocompile.PAYLOAD_DIR
        payload.mkdir(parents=True)
        (payload / "state.patch").write_text(state_patch)
        if base_manifest is not None:
            (payload / "base.manifest").write_text(base_manifest)
        write_script(step_dir / "workdir" / "setup.sh", "setup.sh", {
            "ODYSSEY_STEP": step["name"],
            "ODYSSEY_CODE_STATE": CODE_STATE,
            "ODYSSEY_STATE_TICKETS": " ".join(step["state_tickets"]),
            "ODYSSEY_WORKDIR": seq.workdir,
            "ODYSSEY_MEMORY_DIR": seq.memory_dir,
            "ODYSSEY_MEMORY_FILES": "\n".join(seq.repo_memory_files),
            "ATLAS_SETUP_TOKEN": setup_token,
        })

        tests = step_dir / "tests"
        shutil.copytree(at.ticket.tests_dir, tests / "ticket", symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        shutil.copytree(GRADER_LIB, tests / "atlas", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (tests / "start").mkdir()
        (tests / "start" / "state.patch").write_text(state_patch)
        if base_manifest is not None:
            (tests / "start" / "base.manifest").write_text(base_manifest)
        (tests / "grading.json").write_text(json.dumps(at.grading, indent=1) + "\n")
        write_script(tests / "test.sh", "test.sh", {
            "ODYSSEY_STEP": step["name"],
            "ODYSSEY_POSITION": step["position"],
            "ODYSSEY_TICKET": tid,
            "ODYSSEY_CODE_STATE": CODE_STATE,
            "ODYSSEY_WORKDIR": seq.workdir,
            "ODYSSEY_MEMORY_FILES": "\n".join(seq.repo_memory_files),
            "ODYSSEY_PROTECTED_PATHS": "\n".join(seq.grading.protected_paths),
            "ODYSSEY_PROTECTED_NAMES": "\n".join(seq.grading.protected_names),
            "ODYSSEY_RUN_TIMEOUT_SEC": max(1, int(at.verifier_timeout * RUN_TIMEOUT_SHARE)),
            "ATLAS_VERIFY_TOKEN": verify_token,
        })

        patch_text, deploy = change_patch(atlas, tid, step["action"])
        solution = step_dir / "solution"
        solution.mkdir()
        (solution / "change.patch").write_text(patch_text)
        write_script(solution / "solve.sh", "solve.sh", {
            "ATLAS_WORKDIR": seq.workdir, "ATLAS_ACTION": step["action"], "ATLAS_DEPLOY": " ".join(deploy)})

    (env_dir / "controller").mkdir(exist_ok=True)
    (env_dir / "controller" / "tokens.json").write_text(json.dumps({"version": 1, "tokens": tokens}, indent=1) + "\n")
    (task_dir / ocompile.MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (task_dir / "task.toml").write_text(task_toml(atlas, steps, manifest))
    return task_dir


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def task_toml(atlas: Atlas, steps: list[dict], manifest: dict) -> str:
    seq, env = atlas.seq, atlas.seq.environment
    description = f"SWE-Odyssey {seq.id}, arm {manifest['arm']}, ordering {manifest['ordering_id']}"
    lines = [
        "# Generated by scripts/atlas/compile_atlas.py; edit the sequence and recompile instead.",
        'schema_version = "1.4"',
        'multi_step_reward_strategy = "mean"',
        "",
        "[task]",
        f"name = {_q(manifest['task_name'])}",
        f"description = {_q(description)}",
        f"keywords = {json.dumps(['swe-odyssey', seq.id, manifest['arm']])}",
        "",
        "[metadata]",
        f"odyssey_sequence = {_q(seq.id)}",
        f"odyssey_arm = {_q(manifest['arm'])}",
        f"odyssey_ordering_id = {_q(manifest['ordering_id'])}",
        f"odyssey_ordering = {json.dumps(manifest['ordering'])}",
        f"odyssey_manifest = {_q(json.dumps(manifest, sort_keys=True))}",
        "",
        "[agent]",
        f"timeout_sec = {seq.agent_timeout_sec!r}",
        "",
        "[verifier]",
        f"timeout_sec = {seq.verifier_timeout_sec!r}",
        "",
        "[environment]",
        f"workdir = {_q(seq.workdir)}",
        f"network_mode = {_q('public' if env.allow_internet else 'no-network')}",
    ]
    if env.build_timeout_sec is not None:
        lines.append(f"build_timeout_sec = {env.build_timeout_sec!r}")
    for key in ("cpus", "memory_mb", "storage_mb"):
        value = getattr(env, key)
        if value is not None:
            lines.append(f"{key} = {value}")
    for step in steps:
        at = atlas.tickets[step["ticket"]]
        # Harbor 0.22 multi_step.py: a step's [steps.agent] timeout_sec overrides [agent], and its
        # [steps.verifier] timeout_sec (default 600) is always used for the step's verifier.
        lines += ["", "[[steps]]", f"name = {_q(step['name'])}", "",
                  "[steps.agent]", f"timeout_sec = {at.agent_timeout!r}", "",
                  "[steps.verifier]", f"timeout_sec = {at.verifier_timeout!r}"]
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)
    return text


def ordering_manifest(atlas: Atlas, ordering: odesign.Ordering, framing: str, offline: bool) -> dict:
    arm = ocompile.ArmOptions(code_state="forward-merge", framing=framing, feedback="none")
    manifest = ocompile.build_manifest(atlas.seq, ordering, arm)
    arm_id = arm_label(framing, offline)
    manifest["arm"] = arm_id
    manifest["arm_options"]["code_state"] = CODE_STATE
    if offline:
        manifest["arm_options"]["network"] = "offline"
    manifest["task_name"] = f"odyssey/{atlas.seq.id}__{arm_id}__{ordering.id}"
    manifest["generator"] = "scripts/atlas/compile_atlas.py"
    return manifest


def arm_label(framing: str, offline: bool) -> str:
    return f"{CODE_STATE_SHORT}-{FRAMING_SHORT[framing]}-nofb" + ("-offline" if offline else "")


def compile_ordering(atlas: Atlas, ordering: odesign.Ordering, out_root: Path, *, framing: str = "informed",
                     offline: bool = False, guard: bool = True) -> Path:
    if len(set(ordering.tickets)) != len(ordering.tickets):
        raise SystemExit(f"ordering {ordering.id} repeats a ticket")
    for tid in ordering.tickets:
        if tid not in atlas.tickets:
            raise SystemExit(f"unknown ticket {tid}; known: {', '.join(atlas.tickets)}")
    manifest = ordering_manifest(atlas, ordering, framing, offline)
    if not guard:
        manifest["arm"] += "-noguard"
        manifest["arm_options"]["guard"] = "disabled (experiment)"
        manifest["task_name"] = f"odyssey/{atlas.seq.id}__{manifest['arm']}__{ordering.id}"
    steps = ordering_plan(atlas, ordering)
    name = f"{atlas.seq.id}__{manifest['arm']}__{ordering.id}"
    return write_task(atlas, steps, out_root=out_root, task_dir_name=name, manifest=manifest, offline=offline,
                      guard=guard)


def compile_validation(atlas: Atlas, tid: str, out_root: Path, *, subset_k: int | None, seed: int,
                       repeat_reference: int, offline: bool, others: list[str] | None = None) -> Path:
    steps = validation_plan(atlas, tid, subset_k=subset_k, seed=seed, repeat_reference=repeat_reference,
                            others=others)
    digest = hashlib.sha1(json.dumps([(s["state_tickets"], s["action"]) for s in steps]).encode()).hexdigest()[:6]
    ordering_id = f"v-{tid.lower()}-{digest}"
    arm_id = "validate" + ("-offline" if offline else "")
    at = atlas.tickets[tid]
    manifest = {
        "odyssey_manifest_version": 1,
        "sequence": atlas.seq.id,
        "task_name": f"odyssey/{atlas.seq.id}__{arm_id}__{ordering_id}",
        "arm": arm_id,
        "arm_options": {"code_state": CODE_STATE, "framing": "natural", "purpose": "validation"},
        "ordering_id": ordering_id,
        "design": "validation",
        "ordering": [s["ticket"] for s in steps],
        "workdir": atlas.seq.workdir,
        "memory_dir": atlas.seq.memory_dir,
        "repo_memory_files": list(atlas.seq.repo_memory_files),
        "generator": "scripts/atlas/compile_atlas.py validate-plan",
        "validation": {"ticket": tid, "grading": at.grading, "subset_sample": subset_k, "seed": seed},
        "steps": [{**s, "title": at.ticket.title, "cluster": at.ticket.cluster, "role": at.ticket.role}
                  for s in steps],
    }
    name = f"{atlas.seq.id}__{arm_id}__{ordering_id}"
    return write_task(atlas, steps, out_root=out_root, task_dir_name=name, manifest=manifest, offline=offline)


# --- CLI ----------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("sequence", type=Path)
        p.add_argument("--extra-tickets", type=Path, action="append", default=[],
                       help="directory of extra ticket dirs (each with ticket.toml), e.g. testing/tickets")

    c = sub.add_parser("compile", help="ordering(s) x one arm -> task dirs")
    common(c)
    c.add_argument("--out", type=Path, required=True)
    group = c.add_mutually_exclusive_group(required=True)
    group.add_argument("--ordering", help="comma-separated ticket ids")
    group.add_argument("--design", choices=odesign.DESIGNS)
    c.add_argument("--n", type=int)
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--framing", choices=sorted(FRAMING_SHORT), default="informed")
    c.add_argument("--offline", action="store_true",
                   help="agent_net internal (no internet for main): proves setup and graders need none")
    c.add_argument("--unsafe-no-guard", action="store_true",
                   help="EXPERIMENT ONLY: main runs without the PID-1 guard (shows the forgery it prevents)")

    v = sub.add_parser("validate-plan", help="one ticket's validation conditions as one task")
    common(v)
    v.add_argument("ticket")
    v.add_argument("--out", type=Path, required=True)
    v.add_argument("--subset-sample", type=int, default=None, help="K subsets of the other tickets (default: all)")
    v.add_argument("--seed", type=int, default=0)
    v.add_argument("--repeat-reference", type=int, default=1)
    v.add_argument("--others", help="comma-separated other tickets to draw subsets from (default: all)")
    v.add_argument("--offline", action="store_true")

    k = sub.add_parser("check", help="static checks of the ticket directories")
    common(k)

    args = parser.parse_args(argv)
    atlas = load(args.sequence, args.extra_tickets)
    if args.cmd == "check":
        print(f"{len(atlas.tickets)} ticket(s) OK: {', '.join(atlas.tickets)}")
        return 0
    if args.cmd == "validate-plan":
        others = [t for t in args.others.split(",") if t] if args.others else None
        path = compile_validation(atlas, args.ticket, args.out, subset_k=args.subset_sample, seed=args.seed,
                                  repeat_reference=args.repeat_reference, offline=args.offline, others=others)
        print(path)
        return 0
    if args.ordering:
        orderings = [odesign.explicit([t.strip() for t in args.ordering.split(",") if t.strip()])]
    else:
        orderings = odesign.generate(args.design, list(atlas.tickets), n=args.n, seed=args.seed)
    for ordering in orderings:
        print(compile_ordering(atlas, ordering, args.out, framing=args.framing, offline=args.offline,
                               guard=not args.unsafe_no_guard))
    return 0


if __name__ == "__main__":
    sys.exit(main())
