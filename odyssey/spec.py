"""Load and validate a sequence source directory (SPEC section 3).

Every problem found is collected and reported at once, so an author fixing a
new sequence sees the whole list instead of one error per run.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1

# Ids end up in directory names, step names and Harbor's "org/name" task name,
# so they are restricted to the characters Harbor accepts there.
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_SEQUENCE_KEYS = {
    "id", "description", "workdir", "memory_dir", "repo_memory_files",
    "agent_timeout_sec", "verifier_timeout_sec",
}
_ENVIRONMENT_KEYS = {
    "context", "docker_image", "base_tree", "base_manifest", "cpus", "memory_mb", "storage_mb",
    "allow_internet", "build_timeout_sec",
}
_GRADING_KEYS = {"protected_paths", "protected_names"}
_TICKET_KEYS = {"id", "title", "cluster", "role", "ticket", "gold_patch", "tests"}
_TOP_KEYS = {"schema", "sequence", "environment", "grading", "tickets"}

# Files Python and pytest load from wherever they find them, so an agent could
# use them to change how the grader runs: removed from every grading copy
# unless the segment's trusted start tree has them (then that copy is used).
DEFAULT_PROTECTED_NAMES = (
    "conftest.py", "pytest.py", "_pytest", "sitecustomize.py", "usercustomize.py", "*.pth",
)
_UNSAFE_WORKDIRS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/opt", "/proc", "/root",
    "/sbin", "/sys", "/tmp", "/usr", "/var", "/logs", "/tests", "/solution",
}


class SpecError(ValueError):
    """A sequence directory violates the source layout; the message lists every problem."""

    def __init__(self, problems: list[str], root: Path | None = None):
        self.problems = problems
        where = f" in {root}" if root else ""
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"invalid sequence{where}:\n{lines}")


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    cluster: str
    role: str
    ticket_md: Path
    gold_patch: Path
    tests_dir: Path

    @property
    def run_sh(self) -> Path:
        return self.tests_dir / "run.sh"

    @property
    def files_dir(self) -> Path:
        """Hidden test files, repo-relative; may not exist for tickets that need none."""
        return self.tests_dir / "files"


@dataclass(frozen=True)
class EnvironmentSpec:
    context: Path | None
    docker_image: str | None
    base_tree: Path | None
    cpus: int | None
    memory_mb: int | None
    storage_mb: int | None
    allow_internet: bool
    build_timeout_sec: float | None
    # A checked-in hash manifest of the image's base snapshot, for sequences
    # whose tree lives only in the image (a real repo built by the Dockerfile).
    # Exclusive with base_tree; `odyssey base-manifest` writes it.
    base_manifest: Path | None = None


@dataclass(frozen=True)
class GradingSpec:
    """What test.sh takes from the trusted start tree instead of the agent's tree.

    protected_paths: repo-relative files or directories (the visible tests, the
        test-runner config). In the grading copy each is replaced by the start
        tree's version, or removed if the start tree has none, so the agent can
        neither weaken nor delete the tests it is graded with.
    protected_names: basename globs matched anywhere in the tree (conftest.py
        and friends). Every match is removed from the grading copy, then the
        start tree's matches are copied back.
    """

    protected_paths: tuple[str, ...] = ()
    protected_names: tuple[str, ...] = DEFAULT_PROTECTED_NAMES


@dataclass(frozen=True)
class Sequence:
    root: Path
    id: str
    description: str
    workdir: str
    memory_dir: str
    repo_memory_files: tuple[str, ...]
    agent_timeout_sec: float
    verifier_timeout_sec: float
    environment: EnvironmentSpec
    tickets: tuple[Ticket, ...]
    notes_path: Path | None
    grading: GradingSpec = GradingSpec()

    @property
    def ticket_ids(self) -> list[str]:
        return [t.id for t in self.tickets]

    def ticket(self, ticket_id: str) -> Ticket:
        for t in self.tickets:
            if t.id == ticket_id:
                return t
        raise KeyError(f"unknown ticket id {ticket_id!r}; known: {', '.join(self.ticket_ids)}")


def load_sequence(path: Path | str, *, require_base_manifest: bool = True) -> Sequence:
    """Parse `<path>/sequence.toml` and check that the files it refers to exist.

    require_base_manifest=False lets `odyssey base-manifest` load a sequence
    whose [environment].base_manifest it is about to write.
    """
    root = Path(path).resolve()
    toml_path = root / "sequence.toml"
    if not root.is_dir():
        raise SpecError([f"sequence directory does not exist: {root}"])
    if not toml_path.is_file():
        raise SpecError([f"missing sequence.toml in {root}"])
    try:
        data = tomllib.loads(toml_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise SpecError([f"sequence.toml is not valid TOML: {exc}"], root) from exc

    problems: list[str] = []
    _check_unknown(data, _TOP_KEYS, "top level", problems)
    if data.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema must be {SCHEMA_VERSION}, got {data.get('schema')!r}")

    seq = _table(data, "sequence", problems)
    env = _table(data, "environment", problems)
    _check_unknown(seq, _SEQUENCE_KEYS, "[sequence]", problems)
    _check_unknown(env, _ENVIRONMENT_KEYS, "[environment]", problems)

    seq_id = _string(seq, "id", "[sequence]", problems)
    if seq_id and not ID_PATTERN.match(seq_id):
        problems.append(f"[sequence].id {seq_id!r} must match {ID_PATTERN.pattern}")
    workdir = _abs_path(seq, "workdir", problems)
    if workdir in _UNSAFE_WORKDIRS:
        # setup.sh empties the workdir on every reset.
        problems.append(f"[sequence].workdir {workdir!r} is a system directory; use a dedicated repo dir such as /app")
    memory_dir = _abs_path(seq, "memory_dir", problems)
    if workdir and memory_dir and _is_within(memory_dir, workdir):
        problems.append(f"[sequence].memory_dir {memory_dir} must be outside workdir {workdir}")
    memory_files = _memory_files(seq, problems)

    agent_timeout = _positive_number(seq, "agent_timeout_sec", 1800, problems)
    verifier_timeout = _positive_number(seq, "verifier_timeout_sec", 600, problems)
    environment = _environment(env, root, problems, require_base_manifest)
    grading = _grading(data.get("grading", {}), problems)
    tickets = _tickets(data.get("tickets"), root, problems)

    notes = root / "notes" / "ORACLE_NOTES.md"
    if problems:
        raise SpecError(problems, root)
    return Sequence(
        root=root,
        id=seq_id,
        description=str(seq.get("description", "")),
        workdir=workdir,
        memory_dir=memory_dir,
        repo_memory_files=memory_files,
        agent_timeout_sec=agent_timeout,
        verifier_timeout_sec=verifier_timeout,
        environment=environment,
        tickets=tickets,
        notes_path=notes if notes.is_file() else None,
        grading=grading,
    )


# --- field helpers -----------------------------------------------------------


def _check_unknown(table: dict, allowed: set[str], where: str, problems: list[str]) -> None:
    """Unknown keys are almost always typos, which would otherwise silently take defaults."""
    for key in sorted(set(table) - allowed):
        problems.append(f"unknown key {key!r} in {where}")


def _table(data: dict, key: str, problems: list[str]) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        problems.append(f"missing [{key}] table")
        return {}
    return value


def _string(table: dict, key: str, where: str, problems: list[str]) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        problems.append(f"{where}.{key} must be a non-empty string")
        return ""
    return value


def _abs_path(table: dict, key: str, problems: list[str]) -> str:
    value = _string(table, key, "[sequence]", problems)
    if value and (not value.startswith("/") or ".." in PurePosixPath(value).parts):
        problems.append(f"[sequence].{key} must be an absolute container path, got {value!r}")
        return ""
    if not value:
        return ""
    return value.rstrip("/") or "/"


def _is_within(path: str, parent: str) -> bool:
    return PurePosixPath(path) == PurePosixPath(parent) or PurePosixPath(parent) in PurePosixPath(path).parents


def _memory_files(seq: dict, problems: list[str]) -> tuple[str, ...]:
    files = seq.get("repo_memory_files", [])
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        problems.append("[sequence].repo_memory_files must be a list of strings")
        return ()
    for f in files:
        parts = PurePosixPath(f).parts
        if not f or f.startswith("/") or ".." in parts or "\n" in f or parts[:1] == (".git",):
            problems.append(f"repo_memory_files entry {f!r} must be a repo-relative path outside .git")
    if len(set(files)) != len(files):
        problems.append("repo_memory_files contains duplicates")
    return tuple(files)


def _positive_number(table: dict, key: str, default: float, problems: list[str]) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        problems.append(f"{key} must be a positive number, got {value!r}")
        return float(default)
    return float(value)


def _optional_positive_int(env: dict, key: str, problems: list[str]) -> int | None:
    value = env.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        problems.append(f"[environment].{key} must be a positive integer, got {value!r}")
        return None
    return value


def _environment(env: dict, root: Path, problems: list[str], require_base_manifest: bool = True) -> EnvironmentSpec:
    context, image = env.get("context"), env.get("docker_image")
    context_path = None
    if (context is None) == (image is None):
        problems.append("[environment] needs exactly one of 'context' or 'docker_image'")
    elif context is not None:
        context_path = (root / str(context)).resolve()
        if not context_path.is_dir():
            problems.append(f"[environment].context directory not found: {context_path}")
        elif not (context_path / "Dockerfile").is_file() and not (context_path / "docker-compose.yaml").is_file():
            problems.append(f"[environment].context {context_path} has no Dockerfile or docker-compose.yaml")
    elif not isinstance(image, str) or not image:
        problems.append("[environment].docker_image must be a non-empty string")

    allow_internet = env.get("allow_internet", True)
    if not isinstance(allow_internet, bool):
        problems.append("[environment].allow_internet must be true or false")
        allow_internet = True
    build_timeout = env.get("build_timeout_sec")
    if build_timeout is not None:
        build_timeout = _positive_number(env, "build_timeout_sec", 600, problems)
    base_tree = None
    base_manifest = None
    if "base_tree" in env and "base_manifest" in env:
        problems.append("[environment] takes base_tree or base_manifest, not both")
    if "base_manifest" in env:
        value = env["base_manifest"]
        if not isinstance(value, str) or not value:
            problems.append("[environment].base_manifest must be a non-empty string")
        else:
            base_manifest = (root / value).resolve()
            if not require_base_manifest:
                pass
            elif not base_manifest.is_file():
                problems.append(f"[environment].base_manifest file not found: {base_manifest}")
            else:
                bad = manifest_problem(base_manifest.read_text())
                if bad:
                    problems.append(f"[environment].base_manifest {base_manifest}: {bad}")
    if "base_tree" in env:
        # The host copy of the image's base snapshot. Compile hashes it so the
        # step scripts can tell whether /opt/odyssey/base.tar.gz was altered.
        if not isinstance(env["base_tree"], str) or not env["base_tree"]:
            problems.append("[environment].base_tree must be a non-empty string")
        else:
            base_tree = (root / env["base_tree"]).resolve()
            if not base_tree.is_dir():
                problems.append(f"[environment].base_tree directory not found: {base_tree}")
    return EnvironmentSpec(
        context=context_path,
        docker_image=image if isinstance(image, str) else None,
        base_tree=base_tree,
        cpus=_optional_positive_int(env, "cpus", problems),
        memory_mb=_optional_positive_int(env, "memory_mb", problems),
        storage_mb=_optional_positive_int(env, "storage_mb", problems),
        allow_internet=allow_internet,
        build_timeout_sec=build_timeout,
        base_manifest=base_manifest,
    )


_MANIFEST_LINE = re.compile(r"^(?:[0-9a-f]{64}|link .*)  \./.+$")


def manifest_problem(text: str) -> str | None:
    """Why `text` is not a base manifest in the tree_manifest format, or None if it is one."""
    if not text.strip():
        return "is empty"
    if not text.endswith("\n"):
        return "must end with a newline"
    for number, line in enumerate(text.split("\n")[:-1], start=1):
        if not _MANIFEST_LINE.match(line):
            return f"line {number} is not '<sha256>  ./path' or 'link <target>  ./path': {line[:80]!r}"
    return None


def _grading(table, problems: list[str]) -> GradingSpec:
    if not isinstance(table, dict):
        problems.append("[grading] must be a table")
        return GradingSpec()
    _check_unknown(table, _GRADING_KEYS, "[grading]", problems)
    paths = table.get("protected_paths", [])
    names = table.get("protected_names", list(DEFAULT_PROTECTED_NAMES))
    for key, value in (("protected_paths", paths), ("protected_names", names)):
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            problems.append(f"[grading].{key} must be a list of non-empty strings")
            return GradingSpec()
    for p in paths:
        parts = PurePosixPath(p).parts
        if p.startswith("/") or ".." in parts or "\n" in p or parts[:1] == (".git",):
            problems.append(f"[grading].protected_paths entry {p!r} must be a repo-relative path outside .git")
    for n in names:
        if "/" in n or "\n" in n or n in (".", ".."):
            problems.append(f"[grading].protected_names entry {n!r} must be a basename glob (no '/')")
    return GradingSpec(protected_paths=tuple(p.rstrip("/") for p in paths), protected_names=tuple(names))


def _tickets(raw, root: Path, problems: list[str]) -> tuple[Ticket, ...]:
    if not isinstance(raw, list) or not raw:
        problems.append("no [[tickets]] defined")
        return ()
    tickets: list[Ticket] = []
    seen: dict[str, str] = {}
    for index, entry in enumerate(raw, start=1):
        where = f"[[tickets]] #{index}"
        if not isinstance(entry, dict):
            problems.append(f"{where} is not a table")
            continue
        _check_unknown(entry, _TICKET_KEYS, where, problems)
        ticket_id = _string(entry, "id", where, problems)
        if not ticket_id:
            continue
        where = f"ticket {ticket_id}"
        if not ID_PATTERN.match(ticket_id):
            problems.append(f"{where}: id must match {ID_PATTERN.pattern}")
            continue
        # Step names lowercase the id, so ids differing only in case would collide.
        if ticket_id.lower() in seen:
            problems.append(f"duplicate ticket id {ticket_id!r} (clashes with {seen[ticket_id.lower()]!r})")
            continue
        seen[ticket_id.lower()] = ticket_id
        tickets.append(_ticket(entry, ticket_id, root, problems))
    return tuple(tickets)


def _ticket(entry: dict, ticket_id: str, root: Path, problems: list[str]) -> Ticket:
    where = f"ticket {ticket_id}"
    base = root / "tickets" / ticket_id

    def path(key: str, default: Path) -> Path:
        value = entry.get(key)
        return (root / value).resolve() if isinstance(value, str) else default

    for key in ("title", "cluster", "role"):
        if key in entry and not isinstance(entry[key], str):
            problems.append(f"{where}: {key} must be a string")
    ticket = Ticket(
        id=ticket_id,
        title=str(entry.get("title", "")),
        # A ticket without a cluster label is related to nothing else.
        cluster=str(entry.get("cluster", ticket_id)),
        role=str(entry.get("role", "core")),
        ticket_md=path("ticket", base / "ticket.md"),
        gold_patch=path("gold_patch", base / "gold.patch"),
        tests_dir=path("tests", base / "tests"),
    )
    if not ticket.ticket_md.is_file():
        problems.append(f"{where}: missing ticket text {ticket.ticket_md}")
    elif not ticket.ticket_md.read_text().strip():
        problems.append(f"{where}: ticket text {ticket.ticket_md} is empty")
    if not ticket.gold_patch.is_file():
        problems.append(f"{where}: missing gold patch {ticket.gold_patch}")
    elif not ticket.gold_patch.read_text().strip():
        problems.append(f"{where}: gold patch {ticket.gold_patch} is empty")
    if not ticket.run_sh.is_file():
        problems.append(f"{where}: missing grader entry point {ticket.run_sh}")
    if ticket.files_dir.exists() and not ticket.files_dir.is_dir():
        problems.append(f"{where}: {ticket.files_dir} must be a directory")
    return ticket
