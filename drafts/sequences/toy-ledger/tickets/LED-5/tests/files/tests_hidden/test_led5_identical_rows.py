"""LED-5: genuinely repeated statement rows must all import, exactly once."""

from ledgerly import Money
from ledgerly.io import SeenRows, import_statement

TWO_COFFEES = """Date,Description,Amount
2024-03-02,Coffee Corner,-4.50
2024-03-02,Coffee Corner,-4.50
2024-03-03,Bus fare,-2.80
"""

SAME_DAY_SAME_AMOUNT = """Date,Description,Amount
2024-03-05,Bakery,-4.50
2024-03-05,Newsagent,-4.50
"""

LATER_EXPORT = """Date,Description,Amount
2024-03-02,Coffee Corner,-4.50
2024-03-02,Coffee Corner,-4.50
2024-03-03,Bus fare,-2.80
2024-03-04,Coffee Corner,-4.50
2024-03-04,Coffee Corner,-4.50
"""

CARD = """Date,Merchant,Amount
05/03/2024,Parking,3.00
05/03/2024,Parking,3.00
"""


def usd(text):
    return Money(text, "USD")


def test_identical_rows_in_one_statement_are_all_imported():
    rows = import_statement("bank-csv", TWO_COFFEES, "USD", SeenRows())
    assert [r.description for r in rows] == ["Coffee Corner", "Coffee Corner", "Bus fare"]


def test_different_purchases_with_same_date_and_amount():
    rows = import_statement("bank-csv", SAME_DAY_SAME_AMOUNT, "USD", SeenRows())
    assert [r.description for r in rows] == ["Bakery", "Newsagent"]


def test_reimport_still_adds_nothing():
    seen = SeenRows()
    import_statement("bank-csv", TWO_COFFEES, "USD", seen)
    assert import_statement("bank-csv", TWO_COFFEES, "USD", seen) == []


def test_overlapping_export_adds_only_the_new_rows():
    seen = SeenRows()
    import_statement("bank-csv", TWO_COFFEES, "USD", seen)
    fresh = import_statement("bank-csv", LATER_EXPORT, "USD", seen)
    assert [(r.date.day, r.amount) for r in fresh] == [(4, usd("-4.50")), (4, usd("-4.50"))]


def test_seen_store_round_trip_keeps_counts():
    seen = SeenRows()
    import_statement("bank-csv", TWO_COFFEES, "USD", seen)
    restored = SeenRows.from_json(seen.to_json())
    assert import_statement("bank-csv", LATER_EXPORT, "USD", restored) != []
    assert import_statement("bank-csv", LATER_EXPORT, "USD", restored) == []


def test_card_statements_too():
    rows = import_statement("card-csv", CARD, "USD", SeenRows())
    assert [r.amount for r in rows] == [usd("-3.00"), usd("-3.00")]
