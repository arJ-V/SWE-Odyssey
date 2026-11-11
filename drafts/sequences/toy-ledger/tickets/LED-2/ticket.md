# Kuwaiti and Bahraini dinar amounts lose their third decimal

Our Kuwait entity keeps its books in Kuwaiti dinar. A dinar is 1,000 fils, so
amounts have three decimals, but ledgerly won't accept them:

```
>>> Money("12.345", "KWD")
ledgerly.errors.PrecisionError: 12.345 has more decimals than KWD allows (2)
```

Amounts that do get in are shown with two decimals (`Money("1", "KWD")` prints
as `1.00 KWD`), and splitting a 10.000 KWD invoice three ways gives
3.34 / 3.33 / 3.33 instead of 3.334 / 3.333 / 3.333. Reading statement amounts
like `KWD 1,234.567` fails the same way.

Our Bahrain entity (Bahraini dinar, BHD) has exactly the same problem. Omani
rial books work fine, for what it's worth.

Expected: KWD and BHD amounts carry three decimals everywhere (creating,
printing, splitting, parsing), and amounts with more than three decimals are
still rejected.
