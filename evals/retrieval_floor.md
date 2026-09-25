# Retrieval score floor

_Measured 2026-09-25 — 12 relevant and 12 irrelevant queries against the 28-chunk SON corpus. Regenerate with `python -m evals.calibrate_retrieval --write`._

## Why a floor is needed at all

BGE cosine scores occupy a narrow high band: unrelated text still scores around 0.60. A floor chosen by intuition (0.3, say) admits everything, and a retriever that always returns something cannot support a verdict that rests on the spec being silent. The number below is the separation point actually observed.

## Result

- Lowest score among relevant queries: **0.5865**
- Highest score among irrelevant queries: **0.6273**
- Separation: **-0.0408** (the two bands overlap)
- **Chosen floor: 0.63** — strict recall 83%, document recall 100%, false admits 0%
- **Floor margin: +0.0027** — how far the floor sits above the best-scoring irrelevant query. Thin margins mean one new off-topic query could start clearing the floor, so this is tracked rather than assumed.

The bands overlap, so no floor separates relevant from irrelevant perfectly and the choice is a trade, not an optimum. It is made in favour of zero false admits: a chunk above the floor reaches the agent as a retrieved requirement, and the evidence gate will accept a verbatim quote from it, so an off-topic chunk is a path to a confidently wrong verdict that passes every check. A miss degrades to NEEDS_INFO, which is recoverable.

## Relevant queries

| Query | Expected | Score | Rank | Top hit |
|---|---|---:|---:|---|
| when does the statement period close each month | `SON-003#3.1` | 0.5865 | 15 | `SON-003#2` (0.7210) |
| statement close falls on a bank holiday | `SON-003#3.3` | 0.6170 | 4 | `SON-003#2` (0.6458) |
| senior citizen customers should not be charged the monthly fee | `SON-002#5` | 0.6609 | 1 | `SON-002#5` (0.6609) |
| charge for using another bank cash machine | `SON-002#3.3` | 0.6793 | 1 | `SON-002#3.3` (0.6793) |
| cancelled cash withdrawals appear as duplicate debits | `SON-001#3.2` | 0.6961 | 1 | `SON-001#3.2` (0.6961) |
| pending transactions should show in a separate memo area | `SON-001#3.3` | 0.7366 | 1 | `SON-001#3.3` (0.7366) |
| monthly fee waiver when salary is paid in | `SON-002#3.2` | 0.7701 | 1 | `SON-002#3.2` (0.7701) |
| how many transaction rows print on each statement page | `SON-001#3.5` | 0.7828 | 1 | `SON-001#3.5` (0.7828) |
| what does it mean when an authorisation is dropped | `SON-001#2` | 0.7885 | 1 | `SON-001#2` (0.7885) |
| dropped authorizations showing on ATM statement | `SON-001#3.2` | 0.7894 | 1 | `SON-001#3.2` (0.7894) |
| which timezone is the daily cut off applied in | `SON-003#3.4` | 0.7939 | 1 | `SON-003#3.4` (0.7939) |
| maximum number of fee waivers allowed in one cycle | `SON-002#3.4` | 0.8548 | 1 | `SON-002#3.4` (0.8548) |

## Irrelevant queries (best score anywhere in the corpus)

| Query | Closest chunk | Score |
|---|---|---:|
| what is the interest rate on savings accounts | `SON-002#3.1` | 0.6273 |
| train times from London to Manchester | `SON-003#3.4` | 0.6167 |
| open a new business current account | `SON-002#3.1` | 0.5921 |
| apply for a mortgage on a second property | `SON-001#3.2` | 0.5911 |
| raise a chargeback dispute against a merchant | `SON-002#5` | 0.5731 |
| my debit card was stolen, how do I report it | `SON-001#6` | 0.5729 |
| how do I reset my online banking password | `SON-001#4` | 0.5655 |
| order a replacement PIN for my card | `SON-001#6` | 0.5626 |
| update my registered home address | `SON-003#6` | 0.5559 |
| what is the weather forecast for tomorrow | `SON-003#3.4` | 0.5306 |
| kubernetes pod keeps restarting after deploy | `SON-001#6` | 0.4713 |
| recipe for banana bread | `SON-001#6` | 0.4336 |

## Floor sweep

Strict recall @4 = the labelled chunk came back. Document recall @4 = something from the right SON came back.

| Floor | Strict recall @4 | Document recall @4 | False admits |
|---:|---:|---:|---:|
| 0.55 | 92% | 100% | 75% |
| 0.56 | 92% | 100% | 67% |
| 0.57 | 92% | 100% | 50% |
| 0.58 | 92% | 100% | 33% |
| 0.59 | 92% | 100% | 33% |
| 0.60 | 92% | 100% | 17% |
| 0.61 | 92% | 100% | 17% |
| 0.62 | 83% | 100% | 8% |
| 0.63 | 83% | 100% | 0%  **<- chosen** |
| 0.64 | 83% | 100% | 0% |
| 0.65 | 83% | 92% | 0% |
| 0.66 | 83% | 92% | 0% |
| 0.67 | 75% | 83% | 0% |
| 0.68 | 67% | 75% | 0% |
| 0.69 | 67% | 75% | 0% |
| 0.70 | 58% | 67% | 0% |
| 0.71 | 58% | 67% | 0% |
| 0.72 | 58% | 67% | 0% |
| 0.73 | 58% | 58% | 0% |
| 0.74 | 50% | 50% | 0% |
| 0.75 | 50% | 50% | 0% |
| 0.76 | 50% | 50% | 0% |
| 0.77 | 50% | 50% | 0% |
| 0.78 | 42% | 42% | 0% |
| 0.79 | 17% | 17% | 0% |
| 0.80 | 8% | 8% | 0% |
| 0.81 | 8% | 8% | 0% |
| 0.82 | 8% | 8% | 0% |
| 0.83 | 8% | 8% | 0% |
| 0.84 | 8% | 8% | 0% |
| 0.85 | 8% | 8% | 0% |
| 0.86 | 0% | 0% | 0% |
| 0.87 | 0% | 0% | 0% |
| 0.88 | 0% | 0% | 0% |
| 0.89 | 0% | 0% | 0% |
| 0.90 | 0% | 0% | 0% |
| 0.91 | 0% | 0% | 0% |

## What the strict misses actually return

- `when does the statement period close each month` wanted `SON-003#3.1` (rank 15, 0.5865) and got `SON-003#2`, `SON-003#1`, `SON-003#3.2`, `SON-003#6`.
- `statement close falls on a bank holiday` wanted `SON-003#3.3` (rank 4, 0.6170) and got `SON-003#2`.

Both misses are questions the definitions section of the same document also answers, which is why document recall is the higher number. The labels are left strict rather than widened to sets: a label that moves to match the result measures nothing.

## Is the BGE query prefix worth applying?

`fastembed.query_embed` does not apply it for this model — it returns a vector identical to `embed` — so `src.rag.embed_query` prepends it by hand. Measured both ways:

| | Mean relevant score | Max irrelevant score | Separation |
|---|---:|---:|---:|
| With prefix | 0.7297 | 0.6273 | -0.0408 |
| Without prefix | 0.7258 | 0.6663 | -0.0946 |

## Planned for slice 5

- **Held-out queries.** Add roughly ten relevant and ten irrelevant queries that were not used to pick this floor, and report their numbers separately. The floor above is fitted to the set on this page, so those figures are training accuracy: they say how well the threshold describes the queries it was chosen from, not how it behaves on a query it has never seen.
- **Floor margin as a tracked metric.** Report `floor - max irrelevant score` on every eval run (currently +0.0027). It is the early warning: it shrinks silently as documents are added, and it reaches zero before any recall number moves.

