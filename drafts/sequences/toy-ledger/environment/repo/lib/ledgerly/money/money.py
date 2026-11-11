"""The Money value type.

Repo-wide rule: amounts are Money, never float. A Money holds a Decimal that is
exactly representable in its currency's minor unit. Anything finer raises
PrecisionError instead of being rounded silently, because silent rounding is
how ledgers stop balancing. The one operation that legitimately produces
sub-unit results, multiplying by a rate, rounds explicitly with ROUNDING.
"""

from __future__ import annotations

import warnings
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import total_ordering
from typing import Iterable, Sequence

from ..errors import AmountParseError, CurrencyMismatchError, PrecisionError
from .currency import Currency, get_currency

#: The single rounding rule used whenever arithmetic must round.
ROUNDING = ROUND_HALF_UP


@total_ordering
class Money:
    __slots__ = ("_amount", "_currency")

    def __init__(self, amount: str | int | Decimal, currency: str | Currency) -> None:
        if isinstance(amount, (float, bool)):
            raise TypeError("Money takes str, int or Decimal amounts, never float or bool")
        cur = get_currency(currency)
        value = _to_decimal(amount)
        quantized = value.quantize(cur.quantum)
        if quantized != value:
            raise PrecisionError(
                f"{amount} has more decimals than {cur.code} allows ({cur.exponent})"
            )
        # Normalise -0.00 to 0.00 so that equal amounts also print the same.
        self._amount = quantized if quantized else abs(quantized)
        self._currency = cur

    # --- construction ---------------------------------------------------

    @classmethod
    def from_minor(cls, units: int, currency: str | Currency) -> Money:
        """Build from an integer count of minor units (cents, fils, yen)."""
        cur = get_currency(currency)
        return cls(Decimal(units).scaleb(-cur.exponent), cur)

    @classmethod
    def zero(cls, currency: str | Currency) -> Money:
        return cls(0, currency)

    @classmethod
    def from_float(cls, value: float, currency: str | Currency) -> Money:
        """Deprecated bridge for legacy callers; rounds half-up to the minor unit."""
        warnings.warn(
            "Money.from_float is deprecated; build Money from a str or Decimal",
            DeprecationWarning,
            stacklevel=2,
        )
        cur = get_currency(currency)
        return cls(Decimal(repr(value)).quantize(cur.quantum, rounding=ROUNDING), cur)

    # --- accessors ------------------------------------------------------

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def minor_units(self) -> int:
        """The amount as an integer number of minor units (may be negative)."""
        return int(self._amount.scaleb(self._currency.exponent))

    def is_negative(self) -> bool:
        return self._amount < 0

    def is_zero(self) -> bool:
        return not self._amount

    # --- arithmetic -----------------------------------------------------

    def _same_currency(self, other: Money) -> None:
        if other._currency != self._currency:
            raise CurrencyMismatchError(
                f"cannot combine {self._currency.code} with {other._currency.code}"
            )

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self._amount + other._amount, self._currency)

    def __radd__(self, other: object) -> Money:
        # Lets the builtin sum() start from its default 0.
        if isinstance(other, int) and not isinstance(other, bool) and other == 0:
            return self
        return NotImplemented

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self._amount - other._amount, self._currency)

    def __neg__(self) -> Money:
        return Money(-self._amount, self._currency)

    def __pos__(self) -> Money:
        return self

    def __abs__(self) -> Money:
        return Money(abs(self._amount), self._currency)

    def __mul__(self, factor: object) -> Money:
        if isinstance(factor, (float, bool)) or not isinstance(factor, (int, Decimal)):
            return NotImplemented
        product = (self._amount * factor).quantize(self._currency.quantum, rounding=ROUNDING)
        return Money(product, self._currency)

    __rmul__ = __mul__

    def allocate(self, ratios: Sequence[int | Decimal | str]) -> list[Money]:
        """Split into parts proportional to ``ratios``; see ``allocation.allocate``."""
        from .allocation import allocate

        return allocate(self, ratios)

    # --- comparison -----------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._currency == other._currency and self._amount == other._amount

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self._amount < other._amount

    def __hash__(self) -> int:
        return hash((self._currency.code, self._amount))

    def __bool__(self) -> bool:
        return bool(self._amount)

    # --- display --------------------------------------------------------

    def __repr__(self) -> str:
        return f"Money('{self._amount}', '{self._currency.code}')"

    def __str__(self) -> str:
        return f"{self._amount:,f} {self._currency.code}"


def sum_money(items: Iterable[Money], currency: str | Currency) -> Money:
    """Sum amounts that must all be in ``currency``; an empty sum is zero."""
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total


def _to_decimal(amount: str | int | Decimal) -> Decimal:
    if isinstance(amount, Decimal):
        value = amount
    elif isinstance(amount, int):
        value = Decimal(amount)
    elif isinstance(amount, str):
        try:
            value = Decimal(amount.strip())
        except InvalidOperation:
            raise AmountParseError(f"not a number: {amount!r}") from None
    else:
        raise TypeError(f"unsupported amount type: {type(amount).__name__}")
    if not value.is_finite():
        raise AmountParseError(f"not a finite amount: {amount!r}")
    return value
