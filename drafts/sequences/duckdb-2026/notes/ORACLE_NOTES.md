# DuckDB: notes for a new contributor

## Layout
- `src/` is the engine, one directory per stage a query passes through:
  `parser/` (SQL text to parsed statements; the PEG grammar and transformer live
  in `parser/peg/`), `planner/` (binding, logical plans), `optimizer/`,
  `execution/` (physical operators), `function/` (built-in scalar, aggregate,
  table and window functions), `storage/` (row groups, column data, compression,
  checkpoints, WAL), `catalog/`, `main/` (connections, settings, extensions).
  Public headers mirror this under `src/include/duckdb/`.
- `extension/` holds the in-tree extensions. `core_functions` (most scalar and
  aggregate functions) and `parquet` are linked statically into every build.
- `test/` holds the sqllogictest suite (`test/sql/**`, `test/optimizer/**`, ...),
  the C++ test runner and its helpers. `data/` holds the test data files;
  `third_party/` is vendored code.

## Building
- `GEN=ninja make release` configures `build/release` (Release, Ninja) and
  builds the library, the `duckdb` shell and `test/unittest`. After the first
  build, `ninja -C build/release` (or `ninja -C build/release unittest`) is the
  incremental rebuild.
- ccache is set up machine-wide and holds this tree's objects for `make release`
  (first build under a minute) and `make reldebug` (about five minutes, mostly
  linking). Other configurations (`make debug`, `relassert`, sanitizers) are not
  cached: 20-40 minutes on 4 cores.
- Unity build: each directory compiles as one `ub_*.cpp`, so errors name the
  unity file plus the real file and line, and editing a widely included header
  recompiles much of the engine. `D_ASSERT` is compiled out of release builds.

## Running tests
- Run from the repository root:
  `build/release/test/unittest test/sql/window/test_window_clause.test`.
  Several files or Catch patterns can be given (`"test/sql/window/*"`).
  `.test_slow` files only run when named explicitly.
- A test that `require`s something missing (an extension that is not built,
  a different vector size) is skipped, not failed: read the summary at the end.
- The shell is handy for reproducing reports: `build/release/duckdb` (in
  memory) or `build/release/duckdb /tmp/x.db`; `EXPLAIN ANALYZE` shows the
  filters each scan received and `Row Groups Scanned: n / m`.

## Writing sqllogictests
- `statement ok` / `statement error` (expected message after `----`) and
  `query <column types>` with expected rows (tab separated) after `----`.
- `{TEST_DIR}` is a per-run scratch directory, `{DATA_DIR}` the test data root;
  `load <file>` opens a database file and `restart` reopens it (replaying the
  WAL), which is how persistence behaviour is tested.
- Plans are checked with `query II` over `EXPLAIN`/`EXPLAIN ANALYZE` and an
  expected value like `analyzed_plan	<REGEX>:.*Row Groups Scanned: 1 / 3.*`
  (`<!REGEX>:` for "must not match").
- Every bug fix comes with a test next to the existing tests of that feature.

## Conventions
- C++17, tabs, clang-format style of the surrounding code. Ownership through
  `unique_ptr`/`make_uniq` and `shared_ptr`/`make_shared_ptr`; downcasts with
  `expr.Cast<T>()`; errors are thrown as typed exceptions (`BinderException`,
  `IOException`, ...).
- Storage keeps per-row-group column statistics (min/max, null information);
  the optimizer propagates statistics through expressions and operators, and
  scans compare their pushed-down filters with the statistics to skip row
  groups.
