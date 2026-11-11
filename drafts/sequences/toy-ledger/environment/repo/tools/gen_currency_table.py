#!/usr/bin/env python3
"""Regenerate lib/ledgerly/money/_currency_table.py from the currency TSV.

The TSV is the source of truth; the Python module exists only so that importing
ledgerly never touches the filesystem. Run from the repository root:

    python tools/gen_currency_table.py           # rewrite the module
    python tools/gen_currency_table.py --check   # exit 1 if the module is stale
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSV = ROOT / "lib" / "ledgerly" / "money" / "data" / "currencies.tsv"
OUT = ROOT / "lib" / "ledgerly" / "money" / "_currency_table.py"

HEADER = '''\
# Generated file -- do not edit by hand. See the developer handbook.
"""Currency rows as (code, exponent, name), sorted by code."""

CURRENCY_ROWS = (
'''


def read_rows(path: Path = TSV) -> list[tuple[str, int, str]]:
    """Parse and sanity-check the TSV; fail loudly rather than emit a bad table."""
    rows: list[tuple[str, int, str]] = []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    data = [ln for ln in lines if not ln.startswith("#")]
    if not data or data[0].split("\t") != ["code", "exponent", "name"]:
        raise SystemExit(f"{path}: missing 'code<TAB>exponent<TAB>name' header")
    for lineno, line in enumerate(data[1:], start=2):
        parts = line.split("\t")
        if len(parts) != 3:
            raise SystemExit(f"{path}: data row {lineno}: expected 3 tab-separated fields")
        code, exponent, name = parts
        if len(code) != 3 or not code.isalpha() or not code.isupper():
            raise SystemExit(f"{path}: data row {lineno}: bad currency code {code!r}")
        if not exponent.isdigit() or int(exponent) > 4:
            raise SystemExit(f"{path}: data row {lineno}: bad exponent {exponent!r}")
        rows.append((code, int(exponent), name.strip()))
    codes = [r[0] for r in rows]
    if len(set(codes)) != len(codes):
        raise SystemExit(f"{path}: duplicate currency codes")
    return sorted(rows)


def render(rows: list[tuple[str, int, str]]) -> str:
    body = "".join(f"    ({code!r}, {exp}, {name!r}),\n" for code, exp, name in rows)
    return HEADER + body + ")\n"


def main(argv: list[str]) -> int:
    expected = render(read_rows())
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != expected:
            print(f"{OUT.relative_to(ROOT)} is stale; run tools/gen_currency_table.py")
            return 1
        return 0
    OUT.write_text(expected, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
