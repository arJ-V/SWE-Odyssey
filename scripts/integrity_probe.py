"""Integrity probe: a Harbor "agent" that inspects the container instead of solving.

Run it in place of a real agent to check, inside a real multi-step trial, what an
agent could see at the start of each segment (SPEC sections 4-5):

    PYTHONPATH=scripts harbor run -p <task-dir> --agent-import-path integrity_probe:IntegrityProbe \
        --ak task_dir=<task-dir> --ak sequence_dir=drafts/sequences/toy-ledger -o jobs --job-name probe-...

Per step it writes `probe.json` into the agent logs (steps/<name>/agent/probe.json):
  * /tests empty, /solution empty, no setup.sh or payload dir left in the workdir;
  * no gold patch or hidden test of the current or any later ticket anywhere on the
    container filesystem (searched by distinctive strings and file names);
  * git history is exactly one commit (reset policies);
  * memory written in segment 1 (memory_dir, repo memory files) is still there;
  * a junk file and an edit to a tracked file made in segment 1 are gone (reset
    policies) or still there (carry).
On segment 1 it plants those memory files, the junk file and the edit.

The probe never solves anything, so every step is expected to score 0.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

JUNK = "odyssey_probe_junk.txt"
TRACKED_EDIT_FILE = "README.md"
MARK = "ODYSSEY-PROBE-MARK-7f3a"


def _distinctive_lines(text: str, prefix: str, n: int = 2) -> list[str]:
    """The longest lines (after `prefix`) that look specific to this patch or test."""
    lines = [l[len(prefix):].strip() for l in text.splitlines()
             if l.startswith(prefix) and not l.startswith(("+++", "---"))]
    lines = [l for l in lines if len(l) >= 25]
    return sorted(set(lines), key=len, reverse=True)[:n]


def secrets_for(sequence_dir: Path, ticket: str) -> dict:
    """Strings and file names whose presence would reveal `ticket`'s fix or tests."""
    tdir = sequence_dir / "tickets" / ticket
    strings = _distinctive_lines((tdir / "gold.patch").read_text(), "+")
    names = []
    for f in sorted((tdir / "tests" / "files").rglob("*")):
        if f.is_file():
            names.append(f.name)
            strings += _distinctive_lines(f.read_text(), "", n=1)
    # A line that already exists in the base snapshot proves nothing.
    base = "\n".join(p.read_text(errors="replace") for p in (sequence_dir / "environment").rglob("*")
                     if p.is_file())
    strings = [s for s in strings if s not in base]
    return {"ticket": ticket, "strings": strings, "names": names}


PROBE_SH = r"""
set -u
W="$1"; MEM="$2"; POS="$3"; POLICY="$4"; SECRETS="$5"; OUT="$6"
emit() { printf '%s\t%s\n' "$1" "$2" >> "$OUT"; }
: > "$OUT"
emit tests_ls "$(ls -A /tests 2>/dev/null | tr '\n' ' ')"
emit solution_ls "$(ls -A /solution 2>/dev/null | tr '\n' ' ')"
emit setup_sh_present "$(test -e "$W/setup.sh" && echo yes || echo no)"
emit payload_present "$(test -e "$W/.odyssey_step" && echo yes || echo no)"
emit tmp_odyssey "$(ls -d /tmp/odyssey-* 2>/dev/null | tr '\n' ' ')"
emit git_commits "$(git -c safe.directory='*' -C "$W" rev-list --all --count 2>&1)"
emit git_refs "$(git -c safe.directory='*' -C "$W" for-each-ref --format='%(refname)' | tr '\n' ' ')"
emit git_reflog "$(git -c safe.directory='*' -C "$W" reflog 2>/dev/null | wc -l | tr -d ' ')"
emit git_status "$(git -c safe.directory='*' -C "$W" status --porcelain | tr '\n' ' ')"
emit var_odyssey "$(find /var/odyssey -type f -exec sh -c 'printf "%s=%s " "$1" "$(head -c 200 "$1" | tr "\n" " ")"' _ {} \; 2>/dev/null)"
emit memory_ls "$(ls -A "$MEM" 2>/dev/null | tr '\n' ' ')"
for f in NOTES.md CLAUDE.md AGENTS.md; do
  emit "repo_memory_$f" "$(cat "$W/$f" 2>/dev/null | head -c 200 | tr '\n' ' ')"
done
emit junk_present "$(test -e "$W/__JUNK__" && echo yes || echo no)"
emit tracked_edit_present "$(grep -c "__MARK__" "$W/__TRACKED__" 2>/dev/null || echo 0)"
emit procs "$(ps -eo comm= | sort | uniq -c | tr -s ' ' | tr '\n' ';')"
# Leak search: every distinctive string / file name of the current and later tickets.
while IFS="$(printf '\t')" read -r kind ticket needle; do
  [ -n "$needle" ] || continue
  if [ "$kind" = name ]; then
    hits="$(find / -xdev \( -path /proc -o -path /sys -o -path /logs/agent \) -prune -o -name "$needle" -print 2>/dev/null | head -5 | tr '\n' ' ')"
  else
    hits="$(grep -rlF --exclude-dir=proc --exclude-dir=sys --exclude-dir=dev -e "$needle" / 2>/dev/null | grep -v '^/logs/agent/' | head -5 | tr '\n' ' ')"
  fi
  emit "leak_${kind}_${ticket}" "${hits:-none}"
done < "$SECRETS"
if [ "$POS" = 1 ]; then
  mkdir -p "$MEM" && echo "probe memory from segment 1" > "$MEM/probe_seg1.txt"
  echo "probe notes from segment 1" > "$W/NOTES.md"
  echo "probe claude from segment 1" > "$W/CLAUDE.md"
  echo "junk" > "$W/__JUNK__"
  echo "__MARK__" >> "$W/__TRACKED__"
fi
"""


class IntegrityProbe(BaseAgent):
    """Inspects the container at the start of each step; plants memory/junk in step 1."""

    def __init__(self, logs_dir: Path, task_dir: str, sequence_dir: str, **kwargs):
        super().__init__(logs_dir=logs_dir, **kwargs)
        self._task_dir = Path(task_dir)
        self._sequence_dir = Path(sequence_dir)
        self._manifest = json.loads((self._task_dir / "odyssey.json").read_text())
        self._index = 0

    @staticmethod
    def name() -> str:
        return "odyssey-integrity-probe"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        self._index += 1
        position = self._index
        m = self._manifest
        later = m["ordering"][position - 1:]
        secrets = [secrets_for(self._sequence_dir, t) for t in later]
        rows = []
        for s in secrets:
            rows += [f"string\t{s['ticket']}\t{x}" for x in s["strings"]]
            rows += [f"name\t{s['ticket']}\t{x}" for x in s["names"]]
        work = self.logs_dir / "probe"
        work.mkdir(parents=True, exist_ok=True)
        (work / "secrets.tsv").write_text("\n".join(rows) + "\n")
        script = PROBE_SH.replace("__JUNK__", JUNK).replace("__MARK__", MARK).replace(
            "__TRACKED__", TRACKED_EDIT_FILE)
        (work / "probe.sh").write_text(script)
        if not environment.capabilities.mounted:  # docker bind-mounts logs_dir at /logs/agent
            await environment.upload_dir(work, "/logs/agent/probe")
        cmd = " ".join(shlex.quote(a) for a in [
            "bash", "/logs/agent/probe/probe.sh", m["workdir"], m["memory_dir"], str(position),
            m["arm_options"]["code_state"], "/logs/agent/probe/secrets.tsv", "/logs/agent/probe/out.tsv",
        ])
        result = await environment.exec(cmd, user="root", timeout_sec=600)
        (self.logs_dir / "probe_exit.txt").write_text(f"{result.return_code}\n{result.stderr or ''}")
        (self.logs_dir / "instruction.md").write_text(instruction)
