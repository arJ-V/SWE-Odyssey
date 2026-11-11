Struct field with default value doesn't gets updated after rename
### What happens?

See repro for details.

### To Reproduce

```sql
memory D CREATE TABLE t(s STRUCT(a INT, b INT) DEFAULT {'a':1,'b':2});
memory D INSERT INTO t DEFAULT VALUES;
memory D ALTER TABLE t RENAME COLUMN s.a TO c;
memory D SELECT s FROM t;
┌──────────────────────────────┐
│              s               │
│ struct(c integer, b integer) │
├──────────────────────────────┤
│ {'c': 1, 'b': 2}             │
└──────────────────────────────┘
memory D INSERT INTO t DEFAULT VALUES;

-- both rows should be {'c': 1, 'b': 2}
memory D SELECT s FROM t;
┌──────────────────────────────┐
│              s               │
│ struct(c integer, b integer) │
├──────────────────────────────┤
│ {'c': 1, 'b': 2}             │
│ {'c': NULL, 'b': 2}          │
└──────────────────────────────┘

-- the struct_pack function argument is obviously incorrect
memory D SELECT sql FROM duckdb_tables() WHERE table_name='t';
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                                         sql                                          │
│                                       varchar                                        │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ CREATE TABLE t(s STRUCT(c INTEGER, b INTEGER) DEFAULT(struct_pack(a := 1, b := 2))); │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### OS:

macos

### DuckDB Version:

531911608719cd03b723051b8cb3b3e81cf3e03b

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
