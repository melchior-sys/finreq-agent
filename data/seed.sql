-- FinReq Agent — synthetic seed data for "Northwind Bank" and two other fictional clients.
-- ALL DATA IS INVENTED. No real bank, client, card, or configuration data appears here.
--
-- Build the database with:  python scripts/build_db.py
-- The resulting data/db.sqlite is a build artifact and is not committed.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS son_chunks;
DROP TABLE IF EXISTS son_docs;
DROP TABLE IF EXISTS code_snippets;
DROP TABLE IF EXISTS auth_records;
DROP TABLE IF EXISTS config_params;
DROP TABLE IF EXISTS clients;

-- ---------------------------------------------------------------- clients

CREATE TABLE clients (
    client_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    region       TEXT NOT NULL,
    onboarded_at TEXT NOT NULL
);

INSERT INTO clients (client_id, name, region, onboarded_at) VALUES
    ('NWB', 'Northwind Bank',       'UK', '2024-06-03'),
    ('SVB', 'Silverline Bank',      'UK', '2025-02-17'),
    ('HRB', 'Harbour Credit Union', 'IE', '2025-09-08');

-- ----------------------------------------------------------- config_params
--
-- scope       : 'core'   = platform-wide default value
--               'client' = value set for this client specifically
-- owner_layer : which layer is expected to read the parameter
--               ('core', 'custom', or 'both')

CREATE TABLE config_params (
    param_id       INTEGER PRIMARY KEY,
    client_id      TEXT NOT NULL REFERENCES clients(client_id),
    name           TEXT NOT NULL,
    value          TEXT NOT NULL,
    data_type      TEXT NOT NULL CHECK (data_type IN ('bool', 'int', 'string')),
    default_value  TEXT,
    scope          TEXT NOT NULL CHECK (scope IN ('core', 'client')),
    owner_layer    TEXT NOT NULL CHECK (owner_layer IN ('core', 'custom', 'both')),
    description    TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (client_id, name)
);

INSERT INTO config_params
    (client_id, name, value, data_type, default_value, scope, owner_layer, description, effective_from, updated_at)
VALUES
    -- Statement presentation (SON-001)
    ('NWB', 'STMT_EXCLUDE_DROPPED_AUTHS', 'true', 'bool', 'true', 'client', 'both',
     'When true, authorisations in DROPPED state are excluded from ATM statement lines. SON-001 section 3.2.2.',
     '2025-06-14', '2025-11-04T09:12:44Z'),
    ('NWB', 'STMT_EXCLUDE_EXPIRED_AUTHS', 'true', 'bool', 'true', 'client', 'both',
     'When true, authorisations in EXPIRED state are excluded from ATM statement lines. SON-001 section 3.2.2.',
     '2025-06-14', '2025-11-04T09:12:44Z'),
    ('NWB', 'STMT_MEMO_BLOCK_ENABLED', 'true', 'bool', 'true', 'client', 'core',
     'When true, pending APPROVED authorisations are listed in the statement memo block. SON-001 section 3.3.2.',
     '2025-02-03', '2025-02-03T11:40:02Z'),
    ('NWB', 'STMT_MAX_LINES_PER_PAGE', '45', 'int', '45', 'core', 'core',
     'Statement transaction rows printed per page. SON-001 section 3.5.1.',
     '2024-09-20', '2024-09-20T16:05:00Z'),
    ('NWB', 'AUTH_DROP_WINDOW_DAYS', '7', 'int', '7', 'client', 'core',
     'Days an APPROVED authorisation waits for a matching advice before moving to DROPPED. SON-001 definition 2.3.',
     '2024-09-20', '2025-01-22T08:30:19Z'),

    -- Fees and waivers (SON-002)
    ('NWB', 'FEE_WAIVER_MIN_AVG_BALANCE', '250000', 'int', '250000', 'client', 'both',
     'Minimum cycle average balance in minor units that waives the monthly maintenance fee. SON-002 rule 3.2.1(a).',
     '2026-01-01', '2026-01-01T00:00:00Z'),
    ('NWB', 'FEE_SALARY_CREDIT_MIN', '150000', 'int', '150000', 'client', 'custom',
     'Minimum qualifying salary or pension credit in minor units that waives the monthly maintenance fee. SON-002 rule 3.2.1(b).',
     '2025-08-11', '2025-08-11T14:22:10Z'),
    ('NWB', 'FEE_WAIVER_MAX_PER_CYCLE', '3', 'int', '3', 'client', 'both',
     'Maximum waivers of any type granted to one account in a single cycle. SON-002 rule 3.4.1.',
     '2024-10-05', '2024-10-05T10:00:00Z'),

    -- Cycle dates (SON-003)
    ('NWB', 'STMT_CYCLE_ANCHOR_DAY', '15', 'int', '1', 'client', 'both',
     'Nominal day of month on which the statement cycle closes. SON-003 rule 3.1.1.',
     '2024-08-30', '2024-08-30T12:00:00Z'),
    ('NWB', 'STMT_CYCLE_ROLL_RULE', 'FORWARD', 'string', 'FORWARD', 'client', 'both',
     'Direction a cycle close rolls when it falls on a non-working day: FORWARD, BACKWARD or NONE. SON-003 rule 3.3.1.',
     '2024-08-30', '2025-07-01T09:00:00Z'),
    ('NWB', 'STMT_TIMEZONE', 'Europe/London', 'string', 'UTC', 'client', 'both',
     'Timezone in which cycle boundaries and the daily cut-off are evaluated. SON-003 rule 3.4.1.',
     '2025-01-19', '2025-01-19T15:45:31Z'),

    -- Other clients, so that client_id filtering is exercised rather than decorative.
    ('SVB', 'STMT_EXCLUDE_DROPPED_AUTHS', 'false', 'bool', 'true', 'client', 'both',
     'Time-boxed migration override agreed with Product; DROPPED authorisations remain visible until 2026-12-31.',
     '2025-03-01', '2025-03-01T10:15:00Z'),
    ('SVB', 'STMT_CYCLE_ANCHOR_DAY', '1', 'int', '1', 'client', 'both',
     'Nominal day of month on which the statement cycle closes.',
     '2025-02-17', '2025-02-17T09:00:00Z'),
    ('HRB', 'FEE_WAIVER_MIN_AVG_BALANCE', '100000', 'int', '250000', 'client', 'both',
     'Minimum cycle average balance in minor units that waives the monthly maintenance fee.',
     '2025-09-08', '2025-09-08T09:00:00Z');

-- NOTE: there is deliberately NO parameter named FEE_WAIVER_SENIOR_TIER_ENABLED or
-- FEE_WAIVER_TIER_ENABLED for any client. SON-002 sections 5.1 and 5.2 place tier and
-- age based waivers out of scope for Phase 1, so a request for them is a change
-- request, and get_config_param returning not_found is direct evidence of that.

-- ------------------------------------------------------------ auth_records

CREATE TABLE auth_records (
    auth_id        TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL REFERENCES clients(client_id),
    card_last4     TEXT NOT NULL,
    channel        TEXT NOT NULL CHECK (channel IN ('ATM', 'POS', 'ECOM')),
    status         TEXT NOT NULL CHECK (status IN ('APPROVED', 'SETTLED', 'DROPPED', 'EXPIRED', 'REVERSED')),
    amount_minor   INTEGER NOT NULL,
    currency       TEXT NOT NULL,
    auth_ts        TEXT NOT NULL,
    settled_ts     TEXT,
    dropped_reason TEXT,
    mcc            TEXT,
    terminal_id    TEXT,
    merchant_name  TEXT
);

CREATE INDEX idx_auth_client_status ON auth_records (client_id, status);
CREATE INDEX idx_auth_client_ts     ON auth_records (client_id, auth_ts);

-- Northwind Bank, February-March 2026 statement cycle.
-- Card 4417 is the cardholder in demo ticket NWB-4471: four DROPPED ATM
-- authorisations that should never have reached a statement line.
INSERT INTO auth_records
    (auth_id, client_id, card_last4, channel, status, amount_minor, currency, auth_ts, settled_ts, dropped_reason, mcc, terminal_id, merchant_name)
VALUES
    -- The smoking gun: DROPPED ATM authorisations on card 4417.
    ('A-10231', 'NWB', '4417', 'ATM',  'DROPPED',  6000,  'GBP', '2026-02-18T10:02:11Z', NULL, 'NO_ADVICE_RECEIVED', '6011', 'NWB-ATM-0142', NULL),
    ('A-10244', 'NWB', '4417', 'ATM',  'DROPPED',  10000, 'GBP', '2026-02-24T19:41:55Z', NULL, 'ATM_TIMEOUT',        '6011', 'NWB-ATM-0142', NULL),
    ('A-10267', 'NWB', '4417', 'ATM',  'DROPPED',  4000,  'GBP', '2026-03-02T08:15:03Z', NULL, 'NO_ADVICE_RECEIVED', '6011', 'NWB-ATM-0311', NULL),
    ('A-10289', 'NWB', '4417', 'ATM',  'DROPPED',  20000, 'GBP', '2026-03-09T21:07:48Z', NULL, 'REVERSAL_NO_MATCH',  '6011', 'NWB-ATM-0142', NULL),

    -- Card 4417, legitimate activity in the same cycle.
    ('A-10225', 'NWB', '4417', 'ATM',  'SETTLED',  8000,  'GBP', '2026-02-17T09:30:00Z', '2026-02-18T03:12:00Z', NULL, '6011', 'NWB-ATM-0142', NULL),
    ('A-10238', 'NWB', '4417', 'ATM',  'SETTLED',  12000, 'GBP', '2026-02-21T17:22:41Z', '2026-02-22T03:10:00Z', NULL, '6011', 'NWB-ATM-0311', NULL),
    ('A-10251', 'NWB', '4417', 'POS',  'SETTLED',  2350,  'GBP', '2026-02-26T12:48:19Z', '2026-02-27T03:11:00Z', NULL, '5411', NULL, 'GREENWAY FOODS'),
    ('A-10263', 'NWB', '4417', 'POS',  'SETTLED',  1899,  'GBP', '2026-03-01T18:05:44Z', '2026-03-02T03:09:00Z', NULL, '5812', NULL, 'THE COPPER KETTLE'),
    ('A-10275', 'NWB', '4417', 'ECOM', 'SETTLED',  4599,  'GBP', '2026-03-05T20:14:02Z', '2026-03-06T03:12:00Z', NULL, '5732', NULL, 'ORBIT ELECTRICALS'),
    ('A-10292', 'NWB', '4417', 'ATM',  'APPROVED', 5000,  'GBP', '2026-03-13T22:51:10Z', NULL, NULL, '6011', 'NWB-ATM-0142', NULL),
    ('A-10294', 'NWB', '4417', 'POS',  'REVERSED', 3200,  'GBP', '2026-03-12T14:03:27Z', NULL, NULL, '5651', NULL, 'HARTLEY AND SONS'),

    -- Card 8823: EXPIRED authorisations, which the custom builder does suppress
    -- correctly. Present so that "custom ignores a flag" cannot be assumed globally.
    ('A-10302', 'NWB', '8823', 'ATM',  'EXPIRED',  15000, 'GBP', '2026-02-19T11:12:00Z', NULL, NULL, '6011', 'NWB-ATM-0208', NULL),
    ('A-10311', 'NWB', '8823', 'ATM',  'EXPIRED',  7500,  'GBP', '2026-03-04T16:40:33Z', NULL, NULL, '6011', 'NWB-ATM-0208', NULL),
    ('A-10318', 'NWB', '8823', 'ATM',  'SETTLED',  20000, 'GBP', '2026-02-20T08:05:12Z', '2026-02-21T03:10:00Z', NULL, '6011', 'NWB-ATM-0208', NULL),
    ('A-10326', 'NWB', '8823', 'POS',  'SETTLED',  6720,  'GBP', '2026-02-27T13:19:08Z', '2026-02-28T03:11:00Z', NULL, '5411', NULL, 'GREENWAY FOODS'),
    ('A-10333', 'NWB', '8823', 'POS',  'SETTLED',  1150,  'GBP', '2026-03-03T09:02:55Z', '2026-03-04T03:10:00Z', NULL, '5814', NULL, 'BRIDGE STREET CAFE'),
    ('A-10341', 'NWB', '8823', 'ECOM', 'SETTLED',  8999,  'GBP', '2026-03-08T19:33:21Z', '2026-03-09T03:12:00Z', NULL, '4899', NULL, 'NORTHLINK BROADBAND'),
    ('A-10349', 'NWB', '8823', 'POS',  'APPROVED', 4250,  'GBP', '2026-03-14T18:02:40Z', NULL, NULL, '5812', NULL, 'THE COPPER KETTLE'),

    -- Card 1290: mixed activity, one dropped POS authorisation (not an ATM statement concern).
    ('A-10355', 'NWB', '1290', 'POS',  'DROPPED',  3100,  'GBP', '2026-02-22T15:44:02Z', NULL, 'NO_ADVICE_RECEIVED', '5541', NULL, 'KINGSFORD FUEL'),
    ('A-10361', 'NWB', '1290', 'POS',  'SETTLED',  9980,  'GBP', '2026-02-23T10:11:36Z', '2026-02-24T03:10:00Z', NULL, '5311', NULL, 'MARLOWE DEPARTMENT'),
    ('A-10368', 'NWB', '1290', 'ATM',  'SETTLED',  10000, 'GBP', '2026-02-25T07:58:14Z', '2026-02-26T03:09:00Z', NULL, '6011', 'NWB-ATM-0074', NULL),
    ('A-10374', 'NWB', '1290', 'ATM',  'SETTLED',  5000,  'GBP', '2026-03-06T12:26:50Z', '2026-03-07T03:10:00Z', NULL, '6011', 'NWB-ATM-0074', NULL),
    ('A-10380', 'NWB', '1290', 'ECOM', 'SETTLED',  2499,  'GBP', '2026-03-07T21:40:09Z', '2026-03-08T03:12:00Z', NULL, '5815', NULL, 'PICTUREHOUSE STREAM'),
    ('A-10386', 'NWB', '1290', 'ECOM', 'REVERSED', 12000, 'GBP', '2026-03-10T11:19:23Z', NULL, NULL, '4722', NULL, 'SEAWARD TRAVEL'),
    ('A-10391', 'NWB', '1290', 'POS',  'SETTLED',  760,   'GBP', '2026-03-11T08:47:31Z', '2026-03-12T03:11:00Z', NULL, '5499', NULL, 'ALDGATE CONVENIENCE'),

    -- Card 7702: quiet account, mostly settled, used as a control in evals.
    ('A-10402', 'NWB', '7702', 'ATM',  'SETTLED',  4000,  'GBP', '2026-02-16T09:14:00Z', '2026-02-17T03:10:00Z', NULL, '6011', 'NWB-ATM-0519', NULL),
    ('A-10408', 'NWB', '7702', 'POS',  'SETTLED',  3145,  'GBP', '2026-02-18T16:31:52Z', '2026-02-19T03:11:00Z', NULL, '5411', NULL, 'GREENWAY FOODS'),
    ('A-10414', 'NWB', '7702', 'POS',  'SETTLED',  2210,  'GBP', '2026-02-28T11:05:17Z', '2026-03-01T03:10:00Z', NULL, '5912', NULL, 'ELMSWORTH PHARMACY'),
    ('A-10419', 'NWB', '7702', 'ATM',  'SETTLED',  6000,  'GBP', '2026-03-03T18:22:44Z', '2026-03-04T03:09:00Z', NULL, '6011', 'NWB-ATM-0519', NULL),
    ('A-10425', 'NWB', '7702', 'POS',  'SETTLED',  5480,  'GBP', '2026-03-09T13:50:06Z', '2026-03-10T03:11:00Z', NULL, '5651', NULL, 'HARTLEY AND SONS'),
    ('A-10431', 'NWB', '7702', 'ECOM', 'SETTLED',  1799,  'GBP', '2026-03-12T20:08:39Z', '2026-03-13T03:12:00Z', NULL, '5815', NULL, 'PICTUREHOUSE STREAM'),
    ('A-10437', 'NWB', '7702', 'POS',  'APPROVED', 999,   'GBP', '2026-03-14T19:26:12Z', NULL, NULL, '5814', NULL, 'BRIDGE STREET CAFE'),

    -- Northwind Bank, previous cycle (January-February 2026), so date filtering matters.
    ('A-10118', 'NWB', '4417', 'ATM',  'SETTLED',  10000, 'GBP', '2026-01-20T10:00:00Z', '2026-01-21T03:10:00Z', NULL, '6011', 'NWB-ATM-0142', NULL),
    ('A-10126', 'NWB', '4417', 'ATM',  'DROPPED',  8000,  'GBP', '2026-01-28T18:36:29Z', NULL, 'NO_ADVICE_RECEIVED', '6011', 'NWB-ATM-0142', NULL),
    ('A-10133', 'NWB', '8823', 'POS',  'SETTLED',  4300,  'GBP', '2026-02-02T12:15:41Z', '2026-02-03T03:11:00Z', NULL, '5411', NULL, 'GREENWAY FOODS'),
    ('A-10140', 'NWB', '1290', 'ATM',  'SETTLED',  15000, 'GBP', '2026-02-09T09:44:07Z', '2026-02-10T03:10:00Z', NULL, '6011', 'NWB-ATM-0074', NULL),
    ('A-10147', 'NWB', '7702', 'ECOM', 'SETTLED',  3299,  'GBP', '2026-02-11T21:02:53Z', '2026-02-12T03:12:00Z', NULL, '5732', NULL, 'ORBIT ELECTRICALS'),
    ('A-10154', 'NWB', '4417', 'POS',  'SETTLED',  1450,  'GBP', '2026-02-13T14:28:16Z', '2026-02-14T03:11:00Z', NULL, '5814', NULL, 'BRIDGE STREET CAFE'),

    -- Other clients.
    ('B-20011', 'SVB', '5521', 'ATM',  'DROPPED',  9000,  'GBP', '2026-02-19T14:03:00Z', NULL, 'NO_ADVICE_RECEIVED', '6011', 'SVB-ATM-0031', NULL),
    ('B-20018', 'SVB', '5521', 'ATM',  'SETTLED',  12000, 'GBP', '2026-02-24T10:31:22Z', '2026-02-25T03:10:00Z', NULL, '6011', 'SVB-ATM-0031', NULL),
    ('B-20025', 'SVB', '5521', 'POS',  'SETTLED',  2750,  'GBP', '2026-03-02T17:12:08Z', '2026-03-03T03:11:00Z', NULL, '5411', NULL, 'LARKHILL GROCERS'),
    ('B-20032', 'SVB', '6634', 'ECOM', 'SETTLED',  6120,  'GBP', '2026-03-06T19:45:37Z', '2026-03-07T03:12:00Z', NULL, '4899', NULL, 'NORTHLINK BROADBAND'),
    ('B-20039', 'SVB', '6634', 'ATM',  'EXPIRED',  5000,  'GBP', '2026-03-10T08:20:15Z', NULL, NULL, '6011', 'SVB-ATM-0044', NULL),
    ('B-20046', 'SVB', '6634', 'POS',  'SETTLED',  880,   'GBP', '2026-03-12T09:55:02Z', '2026-03-13T03:11:00Z', NULL, '5499', NULL, 'ALDGATE CONVENIENCE'),
    ('C-30007', 'HRB', '9018', 'ATM',  'SETTLED',  15000, 'EUR', '2026-02-20T11:00:00Z', '2026-02-21T03:10:00Z', NULL, '6011', 'HRB-ATM-0002', NULL),
    ('C-30014', 'HRB', '9018', 'POS',  'SETTLED',  4390,  'EUR', '2026-02-27T15:37:49Z', '2026-02-28T03:11:00Z', NULL, '5411', NULL, 'QUAYSIDE MARKET'),
    ('C-30021', 'HRB', '9018', 'ATM',  'DROPPED',  10000, 'EUR', '2026-03-05T20:11:33Z', NULL, 'ATM_TIMEOUT', '6011', 'HRB-ATM-0002', NULL),
    ('C-30028', 'HRB', '4460', 'ECOM', 'SETTLED',  2999,  'EUR', '2026-03-11T18:24:56Z', '2026-03-12T03:12:00Z', NULL, '5815', NULL, 'PICTUREHOUSE STREAM');

-- --------------------------------------------------------------- son_docs

CREATE TABLE son_docs (
    son_id         TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL REFERENCES clients(client_id),
    title          TEXT NOT NULL,
    version        TEXT NOT NULL,
    path           TEXT NOT NULL,
    effective_from TEXT NOT NULL
);

INSERT INTO son_docs (son_id, client_id, title, version, path, effective_from) VALUES
    ('SON-001', 'NWB', 'ATM Statement Presentation',            '2.3', 'data/sons/SON-001-atm-statement-presentation.md', '2025-11-01'),
    ('SON-002', 'NWB', 'Fee Schedule and Waivers',              '1.7', 'data/sons/SON-002-fee-schedule-and-waivers.md',   '2026-01-01'),
    ('SON-003', 'NWB', 'Statement Cycle and Period Definition', '1.2', 'data/sons/SON-003-statement-cycle-dates.md',      '2025-07-01');

-- -------------------------------------------------------------- son_chunks
-- Populated by src/rag.py in slice 2. Created here so the whole schema lives in one file.

CREATE TABLE son_chunks (
    chunk_id    TEXT PRIMARY KEY,          -- e.g. 'SON-001#3.2'
    son_id      TEXT NOT NULL REFERENCES son_docs(son_id),
    section     TEXT NOT NULL,             -- e.g. '3.2'
    heading     TEXT NOT NULL,             -- e.g. '3.2 Exclusion of non-settling authorisations'
    breadcrumb  TEXT NOT NULL,             -- document and ancestor headings, prepended before embedding
    text        TEXT NOT NULL,             -- section body, verbatim, so quotes of it can be grounded
    token_count INTEGER NOT NULL,          -- estimate: words * 1.3
    embedding   BLOB                       -- float32 little-endian, L2 normalised
);

CREATE INDEX idx_chunks_son ON son_chunks (son_id);

-- Index provenance: which model and which corpus produced the vectors currently stored.
-- Lets a reindex be a no-op when nothing changed, and forces one when the model changes.

CREATE TABLE index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ----------------------------------------------------------- code_snippets
--
-- Registry of the sample code that compare_core_vs_custom reads. Line ranges bound the
-- named function only, so a parameter read elsewhere in the same file does not pollute
-- the comparison.

CREATE TABLE code_snippets (
    snippet_id    INTEGER PRIMARY KEY,
    client_id     TEXT REFERENCES clients(client_id),   -- NULL for core, which is client-agnostic
    layer         TEXT NOT NULL CHECK (layer IN ('core', 'custom')),
    function_name TEXT NOT NULL,
    path          TEXT NOT NULL,
    start_line    INTEGER NOT NULL,
    end_line      INTEGER NOT NULL,
    language      TEXT NOT NULL,
    description   TEXT NOT NULL,
    UNIQUE (layer, client_id, function_name)
);

INSERT INTO code_snippets
    (client_id, layer, function_name, path, start_line, end_line, language, description)
VALUES
    (NULL,  'core',   'build_atm_statement_lines',     'data/code/core/statement_builder.py',       14, 51, 'python',
     'Platform default statement line selection. Honours both SON-001 3.2.2 exclusion flags.'),
    ('NWB', 'custom', 'build_atm_statement_lines',     'data/code/custom/nwb_statement_builder.py', 15, 47, 'python',
     'Northwind Bank override of statement line selection, carried across from SON-001 v2.1.'),
    (NULL,  'core',   'calculate_monthly_fee_waiver',  'data/code/core/fee_engine.py',              13, 29, 'python',
     'Platform default maintenance fee waiver evaluation. Average balance condition only.'),
    ('NWB', 'custom', 'calculate_monthly_fee_waiver',  'data/code/custom/nwb_fee_engine.py',        15, 38, 'python',
     'Northwind Bank override adding the SON-002 3.2.1(b) salary credit waiver condition.'),
    (NULL,  'core',   'resolve_statement_cycle_dates', 'data/code/core/cycle_calendar.py',          13, 45, 'python',
     'Platform default cycle date derivation. Weekends only in the non-working-day test.'),
    ('NWB', 'custom', 'resolve_statement_cycle_dates', 'data/code/custom/nwb_cycle_calendar.py',    26, 49, 'python',
     'Northwind Bank override adding the England and Wales bank holiday calendar to the roll.');
