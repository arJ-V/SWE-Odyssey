# Identical transactions on the same day are dropped on import

On 2 March I bought two coffees at the same café, 4.50 USD each, paid
separately. My bank's CSV export has two identical lines for them:

```
Date,Description,Amount
2024-03-02,Coffee Corner,-4.50
2024-03-02,Coffee Corner,-4.50
```

After importing the statement only one of them is in the books, so my
balance no longer matches the bank. It also happened on a day when two
different shops each charged me the same amount. Card statements behave the
same way (two identical parking charges, one imported).

We keep the record of already-imported rows between imports so that
downloading an overlapping statement (last month's export, then a
month-to-date export) doesn't import anything twice. That has to keep
working: re-importing a statement must add nothing, and an overlapping
statement must add only the rows that are new, including new rows that
happen to repeat each other.
