# Personal Accounting System — Design Spec

**Date:** 2026-07-12 (fiscal module added 2026-07-13)
**Status:** Approved pending user review
**Replaces:** Google Forms → Google Sheets expense tracker with Blue-rate USD
conversion, **and** the "Facturación y Cobranzas" workbook (invoices, currency
conversions, monotributo bracket checks)

## 1. Overview

An agent-agnostic personal accounting system. The entire backend lives in a free-tier
Supabase project (Postgres + auto-generated REST API). Any agent — Claude Code today,
a Hermes/Telegram agent later — interacts over plain HTTPS using conventions documented
in `docs/agent-playbook.md`. There is no hosted application code: business logic lives
in Postgres functions, views, and scheduled jobs inside Supabase.

The system unifies two previously separate trackers: (a) personal expenses/income, and
(b) the fiscal ledger — invoices issued (facturas, monotributo), USDC/USD→ARS
conversions, and bank deposits. Unification enables the cross-cutting question the two
sheets could not answer: invoiced income vs fiscally-registered spending.

## 2. Goals

- Track **outflows and inflows** (current system: outflows only).
- Tag every transaction as fiscally registered or not (**ARCA flag**, default Blanco).
- Handle **group payments**: record full outflow, link reimbursement inflows to it,
  report net cost. No per-person debt tracking.
- **One category per transaction + free-form tags** for cross-cutting grouping.
- **Payment method** tracking (cards, cash, fintech apps).
- ARS + USD, converted to USD at the **Blue sell rate** (Bluelytics), snapshotted per
  transaction — same convention as the current sheet.
- **Statement import**: hand a bank/card statement to an agent; it logs only the
  missing transactions, idempotently.
- **Invoice tracking** (facturas emitidas) with automated **monotributo bracket
  checks** against all ARCA categories.
- **Conversion & deposit tracking**: USDC→ARS sales on exchanges, USD/USDC→ARS cash
  sales via informal dealer, ARS cash deposits into banks — with the five dashboard
  metrics (Total Facturado / Depositado / Cobrado, Restante & Disponible, §5.9).
- **Salary in USDC recorded as income** (inflow transactions, 1 USDC = 1 USD).
- **Invoiced vs spent en Blanco** comparison (the unification payoff).
- **Invoice amount suggestion**: generate a set of invoice amounts covering a deposit
  target (replaces the manual `RANDBETWEEN` workflow).
- **Full history migration** from the existing Google Sheet **and** the Facturación y
  Cobranzas workbook.
- Reporting on demand by asking an agent (by category, month, fiscal status).
- Interim entry via the existing **Google Form**, bridged into Supabase.

## 3. Non-goals (explicitly rejected or deferred)

- Per-person receivables / "who owes me" tracking (chose net-cost-only).
- Currencies beyond ARS/USD, stablecoins, crypto (USDC is treated as USD).
- A web dashboard or scheduled summary reports.
- Multi-user support, auth flows beyond a single service key.
- **ARCA auto-invoicing (deferred, wanted):** issuing real facturas through ARCA's
  WSFE web service with per-invoice manual approval. Deliberately a separate
  follow-up spec — it involves digital certificates, WSAA auth, and homologación
  testing. This spec leaves the hooks ready: `invoices.status`, `cae`,
  `cae_expires_on` (§5.6).
- Tracking USDC/USD holdings or account balances. The system tracks *flows*
  (the sheets never tracked balances either); the five §5.9 metrics are all
  derivable from flows.

## 4. Architecture

**All-in-Supabase.** Chosen over a CLI wrapper (would require a runtime on every agent
host) and a hosted API service (an extra service to maintain for a single user).

- **Supabase free tier**: 500 MB Postgres, unlimited API requests. Decades of headroom.
- **Writes** go through validating RPCs (`add_transaction`, `add_invoice`,
  `add_conversion`).
- **Reads** go through SQL views queried with PostgREST filters, or a read-only SQL RPC
  for ad-hoc analysis.
- **Scheduled jobs** (`pg_cron` + `pg_net` extensions) fetch the daily Blue rate.
- **Keep-alive**: the daily rate job plus a daily Apps Script trigger provide steady
  activity so the free-tier 7-day inactivity pause never triggers. If a pause ever
  happens anyway, add a GitHub Actions cron ping (documented fallback).

## 5. Data model

### 5.1 `categories`

```sql
create table categories (
  id     serial primary key,
  name   text not null unique,
  kind   text not null check (kind in ('inflow','outflow')),
  active boolean not null default true
);
```

Seed data — outflow (12): Comida, Salidas, Transporte, Compras, Regalos, Suscripciones,
Impuestos, Hogar, Servicios, Salud, Viajes, Otros gastos.
Inflow (6): Sueldo, Freelance, Reintegros, Regalos recibidos, Rendimientos, Otros ingresos.
(Educación was considered and rejected as never-used.)

### 5.2 `payment_methods`

```sql
create table payment_methods (
  id     serial primary key,
  name   text not null unique,
  active boolean not null default true
);
```

Seed data (15): BBVA Crédito, BBVA Débito, MercadoPago Crédito, Gnosis Pay, Exa, Lemon,
Ripio, Brubank, Belo Lux, Belo, Peanut, Cash, USD Cash, **BBVA, Buenbit**.

The table doubles as the **platform list for conversions** (§5.7): BBVA (the bank
account, distinct from the two cards) and Buenbit exist only as conversion platforms;
Ripio and Belo serve both roles.

### 5.3 `fx_rates`

```sql
create table fx_rates (
  rate_date  date primary key,
  blue_buy   numeric not null,
  blue_sell  numeric not null,
  blue_avg   numeric not null
);
```

Filled daily by the rate job; backfilled for full history during migration
(Bluelytics evolution endpoint). Conversion always uses **`blue_sell`** to match the
current sheet's `getHistoricalBlueRate` (`json.blue.value_sell`).

### 5.4 `transactions`

```sql
create table transactions (
  id                uuid primary key default gen_random_uuid(),
  occurred_on       date not null,
  type              text not null check (type in ('inflow','outflow')),
  description       text not null,
  category_id       int not null references categories(id),
  amount            numeric(14,2) not null check (amount > 0),
  currency          text not null check (currency in ('ARS','USD')),
  fiscal            boolean not null default true,   -- Blanco by default
  tags              text[] not null default '{}',
  payment_method_id int references payment_methods(id),  -- nullable
  parent_id         uuid references transactions(id),    -- reimbursement → group outflow
  usd_rate          numeric not null,                 -- Blue sell snapshot; 1.0 for USD
  amount_usd        numeric generated always as (amount / usd_rate) stored,
  external_ref      text,                             -- statement-line fingerprint
  notes             text,
  source            text not null,  -- 'form' | 'claude' | 'hermes' | 'statement' | 'migration' | 'test'
  created_at        timestamptz not null default now()
);
create unique index on transactions (external_ref) where external_ref is not null;
```

Trigger-enforced invariants:
- `categories.kind` must equal `transactions.type`.
- `parent_id` links are inflow → outflow only (a payback pointing at its group expense).

### 5.5 Semantics

- **Rate resolution:** a transaction dated D uses `blue_sell` from the latest
  `fx_rates` row with `rate_date <= D` (weekend expense → Friday's rate). USD
  transactions get `usd_rate = 1.0`.
- **Group payments / netting:** reimbursement inflows (`parent_id` set) are *excluded*
  from income in reports and instead *subtracted from their parent outflow*, in the
  **parent's month**. Rationale: "my net cost of that dinner" is the meaningful number;
  a payback arriving in July for a June dinner adjusts June's category total. Each row
  converts at its own date's rate; minor FX drift between payment and payback is
  accepted.
- **Fiscal default:** `true` (Blanco). Agents only set `false` when the user indicates
  en negro / sin factura.

### 5.6 `invoices` (facturas emitidas)

```sql
create table invoices (
  id              uuid primary key default gen_random_uuid(),
  issued_on       date not null,
  punto_venta     text not null default '0001',
  invoice_number  int not null,
  amount_ars      numeric(14,2) not null check (amount_ars > 0),
  status          text not null default 'issued'
                    check (status in ('draft','issued','voided')),
  cae             text,            -- ARCA-ready: null until WSFE integration
  cae_expires_on  date,
  notes           text,
  source          text not null,
  created_at      timestamptz not null default now(),
  unique (punto_venta, invoice_number)
);
```

`draft` exists for the future approval flow (§3); everything recorded today is
`issued`. Only `issued` invoices count toward the §5.9 metrics and bracket totals.

### 5.7 `conversions` (cobranzas y depósitos)

```sql
create table conversions (
  id             uuid primary key default gen_random_uuid(),
  occurred_on    date not null,
  kind           text not null
                   check (kind in ('crypto_sale','cash_sale','bank_deposit')),
  ars_amount     numeric(14,2) not null check (ars_amount > 0),
  usd_amount     numeric(14,2) check (usd_amount > 0),  -- null for bank_deposit
  rate_quoted    numeric,          -- 'Tipo de Cambio' as quoted; optional
  rate_effective numeric generated always as
                   (case when usd_amount is not null
                         then ars_amount / usd_amount end) stored,
  platform_id    int not null references payment_methods(id),
  notes          text,
  source         text not null,
  created_at     timestamptz not null default now()
);
```

Check constraint: `usd_amount` is required for `crypto_sale`/`cash_sale` and must be
null for `bank_deposit`.

The three kinds (verified against every row of the Cobranzas sheet):
- **`crypto_sale`** — USDC → ARS on a digital platform (Belo, Ripio, Buenbit). The
  ARS lands digitally, so it is **en blanco / deposited immediately**.
- **`cash_sale`** — USDC or physical USD → ARS *cash* via informal dealer. Cobrado
  but not deposited.
- **`bank_deposit`** — ARS cash into a bank account. No USD leg, no rate.

**Conversions are not income.** Income is recorded once, as an inflow transaction
when the USDC arrives (§2); a conversion only changes the money's form. Recording a
crypto_sale must never create an inflow row.

### 5.8 `monotributo_brackets`

```sql
create table monotributo_brackets (
  category    text not null,        -- 'A'..'K'
  valid_from  date not null,        -- ARCA scale effective date (semi-annual updates)
  annual_cap  numeric(14,2) not null,
  primary key (category, valid_from)
);
```

Manually maintained: when ARCA publishes new scales, insert rows with the new
`valid_from`. All categories A–K are seeded. Views always use the scale with the
latest `valid_from ≤ today`.

### 5.9 Fiscal metrics (formulas verified against the Overview sheet)

All five reproduce Tommy's dashboard exactly (checked to the peso against the real
workbook):

| metric | formula |
|---|---|
| Total Facturado | Σ `issued` invoices `amount_ars` |
| Total Depositado | Σ crypto_sales + Σ bank_deposits (ARS) |
| Total Cobrado | Σ crypto_sales + Σ cash_sales (ARS) |
| Restante por Depositar | Facturado − Depositado *(deposit headroom still covered by invoices)* |
| Disponible para Depositar | Cobrado − Depositado *(converted ARS not yet banked, i.e. cash on hand)* |

These are **cumulative over all time** by default; views also expose per-month and
12-month-rolling breakdowns for the bracket analysis (monthly averages, cap
percentage, current category vs next).

## 6. API surface (the agent contract)

### 6.1 Writes — `add_transaction` RPC

`POST /rest/v1/rpc/add_transaction`. Agents pass names, not IDs:

```json
{
  "p_occurred_on": "2026-07-11",
  "p_type": "outflow",
  "p_description": "cena en lo de marta",
  "p_category": "Salidas",
  "p_amount": 80000,
  "p_currency": "ARS",
  "p_fiscal": true,
  "p_tags": ["cumple-marta"],
  "p_payment_method": "Lemon",
  "p_parent_id": null,
  "p_external_ref": null,
  "p_notes": null,
  "p_source": "claude"
}
```

Behavior: resolves category/payment-method names case-insensitively; validates
category kind vs type and parent-link direction; resolves `usd_rate`; inserts; returns
the full row. Errors are descriptive and self-correcting for agents, e.g.
`unknown category "Comidaa" — valid outflow categories: Comida, Salidas, ...`.
Duplicate `external_ref` → conflict error (safe re-import).

### 6.2 Writes — fiscal RPCs

- `add_invoice(p_issued_on, p_invoice_number, p_amount_ars, ...)` — validates
  uniqueness of (punto_venta, number) with a descriptive error; defaults
  `punto_venta='0001'`, `status='issued'`.
- `add_conversion(p_occurred_on, p_kind, p_ars_amount, p_usd_amount, p_platform, ...)`
  — resolves the platform name against `payment_methods` (same case-insensitive,
  self-correcting errors as `add_transaction`); enforces the kind/usd_amount rule.
- `suggest_invoices(p_target_ars, p_min, p_max)` — returns a list of invoice amounts,
  each a multiple of 1,000, summing to the target rounded to the nearest 1,000.
  When the target exceeds `p_max`, every amount is within [min, max]; a target of
  `p_max` or less returns a single invoice of the rounded target (which may be below
  `p_min` for small targets — you can't invoice more granularly than the target).
  Plain code with random jitter (replacing the sheet's
  `ROUND(RANDBETWEEN(75000;200000);-3)`), not agent guesswork. Suggestion only: it
  writes nothing; recording still goes through `add_invoice` after Tommy approves.

### 6.3 Reads

- `v_transactions` — all rows joined with category/method names, plus
  `net_amount_usd` (outflows net of linked reimbursements).
- `v_monthly_summary` — month × type × category × fiscal → USD totals, reimbursements
  netted per §5.5.
- `v_group_expenses` — each outflow that has reimbursements: gross, reimbursed, net.
- `v_deposit_status` — single row: the five §5.9 metrics, cumulative.
- `v_monotributo_status` — one row per bracket category of the current scale:
  annual cap, 12-month-rolling invoiced total, remaining headroom, monthly averages
  (max allowed vs actual), and whether the cap is exceeded — the red/yellow/green
  logic of the Overview sheet as data.
- `v_fiscal_balance` — per month, in ARS: invoiced total vs fiscal-flagged (Blanco)
  outflow total, plus cumulative running totals — the "invoiced vs spent en blanco"
  comparison. USD outflows convert to ARS at the Blue **sell** rate for their date
  from `fx_rates` (nearest earlier day, per §5.5) — note this is a view-time lookup,
  not the row's `usd_rate`, which is 1.0 for USD rows by construction.
- Ad-hoc: PostgREST filters on the views
  (`GET /rest/v1/v_transactions?occurred_on=gte.2026-06-01&category=eq.Salidas`), or a
  read-only SQL RPC (`run_report(p_sql)` — executes SELECT-only, read-only transaction)
  for anything the views don't cover.

### 6.4 The playbook

`docs/agent-playbook.md` is the single agent-facing interface doc: endpoints, auth,
category/payment-method lists, parsing conventions (defaults: fiscal=Blanco,
currency=ARS unless stated, today's date unless stated), the statement-import
workflow, the fiscal workflows (logging invoices and conversions, answering
deposit/bracket questions, the suggest-invoices flow), and copy-paste curl examples.
Claude Code reads it today; Hermes reads it tomorrow. Nothing agent-specific lives
anywhere else.

## 7. Blue rate job

- `pg_cron` schedule (daily, after Argentine market open) → `pg_net` GET
  `https://api.bluelytics.com.ar/v2/latest` → upsert into `fx_rates`.
- Missing days are tolerated by nearest-earlier-day fallback.
- One-off backfill during setup from
  `https://api.bluelytics.com.ar/v2/evolution.json` (full history) — so backdated
  entries and statement imports always find a rate. Migrated rows keep the sheet's
  own conversion (§10.1), with one exception: the 19 rows whose sheet conversion is
  broken are repaired from these backfilled rates.

## 8. Google Form bridge (interim entry)

- Apps Script `onFormSubmit` trigger on the linked Sheet maps the response and POSTs
  to `add_transaction` with `source='form'`. The Supabase key lives in Script
  Properties, never in the sheet or repo.
- Failures append to an `errors` tab in the sheet for later retry.
- A daily time-driven trigger retries the `errors` tab and doubles as keep-alive.
- Form updates (manual, documented): the current Form only has 7 of the 12 outflow
  categories — add the five new ones (Hogar, Servicios, Salud, Viajes, Otros gastos);
  switch the Category question from checkboxes to single-choice to match the
  one-category model; add a Payment Method dropdown; make the ARCA question optional —
  blank maps to Blanco (Forms can't preset a default on a required dropdown).
- The Form remains **outflow-only**. Inflows and reimbursement linking happen through
  an agent (they're rare). The Form is retired once Telegram/Hermes entry works.

## 9. Statement import (agent workflow, no new infrastructure)

1. User hands the agent a statement (PDF / CSV / screenshot) and names the payment
   method.
2. Agent parses statement lines; computes `external_ref` per line (deterministic
   fingerprint: method + date + amount + normalized merchant).
3. Agent fetches existing transactions for that method + date range; matches by
   amount/date to find unlogged lines.
4. Agent proposes only the missing transactions with inferred categories; user
   approves; agent inserts with `source='statement'` and `external_ref` set.
5. Re-importing the same statement is a no-op (unique index on `external_ref`).

## 10. Migration

One-off Python scripts run locally (Claude Code). Both source workbooks were
inspected on 2026-07-13; every rule below is written against the real data. Fresh
exports are taken at migration time (both sheets remain in daily use).

### 10.1 Expense tracker (`Personal Expenses Tracker`, 507 rows, Oct 2024→)

Real columns: `Marca temporal, Name, Category, Currency, Amount, ARCA,
Blue USD Rate, USD Amount, Month`. `Marca temporal` (submission timestamp) becomes
`occurred_on`; `Month`/`Blue USD Rate` are derived columns, ignored.

1. Backfill `fx_rates` from Bluelytics evolution (§7).
2. Map old → new categories. Multi-checkbox rows (90 of 507 — all pairs, 9 distinct
   combos) resolve to a single category: **combo rules** first, falling back to the
   first checked category; non-winning categories become tags. Pre-seeded rules from
   the data (subject to dry-run review): `Comida + Salidas → Salidas` (44 rows),
   `Compras + Regalos → Regalos` (27 rows). The remaining 7 combos (19 rows) get
   decided during dry-run review.
3. Rows with **no category** (4 exist): dry-run problem list, Tommy assigns manually.
4. Fiscal from the ARCA column — observed values `Blanco` (268), `Negro` (33), blank
   (206); blank maps to Blanco. `payment_method` null for history;
   `source='migration'`.
5. Take USD values directly from the sheet's per-row conversion (no recomputation):
   ARS rows derive `usd_rate = amount / sheet_usd_amount`; USD rows get
   `usd_rate = 1.0` (verified: all 101 USD rows have USD Amount = Amount).
   **Exception — broken conversions (19 rows exist):** rows whose `USD Amount` is an
   error (`#VALUE!` from Bluelytics API 429 failures) fall back to the backfilled
   `blue_sell` rate for their date. These rows are listed in the dry run and excluded
   from the reconciliation comparison (their sheet value is unusable by definition).
6. **Dry-run report before any insert:** multi-category rows with proposed
   category + tags, problem rows, repaired-rate rows, and per-month totals. Rules are
   adjusted and the dry run re-run until Tommy approves; only then does the import
   execute.

**Success criterion:** per-month USD totals in Supabase reconcile with the Sheet's
totals within rounding tolerance (< 0.5%), computed over rows with valid sheet
conversions. Only then is the Sheet demoted to read-only archive.

### 10.2 Facturación y Cobranzas workbook

**Facturas** (217 invoices, Jul 2025→, verified clean: single punto de venta `0001`,
sequence 1–217, no gaps or duplicates): direct import of
`issued_on, punto_venta, invoice_number, amount_ars`, `source='migration'`.

**Cobranzas y Depósitos** (22 rows): `kind` is derived, verified correct on every
row: `usd_amount` present + platform ≠ Cash → `crypto_sale`; `usd_amount` present +
platform = Cash → `cash_sale`; no `usd_amount` → `bank_deposit`. `Precio Final` is
redundant (= ars/usd, regenerated as `rate_effective`); `Tipo de Cambio` imports as
`rate_quoted` **only when it is a plausible rate** (one known quirk: the Buenbit row
holds the ARS amount in that cell — imported as null). Platform names map to
`payment_methods` rows; `Notas` → `notes`.

**Success criterion:** the recomputed §5.9 metrics over migrated rows must equal the
workbook's Overview values at export time to the peso (at inspection time:
Facturado 28,897,000 / Depositado 17,528,542 / Cobrado 18,572,862 /
Restante 11,368,458 / Disponible 1,044,320).

## 11. Security

- RLS enabled on all tables; no anonymous access policies (anon key is useless).
- Writes/reads use the service key, stored per-agent: local `.env` for Claude Code
  (gitignored), Script Properties for Apps Script, Hermes config later.
- No keys, ever, in the repo.
- Single-user system: no additional auth complexity.

## 12. Error handling

- RPCs raise descriptive, actionable exceptions (agents self-correct and retry).
- Form bridge: failed posts land in a visible `errors` sheet tab; daily retry.
- Rate job: a failed day is harmless (fallback); upsert retries next day.
- Migration script: fails loudly per-row, prints a reconciliation report at the end.

## 13. Testing

- **RPC/schema smoke tests:** script hits the REST API with `source='test'` rows
  covering the validation matrix (bad category, kind mismatch, wrong parent link
  direction, duplicate external_ref, missing rate fallback, USD rate=1), then cleans
  up. These tests encode the invariants in §5.4/§5.5 — they fail if netting or
  validation semantics change.
- **Fiscal smoke tests:** same pattern for the fiscal module — duplicate invoice
  numbers rejected; the kind/usd_amount constraint on conversions; the §5.9 metric
  formulas verified against a small fixture whose expected values are computed by
  hand; `suggest_invoices` output properties (every amount a multiple of 1,000 and —
  for targets above p_max — within [min, max]; sum within 500 of the target, i.e.
  the target rounded to the nearest 1,000); bracket view picks the latest
  applicable scale (and ignores future-dated scales).
- **Migration reconciliation:** the §10.1 and §10.2 success criteria, printed as
  comparison tables.
- **Form bridge:** one live test submission end-to-end.
- **Playbook curl examples** double as manual smoke tests for any new agent host.

## 14. Repo layout

```
supabase/migrations/   -- numbered SQL: schema, triggers, views, RPCs, seeds, cron
scripts/               -- migrate_from_sheets.py, migrate_facturacion.py,
                          backfill_rates.py, smoke tests
apps-script/           -- form-sync.gs (copy-pasted into Apps Script editor)
docs/agent-playbook.md -- the agent-agnostic interface contract
docs/superpowers/specs/ -- this spec
data/                  -- gitignored: workbook exports for migration
```

## 15. Implementation phases

1. **Supabase setup** — create project, enable extensions, apply schema migrations,
   seed categories/payment methods. Verify: tables visible, seeds queryable via REST.
2. **Rate machinery** — backfill script + daily cron job. Verify: fx_rates populated
   through today; cron fires.
3. **RPCs + views + smoke tests** — add_transaction, run_report, the three
   transaction views. Verify: smoke test suite green.
4. **Fiscal module** — invoices/conversions/brackets schema, add_invoice,
   add_conversion, suggest_invoices, the three fiscal views, bracket seeds (current
   ARCA scale, confirmed by Tommy). Verify: fiscal smoke tests green.
5. **Agent playbook** — write doc covering both modules; verify by driving a full
   add/query cycle from Claude Code using only the playbook.
6. **Form bridge** — Apps Script + Form updates. Verify: live submission lands in
   Supabase; error path writes to errors tab.
7. **Migration: expenses** — dry run, Tommy review, execute, reconcile (§10.1).
8. **Migration: facturación** — dry run, execute, reconcile against Overview (§10.2).
9. **Cutover** — Form points at new flow only; both old workbooks archived read-only.

Each phase is independently verifiable; later phases don't block entry via earlier ones
(e.g. transactions can be logged from Claude Code after phase 5 while the Form bridge
is still pending).
