# ledgerly: notes for a new engineer

What an engineer who knows this codebase would tell you on day one: layout,
build quirks and conventions. These notes were checked against the open tickets
so that none of them states a fix or points at the code a fix belongs in. Some
of the conventions are still relevant to open tickets, as good onboarding notes
would be; this arm measures "onboarded", not "told nothing".

## Layout

- The package is `lib/ledgerly/`, not the repo root and not `src/`.
  Tests are in `tests/`, maintenance scripts in `tools/`, developer docs in
  `docs/handbook/dev/CONTRIBUTING.md` (worth reading; it is the only place the
  build quirks are written down).
- `ledgerly/errors.py`: the whole exception hierarchy. `ledgerly/registry.py`: the
  plugin registry.
- `money/`: `money.py` (the `Money` type), `currency.py` (currency lookup),
  `_currency_table.py` (generated, see below), `data/currencies.tsv` (its
  source), `allocation.py` (splitting an amount into parts), `parse.py` (reading
  amounts out of text).
- `books/`: `chart.py` (accounts and types), `journal.py` (`Posting`,
  `Transaction`, including the transfer and split helpers), `ledger.py`
  (`Ledger`), `periods.py` (period locks), `budget.py` (`Budget`).
- `reports/`: `_signs.py` (`natural_sign`), `trial_balance.py`,
  `income_statement.py`, `balance_sheet.py`, `budget_variance.py`.
- `io/`: `base.py` (`FeedRow`, the importer registry, CSV helpers),
  `csv_bank.py` and `csv_card.py` (importers), `dedupe.py` (skipping rows
  already imported), `exporters.py` (CSV/JSON export registry), and
  `__init__.py` (`import_statement`, `rows_to_transactions`).

## Running the tests

    python -m pytest              # from the repo root; pytest.ini adds lib/ to the path

`pytest.ini` sets `filterwarnings = error`, so any warning fails a test,
including the DeprecationWarning from `Money.from_float`. Do not use it.

## Build quirk: the currency table is generated

`money/_currency_table.py` is generated from `money/data/currencies.tsv` by
`python tools/gen_currency_table.py`. Change currency data in the TSV, then
regenerate. `tests/test_currency_table.py` fails if the two disagree, so a hand
edit of the generated module will fail the suite even if it "works".

## Conventions

- **Money, never float.** `Money` refuses floats. Build it from `str`, `int` or
  `Decimal`. An amount with more decimals than its currency allows raises
  `PrecisionError`. Money never rounds silently. Multiplying by a rate is the one
  place it rounds, half-up.
- **Decimals come from the currency.** `Currency.exponent` and `.quantum` say
  how many decimals there are: 2 for most, 0 for JPY/KRW, 3 for some dinars and
  rials. Never assume two. `Money.minor_units` and `Money.from_minor` convert to
  and from integer minor units, and allocation works in those.
- **Errors.** Raise only subclasses of `LedgerlyError`, defined in `errors.py`.
  Tests catch specific classes such as `PeriodLockedError` and
  `UnbalancedTransactionError`.
- **Registries.** Importers and exporters register by name with a decorator.
  A module registers only once it is imported, so `io/__init__.py` imports every
  plugin module.

## Sign conventions (read this twice)

There is more than one, and the modules look alike, so it is easy to mix them
up. Check which convention a module uses before changing a sign.

1. **Ledger (books/)**: signed, debit positive and credit negative. Income,
   liabilities and equity normally have negative balances.
2. **Statements (reports/)**: natural sign. A ledger figure times
   `natural_sign(account_type)` (+1 for asset/expense, -1 for
   liability/equity/income) gives the figure people expect to read: capital,
   loans and sales show as positive numbers.
3. **Trial balance**: neither of the above. It shows raw debit and credit
   columns.
4. **Budget (books/budget.py)**: has a convention of its own, described in
   that module's docstring. Read it before touching code that uses budgets.

Importers have their own convention too: every `FeedRow` is from the account
holder's side (money out is negative). Bank CSVs already write amounts that
way. Card CSVs write them from the issuer's side and are flipped in
`csv_card.py`.
