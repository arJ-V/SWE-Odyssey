import json

import pytest

from ledgerly.errors import RegistryError
from ledgerly.io import EXPORTERS, export
from ledgerly.registry import Registry


def test_csv_has_one_line_per_posting(trading_books):
    lines = export("csv", trading_books).splitlines()
    assert lines[0] == "txn_id,date,account,amount,currency,memo"
    assert lines[1] == "T00001,2024-01-02,Assets:Bank:Checking,5000.00,USD,Bank loan"
    assert len(lines) == 1 + 2 * len(trading_books)


def test_json_amounts_are_strings(trading_books):
    data = json.loads(export("json", trading_books))
    assert data["currency"] == "USD"
    first = data["transactions"][0]
    assert first["postings"][1] == {"account": "Liabilities:Loan", "amount": "-5000.00", "memo": ""}
    assert all(isinstance(p["amount"], str) for t in data["transactions"] for p in t["postings"])


def test_unknown_exporter():
    assert EXPORTERS.names() == ["csv", "json"]
    with pytest.raises(RegistryError, match="known: csv, json"):
        export("xlsx", None)


def test_registry_refuses_duplicates():
    registry = Registry("thing")
    registry.register("a")(object)
    assert "a" in registry
    with pytest.raises(RegistryError):
        registry.register("a")(object)
