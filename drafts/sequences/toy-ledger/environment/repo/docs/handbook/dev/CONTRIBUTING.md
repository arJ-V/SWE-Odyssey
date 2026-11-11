# Contributing to ledgerly

## Running the tests

From the repository root:

    python -m pytest

`pytest.ini` puts `lib/` on the import path, so no install step is needed.
It also sets `filterwarnings = error`: any warning raised while the tests run,
including a DeprecationWarning from our own code, fails the test.

## The currency table is generated

`lib/ledgerly/money/_currency_table.py` is generated from
`lib/ledgerly/money/data/currencies.tsv`. To change currency data, edit the TSV
and regenerate:

    python tools/gen_currency_table.py

Never edit the generated module by hand. `tests/test_currency_table.py` fails
whenever the module and the TSV disagree, and `python tools/gen_currency_table.py
--check` does the same check without running the suite.

## Conventions

- **Money, never float.** Every amount is a `ledgerly.money.Money`. `Money`
  refuses floats outright, and `Money.from_float` exists only for legacy callers:
  it emits a DeprecationWarning, which the test configuration turns into a
  failure. Amounts in exports are strings, never JSON numbers.
- **Decimals come from the currency.** Ask the `Currency` (`exponent`,
  `quantum`) how many decimals an amount has; do not assume two.
- **Errors.** Everything the library raises on purpose subclasses
  `ledgerly.errors.LedgerlyError`, and new error classes are added to
  `errors.py`, not defined locally.
- **Plugins.** Importers and exporters register by name with a
  `ledgerly.registry.Registry` decorator. Registration happens at import time,
  so a new importer module must also be imported in `ledgerly/io/__init__.py`.
- **Signs.** The ledger is debit-positive (credits negative). Statements show
  natural signs via `reports._signs.natural_sign`; the trial balance shows raw
  debit/credit columns instead. Budgets and statement importers have their own
  conventions, documented at the top of their modules: read them before
  touching a sign.
