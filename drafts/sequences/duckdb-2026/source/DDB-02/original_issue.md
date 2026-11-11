Prefix query doesn't always prune row groups correctly
### What happens?

See repro SQL for details, should be straightforward.

### To Reproduce

```sql
memory D CREATE TABLE t AS SELECT printf('%010d', r) s FROM range(50_000_000) tbl(r) ORDER BY r;

-- starts_with doesn't prune correctly
memory D EXPLAIN ANALYZE SELECT count(*) FROM t WHERE starts_with(s, '004999990');
╭─ Summary ───────────╮
│ Total Time: 0.0289s │
╰─────────────────────╯
╭─ Ungrouped Aggregate ────────────────╮
│ 1 row                            0µs │
╰───────────────────┒┎─────────────────╯
╭─ Table Scan ──────┚┖─────────────────╮
│ Table: memory.main.t                 │
│ Type: Sequential Scan                │
│ Filters: starts_with(s, '004999990') │
│ Row Groups Scanned: 407 / 407        │
│ 10 rows                      440.0ms │
╰──────────────────────────────────────╯

-- LIKE prunes as expected
memory D EXPLAIN ANALYZE SELECT count(*) FROM t WHERE s LIKE '004999990%';
╭─ Summary ───────────╮
│ Total Time: 0.0035s │
╰─────────────────────╯
╭─ Ungrouped Aggregate ────────────────────╮
│ Aggregates: count_star()                 │
│ 1 row                                0µs │
╰─────────────────────┒┎───────────────────╯
╭─ Table Scan ────────┚┖───────────────────╮
│ Table: memory.main.t                     │
│ Type: Sequential Scan                    │
│ Filters:                                 │
│ (s >= '004999990') AND (s < '004999991') │
│ Row Groups Scanned: 1 / 407              │
│ 10 rows                              0µs │
╰──────────────────────────────────────────╯

-- prefix operator doesn't prune as well
memory D EXPLAIN ANALYZE SELECT count(*) FROM t WHERE s ^@ '004999990';
╭─ Summary ───────────╮
│ Total Time: 0.0618s │
╰─────────────────────╯
╭─ Ungrouped Aggregate ─────────────╮
│ 1 row                         0µs │
╰─────────────────┒┎────────────────╯
╭─ Table Scan ────┚┖────────────────╮
│ Table: memory.main.t              │
│ Type: Sequential Scan             │
│ Filters: s ^@ '004999990'         │
│ Row Groups Scanned: 407 / 407     │
│ 10 rows                   930.0ms │
╰───────────────────────────────────╯
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
