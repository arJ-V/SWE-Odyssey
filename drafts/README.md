# Drafts

Task sets and notes that are not part of the finalized benchmark. They were used to develop
the method and some have pilot results, but none is presented as finished. The finalized
benchmark is [`sequences/atlas-commerce`](../sequences/atlas-commerce/).

| Path | What it is | State |
|---|---|---|
| [`sequences/httrack-2026/`](sequences/httrack-2026/) | 10 tickets from fixes merged to HTTrack (C) in July 2026, via SWE-bench-Live/MultiLang | built and machine-verified; piloted on six tickets |
| [`sequences/duckdb-2026/`](sequences/duckdb-2026/) | 11 tickets from fixes merged to DuckDB (C++) in July 2026 | built and machine-verified; piloted with one system |
| [`sequences/toy-ledger/`](sequences/toy-ledger/) | a tiny synthetic Python repo | plumbing fixture used by the unit tests, not a benchmark |
| [`docs/REAL_SEQUENCE_GUIDE.md`](docs/REAL_SEQUENCE_GUIDE.md) | recipe for building a sequence from a real repository's merged fixes | |
| [`docs/TOOLING.md`](docs/TOOLING.md) | the general `odyssey` tool: quickstart, compile-time arms, network allowlist | |

The code-repository drafts are run with the general tool rather than the atlas scripts:

```sh
uv run python -m odyssey.cli validate drafts/sequences/toy-ledger --repeat 3
uv run python -m odyssey.cli compile drafts/sequences/httrack-2026 --out build/tasks --design williams --egress allowlist
harbor run -p build/tasks/<task> -a claude-code -m anthropic/<model> --resume-trajectory
uv run python -m odyssey.cli account jobs/ --out segments.csv
uv run python -m odyssey.cli analyze segments.csv
```

`--egress allowlist` limits the agent's container to the model API, so it cannot fetch the
upstream fix. The upstream projects keep their own licences (HTTrack: GPL-3.0; DuckDB: MIT); see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Scripts that serve only these drafts live under `scripts/`: `real_sequence/` (building a
sequence from a real repository), `egress/` (allowlist checks), `open_models/` (open-weight
model runs) and `host_smoke/` (agent smoke runs on the host). The unit tests import
`egress/` and `open_models/`.
