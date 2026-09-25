---
son_id: SON-002
client_id: NWB
client_name: Northwind Bank
title: Fee Schedule and Waivers
version: "1.7"
effective_from: "2026-01-01"
owner: Cards Product — Revenue Workstream
status: Approved
---

# SON-002 — Fee Schedule and Waivers

## 1. Scope

1.1 This Statement of Need defines the fees charged on Northwind Bank debit card
accounts and the conditions under which those fees are waived.

1.2 In scope: the monthly maintenance fee, out-of-network ATM fees, and the waiver
evaluation performed at each statement cycle close.

1.3 Not in scope: interchange, chargeback handling fees, and any credit product.

## 2. Definitions

2.1 **Cycle** — the statement period as derived by SON-003.

2.2 **Average balance** — the arithmetic mean of end-of-day ledger balances across the
cycle, in minor units (pence).

2.3 **Qualifying salary credit** — a single inbound credit in the cycle carrying an
originator category of `SALARY` or `PENSION`.

2.4 **Waiver** — the suppression of a fee that would otherwise be charged. A waiver is
recorded against the cycle even when the fee amount is zero.

## 3. Fee rules

### 3.1 Monthly maintenance fee

3.1.1 A monthly maintenance fee of £4.00 is assessed at cycle close on every open
account, subject to the waiver rules in 3.2.

### 3.2 Maintenance fee waiver conditions

3.2.1 The monthly maintenance fee MUST be waived when **either** of the following holds
for the cycle:

- (a) average balance is greater than or equal to `FEE_WAIVER_MIN_AVG_BALANCE`
  (NWB value: 250000 minor units, i.e. £2,500.00); **or**
- (b) the account received at least one qualifying salary credit of at least
  `FEE_SALARY_CREDIT_MIN` (NWB value: 150000 minor units, i.e. £1,500.00).

3.2.2 Conditions (a) and (b) are evaluated independently and are not cumulative. Meeting
both grants one waiver, not two.

### 3.3 Out-of-network ATM fee

3.3.1 A fee of £1.50 is assessed per out-of-network ATM withdrawal.

3.3.2 This fee is waived for the first two qualifying withdrawals per cycle.

### 3.4 Waiver limits

3.4.1 No account may receive more than `FEE_WAIVER_MAX_PER_CYCLE` waivers of any type in
a single cycle. The NWB value is 3.

3.4.2 When the limit is reached, further eligible fees are charged and the reason code
`WAIVER_LIMIT_REACHED` is recorded against the fee.

## 4. Configuration parameters referenced

| Parameter | Type | NWB value | Governs |
|-----------|------|-----------|---------|
| `FEE_WAIVER_MIN_AVG_BALANCE` | int | 250000 | Rule 3.2.1(a) |
| `FEE_SALARY_CREDIT_MIN`      | int | 150000 | Rule 3.2.1(b) |
| `FEE_WAIVER_MAX_PER_CYCLE`   | int | 3      | Rule 3.4.1 |

## 5. Out of scope

5.1 **Relationship-tier waivers.** Waivers granted on the basis of a customer's product
holding tier (Silver / Gold / Platinum) are explicitly out of scope for Phase 1. No
tier attribute is carried on the fee evaluation input, and no configuration parameter
exists to enable tier-based logic.

5.2 **Age-based (senior citizen) waivers.** Waivers granted on the basis of cardholder
age are explicitly out of scope for Phase 1. Any request to introduce them is a change
request and requires Product and Compliance sign-off, because age-based pricing carries
fair-treatment obligations that Phase 1 has not assessed.

5.3 Promotional and campaign-driven waivers.

5.4 Retrospective refund of fees already charged in a closed cycle.

## 6. Change history

| Version | Date | Change |
|---------|------|--------|
| 1.7 | 2026-01-01 | Clarified 3.2.2 (conditions are not cumulative) after a client query. |
| 1.6 | 2025-08-11 | Added the salary credit waiver condition 3.2.1(b) and `FEE_SALARY_CREDIT_MIN`. |
| 1.5 | 2025-03-02 | Added 5.1 and 5.2 to record tier and age waivers as out of scope for Phase 1. |
| 1.0 | 2024-10-05 | Initial approved version. |
