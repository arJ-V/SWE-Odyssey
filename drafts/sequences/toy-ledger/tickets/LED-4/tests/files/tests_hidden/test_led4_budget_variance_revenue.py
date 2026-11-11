"""LED-4: budget-vs-actual must show revenue budgets as entered and compare correctly."""

import datetime as dt

import pytest

from ledgerly import Budget, ChartOfAccounts, Ledger, Money, Transaction
from ledgerly.reports import budget_variance

MARCH = (dt.date(2024, 3, 1), dt.date(2024, 3, 31))


def usd(text):
    return Money(text, "USD")


@pytest.fixture
def chart():
    return ChartOfAccounts.standard()


def make_books(chart, sales, rent="1500.00"):
    books = Ledger(chart, "USD")
    books.post(Transaction.transfer(dt.date(2024, 3, 10), "Sales", "Assets:Receivables", "Income:Sales", usd(sales)))
    books.post(Transaction.transfer(dt.date(2024, 3, 31), "Rent", "Expenses:Rent", "Assets:Bank:Checking", usd(rent)))
    return books


def make_budget(chart):
    budget = Budget("FY24", chart, "USD")
    budget.set("Income:Sales", "2024-03", "12000")
    budget.set("Expenses:Rent", "2024-03", "1500")
    return budget


def lines_by_account(books, budget):
    return {l.account: l for l in budget_variance(books, budget, *MARCH)}


def test_revenue_below_budget_is_unfavourable(chart):
    lines = lines_by_account(make_books(chart, "11000.00"), make_budget(chart))
    sales = lines["Income:Sales"]
    assert sales.budget == usd("12000.00")
    assert sales.actual == usd("11000.00")
    assert sales.variance == usd("-1000.00")
    assert sales.favourable is False


def test_revenue_above_budget_is_favourable(chart):
    sales = lines_by_account(make_books(chart, "12500.00"), make_budget(chart))["Income:Sales"]
    assert (sales.budget, sales.actual, sales.variance, sales.favourable) == (
        usd("12000.00"), usd("12500.00"), usd("500.00"), True,
    )


def test_expense_lines_unchanged(chart):
    rent = lines_by_account(make_books(chart, "11000.00", rent="1600.00"), make_budget(chart))["Expenses:Rent"]
    assert (rent.budget, rent.actual, rent.variance, rent.favourable) == (
        usd("1500.00"), usd("1600.00"), usd("100.00"), False,
    )


def test_budget_object_still_holds_amounts_as_entered(chart):
    budget = make_budget(chart)
    budget_variance(make_books(chart, "11000.00"), budget, *MARCH)
    assert budget.amount_for("Income:Sales", *MARCH) == usd("12000.00")
