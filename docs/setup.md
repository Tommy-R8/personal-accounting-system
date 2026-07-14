# First-Time Setup

Everything runs against a Supabase project (Postgres + PostgREST). The Python
scripts here are only for provisioning and testing — day-to-day use is agents
talking to the Supabase REST API directly.

## Prerequisites

- A [Supabase](https://supabase.com) account. The free tier works, with one
  caveat: free projects auto-pause after about a week without API traffic.
  Regular use keeps it awake; a paused project restores in one click from the
  dashboard.
- Python 3.12+

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
