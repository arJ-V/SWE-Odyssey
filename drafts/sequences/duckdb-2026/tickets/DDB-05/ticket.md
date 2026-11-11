# `least` / `greatest` lose column statistics, so impossible filters still scan the table

```sql
CREATE TABLE integers AS SELECT i::BIGINT AS id FROM range(0, 100000) tbl(i);

-- greatest(id, 5) is at least 5, so this can never be true
EXPLAIN SELECT count(*) FROM integers WHERE greatest(id, 5) < 3;
--   Seq Scan  Table: integers  Filters: greatest(id, 5) < 3  ~20,000 rows
```

For an ordinary expression on `id` with a provably false filter the plan
collapses to an `Empty Result` and nothing is read. With `least`/`greatest`
the planner knows nothing about the result's range and scans every row.

## What should happen

`least(...)`/`greatest(...)` over numeric inputs always return one of their
non-NULL inputs (NULL only if all inputs are NULL), so their result range
follows from the inputs' min/max statistics and should be propagated. With
`id` in `[0, 99999]`, `EXPLAIN` shows an `Empty Result` plan for
`greatest(id, 5) < 3` and for `least(id, 5) > 10`.

NULL inputs need care, because a NULL argument is skipped:

- `nullable_integers.id` = NULL for even `i`, else `i` (`i` in `[0, 100000)`):
  the constant 5 is never NULL, so `greatest(id, 5)` is still in `[5, 99999]`
  and `least(id, 5)` in `[1, 5]`; `greatest(id, 5) < 3` and `least(id, 5) > 10`
  are `Empty Result` plans (count 0), while `greatest(id, 5) < 100` must still
  be scanned and counts 50050.
- `nullable_pairs`: `a` = NULL when `i % 2 = 0` else `i`; `b` = NULL when
  `i % 3 = 0` else `i + 1000000`. Either input alone can decide the result, so
  both `greatest(a, b)` and `least(a, b)` range over `[1, 1099998]`:
  `greatest(a, b) < 0` and `least(a, b) > 2000000` become `Empty Result`
  plans (count 0), and `greatest(a, b) < 1050000` must not be pruned.

Non-numeric arguments (strings, booleans, ...) keep today's behaviour, and no
query result may change.
