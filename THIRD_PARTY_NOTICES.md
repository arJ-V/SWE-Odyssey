# Third-party notices

The repository's own code and the `atlas-commerce` task are under the MIT licence in
[LICENSE](LICENSE). The draft task sets under `drafts/` contain material from other projects,
which keeps its own licence.

| Where | What | Licence |
|---|---|---|
| `drafts/sequences/httrack-2026/` : every `gold.patch`, `environment/base.patch`, `source/fixups/`, and the files under `tickets/*/tests/files/` | Fixes and tests taken from [HTTrack](https://github.com/xroche/httrack), Copyright (C) 1998 Xavier Roche and other contributors | GPL-3.0-or-later; text in `drafts/sequences/httrack-2026/LICENSE-HTTRACK` |
| `drafts/sequences/duckdb-2026/` : every `gold.patch`, `environment/base.patch`, `source/fixups/`, and the files under `tickets/*/tests/files/` | Fixes and tests taken from [DuckDB](https://github.com/duckdb/duckdb), Copyright 2018-2026 Stichting DuckDB Foundation | MIT; text in `drafts/sequences/duckdb-2026/LICENSE-DUCKDB` |
| `drafts/sequences/*/source/*/original_issue.md` | Issue reports as filed on the upstream projects' trackers | written by their respective authors; reproduced for reference |
| ticket selection and test lists for both draft sets | derived from [SWE-bench-Live / MultiLang](https://github.com/microsoft/SWE-bench-Live) | MIT |
| `odyssey/templates/egress/gost.yaml` | sidecar proxy configuration taken from the Harbor 0.22 agent framework | Harbor's licence |

Container images, Python and npm packages are pinned by version and installed at build time;
none is included in this repository.
