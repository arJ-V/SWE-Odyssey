# A `rowid` lookup at a row-group boundary scans two row groups

```sql
CREATE TABLE p AS SELECT i FROM range(0, 300000) tbl(i);

-- rowid 122880 is the first row of the second row group: one row group is enough
EXPLAIN ANALYZE SELECT count(*) FROM p WHERE rowid = 122880;
--   Filters: rowid = 122880
--   Row Groups Scanned: 2 / 3
```

The count is right (1) but the first row group is read for nothing. Looking
up rowids just past the end of the table also reads data it cannot match.

## What should happen

With the default row-group size of 122880 rows, the table above has the row
groups `[0, 122880)`, `[122880, 245760)` and `[245760, 300000)` (half-open
ranges). A rowid filter should only keep the row groups whose range can
contain a matching rowid:

| filter | Row Groups Scanned | count |
|---|---|---|
| `rowid = 0`, `rowid BETWEEN 0 AND 10` | 1 / 3 | |
| `rowid = 122879` | 1 / 3 | 1 |
| `rowid = 122880` | 1 / 3 | 1 |
| `rowid = 245759` | 1 / 3 | 1 |
| `rowid = 245760` | 1 / 3 | 1 |
| `rowid = 300000` | 0 / 3 | 0 |
| `rowid >= 300000` | 0 / 3 | 0 |

A rowid filter on an empty table (`CREATE TABLE e(i INTEGER)`;
`SELECT count(*) FROM e WHERE rowid = 0`) returns 0.
