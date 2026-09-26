# Eval run — anthropic / retrieval

_2026-09-26T06:10:22+00:00 · model `(backend default)` · 1 cases_

## Headline

| Metric | Value |
|---|---|
| Verdict accuracy | **100%** (1/1) |
| Majority-class baseline | 100% (always BUG) |
| Evidence-anchor recall | **67%** (2/3) |
| Grounded-citation rate | 100% |
| Runs needing a gate retry | 100% |
| Mean latency | 46.8s |
| p95 latency | 46.8s |
| Cost per case | $0.0589 |
| **Cost per correct verdict** | **$0.0589** |
| Total | $0.0589 |

## Where it stopped

| Stop reason | Cases |
|---|---:|
| `gate_exhausted` | 1 |

## Confusion

| expected \ predicted | BUG | CR | NEEDS_INFO |
|---|---:|---:|---:|
| **BUG** | 1 | 0 | 0 |
| **CR** | 0 | 0 | 0 |
| **NEEDS_INFO** | 0 | 0 | 0 |

## Per case

| Case | Expected | Predicted | Anchors | Stop | Conf | Steps | Latency | Cost |
|---|---|---|---:|---|---:|---:|---:|---:|
| EV-001 | BUG | BUG | 2/3 | `gate_exhausted` | 0.45 | 4 | 46.8s | $0.0589 |

## Failures

None.

### Right verdict, wrong reasons

- **EV-001** missed code:data/code/custom/nwb_statement_builder.py.
