# Budget vs actual report is wrong for revenue lines

We budgeted 12,000.00 USD of sales for March and booked 11,000.00 of sales in
March. The budget-vs-actual report for March gives this line for
`Income:Sales`:

```
budget -12,000.00   actual 11,000.00   variance 23,000.00   favourable: yes
```

We expected:

```
budget  12,000.00   actual 11,000.00   variance -1,000.00   favourable: no
```

When sales beat the budget the numbers are just as strange. The expense lines
in the same report (rent, travel) look correct: the budget figure is what we
entered, and overspending is flagged as unfavourable.

Budgets are entered as positive figures, straight from our planning
spreadsheet, for both income and expense accounts.
