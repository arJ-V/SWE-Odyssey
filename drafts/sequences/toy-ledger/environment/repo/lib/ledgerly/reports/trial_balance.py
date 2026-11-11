"""Trial balance: every account's closing balance in a debit or a credit column.

Unlike the statements, a trial balance does not use natural signs. It shows
the raw ledger side: a positive (debit) balance goes in the debit column, a
negative (credit) balance goes in the credit column as a positive figure.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..books import Ledger
from ..money import Money, sum_money


@dataclass(frozen=True)
class TrialBalanceLine:
    account: str
    debit: Money
    credit: Money


@dataclass(frozen=True)
class TrialBalance:
    as_of: dt.date | None
    lines: list[TrialBalanceLine]
    total_debit: Money
    total_credit: Money

    @property
    def balanced(self) -> bool:
        return self.total_debit == self.total_credit


def trial_balance(ledger: Ledger, as_of: dt.date | None = None) -> TrialBalance:
    zero = Money.zero(ledger.currency)
    lines = []
    for account, balance in ledger.balances(as_of).items():
        if balance.is_zero():
            continue
        if balance.is_negative():
            lines.append(TrialBalanceLine(account, zero, -balance))
        else:
            lines.append(TrialBalanceLine(account, balance, zero))
    return TrialBalance(
        as_of=as_of,
        lines=lines,
        total_debit=sum_money((l.debit for l in lines), ledger.currency),
        total_credit=sum_money((l.credit for l in lines), ledger.currency),
    )
