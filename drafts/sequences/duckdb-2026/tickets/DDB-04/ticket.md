# `min(i::BIGINT)` over a Parquet file scans all row groups, `min(i)` reads only metadata

`min`/`max` of a Parquet column are answered from the file's row-group
statistics without reading any data. As soon as the column is widened by a
cast inside the aggregate, the whole file is read instead:

```sql
COPY (SELECT i::INTEGER AS i FROM range(8192) t(i))
  TO '/tmp/minmax_cast_stats.parquet' (FORMAT PARQUET, ROW_GROUP_SIZE 2048);

EXPLAIN ANALYZE SELECT max(i) FROM read_parquet('/tmp/minmax_cast_stats.parquet');
--   no table scan at all: the answer comes from the statistics

EXPLAIN ANALYZE SELECT max(i::BIGINT) FROM read_parquet('/tmp/minmax_cast_stats.parquet');
--   Projection: CAST(i AS BIGINT)
--   Table Scan READ_PARQUET ... Row Groups Scanned: 4 / 4
```

Converting INTEGER to BIGINT cannot change which value is smallest or largest,
so the statistics are just as usable.

## What should happen

When every aggregate is `min`/`max` (possibly with `count(*)`) over a column
that is only widened by a lossless integer cast (for example INTEGER to
BIGINT, via `CAST`/`::` or `TRY_CAST`), the query is answered from the
statistics exactly as for the bare column: for the file above,

- `SELECT min(i::BIGINT), max(i::BIGINT), typeof(min(i::BIGINT)), typeof(max(i::BIGINT))`
  returns `0, 8191, BIGINT, BIGINT`, and its `EXPLAIN ANALYZE` contains no
  `Row Groups Scanned` line (no row group is read);
- the same with `TRY_CAST(i AS BIGINT)`.

The results keep the cast's type. Casts that can change the ordering or lose
values (narrowing, to another type family such as VARCHAR or DOUBLE) keep
being evaluated over the data.
