"""The generated currency module must match its TSV source."""

import importlib.util
from pathlib import Path

from ledgerly.money import get_currency, known_codes

ROOT = Path(__file__).resolve().parent.parent


def _load_generator():
    path = ROOT / "tools" / "gen_currency_table.py"
    spec = importlib.util.spec_from_file_location("gen_currency_table", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_module_is_up_to_date():
    gen = _load_generator()
    assert gen.OUT.read_text(encoding="utf-8") == gen.render(gen.read_rows())


def test_well_known_exponents():
    assert get_currency("USD").exponent == 2
    assert get_currency("EUR").exponent == 2
    assert get_currency("JPY").exponent == 0
    assert get_currency("OMR").exponent == 3


def test_codes_are_sorted_and_unique():
    codes = known_codes()
    assert codes == sorted(set(codes))
    assert len(codes) >= 20
