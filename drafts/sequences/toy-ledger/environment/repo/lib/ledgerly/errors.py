"""Exception hierarchy for ledgerly.

Every error the library raises on purpose derives from LedgerlyError, so a
caller can catch "the books refused this" without also swallowing programming
errors (TypeError, AttributeError, ...). New error classes belong in this
module, not next to the code that raises them, so the whole hierarchy stays
readable in one place.
"""


class LedgerlyError(Exception):
    """Base class for every deliberate ledgerly failure."""


# --- money ---------------------------------------------------------------


class MoneyError(LedgerlyError):
    """A monetary value or operation is invalid."""


class UnknownCurrencyError(MoneyError):
    """The currency code is not in the currency table."""


class CurrencyMismatchError(MoneyError):
    """Two amounts in different currencies were combined."""


class PrecisionError(MoneyError):
    """An amount has more decimals than its currency's minor unit allows."""


class AmountParseError(MoneyError):
    """A string could not be read as an amount."""


class AllocationError(MoneyError):
    """An allocation was asked for with unusable ratios."""


# --- books ---------------------------------------------------------------


class BooksError(LedgerlyError):
    """The books refuse an operation."""


class UnknownAccountError(BooksError):
    """The account does not exist in the chart of accounts."""


class DuplicateAccountError(BooksError):
    """An account with that name is already open."""


class UnbalancedTransactionError(BooksError):
    """Debits and credits of a transaction do not net to zero."""


class PeriodLockedError(BooksError):
    """The transaction date falls inside a locked (closed) period."""


class UnknownTransactionError(BooksError):
    """No transaction with that id has been posted."""


class AlreadyReversedError(BooksError):
    """The transaction has already been reversed once."""


# --- io & plugins --------------------------------------------------------


class FeedError(LedgerlyError):
    """A statement feed could not be imported."""


class FeedFormatError(FeedError):
    """The statement file does not have the expected shape."""


class RegistryError(LedgerlyError):
    """A plugin name is unknown or registered twice."""
