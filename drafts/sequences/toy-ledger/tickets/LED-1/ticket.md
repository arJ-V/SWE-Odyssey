# Refunds split across cost centres do not balance

We share some bills between our three regional cost centres (North, South and
West) and record them with the split helper on `Transaction`, weights 1:1:1.
For a 100.00 USD licence bill that works and gives 33.34 / 33.33 / 33.33.

The vendor has now refunded that bill, and recording the refund the same way
(same cost centres, same weights, total of -100.00) is rejected:

```
ledgerly.errors.UnbalancedTransactionError: transaction does not balance: off by 0.03 USD
```

If I split a negative amount directly with the money API, the parts don't add
up to the amount I started with either, and the size of the error depends on
how many parts there are.

Expected: a refund can be split and posted like the original bill, and it
cancels the bill exactly, cost centre by cost centre. A refund of a bill that
was split 33.34 / 33.33 / 33.33 should be split -33.34 / -33.33 / -33.33.
Splitting positive amounts must keep working exactly as it does today.
