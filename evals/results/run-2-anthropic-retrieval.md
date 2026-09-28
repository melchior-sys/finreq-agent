# Eval run — anthropic / retrieval

_2026-09-26T06:37:25+00:00 · model `(backend default)` · 16 cases_

## Headline

| Metric | Value |
|---|---|
| Verdict accuracy | **88%** (14/16) |
| Majority-class baseline | 38% (always CR) |
| Evidence-anchor recall | **87%** (20/23) |
| Grounded-citation rate | 100% |
| Runs needing a gate retry | 50% |
| Mean latency | 88.5s |
| p95 latency | 111.9s |
| Cost per case | $0.0644 |
| **Cost per correct verdict** | **$0.0736** |
| Total | $1.0309 |

## Where it stopped

| Stop reason | Cases |
|---|---:|
| `verdict` | 9 |
| `gate_exhausted` | 3 |
| `step_budget` | 3 |
| `timeout` | 1 |

## Confusion

| expected \ predicted | BUG | CR | NEEDS_INFO |
|---|---:|---:|---:|
| **BUG** | 5 | 0 | 0 |
| **CR** | 0 | 4 | 2 |
| **NEEDS_INFO** | 0 | 0 | 5 |

## Per case

| Case | Expected | Predicted | Anchors | Stop | Conf | Steps | Latency | Cost |
|---|---|---|---:|---|---:|---:|---:|---:|
| EV-001 | BUG | BUG | 2/3 | `verdict` | 0.70 | 6 | 44.5s | $0.0635 |
| EV-002 | BUG | BUG | 2/2 | `gate_exhausted` | 0.45 | 7 | 49.4s | $0.0998 |
| EV-003 | BUG | BUG | 2/3 | `verdict` | 0.70 | 6 | 22.3s | $0.0390 |
| EV-004 | BUG | BUG | 2/2 | `verdict` | 0.60 | 7 | 801.6s | $0.0493 |
| EV-005 | BUG | BUG | 2/2 | `verdict` | 0.90 | 4 | 24.1s | $0.0362 |
| EV-006 | CR | CR | 1/2 | `verdict` | 0.60 | 3 | 18.7s | $0.0192 |
| EV-007 | CR | CR | 1/1 | `gate_exhausted` | 0.25 | 6 | 39.7s | $0.0708 |
| EV-008 | CR | CR | 1/1 | `verdict` | 0.60 | 5 | 34.7s | $0.0408 |
| EV-009 ⚠ | CR | NEEDS_INFO | 1/1 | `gate_exhausted` | 0.45 | 6 | 41.2s | $0.0657 |
| EV-010 | CR | CR | 1/1 | `verdict` | 0.70 | 4 | 14.5s | $0.0242 |
| EV-011 ⚠ | CR | NEEDS_INFO | 1/1 | `timeout` | 0.35 | 7 | 111.9s | $0.0638 |
| EV-012 | NEEDS_INFO | NEEDS_INFO | 1/1 | `step_budget` | 0.45 | 8 | 37.5s | $0.0946 |
| EV-013 | NEEDS_INFO | NEEDS_INFO | 1/1 | `step_budget` | 0.25 | 8 | 45.2s | $0.0981 |
| EV-014 | NEEDS_INFO | NEEDS_INFO | 1/1 | `step_budget` | 0.55 | 8 | 45.0s | $0.0745 |
| EV-015 | NEEDS_INFO | NEEDS_INFO | 0/0 | `verdict` | 0.80 | 8 | 43.6s | $0.1006 |
| EV-016 | NEEDS_INFO | NEEDS_INFO | 1/1 | `verdict` | 0.60 | 7 | 42.6s | $0.0908 |

## Failures

- **EV-009** expected CR, got NEEDS_INFO (stop `gate_exhausted`, anchors 1/1). Trace `NWB-4558-20260926T063104-82ae5b`.
- **EV-011** expected CR, got NEEDS_INFO (stop `timeout`, anchors 1/1). Trace `NWB-4571-20260926T063159-7347b2`.

### Right verdict, wrong reasons

- **EV-001** missed code:data/code/custom/nwb_statement_builder.py.
- **EV-003** missed code:data/code/custom/nwb_statement_builder.py.
- **EV-006** missed config:FEE_WAIVER_SENIOR_TIER_ENABLED.

## Retrieval, held-out queries

Written without reference to the calibration set or the floor it produced. The numbers in `retrieval_floor.md` are training accuracy; these are not.

| Metric | Held out |
|---|---|
| Strict recall @4 | 60% |
| Document recall @4 | 90% |
| False admits | 10% |
| Lowest relevant score | 0.5847 |
| Highest irrelevant score | 0.6455 |
| **Floor margin** | **-0.0155** |
