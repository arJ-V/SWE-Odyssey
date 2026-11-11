# Renaming a struct field leaves the column's DEFAULT on the old field name

```sql
CREATE TABLE t(s STRUCT(a INT, b INT) DEFAULT {'a': 1, 'b': 2});
INSERT INTO t DEFAULT VALUES;
ALTER TABLE t RENAME COLUMN s.a TO c;
INSERT INTO t DEFAULT VALUES;

SELECT s FROM t;
-- {'c': 1, 'b': 2}
-- {'c': NULL, 'b': 2}      <- expected {'c': 1, 'b': 2}

SELECT sql FROM duckdb_tables() WHERE table_name = 't';
-- CREATE TABLE t(s STRUCT(c INTEGER, b INTEGER) DEFAULT(struct_pack(a := 1, b := 2)));
```

The existing row was converted correctly, but the default expression still
builds a struct with field `a`, which is then cast by name to the new type, so
the renamed field silently becomes NULL in every row inserted afterwards.

## What should happen

Renaming a field of a struct column (at any nesting level) must carry the
column's default along, so that new rows get the same values under the new
names:

- `s STRUCT(a INTEGER, b INTEGER) DEFAULT {'a': 1, 'b': 2}`, then
  `ALTER TABLE ... RENAME s.a TO c`: both default rows read `{'c': 1, 'b': 2}`;
- the same when the default is written as an explicit cast,
  `DEFAULT (struct_pack(a := 1, b := 2)::STRUCT(a INTEGER, b INTEGER))`;
- `s STRUCT(s2 STRUCT(a INTEGER, b INTEGER)) DEFAULT {'s2': {'a': 1, 'b': 2}}`
  and `RENAME s.s2.a TO c`: rows read `{'s2': {'c': 1, 'b': 2}}`;
- the same table and `RENAME s.s2 TO x`: rows read `{'x': {'a': 1, 'b': 2}}`.

Other struct alterations (adding, dropping or retyping fields) and columns
without a default behave as before.
