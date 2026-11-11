"""Skip statement rows that were imported before.

People download overlapping statements (last month's export, then a
year-to-date export) and import both. Each row gets a fingerprint, and a
SeenRows store remembers the fingerprints already imported so a second import
of the same row is dropped. The store round-trips through JSON so callers can
keep it next to their books.
"""

from __future__ import annotations

import json
from typing import Iterable

from .base import FeedRow


class SeenRows:
    """The set of row fingerprints already imported into one account."""

    def __init__(self, fingerprints: Iterable[str] = ()) -> None:
        self._seen = set(fingerprints)

    def __contains__(self, fingerprint: object) -> bool:
        return fingerprint in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def add(self, fingerprint: str) -> None:
        self._seen.add(fingerprint)

    def to_json(self) -> str:
        return json.dumps(sorted(self._seen))

    @classmethod
    def from_json(cls, text: str) -> SeenRows:
        return cls(json.loads(text))


def fingerprint(row: FeedRow) -> str:
    """A stable identity for a statement row."""
    if row.reference:
        return f"ref|{row.reference}"
    return f"{row.date.isoformat()}|{row.amount.amount}|{row.amount.currency.code}"


def filter_new(rows: Iterable[FeedRow], seen: SeenRows) -> list[FeedRow]:
    """Rows whose fingerprint is not in ``seen``; records the new ones in ``seen``."""
    fresh = []
    for row in rows:
        key = fingerprint(row)
        if key in seen:
            continue
        seen.add(key)
        fresh.append(row)
    return fresh
