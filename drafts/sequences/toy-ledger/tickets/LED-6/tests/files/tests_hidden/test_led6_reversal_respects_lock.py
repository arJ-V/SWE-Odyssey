"""LED-6: a reversal must not change a locked period."""

import datetime as dt

import pytest

from ledgerly import ChartOfAccounts, Ledger, Money, Transaction
from ledgerly.errors import PeriodLockedError


def usd(text):
    return Money(text, "USD")


@pytest.fixture
def books():
    books = Ledger(ChartOfAccounts.standard(), "USD")
    books.post(
        Transaction.transfer(
            dt.date(2024, 3, 15), "Consulting", "Assets:Receivables", "Income:Sales", usd("2400.00")
        )
    )
    books.lock.lock_through(dt.date(2024, 3, 31))
    return books


def test_reversal_dated_in_locked_period_is_refused(books):
    with pytest.raises(PeriodLockedError):
        books.reverse("T00001")
    with pytest.raises(PeriodLockedError):
        books.reverse("T00001", on=dt.date(2024, 3, 31))


def test_refused_reversal_leaves_books_untouched(books):
    with pytest.raises(PeriodLockedError):
        books.reverse("T00001")
    assert len(books) == 1
    assert books.balance("Income:Sales", as_of=dt.date(2024, 3, 31)) == usd("-2400.00")
    assert not books.is_reversed("T00001")


def test_can_still_reverse_into_the_open_period(books):
    with pytest.raises(PeriodLockedError):
        books.reverse("T00001")
    rev = books.reverse("T00001", on=dt.date(2024, 4, 1))
    assert rev.reverses == "T00001"
    assert books.is_reversed("T00001")
    assert books.balance("Income:Sales", as_of=dt.date(2024, 3, 31)) == usd("-2400.00")
    assert books.balance("Income:Sales") == usd("0.00")
