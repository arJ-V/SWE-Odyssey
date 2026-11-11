"""Postings and transactions.

Sign convention for the whole books package: a posting's amount is positive
for a debit and negative for a credit. A transaction balances when its
postings sum to zero in every currency. Nothing here knows about accounts
existing or periods being closed; that is the Ledger's job.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Mapping

from ..errors import UnbalancedTransactionError
from ..money import Money


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Money
    memo: str = ""

    @property
    def is_debit(self) -> bool:
        return not self.amount.is_negative()


@dataclass(frozen=True)
class Transaction:
    date: dt.date
    memo: str
    postings: tuple[Posting, ...]
    txn_id: str | None = None
    reverses: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "postings", tuple(self.postings))
        if len(self.postings) < 2:
            raise UnbalancedTransactionError("a transaction needs at least two postings")

    def imbalance(self) -> list[Money]:
        """Non-zero per-currency totals; empty when the transaction balances."""
        totals: dict[str, Money] = {}
        for posting in self.postings:
            code = posting.amount.currency.code
            totals[code] = totals[code] + posting.amount if code in totals else posting.amount
        return [total for _, total in sorted(totals.items()) if total]

    def validate(self) -> None:
        off = self.imbalance()
        if off:
            detail = ", ".join(str(m) for m in off)
            raise UnbalancedTransactionError(f"transaction does not balance: off by {detail}")

    def accounts(self) -> set[str]:
        return {p.account for p in self.postings}

    def reversed(self, on: dt.date, memo: str) -> Transaction:
        """The mirror-image transaction that cancels this one."""
        flipped = tuple(Posting(p.account, -p.amount, p.memo) for p in self.postings)
        return Transaction(on, memo, flipped, reverses=self.txn_id)

    @classmethod
    def transfer(
        cls, date: dt.date, memo: str, debit: str, credit: str, amount: Money
    ) -> Transaction:
        """The common two-line entry: debit one account, credit another."""
        return cls(date, memo, (Posting(debit, amount), Posting(credit, -amount)))

    @classmethod
    def split(
        cls,
        date: dt.date,
        memo: str,
        source: str,
        total: Money,
        targets: Mapping[str, int | str],
    ) -> Transaction:
        """Spread ``total`` over ``targets`` by weight; ``source`` takes the other side.

        A positive total debits the targets (a bill shared by cost centres) and
        credits ``source``; a negative total (a refund of such a bill) does the
        opposite. The result is validated before it is returned.
        """
        shares = total.allocate(list(targets.values()))
        postings = [Posting(account, share) for account, share in zip(targets, shares)]
        postings.append(Posting(source, -total))
        txn = cls(date, memo, tuple(postings))
        txn.validate()
        return txn
