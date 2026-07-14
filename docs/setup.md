# First-Time Setup

Everything runs against a Supabase project (Postgres + PostgREST). The Python
scripts here are only for provisioning and testing — day-to-day use is agents
(or the Google Form) talking to the Supabase REST API directly.

## Prerequisites

- A [Supabase](https://supabase.com) account (the free tier works; the daily
  keep-alive ping in the Form bridge exists precisely to stop free projects
  from auto-pausing)
- Python 3.12+
- Optional: a Google account, if you want the Google Form → Supabase bridge

## 1. Create the Supabase project

1. Create a new project at [supabase.com/dashboard](https://supabase.com/dashboard).
   Save the database password you choose — you need it for `SUPABASE_DB_URL`.
2. Enable two extensions in **Database → Extensions**:
   - `pg_cron` — schedules the daily FX-rate fetch
   - `http` — lets the rate-fetch function call the Bluelytics API

## 2. Clone and configure

```powershell
git clone <this-repo>
cd personal-accounting-system
python -m venv .venv
.venv\Scripts\Activate.ps1        # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
copy .env.example .env            # cp on macOS/Linux
```

Fill in `.env` (never committed — it's gitignored):

| variable | where to find it |
|---|---|
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_SERVICE_KEY` | Project Settings → API → `service_role` key (**not** the anon key — the schema locks everything to `service_role`) |
| `SUPABASE_DB_URL` | Project Settings → Database → Connection string → Session pooler (IPv4) |

## 3. Apply the migrations

```powershell
python scripts/apply_migrations.py
```

Applies `supabase/migrations/*.sql` in filename order and records each in a
`_migrations` table, so re-running is safe. This creates the schema, seeds
categories / payment methods / monotributo brackets, defines the RPCs and
views, and schedules the daily FX-rate cron job (13:30 UTC).

## 4. Load FX-rate history

`add_transaction` converts every ARS amount to USD using the Dólar Blue rate
for that date, and **fails if no rate exists on or before the transaction
date**. Backfill the full history once:

```powershell
python scripts/backfill_rates.py
```

From then on, the pg_cron job keeps `fx_rates` current automatically.

## 5. Verify

```powershell
python -m pytest scripts/test_smoke.py -v
```

The smoke tests exercise the RPCs and views end-to-end against your live
project (they clean up after themselves). All green = the system works.

Or sanity-check by hand:

```bash
curl -s "$SUPABASE_URL/rest/v1/categories?select=name,kind" \
  -H "apikey: $SUPABASE_SERVICE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY"
```

## 6. Point your agent at it

Give any LLM agent the two env vars plus [`docs/agent-playbook.md`](agent-playbook.md)
— the playbook is the complete interface contract (how to log transactions,
query views, record invoices/conversions, import statements). Nothing else is
needed; that's the point of the design.

## 7. Optional: Google Form quick-entry bridge

A Google Form is the lowest-friction way to log an expense from your phone.

1. Create a Form with these questions (titles must match the constants at the
   top of `apps-script/form-sync.gs`, or edit the constants):
   - **Name** (short text) — the expense description
   - **Category** (dropdown) — must match the outflow category names in the DB
   - **ARCA** (multiple choice, optional) — include an option named `Negro`
     for off-the-books expenses; anything else (or blank) means fiscal
   - **Currency** (dropdown, optional) — `ARS` / `USD`; blank defaults to ARS
   - **Amount** (short text with *Number* validation)
   - **Payment Method** (dropdown, optional) — must match DB payment methods
2. Link the Form to a response spreadsheet, then in the spreadsheet:
   **Extensions → Apps Script** and paste in `apps-script/form-sync.gs`.
3. Set **Project Settings → Script Properties**: `SUPABASE_URL` and
   `SUPABASE_SERVICE_KEY`.
4. Add two triggers (clock icon → Add trigger):
   - `onFormSubmitHandler` — From spreadsheet — On form submit
   - `retryErrors` — Time-driven — Day timer (retries failed posts and pings
     Supabase daily so a free-tier project never pauses)

Note the amount parser assumes an **es-AR spreadsheet locale** (comma =
decimal separator, dots = thousands). If your Form/sheet locale differs,
revisit `parseAmount()` in the script.

## What NOT to run

`scripts/migrate_from_sheets.py` and `scripts/migrate_facturacion.py` were
one-time importers for the original owner's personal spreadsheets (expected
under `data/`, which is gitignored). A fresh install starts empty — skip them.
