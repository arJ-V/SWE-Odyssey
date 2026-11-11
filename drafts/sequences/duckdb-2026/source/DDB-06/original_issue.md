Row group pruning seems incorrect on rowid column
### What happens?

See repro for details.

### To Reproduce

```sql
memory D CREATE TABLE p AS SELECT i FROM range(0, 300000) tbl(i);

-- should only access one single row group
memory D EXPLAIN ANALYZE SELECT count(*) FROM p WHERE rowid = 122880;
╭─ Summary ───────────╮
│ Total Time: 0.0007s │
╰─────────────────────╯
╭─ Ungrouped Aggregate ─────╮
│ Aggregates: count_star()  │
│ 1 row                 0µs │
╰─────────────┒┎────────────╯
╭─ Table Scan ┚┖────────────╮
│ Table: memory.main.p      │
│ Type: Sequential Scan     │
│ Filters: rowid = 122880   │
│ Row Groups Scanned: 2 / 3 │
│ 1 row                 0µs │
╰───────────────────────────╯
```

### OS:

macos

### DuckDB Version:

71145c7b2726c29c46a2c8af71ebf7ea8051c22f

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
