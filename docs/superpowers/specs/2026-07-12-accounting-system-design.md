# Personal Accounting System — Design Spec

**Date:** 2026-07-12
**Status:** Approved pending user review
**Replaces:** Google Forms → Google Sheets expense tracker with Blue-rate USD conversion

## 1. Overview

An agent-agnostic personal accounting system. The entire backend lives in a free-tier
Supabase project (Postgres + auto-generated REST API). Any agent — Claude Code today,
a Hermes/Telegram agent later — interacts over plain HTTPS using conventions documented
in `docs/agent-playbook.md`. There is no hosted application code: business logic lives
in Postgres functions, views, and scheduled jobs inside Supabase.

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
- **Full history migration** from the existing Google Sheet.
- Reporting on demand by asking an agent (by category, month, fiscal status).
- Interim entry via the existing **Google Form**, bridged into Supabase.

## 3. Non-goals (explicitly rejected)

- Per-person receivables / "who owes me" tracking (chose net-cost-only).
- Currencies beyond ARS/USD, stablecoins, crypto.
- A web dashboard or scheduled summary reports.
- Multi-user support, auth flows beyond a single service key.

## 4. Architecture

**All-in-Supabase.** Chosen over a CLI wrapper (would require a runtime on every agent
host) and a hosted API service (an extra service to maintain for a single user).

- **Supabase free tier**: 500 MB Postgres, unlimited API requests. Decades of headroom.
- **Writes** go through a validating RPC (`add_transaction`).
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

Seed data (13): BBVA Crédito, BBVA Débito, MercadoPago Crédito, Gnosis Pay, Exa, Lemon,
Ripio, Brubank, Belo Lux, Belo, Peanut, Cash, USD Cash.

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

### 6.2 Reads

- `v_transactions` — all rows joined with category/method names, plus
  `net_amount_usd` (outflows net of linked reimbursements).
- `v_monthly_summary` — month × type × category × fiscal → USD totals, reimbursements
  netted per §5.5.
- `v_group_expenses` — each outflow that has reimbursements: gross, reimbursed, net.
- Ad-hoc: PostgREST filters on the views
  (`GET /rest/v1/v_transactions?occurred_on=gte.2026-06-01&category=eq.Salidas`), or a
  read-only SQL RPC (`run_report(p_sql)` — executes SELECT-only, read-only transaction)
  for anything the views don't cover.

### 6.3 The playbook

`docs/agent-playbook.md` is the single agent-facing interface doc: endpoints, auth,
category/payment-method lists, parsing conventions (defaults: fiscal=Blanco,
currency=ARS unless stated, today's date unless stated), the statement-import
workflow, and copy-paste curl examples. Claude Code reads it today; Hermes reads it
tomorrow. Nothing agent-specific lives anywhere else.

## 7. Blue rate job

- `pg_cron` schedule (daily, after Argentine market open) → `pg_net` GET
  `https://api.bluelytics.com.ar/v2/latest` → upsert into `fx_rates`.
- Missing days are tolerated by nearest-earlier-day fallback.
- One-off backfill during migration from
  `https://api.bluelytics.com.ar/v2/evolution.json` (full history).

## 8. Google Form bridge (interim entry)

- Apps Script `onFormSubmit` trigger on the linked Sheet maps the response and POSTs
  to `add_transaction` with `source='form'`. The Supabase key lives in Script
  Properties, never in the sheet or repo.
- Failures append to an `errors` tab in the sheet for later retry.
- A daily time-driven trigger retries the `errors` tab and doubles as keep-alive.
- Form updates (manual, documented): remove Educación; add a Payment Method dropdown;
  make the ARCA question optional — blank maps to Blanco (Forms can't preset a default
  on a required dropdown).
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

One-off Python script run locally (Claude Code):

1. Export the existing Sheet to CSV.
2. Backfill `fx_rates` from Bluelytics evolution.
3. Map old → new categories (mapping table in the script; multi-checkbox rows take the
   first/primary category, rest become tags).
4. Fiscal from the ARCA column; `payment_method` null for history; `source='migration'`.
5. Recompute USD at historical sell rates.

**Success criterion:** per-month USD totals in Supabase reconcile with the Sheet's
totals within rounding tolerance (< 0.5%). Only then is the Sheet demoted to read-only
archive.

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
- **Migration reconciliation:** the §10 success criterion, printed as a per-month
  comparison table.
- **Form bridge:** one live test submission end-to-end.
- **Playbook curl examples** double as manual smoke tests for any new agent host.

## 14. Repo layout

```
supabase/migrations/   -- numbered SQL: schema, triggers, views, RPCs, seeds, cron
scripts/               -- migrate_from_sheets.py, backfill_rates.py, smoke_tests.py
apps-script/           -- form-sync.gs (copy-pasted into Apps Script editor)
docs/agent-playbook.md -- the agent-agnostic interface contract
docs/superpowers/specs/ -- this spec
```

## 15. Implementation phases

1. **Supabase setup** — create project, enable pg_cron/pg_net, apply schema migrations,
   seed categories/payment methods. Verify: tables visible, seeds queryable via REST.
2. **Rate machinery** — backfill script + daily cron job. Verify: fx_rates populated
   through today; cron fires.
3. **RPCs + views + smoke tests** — add_transaction, run_report, the three views.
   Verify: smoke test suite green.
4. **Agent playbook** — write doc; verify by driving a full add/query cycle from
   Claude Code using only the playbook.
5. **Form bridge** — Apps Script + Form updates. Verify: live submission lands in
   Supabase; error path writes to errors tab.
6. **Migration** — run script, reconcile totals, archive the Sheet.
7. **Cutover** — Form points at new flow only; old Sheet analysis retired.

Each phase is independently verifiable; later phases don't block entry via earlier ones
(e.g. transactions can be logged from Claude Code after phase 4 while the Form bridge
is still pending).
