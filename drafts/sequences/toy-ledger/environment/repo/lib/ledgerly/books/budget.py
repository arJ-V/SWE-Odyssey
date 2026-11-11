"""Budgets: planned income and expense per account per calendar month.

A Budget looks like a little ledger (amounts by account over a date range) but
follows a different sign convention on purpose. The Ledger is signed, debit
positive. A Budget holds amounts in the account's *natural* sign, the way
people type them into a planning spreadsheet: 12,000 of planned sales is
stored as +12000 and 1,500 of rent as +1500. Never negate amounts on the way in
or out of a Budget.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

from ..errors import BooksError, CurrencyMismatchError
from ..money import Currency, Money, get_currency, sum_money
from .chart import AccountType, ChartOfAccounts

_MONTH = re.compile(r"(\d{4})-(\d{2})")
_BUDGETABLE = (AccountType.INCOME, AccountType.EXPENSE)

Month = tuple[int, int]


class Budget:
    def __init__(self, name: str, chart: ChartOfAccounts, currency: str | Currency) -> None:
        self.name = name
        self.chart = chart
        self.currency = get_currency(currency)
        self._plan: dict[tuple[str, Month], Money] = {}

    def set(self, account: str, month: str, amount: Money | str) -> None:
        """Plan ``amount`` (natural sign) for ``account`` in ``month`` ("YYYY-MM")."""
        if self.chart.get(account).type not in _BUDGETABLE:
            raise BooksError(f"only income and expense accounts can be budgeted: {account}")
        money = amount if isinstance(amount, Money) else Money(amount, self.currency)
        if money.currency != self.currency:
            raise CurrencyMismatchError(
                f"budget is in {self.currency.code}, amount is in {money.currency.code}"
            )
        self._plan[(account, _parse_month(month))] = money

    def get(self, account: str, month: str) -> Money:
        return self._plan.get((account, _parse_month(month)), Money.zero(self.currency))

    def amount_for(self, account: str, start: dt.date, end: dt.date) -> Money:
        """Planned amount for every month that overlaps [start, end]."""
        return sum_money(
            (
                amount
                for (name, month), amount in self._plan.items()
                if name == account and _overlaps(month, start, end)
            ),
            self.currency,
        )

    def accounts(self) -> list[str]:
        return sorted({name for name, _ in self._plan})

    def months(self) -> list[str]:
        return [f"{y:04d}-{m:02d}" for y, m in sorted({month for _, month in self._plan})]


def _parse_month(text: str) -> Month:
    match = _MONTH.fullmatch(text)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise BooksError(f"month must look like YYYY-MM: {text!r}")
    return int(match.group(1)), int(match.group(2))


def _overlaps(month: Month, start: dt.date, end: dt.date) -> bool:
    year, mon = month
    first = dt.date(year, mon, 1)
    last = dt.date(year, mon, calendar.monthrange(year, mon)[1])
    return first <= end and last >= start
