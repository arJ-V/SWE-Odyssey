"""LED-2: Kuwaiti and Bahraini dinars have three decimals (1 dinar = 1000 fils)."""

import pytest

from ledgerly.errors import PrecisionError
from ledgerly.money import Money, get_currency, parse_amount


@pytest.mark.parametrize("code", ["KWD", "BHD"])
def test_exponent_is_three(code):
    assert get_currency(code).exponent == 3


@pytest.mark.parametrize("code", ["KWD", "BHD"])
def test_fils_are_kept(code):
    money = Money("12.345", code)
    assert money.minor_units == 12345
    assert str(money) == f"12.345 {code}"
    assert str(Money("1", code)) == f"1.000 {code}"
    with pytest.raises(PrecisionError):
        Money("1.2345", code)


def test_split_invoice_in_fils():
    assert Money("10.000", "KWD").allocate([1, 1, 1]) == [
        Money("3.334", "KWD"),
        Money("3.333", "KWD"),
        Money("3.333", "KWD"),
    ]


def test_parse_three_decimal_amounts():
    assert parse_amount("KWD 1,234.567", "KWD") == Money("1234.567", "KWD")
    assert parse_amount("(0.125)", "BHD") == Money("-0.125", "BHD")


def test_other_currencies_unchanged():
    assert get_currency("USD").exponent == 2
    assert get_currency("JPY").exponent == 0
    assert get_currency("OMR").exponent == 3
