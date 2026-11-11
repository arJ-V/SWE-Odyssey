# `starts_with(s, ...)` and `s ^@ ...` scan every row group, while the equivalent `LIKE 'x%'` prunes

A prefix lookup on a sorted string column is fast or slow depending only on how
it is spelled.

```sql
CREATE TABLE t AS SELECT lpad(i::VARCHAR, 9, '0') AS s FROM range(500000) tbl(i);

EXPLAIN ANALYZE SELECT count(*) FROM t WHERE s LIKE '00049999%';
--   Filters: (s >= '00049999') AND (s < '0004999:')
--   Row Groups Scanned: 1 / 5

EXPLAIN ANALYZE SELECT count(*) FROM t WHERE starts_with(s, '00049999');
--   Filters: starts_with(s, '00049999')
--   Row Groups Scanned: 5 / 5

EXPLAIN ANALYZE SELECT count(*) FROM t WHERE s ^@ '00049999';
--   Filters: s ^@ '00049999'
--   Row Groups Scanned: 5 / 5
```

On a 50-million-row table the `starts_with` query takes over 100 times longer
than the equivalent `LIKE '004999990%'`. All spellings return the same count.

## What should happen

`starts_with(s, 'p')` and `s ^@ 'p'` select exactly the rows `s LIKE 'p%'`
selects, and should be pushed into the table scan the same way, as a range
filter: `s >= 'p'` and `s <` the prefix with its last character incremented.
`EXPLAIN ANALYZE` of both queries above should show the filters
`s >= '00049999'` and `s < '0004999:'` on the scan and
`Row Groups Scanned: 1 / 5`. Results are unchanged, and spellings of a prefix
filter that already prune keep doing so.
