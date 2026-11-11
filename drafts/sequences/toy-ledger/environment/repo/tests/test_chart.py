import pytest

from ledgerly.books import AccountType, ChartOfAccounts, is_descendant
from ledgerly.errors import BooksError, DuplicateAccountError, UnknownAccountError


def test_type_comes_from_root_segment(chart):
    assert chart.get("Assets:Bank:Checking").type is AccountType.ASSET
    assert chart.get("Income:Sales").type is AccountType.INCOME
    assert chart.get("Expenses:Travel:Hotels").type is AccountType.EXPENSE


def test_ancestors_are_opened_implicitly():
    chart = ChartOfAccounts()
    chart.open("Expenses:Marketing:Ads")
    assert "Expenses:Marketing" in chart
    assert "Expenses" in chart
    assert chart.get("Expenses:Marketing:Ads").parent == "Expenses:Marketing"
    assert chart.get("Expenses:Marketing:Ads").leaf == "Ads"


def test_duplicates_and_bad_names():
    chart = ChartOfAccounts()
    chart.open("Assets:Cash")
    with pytest.raises(DuplicateAccountError):
        chart.open("Assets:Cash")
    with pytest.raises(BooksError):
        chart.open("Stuff:Things")
    with pytest.raises(BooksError):
        chart.open("Assets::Cash")
    with pytest.raises(UnknownAccountError):
        chart.get("Assets:Nope")


def test_descendants(chart):
    names = [a.name for a in chart.descendants("Expenses:Travel")]
    assert names == ["Expenses:Travel", "Expenses:Travel:Hotels"]


def test_is_descendant_respects_segment_boundaries():
    assert is_descendant("Expenses:Travel:Hotels", "Expenses:Travel")
    assert is_descendant("Expenses:Travel", "Expenses:Travel")
    assert not is_descendant("Expenses:TravelInsurance", "Expenses:Travel")


def test_normal_balances():
    assert AccountType.ASSET.normal_balance == "debit"
    assert AccountType.EXPENSE.normal_balance == "debit"
    for t in (AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME):
        assert t.normal_balance == "credit"
