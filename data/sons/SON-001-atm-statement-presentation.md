---
son_id: SON-001
client_id: NWB
client_name: Northwind Bank
title: ATM Statement Presentation
version: "2.3"
effective_from: "2025-11-01"
owner: Cards Product — Statements Workstream
status: Approved
---

# SON-001 — ATM Statement Presentation

## 1. Scope

1.1 This Statement of Need defines how card authorisation activity is presented on
**ATM statements** produced by the Northwind Bank (NWB) card platform, for both paper
and PDF channels.

1.2 In scope: statement line selection, ordering, the memo block, and the configuration
parameters that govern them.

1.3 Not in scope: POS and e-commerce statement presentation (see SON-011), statement
cycle date derivation (see SON-003), and fee lines (see SON-002).

## 2. Definitions

2.1 **Authorisation** — a hold placed on a cardholder's available balance at the time of
a transaction request, prior to clearing.

2.2 **Authorisation lifecycle states.** Every authorisation record carries exactly one
status:

| Status    | Meaning |
|-----------|---------|
| APPROVED  | Authorisation granted, awaiting clearing advice. |
| SETTLED   | Clearing advice received and matched; funds moved. |
| DROPPED   | No matching clearing advice received within the drop window; the hold has been released. |
| EXPIRED   | The authorisation aged out of the acquirer window without ever being presented. |
| REVERSED  | Explicitly reversed by the acquirer or by NWB operations. |

2.3 **Drop window** — the number of days an APPROVED authorisation waits for a matching
advice before the platform transitions it to DROPPED. Governed by the configuration
parameter `AUTH_DROP_WINDOW_DAYS`. The NWB value is 7 days.

2.4 **Statement line** — a printed row in the transaction table of the statement,
representing money that has moved or is contractually committed.

2.5 **Memo block** — a separate, clearly labelled section beneath the transaction table
used for informational items that are *not* statement lines.

## 3. Statement content rules

### 3.1 Settled activity

3.1.1 Every authorisation in SETTLED state with a `settled_ts` falling inside the
statement period MUST appear as a statement line.

3.1.2 Statement lines MUST be ordered by `settled_ts` ascending.

### 3.2 Exclusion of non-settling authorisations

3.2.1 Authorisations in **DROPPED** or **EXPIRED** state MUST NOT appear as statement
lines on ATM statements. These authorisations represent money that never left the
cardholder's account, and presenting them has repeatedly been read by cardholders as a
duplicate debit.

3.2.2 The exclusion in 3.2.1 is governed by two independent configuration parameters so
that each state can be suppressed separately during migration:

- `STMT_EXCLUDE_DROPPED_AUTHS` — when true, DROPPED authorisations are excluded.
- `STMT_EXCLUDE_EXPIRED_AUTHS` — when true, EXPIRED authorisations are excluded.

3.2.3 Both parameters MUST default to `true`. A client-specific override to `false` is
permitted only for a time-boxed migration and requires Product sign-off.

3.2.4 Any implementation layer that overrides the platform's statement line selection —
including client-custom builders — MUST honour both parameters. An override that
implements only part of 3.2.2 is a defect, not a limitation.

### 3.3 Pending activity

3.3.1 Authorisations in APPROVED state at the statement cut-off MUST NOT appear as
statement lines, because the amount is not yet committed.

3.3.2 Such authorisations MUST instead be listed in the memo block under the heading
"Pending authorisations", when `STMT_MEMO_BLOCK_ENABLED` is true.

### 3.4 Reversals

3.4.1 REVERSED authorisations MUST NOT appear as statement lines and MUST NOT appear in
the memo block.

### 3.5 Pagination

3.5.1 Statement lines are paginated at `STMT_MAX_LINES_PER_PAGE` rows per page.

## 4. Configuration parameters referenced

| Parameter | Type | NWB value | Governs |
|-----------|------|-----------|---------|
| `STMT_EXCLUDE_DROPPED_AUTHS` | bool | true | Rule 3.2.2 |
| `STMT_EXCLUDE_EXPIRED_AUTHS` | bool | true | Rule 3.2.2 |
| `STMT_MEMO_BLOCK_ENABLED`    | bool | true | Rule 3.3.2 |
| `STMT_MAX_LINES_PER_PAGE`    | int  | 45   | Rule 3.5.1 |
| `AUTH_DROP_WINDOW_DAYS`      | int  | 7    | Definition 2.3 |

## 5. Out of scope

5.1 Real-time balance display in the mobile app.

5.2 Statement presentation for closed or charged-off accounts.

5.3 Retrospective restatement of statements already dispatched.

## 6. Change history

| Version | Date | Change |
|---------|------|--------|
| 2.3 | 2025-11-01 | Added 3.2.4 making partial implementation of the exclusion rules an explicit defect. |
| 2.2 | 2025-06-14 | Split the single exclusion flag into `STMT_EXCLUDE_DROPPED_AUTHS` and `STMT_EXCLUDE_EXPIRED_AUTHS`. |
| 2.1 | 2025-02-03 | Added memo block rules (3.3). |
| 2.0 | 2024-09-20 | Initial approved version for the NWB migration programme. |
