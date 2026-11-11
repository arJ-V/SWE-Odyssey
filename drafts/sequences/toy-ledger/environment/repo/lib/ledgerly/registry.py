"""A tiny named-plugin registry.

Pluggable pieces (statement importers, exporters) register themselves under a
short name with a decorator at import time, and callers look them up by that
name. Registration happens when the defining module is imported, so a plugin
module only becomes available once its package ``__init__`` imports it.
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

from .errors import RegistryError

T = TypeVar("T")


class Registry(Generic[T]):
    """Maps plugin names to objects (usually classes) of one kind."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str) -> Callable[[T], T]:
        """Decorator: ``@IMPORTERS.register("bank-csv")``."""

        def decorator(obj: T) -> T:
            if name in self._items:
                raise RegistryError(f"{self.kind} {name!r} is already registered")
            self._items[name] = obj
            return obj

        return decorator

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(self.names()) or "none"
            raise RegistryError(
                f"unknown {self.kind} {name!r} (known: {known})"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items
