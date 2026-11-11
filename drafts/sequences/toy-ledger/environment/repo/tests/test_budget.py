import datetime as dt

import pytest

from ledgerly import Budget, Money
from ledgerly.errors import BooksError, CurrencyMismatchError


def usd(text):
    return Money(text, "USD")


@pytest.fixture
def budget(chart):
    b = Budget("FY24", chart, "USD")
    b.set("Income:Sales", "2024-01", "12000")
    b.set("Income:Sales", "2024-02", "12000")
    b.set("Expenses:Rent", "2024-01", "1500")
    b.set("Expenses:Rent", "2024-02", "1500")
    return b


def test_amounts_are_kept_as_entered(budget):
    assert budget.get("Income:Sales", "2024-01") == usd("12000.00")
    assert budget.get("Expenses:Rent", "2024-02") == usd("1500.00")
    assert budget.get("Expenses:Office", "2024-02") == usd("0.00")


def test_amount_for_overlapping_months(budget):
    jan = budget.amount_for("Income:Sales", dt.date(2024, 1, 1), dt.date(2024, 1, 31))
    assert jan == usd("12000.00")
    q1 = budget.amount_for("Income:Sales", dt.date(2024, 1, 15), dt.date(2024, 3, 31))
    assert q1 == usd("24000.00")
    assert budget.amount_for("Expenses:Rent", dt.date(2024, 3, 1), dt.date(2024, 3, 31)) == usd("0")


def test_listing(budget):
    assert budget.accounts() == ["Expenses:Rent", "Income:Sales"]
    assert budget.months() == ["2024-01", "2024-02"]


def test_only_income_and_expense_accounts(budget):
    with pytest.raises(BooksError):
        budget.set("Assets:Bank:Checking", "2024-01", "1")


def test_bad_month_and_currency(budget):
    with pytest.raises(BooksError):
        budget.set("Expenses:Rent", "2024-13", "1")
    with pytest.raises(BooksError):
        budget.set("Expenses:Rent", "Jan 2024", "1")
    with pytest.raises(CurrencyMismatchError):
        budget.set("Expenses:Rent", "2024-03", Money("1", "EUR"))
