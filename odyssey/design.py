"""Ordering generators for counterbalancing ticket position and carryover.

Each ordering is one compiled Harbor task (step order is fixed in task.toml),
so these functions decide how many task dirs an experiment needs and which
position/predecessor effects it can separate.
"""

from __future__ import annotations

import hashlib
import random as _random
from collections import Counter
from dataclasses import dataclass
from itertools import permutations
from typing import Sequence as Seq

DESIGNS = ("williams", "latin", "random", "cold")


@dataclass(frozen=True)
class Ordering:
    id: str
    design: str
    tickets: tuple[str, ...]


def _digest(tickets: Seq[str]) -> str:
    return hashlib.sha1(",".join(tickets).encode()).hexdigest()[:6]


def _make(prefix: str, design: str, rows: list[list[str]]) -> list[Ordering]:
    """Ids like w01-3fa2c1: a sortable row number plus a hash of the row's tickets.

    The ordering id names the compiled task dir, and accounting reads labels
    from that dir. A purely positional id (c01 = "first ticket of whatever
    pool") would let a later compile with another --subset or --seed overwrite
    a task with different tickets under the same name; the hash rules that out.
    """
    width = max(2, len(str(len(rows))))
    return [Ordering(f"{prefix}{i:0{width}d}-{_digest(row)}", design, tuple(row))
            for i, row in enumerate(rows, start=1)]


def _check_tickets(tickets: Seq[str]) -> list[str]:
    tickets = list(tickets)
    if not tickets:
        raise ValueError("need at least one ticket")
    if len(set(tickets)) != len(tickets):
        raise ValueError(f"duplicate tickets in {tickets}")
    return tickets


def williams(tickets: Seq[str]) -> list[Ordering]:
    """Williams design: every ticket once per position, every ordered pair adjacent equally often.

    Even T needs T orderings; odd T needs the T rows plus their mirror images (2T).
    The first row interleaves 0, 1, T-1, 2, T-2, ...; the other rows add a constant mod T.
    """
    tickets = _check_tickets(tickets)
    t = len(tickets)
    first, low, high = [0], 1, t - 1
    while len(first) < t:
        first.append(low)
        low += 1
        if len(first) < t:
            first.append(high)
            high -= 1
    rows = [[tickets[(x + shift) % t] for x in first] for shift in range(t)]
    if t % 2 == 1 and t > 1:
        rows += [list(reversed(row)) for row in rows]
    return _make("w", "williams", rows)


def latin(tickets: Seq[str]) -> list[Ordering]:
    """Cyclic Latin square: position-balanced, but each ticket always follows the same predecessor."""
    tickets = _check_tickets(tickets)
    rows = [tickets[i:] + tickets[:i] for i in range(len(tickets))]
    return _make("l", "latin", rows)


def random(tickets: Seq[str], n: int, seed: int) -> list[Ordering]:
    """n uniformly random permutations, reproducible from the seed."""
    tickets = _check_tickets(tickets)
    if n < 1:
        raise ValueError("n must be >= 1")
    rng = _random.Random(seed)
    rows = [rng.sample(tickets, len(tickets)) for _ in range(n)]
    return _make("r", "random", rows)


def cold(tickets: Seq[str]) -> list[Ordering]:
    """One length-1 ordering per ticket: the cold baseline."""
    tickets = _check_tickets(tickets)
    return _make("c", "cold", [[t] for t in tickets])


def explicit(tickets: Seq[str]) -> Ordering:
    """A hand-picked ordering; the id is derived from its content so it is stable across runs."""
    tickets = _check_tickets(tickets)
    return Ordering(f"x{_digest(tickets)}", "explicit", tuple(tickets))


def generate(design: str, tickets: Seq[str], n: int | None = None, seed: int = 0) -> list[Ordering]:
    """Dispatch by design name (the CLI's --design)."""
    if design == "williams":
        return williams(tickets)
    if design == "latin":
        return latin(tickets)
    if design == "random":
        return random(tickets, n if n is not None else len(tickets), seed)
    if design == "cold":
        return cold(tickets)
    raise ValueError(f"unknown design {design!r}; choose from {', '.join(DESIGNS)}")


# --- balance checkers --------------------------------------------------------


def position_counts(orderings: Seq[Ordering]) -> Counter:
    """How often each (ticket, position) pair occurs; positions are 1-based."""
    return Counter((t, k) for o in orderings for k, t in enumerate(o.tickets, start=1))


def carryover_counts(orderings: Seq[Ordering]) -> Counter:
    """How often each ordered (predecessor, successor) pair is adjacent."""
    return Counter(pair for o in orderings for pair in zip(o.tickets, o.tickets[1:]))


def is_position_balanced(orderings: Seq[Ordering], tickets: Seq[str]) -> bool:
    """Every ticket appears equally often in every position (full-length orderings only)."""
    counts = position_counts(orderings)
    cells = [counts[(t, k)] for t in tickets for k in range(1, len(tickets) + 1)]
    return len(set(cells)) == 1 and cells[0] > 0


def is_carryover_balanced(orderings: Seq[Ordering], tickets: Seq[str]) -> bool:
    """Every ticket immediately follows every other ticket equally often (first-order carryover)."""
    counts = carryover_counts(orderings)
    cells = [counts[pair] for pair in permutations(tickets, 2)]
    return not cells or (len(set(cells)) == 1 and cells[0] > 0)
