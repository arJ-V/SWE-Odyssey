"""Ledger exporters, by format name.

Exporters write amounts as strings, never JSON numbers: a JSON number is a
float to most readers and would undo everything Money exists for.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Callable

from ..books import Ledger
from ..registry import Registry

Exporter = Callable[[Ledger], str]

EXPORTERS: Registry[Exporter] = Registry("exporter")


@EXPORTERS.register("csv")
def export_csv(ledger: Ledger) -> str:
    """One line per posting: txn_id, date, account, amount, currency, memo."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["txn_id", "date", "account", "amount", "currency", "memo"])
    for txn in ledger.transactions():
        for posting in txn.postings:
            writer.writerow(
                [
                    txn.txn_id,
                    txn.date.isoformat(),
                    posting.account,
                    str(posting.amount.amount),
                    posting.amount.currency.code,
                    posting.memo or txn.memo,
                ]
            )
    return out.getvalue()


@EXPORTERS.register("json")
def export_json(ledger: Ledger) -> str:
    """The journal as a JSON list of transactions."""
    payload = [
        {
            "id": txn.txn_id,
            "date": txn.date.isoformat(),
            "memo": txn.memo,
            "reverses": txn.reverses,
            "postings": [
                {"account": p.account, "amount": str(p.amount.amount), "memo": p.memo}
                for p in txn.postings
            ],
        }
        for txn in ledger.transactions()
    ]
    return json.dumps({"currency": ledger.currency.code, "transactions": payload}, indent=2)


def export(kind: str, ledger: Ledger) -> str:
    return EXPORTERS.get(kind)(ledger)
