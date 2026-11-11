"""Read amounts written the way bank exports and people write them.

Handles thousands separators, a leading or trailing minus sign, accounting
parentheses for negatives, and a currency symbol or code next to the number.
The result is always a Money in the requested currency; if the text has more
decimals than that currency allows, Money's PrecisionError propagates.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..errors import AmountParseError
from .currency import Currency, get_currency
from .money import Money

_SYMBOLS = "$£€¥"
_NUMBER = re.compile(r"(\d+(\.\d*)?|\.\d+)")


def parse_amount(
    text: str,
    currency: str | Currency,
    *,
    decimal_sep: str = ".",
    thousands_sep: str = ",",
) -> Money:
    """Parse ``text`` such as ``"(1,234.50)"`` or ``"EUR 12,00-"`` into Money."""
    code = get_currency(currency).code
    s = text.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    s = _strip_currency(s, code)
    if s[:1] in ("-", "+"):
        negative ^= s[0] == "-"
        s = s[1:].lstrip()
    elif s.endswith("-"):
        negative, s = not negative, s[:-1].rstrip()
    s = _strip_currency(s, code)
    if thousands_sep:
        s = s.replace(thousands_sep, "")
    if decimal_sep != ".":
        s = s.replace(decimal_sep, ".")
    if not _NUMBER.fullmatch(s):
        raise AmountParseError(f"cannot read an amount from {text!r}")
    value = Decimal(s)
    return Money(-value if negative else value, code)


def _strip_currency(s: str, code: str) -> str:
    """Remove a currency code or symbol from either end of ``s``."""
    upper = s.upper()
    if upper.startswith(code):
        s = s[len(code):]
    elif upper.endswith(code):
        s = s[: -len(code)]
    return s.strip().strip(_SYMBOLS).strip()
