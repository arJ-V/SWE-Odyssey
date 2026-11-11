"""Chart of accounts.

Account names are colon-separated paths, and the first segment fixes the
account type: "Assets:Bank:Checking" is an asset, "Expenses:Travel" an expense.
Opening an account opens its missing ancestors too, so roll-ups can always walk
up and down the tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from ..errors import BooksError, DuplicateAccountError, UnknownAccountError

SEP = ":"


class AccountType(Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"

    @property
    def normal_balance(self) -> str:
        """Which side increases the account: 'debit' or 'credit'."""
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return "debit"
        return "credit"


ROOT_TYPES = {
    "Assets": AccountType.ASSET,
    "Liabilities": AccountType.LIABILITY,
    "Equity": AccountType.EQUITY,
    "Income": AccountType.INCOME,
    "Expenses": AccountType.EXPENSE,
}


@dataclass(frozen=True)
class Account:
    name: str
    type: AccountType
    description: str = ""

    @property
    def parent(self) -> str | None:
        head, sep, _ = self.name.rpartition(SEP)
        return head if sep else None

    @property
    def leaf(self) -> str:
        return self.name.rpartition(SEP)[2]

    @property
    def depth(self) -> int:
        return self.name.count(SEP)


def is_descendant(name: str, ancestor: str) -> bool:
    """True if ``name`` is ``ancestor`` itself or sits below it in the tree."""
    return name == ancestor or name.startswith(ancestor + SEP)


class ChartOfAccounts:
    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}

    def open(self, name: str, description: str = "") -> Account:
        """Open ``name`` (and any missing ancestors); reopening is an error."""
        account_type = _type_for(name)
        if name in self._accounts:
            raise DuplicateAccountError(f"account already open: {name}")
        segments = name.split(SEP)
        for depth in range(1, len(segments)):
            ancestor = SEP.join(segments[:depth])
            self._accounts.setdefault(ancestor, Account(ancestor, account_type))
        account = Account(name, account_type, description)
        self._accounts[name] = account
        return account

    def get(self, name: str) -> Account:
        try:
            return self._accounts[name]
        except KeyError:
            raise UnknownAccountError(f"no such account: {name}") from None

    def descendants(self, name: str) -> list[Account]:
        """``name`` and every account below it, sorted by name."""
        self.get(name)
        return [a for a in self if is_descendant(a.name, name)]

    def of_type(self, account_type: AccountType) -> list[Account]:
        return [a for a in self if a.type is account_type]

    def __contains__(self, name: object) -> bool:
        return name in self._accounts

    def __iter__(self) -> Iterator[Account]:
        return iter(sorted(self._accounts.values(), key=lambda a: a.name))

    def __len__(self) -> int:
        return len(self._accounts)

    @classmethod
    def standard(cls) -> ChartOfAccounts:
        """A small starter chart for a services business."""
        chart = cls()
        for name in (
            "Assets:Bank:Checking",
            "Assets:Bank:Savings",
            "Assets:Receivables",
            "Liabilities:CreditCard",
            "Liabilities:Loan",
            "Equity:Capital",
            "Equity:Drawings",
            "Income:Sales",
            "Income:Interest",
            "Expenses:Rent",
            "Expenses:Office",
            "Expenses:Travel",
            "Expenses:Travel:Hotels",
            "Expenses:Ops:North",
            "Expenses:Ops:South",
            "Expenses:Ops:West",
            "Expenses:Uncategorised",
        ):
            chart.open(name)
        return chart


def _type_for(name: str) -> AccountType:
    segments = name.split(SEP)
    if any(not s or s != s.strip() for s in segments):
        raise BooksError(f"malformed account name: {name!r}")
    try:
        return ROOT_TYPES[segments[0]]
    except KeyError:
        roots = ", ".join(ROOT_TYPES)
        raise BooksError(f"account {name!r} must start with one of: {roots}") from None
