"""Statement importers and ledger exporters.

Importer modules register themselves with ``IMPORTERS`` when imported, so a
new format must be imported below to become available.
"""

from __future__ import annotations

from ..books import Posting, Transaction
from ..money import Currency
from . import csv_bank, csv_card  # noqa: F401  (registers the importers)
from .base import IMPORTERS, FeedRow
from .dedupe import SeenRows, filter_new, fingerprint
from .exporters import EXPORTERS, export


def import_statement(
    kind: str, text: str, currency: str | Currency, seen: SeenRows | None = None
) -> list[FeedRow]:
    """Parse a statement with the ``kind`` importer, skipping rows in ``seen``."""
    rows = IMPORTERS.get(kind)().parse(text, currency)
    if seen is not None:
        rows = filter_new(rows, seen)
    return rows


def rows_to_transactions(
    rows: list[FeedRow], account: str, contra: str = "Expenses:Uncategorised"
) -> list[Transaction]:
    """One two-line transaction per row: ``account`` against ``contra``."""
    return [
        Transaction(
            row.date,
            row.description,
            (Posting(account, row.amount), Posting(contra, -row.amount)),
        )
        for row in rows
    ]


__all__ = [
    "EXPORTERS",
    "IMPORTERS",
    "FeedRow",
    "SeenRows",
    "export",
    "filter_new",
    "fingerprint",
    "import_statement",
    "rows_to_transactions",
]
