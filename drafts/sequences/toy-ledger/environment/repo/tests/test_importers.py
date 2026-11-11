import datetime as dt

import pytest

from ledgerly import Money
from ledgerly.errors import FeedFormatError, RegistryError
from ledgerly.io import IMPORTERS, SeenRows, import_statement, rows_to_transactions

BANK_MARCH = """Date,Description,Amount,Reference
2024-03-01,Salary,"2,500.00",
2024-03-02,Coffee Corner,-4.50,
2024-03-04,Rent,"(1,200.00)",
"""

BANK_MARCH_TO_APRIL = """Date,Description,Amount
2024-03-04,Rent,-1200.00
2024-04-01,Salary,2500.00
2024-04-03,Bookshop,-18.99
"""

CARD = """Date,Merchant,Amount
05/03/2024,Airline,320.00
07/03/2024,Airline refund,45.00 CR
09/03/2024,Payment received,-200.00
"""


def usd(text):
    return Money(text, "USD")


def test_registry_lists_importers():
    assert IMPORTERS.names() == ["bank-csv", "card-csv"]
    with pytest.raises(RegistryError):
        import_statement("ofx", "", "USD")


def test_bank_rows_keep_their_sign():
    rows = import_statement("bank-csv", BANK_MARCH, "USD")
    assert [(r.date.day, r.description, r.amount) for r in rows] == [
        (1, "Salary", usd("2500.00")),
        (2, "Coffee Corner", usd("-4.50")),
        (4, "Rent", usd("-1200.00")),
    ]


def test_card_rows_are_flipped_to_holder_side():
    rows = import_statement("card-csv", CARD, "USD")
    assert [r.amount for r in rows] == [usd("-320.00"), usd("45.00"), usd("200.00")]
    assert rows[0].date == dt.date(2024, 3, 5)


def test_bad_statements():
    with pytest.raises(FeedFormatError, match="amount"):
        import_statement("bank-csv", "Date,Description\n2024-01-01,x\n", "USD")
    with pytest.raises(FeedFormatError, match="date"):
        import_statement("bank-csv", "Date,Description,Amount\n01/02/2024,x,1\n", "USD")
    with pytest.raises(FeedFormatError, match="line 2"):
        import_statement("bank-csv", "Date,Description,Amount\n2024-01-01,x,lots\n", "USD")


def test_reimporting_the_same_statement_adds_nothing():
    seen = SeenRows()
    assert len(import_statement("bank-csv", BANK_MARCH, "USD", seen)) == 3
    assert import_statement("bank-csv", BANK_MARCH, "USD", seen) == []


def test_overlapping_statements_only_add_new_rows():
    seen = SeenRows()
    import_statement("bank-csv", BANK_MARCH, "USD", seen)
    fresh = import_statement("bank-csv", BANK_MARCH_TO_APRIL, "USD", seen)
    assert [r.description for r in fresh] == ["Salary", "Bookshop"]


def test_seen_rows_survive_a_json_round_trip():
    seen = SeenRows()
    import_statement("bank-csv", BANK_MARCH, "USD", seen)
    restored = SeenRows.from_json(seen.to_json())
    assert len(restored) == len(seen)
    assert import_statement("bank-csv", BANK_MARCH, "USD", restored) == []


def test_rows_become_balanced_transactions(books):
    rows = import_statement("bank-csv", BANK_MARCH, "USD")
    for txn in rows_to_transactions(rows, "Assets:Bank:Checking"):
        books.post(txn)
    assert books.balance("Assets:Bank:Checking") == usd("1295.50")
    assert books.balance("Expenses:Uncategorised") == usd("-1295.50")
