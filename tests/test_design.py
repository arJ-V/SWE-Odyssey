import pytest

from odyssey import design


def tickets(t: int) -> list[str]:
    return [f"T{i}" for i in range(1, t + 1)]


@pytest.mark.parametrize("t, expected_rows", [(4, 4), (5, 10), (6, 6)])
def test_williams_is_position_and_carryover_balanced(t, expected_rows):
    pool = tickets(t)
    orderings = design.williams(pool)
    assert len(orderings) == expected_rows
    assert all(sorted(o.tickets) == sorted(pool) for o in orderings)
    assert design.is_position_balanced(orderings, pool)
    assert design.is_carryover_balanced(orderings, pool)
    # Exact multiplicities: each pair adjacent once (even T) or twice (odd T).
    per_pair = 1 if t % 2 == 0 else 2
    assert set(design.carryover_counts(orderings).values()) == {per_pair}


@pytest.mark.parametrize("t", [4, 5, 6])
def test_latin_is_position_balanced_but_not_carryover_balanced(t):
    pool = tickets(t)
    orderings = design.latin(pool)
    assert len(orderings) == t
    assert design.is_position_balanced(orderings, pool)
    assert not design.is_carryover_balanced(orderings, pool)


def test_checkers_detect_imbalance():
    pool = tickets(4)
    unbalanced = design.williams(pool)[:-1]
    assert not design.is_position_balanced(unbalanced, pool)
    assert not design.is_carryover_balanced(unbalanced, pool)


def test_small_designs():
    assert [o.tickets for o in design.williams(["A"])] == [("A",)]
    assert [o.tickets for o in design.williams(["A", "B"])] == [("A", "B"), ("B", "A")]


def test_random_is_reproducible_and_permutations():
    pool = tickets(5)
    first = design.random(pool, n=7, seed=42)
    assert first == design.random(pool, n=7, seed=42)
    assert first != design.random(pool, n=7, seed=43)
    assert len(first) == 7
    assert all(sorted(o.tickets) == sorted(pool) for o in first)
    assert [o.id.split("-")[0] for o in first] == [f"r0{i}" for i in range(1, 8)]


def test_cold_is_one_single_ticket_ordering_per_ticket():
    orderings = design.cold(tickets(3))
    assert [o.tickets for o in orderings] == [("T1",), ("T2",), ("T3",)]
    assert [o.id.split("-")[0] for o in orderings] == ["c01", "c02", "c03"]
    assert {o.design for o in orderings} == {"cold"}


def test_ids_are_stable_and_widen_when_needed():
    assert [o.id.split("-")[0] for o in design.williams(tickets(4))] == ["w01", "w02", "w03", "w04"]
    assert design.random(tickets(3), n=120, seed=0)[-1].id.startswith("r120-")
    assert design.williams(tickets(4)) == design.williams(tickets(4))


def test_ids_name_the_content_so_different_pools_never_collide():
    """`--design cold --subset LED-3` must not reuse the task dir name of LED-1's cold run."""
    assert design.cold(["T1", "T2"])[0].id != design.cold(["T3"])[0].id
    assert design.random(tickets(4), n=1, seed=1)[0].id != design.random(tickets(4), n=1, seed=2)[0].id
    # Across pools, subsets and seeds an id always names the same tickets, so a
    # recompile into the same --out can only replace a task with an identical one.
    seen: dict[str, tuple[str, ...]] = {}
    pools = [tickets(4), tickets(5), ["T3"], ["T2", "T4"], ["T4", "T2", "T1"]]
    for pool in pools:
        for o in (design.williams(pool) + design.latin(pool) + design.cold(pool)
                  + design.random(pool, n=3, seed=len(pool)) + design.random(pool, n=3, seed=99)):
            assert seen.setdefault(o.id, o.tickets) == o.tickets, o.id


def test_explicit_id_depends_only_on_content():
    a = design.explicit(["T2", "T1"])
    assert a == design.explicit(["T2", "T1"])
    assert a.id != design.explicit(["T1", "T2"]).id
    assert a.id.startswith("x") and a.design == "explicit"


def test_generate_dispatch_and_errors():
    assert design.generate("random", tickets(4), seed=1) == design.random(tickets(4), 4, 1)
    with pytest.raises(ValueError, match="unknown design"):
        design.generate("graeco", tickets(3))
    with pytest.raises(ValueError, match="duplicate"):
        design.williams(["A", "A"])
    with pytest.raises(ValueError, match="at least one"):
        design.latin([])
