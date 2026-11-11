import datetime as dt

from ledgerly import Budget, Money
from ledgerly.books import AccountType
from ledgerly.reports import (
    CURRENT_EARNINGS,
    balance_sheet,
    budget_variance,
    income_statement,
    natural_sign,
    net_income,
    trial_balance,
)


def usd(text):
    return Money(text, "USD")


def test_natural_sign():
    assert natural_sign(AccountType.ASSET) == 1
    assert natural_sign(AccountType.EXPENSE) == 1
    assert natural_sign(AccountType.LIABILITY) == -1
    assert natural_sign(AccountType.INCOME) == -1


def test_trial_balance_uses_raw_debit_and_credit_columns(trading_books):
    tb = trial_balance(trading_books)
    assert tb.balanced
    lines = {l.account: (l.debit, l.credit) for l in tb.lines}
    assert lines["Assets:Bank:Checking"] == (usd("6800.00"), usd("0"))
    assert lines["Income:Sales"] == (usd("0"), usd("3000.00"))
    assert "Assets:Receivables" not in lines
    assert tb.total_debit == usd("8400.00")


def test_income_statement_in_natural_sign(trading_books):
    stmt = income_statement(trading_books, dt.date(2024, 1, 1), dt.date(2024, 3, 31))
    assert [(l.account, l.amount) for l in stmt.income] == [("Income:Sales", usd("3000.00"))]
    assert stmt.total_expenses == usd("1600.00")
    assert stmt.net_income == usd("1400.00")
    assert net_income(trading_books, dt.date(2024, 2, 1), dt.date(2024, 2, 29)) == usd("-340.50")


def test_balance_sheet_with_loan_and_card(trading_books):
    sheet = balance_sheet(trading_books, as_of=dt.date(2024, 3, 31))
    assert sheet.total_assets == usd("6800.00")
    assert {l.account: l.amount for l in sheet.liabilities} == {
        "Liabilities:CreditCard": usd("400.00"),
        "Liabilities:Loan": usd("5000.00"),
    }
    assert [(l.account, l.amount) for l in sheet.equity] == [(CURRENT_EARNINGS, usd("1400.00"))]
    assert sheet.balanced


def test_balance_sheet_as_of_earlier_date(trading_books):
    sheet = balance_sheet(trading_books, as_of=dt.date(2024, 1, 15))
    assert sheet.total_assets == usd("8000.00")
    assert sheet.total_liabilities == usd("5000.00")
    assert sheet.balanced


def test_budget_variance_for_expenses(trading_books, chart):
    budget = Budget("Q1", chart, "USD")
    budget.set("Expenses:Rent", "2024-01", "1500")
    budget.set("Expenses:Travel", "2024-02", "300")
    lines = {
        l.account: l
        for l in budget_variance(trading_books, budget, dt.date(2024, 1, 1), dt.date(2024, 2, 29))
    }
    rent = lines["Expenses:Rent"]
    assert (rent.budget, rent.actual, rent.variance, rent.favourable) == (
        usd("1500.00"), usd("1200.00"), usd("-300.00"), True,
    )
    travel = lines["Expenses:Travel"]
    assert (travel.actual, travel.variance, travel.favourable) == (usd("340.50"), usd("40.50"), False)
