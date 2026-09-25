---
son_id: SON-003
client_id: NWB
client_name: Northwind Bank
title: Statement Cycle and Period Definition
version: "1.2"
effective_from: "2025-07-01"
owner: Cards Product — Statements Workstream
status: Approved
---

# SON-003 — Statement Cycle and Period Definition

## 1. Scope

1.1 This Statement of Need defines how the start and end dates of a statement cycle are
derived for Northwind Bank card accounts.

1.2 In scope: the cycle anchor, short-month handling, non-working-day roll behaviour, and
the timezone and cut-off applied to period boundaries.

1.3 Not in scope: what appears on the statement (see SON-001) and fee assessment timing
(see SON-002).

## 2. Definitions

2.1 **Anchor day** — the nominal day-of-month on which a cycle closes.

2.2 **Cycle close** — the instant at which the period ends and statement generation
becomes eligible to run.

2.3 **Non-working day** — a Saturday, a Sunday, or a date listed in the England and Wales
bank holiday calendar.

## 3. Cycle derivation rules

### 3.1 Anchor day

3.1.1 The anchor day is given by `STMT_CYCLE_ANCHOR_DAY`. The NWB value is 15.

3.1.2 A cycle runs from the day after the previous cycle close to the current cycle
close, inclusive of both boundary days at the cut-off defined in 3.4.

### 3.2 Short-month handling

3.2.1 Where the anchor day does not exist in the target month — anchor 29, 30 or 31 in a
month with fewer days — the cycle closes on the **last calendar day of that month**.

3.2.2 The adjustment in 3.2.1 is applied before the non-working-day roll in 3.3.

### 3.3 Non-working-day roll

3.3.1 Where a computed cycle close falls on a non-working day, the close MUST roll
according to `STMT_CYCLE_ROLL_RULE`:

- `FORWARD` — roll to the next working day.
- `BACKWARD` — roll to the previous working day.
- `NONE` — do not roll.

3.3.2 The NWB value is `FORWARD`.

3.3.3 A roll MUST NOT push a cycle close past the anchor of the following month. Where it
would, the close is held on the anchor date without rolling.

### 3.4 Timezone and cut-off

3.4.1 All cycle boundaries are evaluated in the timezone given by `STMT_TIMEZONE`. The
NWB value is `Europe/London`.

3.4.2 The cut-off instant is 23:59:59.999 local time on the close date.

3.4.3 Activity timestamped after the cut-off belongs to the following cycle, irrespective
of the acquirer's own date stamp.

## 4. Configuration parameters referenced

| Parameter | Type | NWB value | Governs |
|-----------|------|-----------|---------|
| `STMT_CYCLE_ANCHOR_DAY` | int    | 15            | Rule 3.1.1 |
| `STMT_CYCLE_ROLL_RULE`  | string | FORWARD       | Rule 3.3.1 |
| `STMT_TIMEZONE`         | string | Europe/London | Rule 3.4.1 |

## 5. Out of scope

5.1 Mid-cycle anchor changes requested by the cardholder.

5.2 Multiple concurrent cycles on a single account.

5.3 Cycle derivation for accounts in collections.

## 6. Change history

| Version | Date | Change |
|---------|------|--------|
| 1.2 | 2025-07-01 | Added 3.3.3 to prevent a roll crossing the following anchor. |
| 1.1 | 2025-01-19 | Added the timezone and cut-off rules (3.4). |
| 1.0 | 2024-08-30 | Initial approved version. |
