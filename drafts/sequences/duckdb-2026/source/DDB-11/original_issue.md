COUNT(*) over a reversed ROWS frame can return a negative count
### What happens?

`COUNT(*)` over a reversed `ROWS` frame can return `-1`.
This reproduces on current main, but does not reproduce on v1.5.4.

Not sure whether this is a bug, as COUNT() should be non-negative?



### To Reproduce

To reproduce:

```sql
SELECT
  COUNT(*) OVER (
    ROWS BETWEEN 1 FOLLOWING AND 1 PRECEDING
  ) AS actual
FROM range(1);
```

Run:

```sh
./build/release/duckdb -f bug.sql
```

Actual:

```json
[{"actual":-1}]
```

Expected:

| actual |
| ---: |
| 0 |

or 

`Parser Error: frame starting from following row cannot have preceding rows`

Notes From Minimization:

- `SET debug_window_mode='separate'` makes the same query return `0`.
- `ORDER BY` is not required.
- A named `VALUES` input is not required; `range(1)` is enough.
- The issue requires a reversed `ROWS` frame. Nearby empty but non-reversed frames such as `ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING` return `0` locally.
- v1.5.4 rejects this query: `Parser Error: frame starting from following row cannot have preceding rows`


### OS:

macOS 15.7.4, arm64

### DuckDB Version:

current main (a81547b43c)

### DuckDB Client:

CLI

### Hardware:

_No response_

### Full Name:

Yibo Dong

### Affiliation:

National University of Singapore

### Did you include all relevant configuration (e.g., CPU architecture, Linux distribution) to reproduce the issue?

- [x] Yes, I have

### Did you include all code required to reproduce the issue?

- [x] Yes, I have

### Did you include all relevant data sets for reproducing the issue?

Yes
