"""The general ledger: the single place transactions are recorded.

Balances come back in the books' raw sign convention (debit positive, credit
negative; see journal.py). Turning them into the figures people expect on a
statement is the reports package's job, not the ledger's.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Iterator

from ..errors import (
    AlreadyReversedError,
    CurrencyMismatchError,
    UnknownAccountError,
    UnknownTransactionError,
)
from ..money import Currency, Money, get_currency, sum_money
from .chart import ChartOfAccounts, is_descendant
from .journal import Posting, Transaction
from .periods import PeriodLock


class Ledger:
    """A single-currency general ledger over a chart of accounts."""

    def __init__(self, chart: ChartOfAccounts, currency: str | Currency) -> None:
        self.chart = chart
        self.currency = get_currency(currency)
        self.lock = PeriodLock()
        self._txns: dict[str, Transaction] = {}
        self._reversed_by: dict[str, str] = {}
        self._seq = 0

    # --- recording ------------------------------------------------------

    def post(self, txn: Transaction) -> Transaction:
        """Validate and record ``txn``; returns the stored copy with its id."""
        self._validate(txn)
        return self._record(txn)

    def reverse(
        self, txn_id: str, on: dt.date | None = None, memo: str | None = None
    ) -> Transaction:
        """Cancel a posted transaction with a mirror-image entry.

        The reversal is dated ``on`` (default: the original's date). A
        transaction can be reversed only once.
        """
        original = self.get(txn_id)
        if txn_id in self._reversed_by:
            raise AlreadyReversedError(
                f"{txn_id} was already reversed by {self._reversed_by[txn_id]}"
            )
        reversal = original.reversed(
            on=on or original.date, memo=memo or f"Reversal of {txn_id}"
        )
        # A reversal mirrors an entry that already passed validation, so it is
        # balanced and its accounts exist by construction; record it directly.
        stored = self._record(reversal)
        self._reversed_by[txn_id] = stored.txn_id
        return stored

    def is_reversed(self, txn_id: str) -> bool:
        self.get(txn_id)
        return txn_id in self._reversed_by

    def _validate(self, txn: Transaction) -> None:
        self.lock.check(txn.date)
        for posting in txn.postings:
            if posting.account not in self.chart:
                raise UnknownAccountError(f"no such account: {posting.account}")
            if posting.amount.currency != self.currency:
                raise CurrencyMismatchError(
                    f"ledger is in {self.currency.code}, posting is in "
                    f"{posting.amount.currency.code}"
                )
        txn.validate()

    def _record(self, txn: Transaction) -> Transaction:
        self._seq += 1
        stored = replace(txn, txn_id=f"T{self._seq:05d}")
        self._txns[stored.txn_id] = stored
        return stored

    # --- queries --------------------------------------------------------

    def get(self, txn_id: str) -> Transaction:
        try:
            return self._txns[txn_id]
        except KeyError:
            raise UnknownTransactionError(f"no transaction {txn_id}") from None

    def transactions(
        self, start: dt.date | None = None, end: dt.date | None = None
    ) -> list[Transaction]:
        """Transactions dated within [start, end], oldest first."""
        chosen = [t for t in self._txns.values() if _within(t.date, start, end)]
        return sorted(chosen, key=lambda t: (t.date, t.txn_id))

    def __len__(self) -> int:
        return len(self._txns)

    def balance(
        self, account: str, as_of: dt.date | None = None, rollup: bool = False
    ) -> Money:
        """Signed balance of ``account`` (and its sub-accounts if ``rollup``)."""
        return self.activity(account, None, as_of, rollup=rollup)

    def activity(
        self,
        account: str,
        start: dt.date | None,
        end: dt.date | None,
        rollup: bool = False,
    ) -> Money:
        """Signed net movement on ``account`` between ``start`` and ``end`` inclusive."""
        self.chart.get(account)
        matches = (
            (lambda name: is_descendant(name, account)) if rollup else account.__eq__
        )
        return sum_money(
            (p.amount for p in self._postings(start, end) if matches(p.account)),
            self.currency,
        )

    def activity_by_account(
        self, start: dt.date | None = None, end: dt.date | None = None
    ) -> dict[str, Money]:
        """Signed movement per account that has postings in the window, by name."""
        totals: dict[str, Money] = {}
        for posting in self._postings(start, end):
            previous = totals.get(posting.account, Money.zero(self.currency))
            totals[posting.account] = previous + posting.amount
        return dict(sorted(totals.items()))

    def balances(self, as_of: dt.date | None = None) -> dict[str, Money]:
        """Signed closing balance per account that has postings, by name."""
        return self.activity_by_account(None, as_of)

    def _postings(self, start: dt.date | None, end: dt.date | None) -> Iterator[Posting]:
        for txn in self.transactions(start, end):
            yield from txn.postings


def _within(date: dt.date, start: dt.date | None, end: dt.date | None) -> bool:
    return (start is None or date >= start) and (end is None or date <= end)
