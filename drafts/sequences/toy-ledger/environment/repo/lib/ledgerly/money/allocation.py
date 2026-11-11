"""Splitting an amount into proportional parts without losing a minor unit.

Multiplying an amount by each ratio and rounding each part independently can
gain or lose a cent overall. Instead we work in integer minor units, give each
part the whole units it is owed, then hand out the leftover units one at a time
to the parts with the largest fractional claim (the "largest remainder"
method). Ties go to the earlier part, so results are deterministic and the parts
always add back up to the total exactly.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Sequence

from ..errors import AllocationError
from .money import Money


def allocate(total: Money, ratios: Sequence[int | Decimal | str]) -> list[Money]:
    """Split ``total`` into ``len(ratios)`` parts proportional to ``ratios``.

    >>> [str(m) for m in Money("10.00", "USD").allocate([1, 1, 1])]
    ['3.34 USD', '3.33 USD', '3.33 USD']
    """
    weights = _weights(ratios)
    weight_sum = sum(weights)
    units = total.minor_units

    exact = [Decimal(units) * w / weight_sum for w in weights]
    shares = [int(claim) for claim in exact]
    leftover = units - sum(shares)

    by_claim = sorted(
        range(len(shares)),
        key=lambda i: (exact[i] - shares[i], -i),
        reverse=True,
    )
    for i in by_claim[:leftover]:
        shares[i] += 1

    return [Money.from_minor(share, total.currency) for share in shares]


def split_evenly(total: Money, parts: int) -> list[Money]:
    """Split into ``parts`` equal-as-possible amounts."""
    if parts < 1:
        raise AllocationError("cannot split into fewer than one part")
    return allocate(total, [1] * parts)


def _weights(ratios: Sequence[int | Decimal | str]) -> list[Decimal]:
    if not ratios:
        raise AllocationError("allocate() needs at least one ratio")
    weights = []
    for ratio in ratios:
        if isinstance(ratio, (float, bool)):
            raise TypeError("ratios must be int, Decimal or str, never float")
        try:
            weight = Decimal(ratio)
        except InvalidOperation:
            raise AllocationError(f"bad ratio: {ratio!r}") from None
        if not weight.is_finite() or weight < 0:
            raise AllocationError(f"ratios must be finite and non-negative: {ratio!r}")
        weights.append(weight)
    if sum(weights) == 0:
        raise AllocationError("ratios must not all be zero")
    return weights
