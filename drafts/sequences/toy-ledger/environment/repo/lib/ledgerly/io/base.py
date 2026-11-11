"""Shared pieces for statement importers.

Every importer turns a statement file into FeedRow values with one sign
convention, the account holder's: negative means money left the account,
positive means money came in. How a given file format writes signs is the
importer's problem, so nothing downstream has to care which bank or card
issuer a row came from.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass
from typing import Protocol

from ..errors import FeedFormatError
from ..money import Currency, Money
from ..registry import Registry


@dataclass(frozen=True)
class FeedRow:
    date: dt.date
    description: str
    amount: Money
    reference: str = ""


class Importer(Protocol):
    def parse(self, text: str, currency: str | Currency) -> list[FeedRow]: ...


#: Statement importers, by format name. See ``io/__init__.py`` for the list.
IMPORTERS: Registry[type[Importer]] = Registry("importer")


def read_csv(text: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of a CSV with a header, keys lower-cased and stripped.

    Raises FeedFormatError if a required column is missing, so a wrong file
    fails loudly instead of importing nonsense.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        raise FeedFormatError("statement is empty")
    columns = [name.strip().lower() for name in reader.fieldnames]
    missing = [c for c in required if c not in columns]
    if missing:
        raise FeedFormatError(f"statement is missing column(s): {', '.join(missing)}")
    rows = []
    for raw in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
        if any(row.values()):
            rows.append(row)
    return rows


def parse_date(text: str, fmt: str) -> dt.date:
    try:
        return dt.datetime.strptime(text.strip(), fmt).date()
    except ValueError:
        raise FeedFormatError(f"bad date {text!r} (expected format {fmt})") from None
