# Eval run — anthropic / retrieval

_2026-09-28T20:02:20+00:00 · 16 cases_

Model requested: `claude-haiku-4-5` · served by: `claude-haiku-4-5-20251001`

## Headline

| Metric | Value |
|---|---|
| Verdict accuracy | **94%** (15/16) |
| Majority-class baseline | 38% (always CR) |
| Evidence-anchor recall | **91%** (21/23) |
| Grounded-citation rate | 100% |
| Runs needing a gate retry | 44% |
| Mean latency | 33.6s |
| p95 latency | 46.6s |
| Cost per case | $0.0560 |
| **Cost per correct verdict** | **$0.0598** |
| Total | $0.8964 |

## Where it stopped

| Stop reason | Cases |
|---|---:|
| `verdict` | 12 |
| `step_budget` | 3 |
| `gate_exhausted` | 1 |

## Confusion

| expected \ predicted | BUG | CR | NEEDS_INFO |
|---|---:|---:|---:|
| **BUG** | 5 | 0 | 0 |
| **CR** | 0 | 5 | 1 |
| **NEEDS_INFO** | 0 | 0 | 5 |

## Per case

| Case | Expected | Predicted | Anchors | Stop | Conf | Steps | Latency | Cost |
|---|---|---|---:|---|---:|---:|---:|---:|
| EV-001 | BUG | BUG | 3/3 | `verdict` | 0.90 | 3 | 46.6s | $0.0270 |
| EV-002 | BUG | BUG | 1/2 | `verdict` | 0.60 | 5 | 24.3s | $0.0453 |
| EV-003 | BUG | BUG | 3/3 | `verdict` | 0.70 | 6 | 19.3s | $0.0403 |
| EV-004 | BUG | BUG | 2/2 | `verdict` | 0.60 | 7 | 25.5s | $0.0541 |
| EV-005 | BUG | BUG | 2/2 | `verdict` | 0.80 | 4 | 23.8s | $0.0344 |
| EV-006 | CR | CR | 1/2 | `verdict` | 0.60 | 4 | 31.6s | $0.0322 |
| EV-007 | CR | CR | 1/1 | `verdict` | 0.60 | 3 | 16.8s | $0.0256 |
| EV-008 | CR | CR | 1/1 | `verdict` | 0.60 | 7 | 32.7s | $0.0708 |
| EV-009 ⚠ | CR | NEEDS_INFO | 1/1 | `gate_exhausted` | 0.45 | 6 | 62.7s | $0.0806 |
| EV-010 | CR | CR | 1/1 | `verdict` | 0.50 | 6 | 23.9s | $0.0433 |
| EV-011 | CR | CR | 1/1 | `verdict` | 0.60 | 5 | 28.1s | $0.0467 |
| EV-012 | NEEDS_INFO | NEEDS_INFO | 1/1 | `verdict` | 0.80 | 8 | 45.4s | $0.0855 |
| EV-013 | NEEDS_INFO | NEEDS_INFO | 1/1 | `verdict` | 0.80 | 5 | 31.5s | $0.0457 |
| EV-014 | NEEDS_INFO | NEEDS_INFO | 1/1 | `step_budget` | 0.45 | 8 | 41.9s | $0.0786 |
| EV-015 | NEEDS_INFO | NEEDS_INFO | 0/0 | `step_budget` | 0.45 | 8 | 42.4s | $0.1001 |
| EV-016 | NEEDS_INFO | NEEDS_INFO | 1/1 | `step_budget` | 0.25 | 8 | 40.4s | $0.0861 |

## Failures

- **EV-009** expected CR, got NEEDS_INFO (stop `gate_exhausted`, anchors 1/1). Trace `NWB-4558-20260928T195704-0e23be`.

### Right verdict, wrong reasons

- **EV-002** missed code:data/code/custom/nwb_statement_builder.py.
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
