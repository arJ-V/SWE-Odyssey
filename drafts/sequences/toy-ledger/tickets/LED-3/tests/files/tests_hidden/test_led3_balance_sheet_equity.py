"""LED-3: equity must be shown in natural sign and the balance sheet must balance."""

import datetime as dt

import pytest

from ledgerly import ChartOfAccounts, Ledger, Money, Transaction
from ledgerly.reports import CURRENT_EARNINGS, balance_sheet


def usd(text):
    return Money(text, "USD")


@pytest.fixture
def books():
    books = Ledger(ChartOfAccounts.standard(), "USD")
    entries = [
        (dt.date(2024, 1, 1), "Owner capital", "Assets:Bank:Checking", "Equity:Capital", "50000.00"),
        (dt.date(2024, 1, 5), "Loan", "Assets:Bank:Checking", "Liabilities:Loan", "10000.00"),
        (dt.date(2024, 1, 20), "Sales", "Assets:Bank:Checking", "Income:Sales", "8000.00"),
        (dt.date(2024, 1, 31), "Rent", "Expenses:Rent", "Assets:Bank:Checking", "2000.00"),
        (dt.date(2024, 2, 1), "Owner draws", "Equity:Drawings", "Assets:Bank:Checking", "1500.00"),
    ]
    for date, memo, debit, credit, amount in entries:
        books.post(Transaction.transfer(date, memo, debit, credit, usd(amount)))
    return books


def test_opening_capital_is_positive_and_sheet_balances(books):
    sheet = balance_sheet(books, as_of=dt.date(2024, 1, 1))
    assert [(l.account, l.amount) for l in sheet.equity] == [
        ("Equity:Capital", usd("50000.00")),
        (CURRENT_EARNINGS, usd("0.00")),
    ]
    assert sheet.total_equity == usd("50000.00")
    assert sheet.total_assets == usd("50000.00")
    assert sheet.balanced


def test_full_sheet(books):
    sheet = balance_sheet(books, as_of=dt.date(2024, 2, 29))
    equity = {l.account: l.amount for l in sheet.equity}
    assert equity == {
        "Equity:Capital": usd("50000.00"),
        "Equity:Drawings": usd("-1500.00"),
        CURRENT_EARNINGS: usd("6000.00"),
    }
    assert sheet.total_assets == usd("64500.00")
    assert sheet.total_liabilities == usd("10000.00")
    assert sheet.total_equity == usd("54500.00")
    assert sheet.balanced


def test_liabilities_and_assets_still_positive(books):
    sheet = balance_sheet(books, as_of=dt.date(2024, 1, 31))
    assert {l.account: l.amount for l in sheet.liabilities} == {"Liabilities:Loan": usd("10000.00")}
    assert {l.account: l.amount for l in sheet.assets} == {"Assets:Bank:Checking": usd("66000.00")}
    assert sheet.balanced
