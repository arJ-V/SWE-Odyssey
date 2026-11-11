from decimal import Decimal

import pytest

from ledgerly.errors import AllocationError
from ledgerly.money import Money, split_evenly


def usd(text):
    return Money(text, "USD")


def test_even_split_hands_leftover_cents_to_first_parts():
    assert Money("10.00", "USD").allocate([1, 1, 1]) == [usd("3.34"), usd("3.33"), usd("3.33")]


def test_weighted_split():
    parts = Money("100.00", "USD").allocate([50, 30, 20])
    assert parts == [usd("50.00"), usd("30.00"), usd("20.00")]


def test_largest_remainder_wins_the_leftover():
    parts = Money("1.00", "USD").allocate([Decimal("0.2"), Decimal("0.7"), Decimal("0.1")])
    assert parts == [usd("0.20"), usd("0.70"), usd("0.10")]
    parts = Money("0.05", "USD").allocate([1, 3])
    assert parts == [usd("0.01"), usd("0.04")]


@pytest.mark.parametrize("total", ["0.01", "0.99", "10.00", "1234.57"])
@pytest.mark.parametrize("ratios", [[1, 1], [1, 2, 3], [7, 0, 5], [3, 3, 3, 3, 3, 3, 3]])
def test_parts_always_add_up(total, ratios):
    parts = usd(total).allocate(ratios)
    assert len(parts) == len(ratios)
    assert sum(parts) == usd(total)


def test_yen_split_has_no_fractional_yen():
    assert Money("100", "JPY").allocate([1, 1, 1]) == [
        Money("34", "JPY"),
        Money("33", "JPY"),
        Money("33", "JPY"),
    ]


def test_split_evenly():
    assert split_evenly(usd("0.10"), 4) == [usd("0.03"), usd("0.03"), usd("0.02"), usd("0.02")]
    with pytest.raises(AllocationError):
        split_evenly(usd("1"), 0)


@pytest.mark.parametrize("ratios", [[], [0, 0], [-1, 2], ["x"]])
def test_bad_ratios(ratios):
    with pytest.raises(AllocationError):
        usd("1.00").allocate(ratios)


def test_float_ratios_are_rejected():
    with pytest.raises(TypeError):
        usd("1.00").allocate([0.5, 0.5])
