# Pilot rollouts on atlas-commerce

Every model run behind the figure in the top-level README: the eight systems shown there, two cold runs per ticket and five to nine warm sequences each.

| File | What |
|---|---|
| `trajectories/<system>.tgz` | the agents' full trajectories (Harbor ATIF `trajectory.json`, one per ticket attempt), compressed per system; `kimi-cold` and `kimi-warm` are Kimi K3 |
| `segments.csv` | one row per ticket attempt (497 rows): job, trial, ordering, position, ticket, model, whether the session was resumed, reward, model calls, tokens, cost, time, exception, egress counts; produced by `odyssey account` |
| `ratios.json` | warm ÷ cold by position and pooled over positions 2+, with 95% bootstrap intervals, per system; produced by `scripts/analysis/poc_ratios.py` |

Jobs ending in `-cold1` and `-cold2` are the two cold passes; `-warm` jobs are the rotated orderings with the session resumed. Rows whose `exception` names a provider rate limit belong to sequences the analysis drops. The `AKIAIOSFODNN7EXAMPLE` key that appears in some trajectories is the AWS documentation example key from the sample event in the repository, not a credential.
