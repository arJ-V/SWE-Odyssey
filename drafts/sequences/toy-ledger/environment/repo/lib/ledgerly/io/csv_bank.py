"""Bank account statements exported as CSV.

Columns: ``Date`` (YYYY-MM-DD), ``Description``, ``Amount`` and an optional
``Reference``. Banks already write amounts from the account holder's side
(withdrawals negative), which is our FeedRow convention, so amounts pass
through unchanged.
"""

from __future__ import annotations

from ..errors import AmountParseError, FeedFormatError
from ..money import Currency, parse_amount
from .base import IMPORTERS, FeedRow, parse_date, read_csv


@IMPORTERS.register("bank-csv")
class BankCsvImporter:
    date_format = "%Y-%m-%d"

    def parse(self, text: str, currency: str | Currency) -> list[FeedRow]:
        rows = []
        for line, row in enumerate(read_csv(text, ("date", "description", "amount")), 2):
            try:
                amount = parse_amount(row["amount"], currency)
            except AmountParseError as exc:
                raise FeedFormatError(f"line {line}: {exc}") from None
            rows.append(
                FeedRow(
                    date=parse_date(row["date"], self.date_format),
                    description=row["description"],
                    amount=amount,
                    reference=row.get("reference", ""),
                )
            )
        return rows
