"""Budget versus actual for a date range.

For every budgeted account: the planned figure, the actual figure, the
variance (actual minus budget) and whether that variance is favourable. All
three figures are in natural sign, so "more" always means more sales or more
spending. Earning more than planned is favourable; spending more is not.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..books import AccountType, Budget, Ledger
from ..money import Money
from ._signs import natural_sign


@dataclass(frozen=True)
class VarianceLine:
    account: str
    budget: Money
    actual: Money
    variance: Money
    favourable: bool


def budget_variance(
    ledger: Ledger, budget: Budget, start: dt.date, end: dt.date
) -> list[VarianceLine]:
    lines = []
    for account in budget.accounts():
        account_type = ledger.chart.get(account).type
        sign = natural_sign(account_type)
        actual = ledger.activity(account, start, end, rollup=True) * sign
        planned = budget.amount_for(account, start, end) * sign
        variance = actual - planned
        lines.append(
            VarianceLine(
                account=account,
                budget=planned,
                actual=actual,
                variance=variance,
                favourable=_favourable(account_type, variance),
            )
        )
    return lines


def _favourable(account_type: AccountType, variance: Money) -> bool:
    if account_type is AccountType.INCOME:
        return not variance.is_negative()
    return variance.is_negative() or variance.is_zero()
