#!/usr/bin/env python3
"""Render a ticket's tests/run.sh from a grader template (stdlib only).

    python3 -I render_run_sh.py --template templates/run_automake.sh --sequence httrack-2026 \
        --ticket HTT-1 --f2p 110_local-ftp-parallel.test --p2p 00_runnable.test,01_engine-mime.test \
        --out drafts/sequences/httrack-2026/tickets/HTT-1/tests/run.sh

Placeholders: @@SEQUENCE@@, @@TICKET@@, @@F2P@@, @@P2P@@ (bash array bodies, one
test per line). Test names are restricted to a safe character set, so they
need no quoting.
"""
import argparse
import re
from pathlib import Path

SAFE = re.compile(r"^[A-Za-z0-9._/+-]+$")


def array(names: list[str]) -> str:
    for n in names:
        if not SAFE.match(n):
            raise SystemExit(f"unsafe test name {n!r}")
    return "\n    " + "\n    ".join(names) + "\n" if names else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, type=Path)
    ap.add_argument("--sequence", required=True)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--f2p", required=True, help="comma-separated")
    ap.add_argument("--p2p", required=True, help="comma-separated")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    f2p = [x for x in a.f2p.split(",") if x]
    p2p = [x for x in a.p2p.split(",") if x]
    if not f2p or set(f2p) & set(p2p):
        raise SystemExit("need at least one F2P test, and F2P and P2P must not overlap")
    text = a.template.read_text()
    for key, value in (("SEQUENCE", a.sequence), ("TICKET", a.ticket), ("F2P", array(f2p)), ("P2P", array(p2p))):
        if text.count(f"@@{key}@@") != 1:
            raise SystemExit(f"template needs exactly one @@{key}@@")
        text = text.replace(f"@@{key}@@", value)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(text)
    a.out.chmod(0o755)
    print(f"wrote {a.out}: {len(f2p)} F2P, {len(p2p)} P2P")


if __name__ == "__main__":
    main()
