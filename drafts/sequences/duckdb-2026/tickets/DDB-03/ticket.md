# Filters on an element of a fixed-size ARRAY column never prune row groups

The same range filter skips row groups on a plain integer column but reads the
whole table when the value is an element of a fixed-size array:

```sql
CREATE TABLE scalar_small AS SELECT i::INTEGER AS x FROM range(0, 1000000) tbl(i);
CREATE TABLE array_small AS
    SELECT ([i::INTEGER, (i+1)::INTEGER, (i+2)::INTEGER]::INTEGER[3]) AS a
    FROM range(0, 1000000) tbl(i);

EXPLAIN ANALYZE SELECT count(*) FROM scalar_small WHERE x BETWEEN 500000 AND 500010;
--   Filters: (x >= 500000) AND (x <= 500010)
--   Row Groups Scanned: 1 / 9

EXPLAIN ANALYZE SELECT count(*) FROM array_small WHERE a[1] BETWEEN 500000 AND 500010;
--   Filters: array_extract(CAST(a AS INTEGER[]), 1) BETWEEN 500000 AND 500010
--   Row Groups Scanned: 9 / 9
```

The storage keeps min/max statistics for the array's elements, but the planner
does not seem to use them once an element is extracted.

## What should happen

Extracting an element of a fixed-size array should keep the element
statistics, so filters on `a[1]`, `a[-1]` and the like prune row groups just
as they do on a scalar column. For example, in a database attached with
`ROW_GROUP_SIZE 2048` holding

```sql
CREATE TABLE arrays AS
SELECT [i::INTEGER, (i + 1)::INTEGER, (i + 2)::INTEGER]::INTEGER[3] AS a FROM range(6144) tbl(i);
```

(three row groups), `WHERE a[1] BETWEEN 2900 AND 3100` and
`WHERE a[-1] BETWEEN 2902 AND 3102` each report `Row Groups Scanned: 1 / 3`
and count 201 rows.

Results must not change: an out-of-range extract such as `a[4]` is NULL for
every row (6144 of 6144), and with NULL arrays and NULL elements
(`INSERT INTO n VALUES (NULL), ([1, NULL, 3]), ([4, 5, 6])` into an
`INTEGER[3]` column) `a[1] IS NULL` matches 1 row, `a[2] IS NULL` 2 rows and
`a[4] IS NULL` all 3.
