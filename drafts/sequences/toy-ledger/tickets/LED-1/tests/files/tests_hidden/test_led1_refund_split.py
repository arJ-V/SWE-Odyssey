"""LED-1: splitting a negative amount (a refund) must add up and mirror the charge."""

import datetime as dt

import pytest

from ledgerly import ChartOfAccounts, Ledger, Money, Transaction


def usd(text):
    return Money(text, "USD")


def test_refund_split_mirrors_the_original_split():
    charge = usd("10.00").allocate([1, 1, 1])
    refund = usd("-10.00").allocate([1, 1, 1])
    assert charge == [usd("3.34"), usd("3.33"), usd("3.33")]
    assert refund == [-part for part in charge]


@pytest.mark.parametrize("total", ["-0.01", "-0.05", "-10.00", "-99.99", "-1234.57"])
@pytest.mark.parametrize("ratios", [[1, 1], [1, 1, 1], [1, 2, 3], [7, 0, 5], [3, 3, 3, 3, 3, 3, 3]])
def test_negative_parts_add_up_and_mirror(total, ratios):
    parts = usd(total).allocate(ratios)
    assert len(parts) == len(ratios)
    assert sum(parts) == usd(total)
    assert parts == [-p for p in (-usd(total)).allocate(ratios)]
    assert all(not p.amount > 0 for p in parts)


def test_negative_yen_split():
    assert Money("-100", "JPY").allocate([1, 1, 1]) == [
        Money("-34", "JPY"),
        Money("-33", "JPY"),
        Money("-33", "JPY"),
    ]


def test_refund_split_posts_and_cancels_the_bill():
    books = Ledger(ChartOfAccounts.standard(), "USD")
    centres = {"Expenses:Ops:North": 1, "Expenses:Ops:South": 1, "Expenses:Ops:West": 1}
    bill = Transaction.split(
        dt.date(2024, 5, 1), "Licence", "Liabilities:CreditCard", usd("100.00"), centres
    )
    refund = Transaction.split(
        dt.date(2024, 5, 20), "Licence refund", "Liabilities:CreditCard", usd("-100.00"), centres
    )
    books.post(bill)
    books.post(refund)
    for account in [*centres, "Liabilities:CreditCard"]:
        assert books.balance(account) == usd("0.00"), account
