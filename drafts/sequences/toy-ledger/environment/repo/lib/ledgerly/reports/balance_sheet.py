"""Balance sheet at a date.

Assets on one side, liabilities and equity on the other, all shown as positive
figures in normal operation. Profit not yet closed into a capital account
appears as a synthetic "current earnings" line in the equity section, which is
what makes the two sides agree before year-end closing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..books import AccountType, Ledger
from ..money import Money, sum_money
from .income_statement import StatementLine, net_income

CURRENT_EARNINGS = "Equity:Current earnings"

_SECTIONS = (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)


@dataclass(frozen=True)
class BalanceSheet:
    as_of: dt.date | None
    assets: list[StatementLine]
    liabilities: list[StatementLine]
    equity: list[StatementLine]
    total_assets: Money
    total_liabilities: Money
    total_equity: Money

    @property
    def balanced(self) -> bool:
        return self.total_assets == self.total_liabilities + self.total_equity


def balance_sheet(ledger: Ledger, as_of: dt.date | None = None) -> BalanceSheet:
    sections: dict[AccountType, list[StatementLine]] = {t: [] for t in _SECTIONS}
    for account, balance in ledger.balances(as_of).items():
        account_type = ledger.chart.get(account).type
        if account_type in sections and not balance.is_zero():
            sections[account_type].append(
                StatementLine(account, balance * _display_sign(account_type))
            )
    equity = sections[AccountType.EQUITY]
    equity.append(StatementLine(CURRENT_EARNINGS, net_income(ledger, None, as_of)))

    def total(lines: list[StatementLine]) -> Money:
        return sum_money((l.amount for l in lines), ledger.currency)

    return BalanceSheet(
        as_of=as_of,
        assets=sections[AccountType.ASSET],
        liabilities=sections[AccountType.LIABILITY],
        equity=equity,
        total_assets=total(sections[AccountType.ASSET]),
        total_liabilities=total(sections[AccountType.LIABILITY]),
        total_equity=total(equity),
    )


def _display_sign(account_type: AccountType) -> int:
    # Liabilities are credit balances in the ledger; show them as positive.
    return -1 if account_type is AccountType.LIABILITY else 1
