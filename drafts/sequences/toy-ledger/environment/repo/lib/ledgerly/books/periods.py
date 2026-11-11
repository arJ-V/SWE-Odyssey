"""Period locking ("closing the books").

Books are locked *through* a date: that date and every earlier date are closed,
and nothing may be posted into them. Locks only move forward; reopening is a
deliberate, separate act because it invalidates reports already sent out.
"""

from __future__ import annotations

import datetime as dt

from ..errors import BooksError, PeriodLockedError


class PeriodLock:
    def __init__(self) -> None:
        self._through: dt.date | None = None

    @property
    def locked_through(self) -> dt.date | None:
        return self._through

    def lock_through(self, date: dt.date) -> None:
        if self._through is not None and date < self._through:
            raise BooksError(
                f"books are already locked through {self._through}; reopen() first"
            )
        self._through = date

    def reopen(self) -> None:
        self._through = None

    def is_locked(self, date: dt.date) -> bool:
        return self._through is not None and date <= self._through

    def check(self, date: dt.date) -> None:
        """Raise PeriodLockedError if ``date`` is in a closed period."""
        if self.is_locked(date):
            raise PeriodLockedError(
                f"{date} is in a locked period (books closed through {self._through})"
            )
