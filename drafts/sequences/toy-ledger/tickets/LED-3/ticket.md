# Balance sheet shows equity as negative and never balances

We set up books for a new company. The owner put 50,000.00 USD into the
business bank account, recorded as a transfer from `Equity:Capital` to
`Assets:Bank:Checking`. The balance sheet straight afterwards shows:

```
Assets       Assets:Bank:Checking        50,000.00
Equity       Equity:Capital             -50,000.00
             Equity:Current earnings          0.00
balanced: False
```

Liabilities look right: after we took a 10,000.00 loan it showed as a positive
10,000.00 under liabilities. But every balance sheet we produce for a company
with capital in it says it doesn't balance, and the equity section reads
upside down. Owner drawings show as a positive number as well, as if they
added to equity.

Expected: the equity section reads the way an accountant would read it
(capital positive, drawings reducing equity, current earnings as today), and
for books that balance, total assets equal total liabilities plus total equity
and `balanced` is True.
