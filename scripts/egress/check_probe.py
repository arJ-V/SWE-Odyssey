"""Judge egress_probe.txt files (probe.sh output) against the allowlist policy.

    python3 -I check_probe.py <jobs-dir-or-file>...

Expected under `--egress allowlist`: every `allow_*` probe reaches its provider and gets
an unauthenticated 401/403/404; every `deny_*` probe is BLOCKED; `cli_claude` and
`cli_codex` start. INFO lines are printed, not judged. Exit 1 on any mismatch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

AUTH_ERRORS = {"401", "403", "404"}


def judge(name: str, status: str, detail: str) -> str | None:
    """None when the probe matches the policy, else why not."""
    if name.startswith("allow_"):
        code = re.search(r"http=(\d{3})", detail)
        if status != "OPEN" or not code or code.group(1) not in AUTH_ERRORS:
            return "provider endpoint should answer with its unauthenticated error"
    elif name.startswith("deny_"):
        if status != "BLOCKED":
            return "should be blocked"
    elif name in ("cli_claude", "cli_codex"):
        if status != "OPEN":
            return "agent CLI should be on PATH and start"
    return None


def files(args: list[str]) -> list[Path]:
    out: list[Path] = []
    for arg in args:
        p = Path(arg)
        out.extend([p] if p.is_file() else sorted(p.rglob("egress_probe.txt")))
    return out


def main(argv: list[str]) -> int:
    failures = 0
    found = files(argv)
    if not found:
        print("no egress_probe.txt found", file=sys.stderr)
        return 1
    for path in found:
        lines = [l.split(" ", 3) for l in path.read_text().splitlines() if l.startswith("PROBE ")]
        bad = []
        for parts in lines:
            _, name, status, detail = (parts + ["", "", ""])[:4]
            why = judge(name, status, detail)
            if why:
                bad.append(f"  FAIL {name} {status} {detail[:160]} -- {why}")
        judged = sum(1 for p in lines if p[1].startswith(("allow_", "deny_", "cli_claude", "cli_codex")))
        done = any(p[1] == "done" for p in lines)
        if not done:
            bad.append("  FAIL probe did not finish")
        print(f"{path}: {judged - len([b for b in bad if 'did not finish' not in b])}/{judged} as expected"
              + ("" if bad else ", all ok"))
        for parts in lines:
            if parts[2] == "INFO" or parts[1].startswith(("dns_", "cli_")):
                print(f"  {' '.join(parts[1:])[:200]}")
        for b in bad:
            print(b)
        failures += len(bad)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
