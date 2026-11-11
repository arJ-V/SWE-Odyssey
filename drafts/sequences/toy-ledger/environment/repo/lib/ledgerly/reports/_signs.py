"""Presentation sign for ledger figures.

The ledger is debit-positive, so credit-normal accounts (liabilities, equity,
income) carry negative balances in normal operation. Statements show every
account in its *natural* sign instead: a 5,000 loan, 50,000 of capital and
12,000 of sales all print as positive numbers. Multiply a signed ledger figure
by ``natural_sign(type)`` to get the statement figure.
"""

from __future__ import annotations

from ..books import AccountType


def natural_sign(account_type: AccountType) -> int:
    """+1 for debit-normal accounts, -1 for credit-normal ones."""
    return 1 if account_type.normal_balance == "debit" else -1
