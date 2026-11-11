import datetime as dt

import pytest

from ledgerly import Money, Posting, Transaction
from ledgerly.errors import UnbalancedTransactionError

D = dt.date(2024, 4, 1)


def usd(text):
    return Money(text, "USD")


def test_transfer_balances():
    txn = Transaction.transfer(D, "Rent", "Expenses:Rent", "Assets:Bank:Checking", usd("900"))
    txn.validate()
    assert [p.amount for p in txn.postings] == [usd("900"), usd("-900")]
    assert txn.postings[0].is_debit and not txn.postings[1].is_debit


def test_unbalanced_is_rejected_with_the_difference():
    txn = Transaction(D, "Oops", (Posting("Expenses:Rent", usd("10")), Posting("Assets:Bank:Checking", usd("-9.99"))))
    with pytest.raises(UnbalancedTransactionError, match="0.01 USD"):
        txn.validate()


def test_needs_two_postings():
    with pytest.raises(UnbalancedTransactionError):
        Transaction(D, "Lonely", (Posting("Expenses:Rent", usd("0")),))


def test_reversed_flips_every_posting():
    txn = Transaction.transfer(D, "Rent", "Expenses:Rent", "Assets:Bank:Checking", usd("900"))
    rev = txn.reversed(on=dt.date(2024, 4, 2), memo="undo")
    assert rev.date == dt.date(2024, 4, 2)
    assert [p.amount for p in rev.postings] == [usd("-900"), usd("900")]


def test_split_bill_across_cost_centres():
    txn = Transaction.split(
        D,
        "Shared software licence",
        "Liabilities:CreditCard",
        usd("100.00"),
        {"Expenses:Ops:North": 1, "Expenses:Ops:South": 1, "Expenses:Ops:West": 1},
    )
    amounts = {p.account: p.amount for p in txn.postings}
    assert amounts == {
        "Expenses:Ops:North": usd("33.34"),
        "Expenses:Ops:South": usd("33.33"),
        "Expenses:Ops:West": usd("33.33"),
        "Liabilities:CreditCard": usd("-100.00"),
    }


def test_split_with_weights():
    txn = Transaction.split(
        D, "Office move", "Assets:Bank:Checking", usd("250.00"),
        {"Expenses:Ops:North": 3, "Expenses:Ops:South": 2},
    )
    assert [p.amount for p in txn.postings] == [usd("150.00"), usd("100.00"), usd("-250.00")]
