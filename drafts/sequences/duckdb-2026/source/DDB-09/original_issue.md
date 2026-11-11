VARCHAR hive partition value 'NULL' round-trips as SQL NULL
### What happens?

See repro for details

### To Reproduce

```sql
memory D CREATE TABLE t AS SELECT * FROM (VALUES ('NULL', 1), ('ok', 2)) v(p, id);
memory D COPY t TO '/private/tmp/duckdb_partition_literal_NULL_only'
         (FORMAT PARQUET, PARTITION_BY (p), OVERWRITE);
memory D SELECT p IS NULL AS is_null, p='NULL' AS equals_literal, id
         FROM read_parquet('/private/tmp/duckdb_partition_literal_NULL_only/**/*.parquet',
                           hive_partitioning=true)
         ORDER BY id;
┌─────────┬────────────────┬───────┐
│ is_null │ equals_literal │  id   │
│ boolean │    boolean     │ int32 │
├─────────┼────────────────┼───────┤
│ true    │ NULL           │     1 │
│ false   │ false          │     2 │
└─────────┴────────────────┴───────┘
```

### OS:

macos

### DuckDB Version:

d54e4f6a20c75e4cf1bdd7bbecb24bd08a62a491

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
