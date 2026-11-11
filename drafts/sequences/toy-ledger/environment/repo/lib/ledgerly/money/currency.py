"""Currency metadata.

The table itself is generated (see ``_currency_table.py``); this module wraps
each row in a Currency value and resolves codes. Code that needs to know how
many decimals a currency has must ask a Currency rather than assume two:
yen have none and several dinars have three.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..errors import UnknownCurrencyError
from ._currency_table import CURRENCY_ROWS


@dataclass(frozen=True)
class Currency:
    code: str
    exponent: int
    name: str

    @property
    def quantum(self) -> Decimal:
        """The minor unit as a Decimal: 0.01 for USD, 1 for JPY, 0.001 for OMR."""
        return Decimal(1).scaleb(-self.exponent)

    def __str__(self) -> str:
        return self.code


_BY_CODE = {code: Currency(code, exponent, name) for code, exponent, name in CURRENCY_ROWS}


def get_currency(code: str | Currency) -> Currency:
    """Resolve a code (case-insensitive) or pass a Currency through unchanged."""
    if isinstance(code, Currency):
        return code
    try:
        return _BY_CODE[code.upper()]
    except (KeyError, AttributeError):
        raise UnknownCurrencyError(f"unknown currency: {code!r}") from None


def known_codes() -> list[str]:
    return sorted(_BY_CODE)
