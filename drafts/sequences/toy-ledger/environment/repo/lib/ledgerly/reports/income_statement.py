"""Income statement (profit and loss) for a date range."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..books import AccountType, Ledger
from ..money import Money, sum_money
from ._signs import natural_sign


@dataclass(frozen=True)
class StatementLine:
    account: str
    amount: Money


@dataclass(frozen=True)
class IncomeStatement:
    start: dt.date | None
    end: dt.date | None
    income: list[StatementLine]
    expenses: list[StatementLine]
    total_income: Money
    total_expenses: Money

    @property
    def net_income(self) -> Money:
        return self.total_income - self.total_expenses


def income_statement(
    ledger: Ledger, start: dt.date | None = None, end: dt.date | None = None
) -> IncomeStatement:
    """Income and expense activity in [start, end], in natural sign."""
    sections: dict[AccountType, list[StatementLine]] = {
        AccountType.INCOME: [],
        AccountType.EXPENSE: [],
    }
    for account, movement in ledger.activity_by_account(start, end).items():
        account_type = ledger.chart.get(account).type
        if account_type in sections:
            line = StatementLine(account, movement * natural_sign(account_type))
            sections[account_type].append(line)
    income, expenses = sections[AccountType.INCOME], sections[AccountType.EXPENSE]
    return IncomeStatement(
        start=start,
        end=end,
        income=income,
        expenses=expenses,
        total_income=sum_money((l.amount for l in income), ledger.currency),
        total_expenses=sum_money((l.amount for l in expenses), ledger.currency),
    )


def net_income(
    ledger: Ledger, start: dt.date | None = None, end: dt.date | None = None
) -> Money:
    """Profit (positive) or loss (negative) for [start, end]."""
    return income_statement(ledger, start, end).net_income
