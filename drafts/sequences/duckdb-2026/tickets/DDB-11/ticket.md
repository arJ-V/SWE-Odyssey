# `COUNT(*)` over a reversed ROWS frame returns -1

A window frame whose start lies after its end is empty, but the window
operator returns a negative count for it:

```sql
SELECT COUNT(*) OVER (ROWS BETWEEN 1 FOLLOWING AND 1 PRECEDING) AS actual
FROM range(1);
-- -1      expected 0
```

`ORDER BY` is not required, and `range(1)` is enough. Frames that are empty
without being reversed (e.g. `ROWS BETWEEN 2 PRECEDING AND 1 PRECEDING` on the
first row) correctly give 0. Older releases rejected this frame at parse time;
now that it is accepted it has to evaluate as an empty frame.

## What should happen

A reversed frame (start after end) is an empty frame for every window
function. With

```sql
CREATE TABLE t1 (id INTEGER, ch CHAR(1));
INSERT INTO t1 VALUES (1, 'A'), (2, 'B'), (NULL, 'B');
```

- `count(*)` and `count_star()` over `ROWS BETWEEN 1 FOLLOWING AND 1 PRECEDING`
  on `range(1)` give 0, also with `EXCLUDE TIES` added to the frame;
- `count(*) OVER (PARTITION BY ch ORDER BY id ROWS BETWEEN 3 FOLLOWING AND 1 FOLLOWING)`
  gives 0 for every row of `t1`;
- `lead(id ORDER BY id) OVER (ORDER BY ch ROWS BETWEEN 3 FOLLOWING AND 1 FOLLOWING)`
  gives NULL for every row;
- ranking functions ignore the frame: `rank() OVER (ORDER BY id ROWS BETWEEN 3 FOLLOWING AND 1 FOLLOWING)`
  gives 1, 2, 3 for ids 1, 2, NULL.

Non-empty frames behave as before.
