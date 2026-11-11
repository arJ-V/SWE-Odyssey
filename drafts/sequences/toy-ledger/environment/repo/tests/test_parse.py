import pytest

from ledgerly.errors import AmountParseError, PrecisionError
from ledgerly.money import Money, parse_amount


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12.50", "12.50"),
        ("1,234.56", "1234.56"),
        ("-7.00", "-7.00"),
        ("(1,000.00)", "-1000.00"),
        ("$42", "42.00"),
        ("-$3.10", "-3.10"),
        ("USD 99.99", "99.99"),
        ("15.00-", "-15.00"),
        ("+0.5", "0.50"),
    ],
)
def test_us_style(text, expected):
    assert parse_amount(text, "USD") == Money(expected, "USD")


def test_european_separators():
    got = parse_amount("EUR 1.234,50", "EUR", decimal_sep=",", thousands_sep=".")
    assert got == Money("1234.50", "EUR")


def test_yen_has_no_decimals():
    assert parse_amount("¥12,000", "JPY") == Money("12000", "JPY")
    with pytest.raises(PrecisionError):
        parse_amount("12.5", "JPY")


@pytest.mark.parametrize("text", ["", "abc", "1.2.3", "--4", "12 34"])
def test_garbage(text):
    with pytest.raises(AmountParseError):
        parse_amount(text, "USD")
