# ledgerly

A small double-entry bookkeeping library: a chart of accounts, a journal and
general ledger, period locks, budgets, the usual statements, and importers for
bank and card statement CSVs. Pure standard library, Python 3.11+.

```python
import datetime as dt
from ledgerly import ChartOfAccounts, Ledger, Money, Transaction
from ledgerly.reports import income_statement

chart = ChartOfAccounts.standard()
books = Ledger(chart, "USD")
books.post(Transaction.transfer(dt.date(2024, 3, 1), "March rent",
                                "Expenses:Rent", "Assets:Bank:Checking",
                                Money("1500.00", "USD")))
print(income_statement(books).net_income)   # -1,500.00 USD
```

The package lives under `lib/`, the test-suite under `tests/`, maintenance
scripts under `tools/`. Developer documentation is under `docs/`.
