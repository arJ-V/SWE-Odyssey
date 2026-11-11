import datetime as dt

import pytest

from ledgerly import Money, Posting, Transaction
from ledgerly.errors import (
    AlreadyReversedError,
    BooksError,
    CurrencyMismatchError,
    PeriodLockedError,
    UnbalancedTransactionError,
    UnknownAccountError,
    UnknownTransactionError,
)


def usd(text):
    return Money(text, "USD")


def rent(date, amount="1200.00"):
    return Transaction.transfer(date, "Rent", "Expenses:Rent", "Assets:Bank:Checking", usd(amount))


def test_post_assigns_sequential_ids(books):
    first = books.post(rent(dt.date(2024, 1, 31)))
    second = books.post(rent(dt.date(2024, 2, 29)))
    assert (first.txn_id, second.txn_id) == ("T00001", "T00002")
    assert books.get("T00002") == second
    with pytest.raises(UnknownTransactionError):
        books.get("T99999")


def test_post_validates(books):
    with pytest.raises(UnknownAccountError):
        books.post(Transaction.transfer(dt.date(2024, 1, 1), "x", "Expenses:Nope", "Assets:Bank:Checking", usd("1")))
    with pytest.raises(CurrencyMismatchError):
        books.post(Transaction.transfer(dt.date(2024, 1, 1), "x", "Expenses:Rent", "Assets:Bank:Checking", Money("1", "EUR")))
    unbalanced = Transaction(dt.date(2024, 1, 1), "x", (Posting("Expenses:Rent", usd("2")), Posting("Assets:Bank:Checking", usd("-1"))))
    with pytest.raises(UnbalancedTransactionError):
        books.post(unbalanced)
    assert len(books) == 0


def test_balances_are_signed_debit_positive(trading_books):
    assert trading_books.balance("Assets:Bank:Checking") == usd("6800.00")
    assert trading_books.balance("Liabilities:Loan") == usd("-5000.00")
    assert trading_books.balance("Income:Sales") == usd("-3000.00")
    assert trading_books.balance("Assets:Receivables") == usd("0.00")


def test_balance_as_of_and_rollup(trading_books):
    assert trading_books.balance("Assets:Bank:Checking", as_of=dt.date(2024, 1, 31)) == usd("3800.00")
    assert trading_books.balance("Expenses:Travel") == usd("59.50")
    assert trading_books.balance("Expenses:Travel", rollup=True) == usd("400.00")
    assert trading_books.balance("Assets", rollup=True) == usd("6800.00")


def test_activity_window(trading_books):
    feb = trading_books.activity("Assets:Bank:Checking", dt.date(2024, 2, 1), dt.date(2024, 2, 29))
    assert feb == usd("3000.00")
    assert list(trading_books.activity_by_account(dt.date(2024, 3, 1), None)) == [
        "Expenses:Travel",
        "Liabilities:CreditCard",
    ]


def test_transactions_are_date_ordered(books):
    books.post(rent(dt.date(2024, 3, 1)))
    books.post(rent(dt.date(2024, 1, 1)))
    assert [t.date.month for t in books.transactions()] == [1, 3]
    assert len(books.transactions(start=dt.date(2024, 2, 1))) == 1


def test_reverse_in_open_period(books):
    original = books.post(rent(dt.date(2024, 5, 31)))
    rev = books.reverse(original.txn_id, on=dt.date(2024, 6, 1))
    assert rev.reverses == original.txn_id
    assert rev.date == dt.date(2024, 6, 1)
    assert books.balance("Expenses:Rent") == usd("0.00")
    assert books.is_reversed(original.txn_id)
    with pytest.raises(AlreadyReversedError):
        books.reverse(original.txn_id)


def test_reverse_defaults_to_original_date_and_memo(books):
    original = books.post(rent(dt.date(2024, 5, 31)))
    rev = books.reverse(original.txn_id)
    assert rev.date == original.date
    assert rev.memo == f"Reversal of {original.txn_id}"


def test_locked_period_rejects_posting(books):
    books.lock.lock_through(dt.date(2024, 3, 31))
    with pytest.raises(PeriodLockedError):
        books.post(rent(dt.date(2024, 3, 31)))
    books.post(rent(dt.date(2024, 4, 1)))
    assert len(books) == 1


def test_lock_only_moves_forward(books):
    books.lock.lock_through(dt.date(2024, 3, 31))
    with pytest.raises(BooksError):
        books.lock.lock_through(dt.date(2024, 2, 29))
    books.lock.reopen()
    books.post(rent(dt.date(2024, 1, 31)))
    assert books.lock.locked_through is None
