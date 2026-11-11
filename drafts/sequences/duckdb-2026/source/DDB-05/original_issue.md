`least`/`greatest` function doesn't propagate stats
### What happens?

Hi team, I found these functions don't propagate stats correctly, which leads to unnecessary reads; see repro SQL for details, should be straightforward.

### To Reproduce

```sql
memory D CREATE TABLE integers AS
         SELECT i::BIGINT AS id
         FROM range(0, 100000) tbl(i);

-- The possible min is 5, so no scan and IO should happen
memory D EXPLAIN SELECT count(*) FROM integers WHERE greatest(id, 5) < 3;
╭─ Ungrouped Aggregate ────────╮
│ Aggregates: count_star()     │
│ ~1 row                       │
╰───────────────┬──────────────╯
╭─ Seq Scan ────┴──────────────╮
│ Table: memory.main.integers  │
│ Type: Sequential Scan        │
│ Filters: greatest(id, 5) < 3 │
│ ~20,000 rows                 │
╰──────────────────────────────╯
```

### OS:

macos

### DuckDB Version:

a81547b43c56c671a7ad1a636f83886713efdd8a

### DuckDB Client:

SQL

### Hardware:

_No response_

### Full Name:

dentiny

### Affiliation:

N/A

### Did you include all relevant configuration (e.g., CPU architecture, Linux distribution) to reproduce the issue?

- [x] Yes, I have

### Did you include all code required to reproduce the issue?

- [x] Yes, I have

### Did you include all relevant data sets for reproducing the issue?

Yes
