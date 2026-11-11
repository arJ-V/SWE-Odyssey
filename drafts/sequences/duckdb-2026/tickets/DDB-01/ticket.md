# Parquet scan reads every row group for `x BETWEEN c AND c`, although `x = c` skips them all

I write Parquet files with Bloom filters and rely on them to skip row groups
for point lookups. That works for `r = 101`, but the same lookup written as a
range with equal bounds reads the whole file.

```sql
COPY (
    SELECT ((i % 100) * 2)::INTEGER AS r, i::BIGINT AS payload
    FROM range(20480) AS t(i)
) TO '/tmp/bloom_gap.parquet'
  (FORMAT PARQUET, ROW_GROUP_SIZE 2048, WRITE_BLOOM_FILTER TRUE, OVERWRITE TRUE);

-- 101 is inside every row group's min/max but never present: the Bloom
-- filters exclude it, and nothing is read
EXPLAIN ANALYZE SELECT sum(payload) FROM read_parquet('/tmp/bloom_gap.parquet') WHERE r = 101;
--   Row Groups Scanned: 0 / 10

EXPLAIN ANALYZE SELECT sum(payload) FROM read_parquet('/tmp/bloom_gap.parquet') WHERE r BETWEEN 101 AND 101;
--   Filters: (r >= 101) AND (r <= 101)
--   Row Groups Scanned: 10 / 10

EXPLAIN ANALYZE SELECT sum(payload) FROM read_parquet('/tmp/bloom_gap.parquet') WHERE r >= 101 AND r <= 101;
--   Row Groups Scanned: 10 / 10
```

The results are correct (no rows); only the pruning is missing.

## What should happen

A range whose lower and upper bounds are both inclusive and equal selects
exactly one value, so it should get the same treatment as an equality on that
value: `r BETWEEN 101 AND 101` and `r >= 101 AND r <= 101` must skip every row
group above (`Row Groups Scanned: 0 / 10` in `EXPLAIN ANALYZE`), just like
`r = 101` and `r IS NOT DISTINCT FROM 101` do. The same holds for any other
file and column with Bloom filters, e.g. a `BIGINT` column written with
`ROW_GROUP_SIZE 10000` where the probed value lies within min/max but is absent.

Ranges with an exclusive bound, or with different bounds, keep their current
behaviour, and query results must not change anywhere.
