# Eval run — anthropic / retrieval

_2026-09-26T05:33:37+00:00 · model `(backend default)` · 16 cases_

## Headline

| Metric | Value |
|---|---|
| Verdict accuracy | **12%** (2/16) |
| Majority-class baseline | 38% (always CR) |
| Evidence-anchor recall | **45%** (9/20) |
| Grounded-citation rate | 100% |
| Runs needing a gate retry | 56% |
| Mean latency | 312.0s |
| p95 latency | 1677.3s |
| Cost per case | $0.0461 |
| **Cost per correct verdict** | **$0.3689** |
| Total | $0.7377 |

## Where it stopped

| Stop reason | Cases |
|---|---:|
| `gate_exhausted` | 7 |
| `error` | 4 |
| `step_budget` | 3 |
| `timeout` | 1 |
| `verdict` | 1 |

## Confusion

| expected \ predicted | BUG | CR | NEEDS_INFO |
|---|---:|---:|---:|
| **BUG** | 0 | 0 | 5 |
| **CR** | 0 | 1 | 5 |
| **NEEDS_INFO** | 0 | 0 | 1 |

## Per case

| Case | Expected | Predicted | Anchors | Stop | Conf | Steps | Latency | Cost |
|---|---|---|---:|---|---:|---:|---:|---:|
| EV-001 ⚠ | BUG | NEEDS_INFO | 2/3 | `gate_exhausted` | 0.45 | 5 | 70.2s | $0.0717 |
| EV-002 ⚠ | BUG | NEEDS_INFO | 0/2 | `gate_exhausted` | 0.25 | 6 | 55.9s | $0.0727 |
| EV-003 ⚠ | BUG | NEEDS_INFO | 0/3 | `timeout` | 0.25 | 2 | 143.1s | $0.0103 |
| EV-004 ⚠ | BUG | NEEDS_INFO | 0/2 | `step_budget` | 0.35 | 8 | 43.8s | $0.0757 |
| EV-005 ⚠ | BUG | NEEDS_INFO | 1/2 | `gate_exhausted` | 0.35 | 6 | 42.2s | $0.0685 |
| EV-006 ⚠ | CR | NEEDS_INFO | 1/2 | `gate_exhausted` | 0.45 | 4 | 49.2s | $0.0468 |
| EV-007 ⚠ | CR | NEEDS_INFO | 1/1 | `gate_exhausted` | 0.45 | 3 | 28.4s | $0.0380 |
| EV-008 ⚠ | CR | NEEDS_INFO | 1/1 | `gate_exhausted` | 0.35 | 4 | 22.3s | $0.0376 |
| EV-009 ⚠ | CR | NEEDS_INFO | 1/1 | `step_budget` | 0.45 | 8 | 40.7s | $0.0902 |
| EV-010 ⚠ | CR | NEEDS_INFO | 1/1 | `gate_exhausted` | 0.35 | 5 | 28.9s | $0.0459 |
| EV-011 | CR | CR | 0/1 | `verdict` | 0.60 | 8 | 46.5s | $0.0777 |
| EV-012 | NEEDS_INFO | NEEDS_INFO | 1/1 | `step_budget` | 0.35 | 8 | 56.3s | $0.1025 |
| EV-013 ⚠ | NEEDS_INFO | ERROR | 0/0 | `error` | 0.00 | 0 | 1677.3s | — |
| EV-014 ⚠ | NEEDS_INFO | ERROR | 0/0 | `error` | 0.00 | 0 | 1.4s | — |
| EV-015 ⚠ | NEEDS_INFO | ERROR | 0/0 | `error` | 0.00 | 0 | 1.4s | — |
| EV-016 ⚠ | NEEDS_INFO | ERROR | 0/0 | `error` | 0.00 | 0 | 2683.9s | — |

## Failures

- **EV-001** expected BUG, got NEEDS_INFO (stop `gate_exhausted`, anchors 2/3, missed config:STMT_EXCLUDE_DROPPED_AUTHS). Trace `NWB-4471-20260926T041025-df3c9f`.
- **EV-002** expected BUG, got NEEDS_INFO (stop `gate_exhausted`, anchors 0/2, missed son:SON-001#3.2, code:data/code/custom/nwb_statement_builder.py). Trace `NWB-4519-20260926T041135-57a7ff`.
- **EV-003** expected BUG, got NEEDS_INFO (stop `timeout`, anchors 0/3, missed son:SON-001#3.5, config:STMT_MAX_LINES_PER_PAGE, code:data/code/custom/nwb_statement_builder.py). Trace `NWB-4525-20260926T041231-314c58`.
- **EV-004** expected BUG, got NEEDS_INFO (stop `step_budget`, anchors 0/2, missed son:SON-002#3.3, code:data/code/custom/nwb_fee_engine.py). Trace `NWB-4537-20260926T041454-a99739`.
- **EV-005** expected BUG, got NEEDS_INFO (stop `gate_exhausted`, anchors 1/2, missed config:STMT_EXCLUDE_DROPPED_AUTHS). Trace `NWB-4531-20260926T041538-68b16e`.
- **EV-006** expected CR, got NEEDS_INFO (stop `gate_exhausted`, anchors 1/2, missed config:FEE_WAIVER_SENIOR_TIER_ENABLED). Trace `NWB-4488-20260926T041620-37c0a2`.
- **EV-007** expected CR, got NEEDS_INFO (stop `gate_exhausted`, anchors 1/1). Trace `NWB-4544-20260926T041710-02ce18`.
- **EV-008** expected CR, got NEEDS_INFO (stop `gate_exhausted`, anchors 1/1). Trace `NWB-4550-20260926T041738-d8070b`.
- **EV-009** expected CR, got NEEDS_INFO (stop `step_budget`, anchors 1/1). Trace `NWB-4558-20260926T041800-25df43`.
- **EV-010** expected CR, got NEEDS_INFO (stop `gate_exhausted`, anchors 1/1). Trace `NWB-4562-20260926T041841-10484b`.
- **EV-013** expected NEEDS_INFO, got ERROR (stop `error`, anchors 0/0). Trace `None`.
- **EV-014** expected NEEDS_INFO, got ERROR (stop `error`, anchors 0/0). Trace `None`.
- **EV-015** expected NEEDS_INFO, got ERROR (stop `error`, anchors 0/0). Trace `None`.
- **EV-016** expected NEEDS_INFO, got ERROR (stop `error`, anchors 0/0). Trace `None`.

### Right verdict, wrong reasons

- **EV-011** missed son:SON-001#5.

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
