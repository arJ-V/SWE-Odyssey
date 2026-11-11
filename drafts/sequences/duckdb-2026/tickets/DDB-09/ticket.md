# A VARCHAR Hive partition with the value `'NULL'` is read back as SQL NULL

```sql
CREATE TABLE t AS SELECT * FROM (VALUES ('NULL', 1), ('ok', 2)) v(p, id);
COPY t TO '/tmp/partition_literal_null' (FORMAT PARQUET, PARTITION_BY (p), OVERWRITE);

SELECT p IS NULL AS is_null, p = 'NULL' AS equals_literal, id
FROM read_parquet('/tmp/partition_literal_null/**/*.parquet', hive_partitioning = true)
ORDER BY id;
-- is_null | equals_literal | id
-- true    | NULL           | 1      <- was the string 'NULL'
-- false   | false          | 2
```

The write side is fine: a real SQL NULL is written to the directory
`p=__HIVE_DEFAULT_PARTITION__`, and the string `'NULL'` to `p=NULL`. It is the
reader that turns the string back into a NULL, so a string value does not
survive a round trip.

## What should happen

When a partition value is read:

- `__HIVE_DEFAULT_PARTITION__` is SQL NULL, whatever the column type;
- for a string (VARCHAR) partition column, any other value is the string
  itself, including `NULL`; the query above returns `false, true, 1` and
  `false, false, 2`, and a filter `WHERE key = 'NULL'` on such a column finds
  the `key=NULL` partition;
- for a partition column of another type (given with `hive_types` or detected),
  the value `NULL` (any letter case) and the empty string still mean SQL NULL.
  For example, writing `(SELECT i%5 AS a, i%2 AS b FROM range(10)) UNION ALL
  SELECT 'NULL', 'NULL'` with `PARTITION_BY (a, b)` and reading it back with
  `hive_types={'a': INT}` gives the ten integer pairs plus one row `NULL, NULL`.

Type auto-detection keeps treating `NULL` directory values as "no information"
so a column of numbers with a `NULL` partition is still detected as numeric,
and SQL NULLs keep being written as `__HIVE_DEFAULT_PARTITION__`.
