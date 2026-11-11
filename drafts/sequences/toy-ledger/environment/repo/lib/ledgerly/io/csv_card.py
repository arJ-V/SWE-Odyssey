"""Credit card statements exported as CSV.

Columns: ``Date`` (DD/MM/YYYY), ``Merchant``, ``Amount``. Card issuers write
amounts from *their* side: a purchase is a positive charge, and a refund or
payment is either negative or carries a ``CR`` suffix ("45.00 CR"). We flip
to the account holder's side here, so a purchase becomes a negative FeedRow.
"""

from __future__ import annotations

from ..errors import AmountParseError, FeedFormatError
from ..money import Currency, Money, parse_amount
from .base import IMPORTERS, FeedRow, parse_date, read_csv


@IMPORTERS.register("card-csv")
class CardCsvImporter:
    date_format = "%d/%m/%Y"

    def parse(self, text: str, currency: str | Currency) -> list[FeedRow]:
        rows = []
        for line, row in enumerate(read_csv(text, ("date", "merchant", "amount")), 2):
            try:
                charge = _issuer_amount(row["amount"], currency)
            except AmountParseError as exc:
                raise FeedFormatError(f"line {line}: {exc}") from None
            rows.append(
                FeedRow(
                    date=parse_date(row["date"], self.date_format),
                    description=row["merchant"],
                    amount=-charge,
                )
            )
        return rows


def _issuer_amount(text: str, currency: str | Currency) -> Money:
    """The amount as the issuer writes it: charges positive, credits negative."""
    body = text.strip()
    if body.upper().endswith("CR"):
        return -parse_amount(body[:-2], currency)
    return parse_amount(body, currency)
