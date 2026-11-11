import datetime as dt

import pytest

from ledgerly import ChartOfAccounts, Ledger, Money, Transaction


def usd(amount: str) -> Money:
    return Money(amount, "USD")


@pytest.fixture
def chart() -> ChartOfAccounts:
    return ChartOfAccounts.standard()


@pytest.fixture
def books(chart: ChartOfAccounts) -> Ledger:
    return Ledger(chart, "USD")


@pytest.fixture
def trading_books(books: Ledger) -> Ledger:
    """A quarter of activity: a loan, sales, rent and a hotel bill."""
    entries = [
        (dt.date(2024, 1, 2), "Bank loan", "Assets:Bank:Checking", "Liabilities:Loan", "5000.00"),
        (dt.date(2024, 1, 15), "Invoice 1", "Assets:Receivables", "Income:Sales", "3000.00"),
        (dt.date(2024, 1, 31), "January rent", "Expenses:Rent", "Assets:Bank:Checking", "1200.00"),
        (dt.date(2024, 2, 10), "Invoice 1 paid", "Assets:Bank:Checking", "Assets:Receivables", "3000.00"),
        (dt.date(2024, 2, 20), "Hotel", "Expenses:Travel:Hotels", "Liabilities:CreditCard", "340.50"),
        (dt.date(2024, 3, 5), "Train", "Expenses:Travel", "Liabilities:CreditCard", "59.50"),
    ]
    for date, memo, debit, credit, amount in entries:
        books.post(Transaction.transfer(date, memo, debit, credit, usd(amount)))
    return books
