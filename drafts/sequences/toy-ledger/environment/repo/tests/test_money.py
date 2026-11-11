from decimal import Decimal

import pytest

from ledgerly.errors import (
    AmountParseError,
    CurrencyMismatchError,
    LedgerlyError,
    PrecisionError,
    UnknownCurrencyError,
)
from ledgerly.money import Money, get_currency, sum_money


def test_amount_is_quantized_to_currency():
    assert Money("12.5", "USD").amount == Decimal("12.50")
    assert str(Money("1234.5", "USD")) == "1,234.50 USD"
    assert str(Money("1500", "JPY")) == "1,500 JPY"


def test_rejects_floats_and_bools():
    with pytest.raises(TypeError):
        Money(1.5, "USD")
    with pytest.raises(TypeError):
        Money(True, "USD")


def test_too_many_decimals_is_an_error_not_a_rounding():
    with pytest.raises(PrecisionError):
        Money("1.005", "USD")
    with pytest.raises(PrecisionError):
        Money("10.5", "JPY")
    assert Money("1.250", "OMR").minor_units == 1250


def test_bad_inputs():
    with pytest.raises(AmountParseError):
        Money("twelve", "USD")
    with pytest.raises(AmountParseError):
        Money("NaN", "USD")
    with pytest.raises(UnknownCurrencyError):
        Money("1", "XXX")
    assert issubclass(PrecisionError, LedgerlyError)


def test_arithmetic():
    a, b = Money("10.00", "USD"), Money("2.50", "USD")
    assert a + b == Money("12.50", "USD")
    assert a - b == Money("7.50", "USD")
    assert -a == Money("-10.00", "USD")
    assert abs(Money("-3", "USD")) == Money("3", "USD")
    assert sum([a, b]) == Money("12.50", "USD")


def test_currency_mismatch():
    with pytest.raises(CurrencyMismatchError):
        Money("1", "USD") + Money("1", "EUR")
    with pytest.raises(CurrencyMismatchError):
        Money("1", "USD") < Money("1", "EUR")
    assert Money("1", "USD") != Money("1", "EUR")


def test_multiplication_rounds_half_up():
    assert Money("10.00", "USD") * Decimal("0.0725") == Money("0.73", "USD")
    assert Money("0.05", "USD") * Decimal("0.5") == Money("0.03", "USD")
    assert 3 * Money("1.10", "USD") == Money("3.30", "USD")
    with pytest.raises(TypeError):
        Money("1", "USD") * 1.5


def test_minor_units_round_trip():
    for code, text in [("USD", "-12.34"), ("JPY", "987"), ("TND", "0.125")]:
        money = Money(text, code)
        assert Money.from_minor(money.minor_units, code) == money


def test_negative_zero_is_normalised():
    assert str(-Money("0", "USD")) == "0.00 USD"
    assert hash(Money("-0.00", "USD")) == hash(Money("0", "USD"))


def test_ordering_and_truthiness():
    assert Money("1", "USD") < Money("2", "USD")
    assert not Money("0", "USD")
    assert Money("-1", "USD").is_negative()


def test_from_float_is_deprecated():
    with pytest.warns(DeprecationWarning):
        assert Money.from_float(0.1 + 0.2, "USD") == Money("0.30", "USD")


def test_sum_money_and_currency_lookup():
    assert sum_money([], "EUR") == Money("0", "EUR")
    assert get_currency("usd").exponent == 2
    assert get_currency("JPY").quantum == Decimal("1")
