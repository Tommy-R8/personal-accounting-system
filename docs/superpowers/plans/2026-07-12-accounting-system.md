# Personal Accounting System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the agent-agnostic accounting backend specified in `docs/superpowers/specs/2026-07-12-accounting-system-design.md` — Supabase database with validating RPCs, report views, daily Blue-rate job, Google Form bridge, and full migration from the old Google Sheet.

**Architecture:** All logic lives inside a free-tier Supabase Postgres (tables, triggers, RPCs, views, pg_cron job). Local Python scripts handle migrations-apply, rate backfill, sheet migration, and smoke tests over the REST API. An Apps Script bridges the Google Form. `docs/agent-playbook.md` is the agent-facing contract.

**Tech Stack:** Supabase (Postgres 15+, PostgREST, pg_cron, `http` extension), Python 3.11+ (psycopg 3, requests, pytest, python-dotenv), Google Apps Script.

**Conventions for the executor:**
- All commands run **from the repo root** in PowerShell with the venv activated (Task 1 sets this up).
- Tests hit the **live** Supabase project; every test row uses `source='test'` and is deleted by a session-scoped fixture.
- Tasks marked **USER CHECKPOINT** need Tommy to do something outside the repo (Supabase dashboard, Google Forms). Stop and ask; do not fake these steps.

---

### Task 1: Repo scaffolding, Python toolchain, Supabase project

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `README.md`
- Create: `scripts/lib.py`
- Create: `scripts/apply_migrations.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 2: Create `requirements.txt`**

```text
psycopg[binary]>=3.1
requests>=2.31
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 3: Create `.env.example`**

```text
# Supabase project settings -> API
SUPABASE_URL=https://YOURPROJECTREF.supabase.co
SUPABASE_SERVICE_KEY=eyJ...service_role key...
# Supabase project settings -> Database -> Connection string -> Session pooler (IPv4)
SUPABASE_DB_URL=postgresql://postgres.YOURPROJECTREF:YOURDBPASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

- [ ] **Step 4: Create `README.md`**

```markdown
# Personal Accounting System

Agent-agnostic accounting on Supabase. Spec:
`docs/superpowers/specs/2026-07-12-accounting-system-design.md`.
Agents: read `docs/agent-playbook.md` — it is the entire interface contract.

## Layout
- `supabase/migrations/` — numbered SQL, applied by `scripts/apply_migrations.py`
- `scripts/` — migrations applier, rate backfill, sheet migration, smoke tests
- `apps-script/` — Google Form bridge (pasted into Apps Script editor)

## Setup
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill in real values
python scripts/apply_migrations.py

## Tests
python -m pytest scripts/test_smoke.py -v
```

- [ ] **Step 5: Create `scripts/lib.py`** (shared helpers for every script)

```python
"""Shared helpers: DB connection + Supabase REST access. Load .env from repo root."""
import os

import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
DB_URL = os.environ["SUPABASE_DB_URL"]

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def db_conn():
    return psycopg.connect(DB_URL)


def rpc(name, payload):
    """POST /rest/v1/rpc/<name>. Returns the raw response (callers assert status)."""
    return requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/{name}", json=payload, headers=HEADERS, timeout=30
    )


def rest_get(path, params=None):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{path}", params=params or {}, headers=HEADERS, timeout=30
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path, params):
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/{path}", params=params, headers=HEADERS, timeout=30
    )
    r.raise_for_status()
```

- [ ] **Step 6: Create `scripts/apply_migrations.py`**

```python
"""Apply supabase/migrations/*.sql in filename order, tracking in _migrations."""
import pathlib

from lib import db_conn

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "supabase" / "migrations"


def main():
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """create table if not exists _migrations (
                 filename text primary key,
                 applied_at timestamptz not null default now())"""
        )
        cur.execute("select filename from _migrations")
        applied = {row[0] for row in cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"skip  {path.name}")
                continue
            print(f"apply {path.name}")
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("insert into _migrations (filename) values (%s)", (path.name,))
        conn.commit()
    print("done")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Set up the venv and install dependencies**

Run:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Expected: pip installs psycopg, requests, python-dotenv, pytest without errors.

- [ ] **Step 8: USER CHECKPOINT — create the Supabase project**

Ask Tommy to:
1. Create a free project at https://supabase.com/dashboard (suggested name `accounting`, region `sa-east-1` São Paulo).
2. Dashboard → Database → Extensions: enable **pg_cron** and **http**.
3. Copy into a new `.env` (from `.env.example`): the project URL, the **service_role** key (Settings → API), and the **Session pooler** connection string with the DB password (Settings → Database).

- [ ] **Step 9: Verify connectivity end-to-end**

Run:
```powershell
python scripts/apply_migrations.py
```
Expected output: `done` (no migrations yet — this proves the DB connection string works).

Run:
```powershell
python -c "import sys; sys.path.insert(0, 'scripts'); from lib import rest_get; print(rest_get('', {}) is not None)"
```
Expected: no exception (any JSON response proves URL + service key work). If it raises 401, the service key is wrong.

- [ ] **Step 10: Commit**

```powershell
git add .gitignore requirements.txt .env.example README.md scripts/lib.py scripts/apply_migrations.py
git commit -m "chore: scaffolding, migrations applier, Supabase env"
```

---

### Task 2: Schema migration — tables, trigger, RLS, seeds

**Files:**
- Create: `supabase/migrations/001_schema.sql`
- Create: `scripts/test_smoke.py`

- [ ] **Step 1: Write the failing tests** — create `scripts/test_smoke.py`:

```python
"""Smoke tests against the LIVE Supabase project. All test rows use source='test'
and are cleaned up by the session fixture. Run from repo root:
    python -m pytest scripts/test_smoke.py -v
"""
import psycopg
import pytest

from lib import db_conn, rest_delete, rest_get, rpc


@pytest.fixture(autouse=True, scope="session")
def cleanup():
    yield
    # children first: reimbursements reference their parent via FK
    rest_delete("transactions", {"source": "eq.test", "parent_id": "not.is.null"})
    rest_delete("transactions", {"source": "eq.test"})


# --- Task 2: schema + seeds -------------------------------------------------

def test_seed_categories():
    cats = rest_get("categories", {"select": "name,kind"})
    assert len(cats) == 18
    assert {"name": "Salidas", "kind": "outflow"} in cats
    assert {"name": "Reintegros", "kind": "inflow"} in cats
    assert not any(c["name"] == "Educación" for c in cats)


def test_seed_payment_methods():
    pms = rest_get("payment_methods", {"select": "name"})
    assert len(pms) == 13
    assert {"name": "Gnosis Pay"} in pms


def test_trigger_blocks_kind_mismatch_on_direct_insert():
    # Direct inserts (the migration path) must be validated too, not just the RPC.
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from categories where name = 'Sueldo'")
        inflow_cat = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                """insert into transactions
                     (occurred_on, type, description, category_id, amount,
                      currency, usd_rate, source)
                   values ('2026-07-06', 'outflow', 'x', %s, 1, 'ARS', 1000, 'test')""",
                (inflow_cat,),
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -v`
Expected: all 3 FAIL (404 from `rest_get` — tables don't exist yet).

- [ ] **Step 3: Create `supabase/migrations/001_schema.sql`**

```sql
-- Extensions (no-ops if already enabled from the dashboard)
create extension if not exists pg_cron;
create extension if not exists http with schema extensions;

-- Future functions must be granted explicitly (default EXECUTE to PUBLIC revoked)
alter default privileges in schema public
  revoke execute on functions from public, anon, authenticated;

create table categories (
  id     serial primary key,
  name   text not null unique,
  kind   text not null check (kind in ('inflow','outflow')),
  active boolean not null default true
);

create table payment_methods (
  id     serial primary key,
  name   text not null unique,
  active boolean not null default true
);

create table fx_rates (
  rate_date  date primary key,
  blue_buy   numeric not null,
  blue_sell  numeric not null,
  blue_avg   numeric not null
);

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
  payment_method_id int references payment_methods(id),
  parent_id         uuid references transactions(id),
  usd_rate          numeric not null check (usd_rate > 0),
  amount_usd        numeric generated always as (amount / usd_rate) stored,
  external_ref      text,
  notes             text,
  source            text not null,
  created_at        timestamptz not null default now()
);

create unique index transactions_external_ref_key
  on transactions (external_ref) where external_ref is not null;

create or replace function trg_validate_transaction() returns trigger
language plpgsql as $$
declare
  v_kind text;
  v_parent_type text;
begin
  select kind into v_kind from categories where id = new.category_id;
  if v_kind is distinct from new.type then
    raise exception 'category kind (%) does not match transaction type (%)',
      v_kind, new.type;
  end if;
  if new.parent_id is not null then
    if new.type <> 'inflow' then
      raise exception 'only inflows (reimbursements) can have a parent_id';
    end if;
    select type into v_parent_type from transactions where id = new.parent_id;
    if v_parent_type is null then
      raise exception 'parent transaction % not found', new.parent_id;
    end if;
    if v_parent_type <> 'outflow' then
      raise exception 'parent of a reimbursement must be an outflow';
    end if;
  end if;
  return new;
end $$;

create trigger validate_transaction
  before insert or update on transactions
  for each row execute function trg_validate_transaction();

-- RLS: no policies. anon/authenticated are blocked; service_role bypasses RLS.
alter table categories enable row level security;
alter table payment_methods enable row level security;
alter table fx_rates enable row level security;
alter table transactions enable row level security;
alter table _migrations enable row level security;

insert into categories (name, kind) values
  ('Comida', 'outflow'), ('Salidas', 'outflow'), ('Transporte', 'outflow'),
  ('Compras', 'outflow'), ('Regalos', 'outflow'), ('Suscripciones', 'outflow'),
  ('Impuestos', 'outflow'), ('Hogar', 'outflow'), ('Servicios', 'outflow'),
  ('Salud', 'outflow'), ('Viajes', 'outflow'), ('Otros gastos', 'outflow'),
  ('Sueldo', 'inflow'), ('Freelance', 'inflow'), ('Reintegros', 'inflow'),
  ('Regalos recibidos', 'inflow'), ('Rendimientos', 'inflow'),
  ('Otros ingresos', 'inflow');

insert into payment_methods (name) values
  ('BBVA Crédito'), ('BBVA Débito'), ('MercadoPago Crédito'), ('Gnosis Pay'),
  ('Exa'), ('Lemon'), ('Ripio'), ('Brubank'), ('Belo Lux'), ('Belo'),
  ('Peanut'), ('Cash'), ('USD Cash');
```

- [ ] **Step 4: Apply and run tests**

Run:
```powershell
python scripts/apply_migrations.py
python -m pytest scripts/test_smoke.py -v
```
Expected: `apply 001_schema.sql` then `done`; all 3 tests PASS.
If `create extension pg_cron` errors with permissions, enable both extensions in the dashboard (Task 1 Step 8) and rerun — the `if not exists` guards make it safe.

- [ ] **Step 5: Commit**

```powershell
git add supabase/migrations/001_schema.sql scripts/test_smoke.py
git commit -m "feat: schema, validation trigger, RLS, seed categories and payment methods"
```

---

### Task 3: Blue-rate machinery — backfill script + daily cron job

**Files:**
- Create: `scripts/backfill_rates.py`
- Create: `supabase/migrations/002_rate_job.sql`
- Modify: `scripts/test_smoke.py` (append tests)
- Modify: `docs/superpowers/specs/2026-07-12-accounting-system-design.md` (pg_net → http wording)

**Deviation from spec, on purpose:** the spec says `pg_net`, but `pg_net` is async — the HTTP response lands in a separate table out-of-band, which would need a second processing step. The synchronous `http` extension lets one function fetch → parse → upsert atomically. Step 6 updates the spec wording.

- [ ] **Step 1: Append failing tests to `scripts/test_smoke.py`**

```python
# --- Task 3: fx rates ---------------------------------------------------------

def test_backfill_populated_history():
    oldest = rest_get("fx_rates", {"select": "rate_date", "order": "rate_date.asc", "limit": "1"})
    assert oldest and oldest[0]["rate_date"] <= "2015-01-01"


def test_fetch_blue_rate_upserts_recent_row():
    from datetime import date
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select fetch_blue_rate()")
        cur.execute("select max(rate_date), max(blue_sell) from fx_rates")
        max_date, sell = cur.fetchone()
        conn.commit()
    assert (date.today() - max_date).days <= 4  # Fri rate on a long weekend at worst
    assert sell > 0


def test_daily_cron_job_registered():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select schedule from cron.job where jobname = 'daily-blue-rate'")
        row = cur.fetchone()
    assert row is not None and row[0] == "30 13 * * *"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -v -k "rate or cron or backfill"`
Expected: 3 FAIL (empty fx_rates / missing function / missing cron job).

- [ ] **Step 3: Create `scripts/backfill_rates.py`**

```python
"""One-off: load full Blue-rate history from Bluelytics into fx_rates."""
import requests

from lib import db_conn

EVOLUTION_URL = "https://api.bluelytics.com.ar/v2/evolution.json"


def main():
    data = requests.get(EVOLUTION_URL, timeout=120).json()
    rows = [
        (d["date"], d["value_buy"], d["value_sell"], (d["value_buy"] + d["value_sell"]) / 2)
        for d in data
        if d["source"] == "Blue"
    ]
    with db_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """insert into fx_rates (rate_date, blue_buy, blue_sell, blue_avg)
               values (%s, %s, %s, %s)
               on conflict (rate_date) do update set
                 blue_buy = excluded.blue_buy,
                 blue_sell = excluded.blue_sell,
                 blue_avg = excluded.blue_avg""",
            rows,
        )
        conn.commit()
    print(f"{len(rows)} Blue rate days upserted")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `supabase/migrations/002_rate_job.sql`**

```sql
create or replace function fetch_blue_rate() returns void
language plpgsql as $$
declare
  v_body jsonb;
begin
  select content::jsonb into v_body
    from extensions.http_get('https://api.bluelytics.com.ar/v2/latest');
  insert into fx_rates (rate_date, blue_buy, blue_sell, blue_avg)
  values (
    ((v_body->>'last_update')::timestamptz
       at time zone 'America/Argentina/Buenos_Aires')::date,
    (v_body->'blue'->>'value_buy')::numeric,
    (v_body->'blue'->>'value_sell')::numeric,
    (v_body->'blue'->>'value_avg')::numeric
  )
  on conflict (rate_date) do update set
    blue_buy = excluded.blue_buy,
    blue_sell = excluded.blue_sell,
    blue_avg = excluded.blue_avg;
end $$;

-- 13:30 UTC = 10:30 ART, after the day's Blue quotes are published
select cron.schedule('daily-blue-rate', '30 13 * * *', $$select fetch_blue_rate()$$);
```

- [ ] **Step 5: Apply, backfill, and run tests**

Run:
```powershell
python scripts/apply_migrations.py
python scripts/backfill_rates.py
python -m pytest scripts/test_smoke.py -v
```
Expected: `apply 002_rate_job.sql`; backfill prints roughly `4000+ Blue rate days upserted`; all tests PASS.

- [ ] **Step 6: Update the spec wording (pg_net → http)**

In `docs/superpowers/specs/2026-07-12-accounting-system-design.md`:
- §4: change ``**Scheduled jobs** (`pg_cron` + `pg_net` extensions)`` to ``**Scheduled jobs** (`pg_cron` + the synchronous `http` extension)``.
- §7 first bullet: change ``→ `pg_net` GET`` to ``→ synchronous `http` GET``.

- [ ] **Step 7: Commit**

```powershell
git add scripts/backfill_rates.py supabase/migrations/002_rate_job.sql scripts/test_smoke.py "docs/superpowers/specs/2026-07-12-accounting-system-design.md"
git commit -m "feat: Blue rate backfill and daily pg_cron fetch (http ext, spec updated)"
```

---

### Task 4: `add_transaction` RPC

**Files:**
- Create: `supabase/migrations/003_add_transaction.sql`
- Modify: `scripts/test_smoke.py` (append tests + helper)

- [ ] **Step 1: Append failing tests to `scripts/test_smoke.py`**

```python
# --- Task 4: add_transaction RPC ----------------------------------------------

def add(**kw):
    """Call add_transaction with test defaults; individual kwargs override."""
    payload = {
        "p_occurred_on": "2026-07-06",
        "p_type": "outflow",
        "p_description": "smoke test row",
        "p_category": "Comida",
        "p_amount": 100,
        "p_source": "test",
    }
    payload.update(kw)
    return rpc("add_transaction", payload)


def test_add_ars_outflow_defaults():
    r = add(p_description="test asado", p_category="Salidas", p_amount=80000)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["currency"] == "ARS"
    assert row["fiscal"] is True  # Blanco default
    assert float(row["usd_rate"]) > 0
    assert abs(float(row["amount_usd"]) - 80000 / float(row["usd_rate"])) < 0.01


def test_usd_rate_is_one():
    r = add(p_type="inflow", p_category="Sueldo", p_amount=1000, p_currency="USD")
    assert r.status_code == 200, r.text
    row = r.json()
    assert float(row["usd_rate"]) == 1.0
    assert float(row["amount_usd"]) == 1000.0


def test_rate_snapshot_uses_latest_available_day():
    latest = rest_get(
        "fx_rates",
        {"select": "blue_sell", "rate_date": "lte.2026-07-06",
         "order": "rate_date.desc", "limit": "1"},
    )[0]
    r = add()
    assert r.status_code == 200, r.text
    assert float(r.json()["usd_rate"]) == float(latest["blue_sell"])


def test_names_resolve_case_insensitively():
    r = add(p_category="salidas", p_payment_method="lemon")
    assert r.status_code == 200, r.text
    assert r.json()["category_id"] is not None


def test_unknown_category_lists_valid_ones():
    r = add(p_category="Comidaa")
    assert r.status_code == 400
    msg = r.json()["message"]
    assert "valid outflow categories" in msg and "Comida" in msg


def test_category_kind_mismatch_is_friendly():
    r = add(p_category="Sueldo")  # inflow category on an outflow
    assert r.status_code == 400
    assert "valid outflow categories" in r.json()["message"]


def test_unknown_payment_method_lists_valid_ones():
    r = add(p_payment_method="Visa Galicia")
    assert r.status_code == 400
    assert "valid payment methods" in r.json()["message"]


def test_reimbursement_links_to_outflow():
    dinner = add(p_category="Salidas", p_amount=80000).json()
    r = add(p_type="inflow", p_category="Reintegros", p_amount=20000,
            p_parent_id=dinner["id"])
    assert r.status_code == 200, r.text
    assert r.json()["parent_id"] == dinner["id"]


def test_outflow_cannot_have_parent():
    dinner = add(p_category="Salidas").json()
    r = add(p_parent_id=dinner["id"])
    assert r.status_code == 400
    assert "reimbursements" in r.json()["message"]


def test_reimbursement_cannot_point_at_inflow():
    salary = add(p_type="inflow", p_category="Sueldo").json()
    r = add(p_type="inflow", p_category="Reintegros", p_parent_id=salary["id"])
    assert r.status_code == 400
    assert "outflow" in r.json()["message"]


def test_external_ref_blocks_duplicates():
    ref = "test:2026-07-06:100:smoke"
    r1 = add(p_external_ref=ref)
    assert r1.status_code == 200, r1.text
    r2 = add(p_external_ref=ref)
    assert r2.status_code == 400
    assert "already imported" in r2.json()["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -v -k "add or rate_snapshot or unknown or mismatch or reimbursement or parent or external"`
Expected: all new tests FAIL with 404 (`add_transaction` not found).

- [ ] **Step 3: Create `supabase/migrations/003_add_transaction.sql`**

```sql
create or replace function add_transaction(
  p_occurred_on date,
  p_type text,
  p_description text,
  p_category text,
  p_amount numeric,
  p_source text,
  p_currency text default 'ARS',
  p_fiscal boolean default true,
  p_tags text[] default '{}',
  p_payment_method text default null,
  p_parent_id uuid default null,
  p_external_ref text default null,
  p_notes text default null
) returns transactions
language plpgsql as $$
declare
  v_cat categories%rowtype;
  v_pm_id int;
  v_rate numeric;
  v_valid text;
  v_row transactions%rowtype;
begin
  select * into v_cat
    from categories
   where lower(name) = lower(trim(p_category)) and active;
  if not found or v_cat.kind is distinct from p_type then
    select string_agg(name, ', ' order by id) into v_valid
      from categories where kind = p_type and active;
    raise exception 'unknown category "%" — valid % categories: %',
      p_category, p_type, v_valid;
  end if;

  if p_payment_method is not null then
    select id into v_pm_id
      from payment_methods
     where lower(name) = lower(trim(p_payment_method)) and active;
    if not found then
      select string_agg(name, ', ' order by id) into v_valid
        from payment_methods where active;
      raise exception 'unknown payment method "%" — valid payment methods: %',
        p_payment_method, v_valid;
    end if;
  end if;

  if p_external_ref is not null
     and exists (select 1 from transactions where external_ref = p_external_ref) then
    raise exception 'external_ref "%" already imported', p_external_ref;
  end if;

  if p_currency = 'USD' then
    v_rate := 1.0;
  else
    select blue_sell into v_rate
      from fx_rates
     where rate_date <= p_occurred_on
     order by rate_date desc
     limit 1;
    if v_rate is null then
      raise exception 'no Blue rate available on or before %', p_occurred_on;
    end if;
  end if;

  insert into transactions
    (occurred_on, type, description, category_id, amount, currency, fiscal,
     tags, payment_method_id, parent_id, usd_rate, external_ref, notes, source)
  values
    (p_occurred_on, p_type, p_description, v_cat.id, p_amount, p_currency, p_fiscal,
     p_tags, v_pm_id, p_parent_id, v_rate, p_external_ref, p_notes, p_source)
  returning * into v_row;
  return v_row;
end $$;

grant execute on function add_transaction(
  date, text, text, text, numeric, text, text, boolean, text[], text, uuid, text, text
) to service_role;
```

- [ ] **Step 4: Apply and run the full suite**

Run:
```powershell
python scripts/apply_migrations.py
python -m pytest scripts/test_smoke.py -v
```
Expected: `apply 003_add_transaction.sql`; all tests PASS (schema + rates + RPC).

- [ ] **Step 5: Commit**

```powershell
git add supabase/migrations/003_add_transaction.sql scripts/test_smoke.py
git commit -m "feat: add_transaction RPC with name resolution, validation, rate snapshot"
```

---

### Task 5: Report views + `run_report` RPC

**Files:**
- Create: `supabase/migrations/004_views_and_report.sql`
- Modify: `scripts/test_smoke.py` (append tests)

- [ ] **Step 1: Append failing tests to `scripts/test_smoke.py`**

```python
# --- Task 5: views + run_report -------------------------------------------------

def test_group_netting_in_views():
    dinner = add(p_category="Salidas", p_amount=80000,
                 p_description="test group dinner").json()
    for _ in range(3):
        add(p_type="inflow", p_category="Reintegros", p_amount=20000,
            p_parent_id=dinner["id"])

    v = rest_get("v_transactions", {"id": f"eq.{dinner['id']}"})[0]
    rate = float(dinner["usd_rate"])
    # same-date reimbursements share the rate: net = (80000 - 60000) / rate
    assert abs(float(v["net_amount_usd"]) - 20000 / rate) < 0.05

    g = rest_get("v_group_expenses", {"id": f"eq.{dinner['id']}"})[0]
    assert g["reimbursement_count"] == 3
    assert abs(float(g["net_usd"]) - 20000 / rate) < 0.05


def test_summary_excludes_reimbursements_and_nets_parents():
    # via run_report so we can filter to source='test' (v_monthly_summary can't)
    r = rpc("run_report", {"p_sql": (
        "select round(sum(net_amount_usd)::numeric, 2) as total "
        "from v_transactions where source = 'test' "
        "and type = 'outflow' and parent_id is null"
    )})
    assert r.status_code == 200, r.text
    assert float(r.json()[0]["total"]) > 0


def test_monthly_summary_view_shape():
    rows = rest_get("v_monthly_summary", {"limit": "1"})
    assert set(rows[0].keys()) == {"month", "type", "category", "fiscal",
                                   "total_usd", "tx_count"}


def test_run_report_rejects_non_select():
    r = rpc("run_report", {"p_sql": "delete from transactions"})
    assert r.status_code == 400
    assert "SELECT" in r.json()["message"]


def test_run_report_rejects_multi_statement():
    r = rpc("run_report", {"p_sql": "select 1; select 2"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -v -k "view or summary or run_report or netting"`
Expected: new tests FAIL (views/RPC missing).

- [ ] **Step 3: Create `supabase/migrations/004_views_and_report.sql`**

```sql
create view v_transactions as
select t.id, t.occurred_on, t.type, t.description,
       c.name as category, t.category_id,
       t.amount, t.currency, t.fiscal, t.tags,
       pm.name as payment_method, t.parent_id,
       t.usd_rate, t.amount_usd,
       case when t.type = 'outflow'
            then t.amount_usd - coalesce(r.reimbursed_usd, 0)
            else t.amount_usd
       end as net_amount_usd,
       t.external_ref, t.notes, t.source, t.created_at
from transactions t
join categories c on c.id = t.category_id
left join payment_methods pm on pm.id = t.payment_method_id
left join lateral (
  select sum(amount_usd) as reimbursed_usd
    from transactions r
   where r.parent_id = t.id
) r on true;

-- Reimbursement rows (parent_id set) are excluded; their value is already
-- subtracted from the parent's net_amount_usd, in the parent's month (spec §5.5).
create view v_monthly_summary as
select date_trunc('month', occurred_on)::date as month,
       type, category, fiscal,
       round(sum(net_amount_usd)::numeric, 2) as total_usd,
       count(*) as tx_count
from v_transactions
where parent_id is null
group by 1, 2, 3, 4;

create view v_group_expenses as
select p.id, p.occurred_on, p.description, c.name as category,
       round(p.amount_usd::numeric, 2) as gross_usd,
       round(sum(r.amount_usd)::numeric, 2) as reimbursed_usd,
       round((p.amount_usd - sum(r.amount_usd))::numeric, 2) as net_usd,
       count(r.id) as reimbursement_count
from transactions p
join categories c on c.id = p.category_id
join transactions r on r.parent_id = p.id
group by p.id, p.occurred_on, p.description, c.name, p.amount_usd;

-- Views must not leak data to anon: evaluate RLS as the caller, not the owner.
alter view v_transactions set (security_invoker = true);
alter view v_monthly_summary set (security_invoker = true);
alter view v_group_expenses set (security_invoker = true);

-- Ad-hoc read-only SQL for agents. STABLE => PostgREST runs it in a
-- read-only transaction, so any write attempt inside errors out.
create or replace function run_report(p_sql text) returns jsonb
language plpgsql stable as $$
declare
  v_result jsonb;
  v_clean text := btrim(p_sql, E' \t\n\r;');
begin
  if v_clean !~* '^(select|with)\M' or position(';' in v_clean) > 0 then
    raise exception 'run_report only accepts a single SELECT statement';
  end if;
  execute format(
    'select coalesce(jsonb_agg(to_jsonb(q)), ''[]''::jsonb) from (%s) q', v_clean
  ) into v_result;
  return v_result;
end $$;

grant execute on function run_report(text) to service_role;
```

- [ ] **Step 4: Apply and run the full suite**

Run:
```powershell
python scripts/apply_migrations.py
python -m pytest scripts/test_smoke.py -v
```
Expected: `apply 004_views_and_report.sql`; **all** tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add supabase/migrations/004_views_and_report.sql scripts/test_smoke.py
git commit -m "feat: report views with reimbursement netting, run_report RPC"
```

---

### Task 6: Agent playbook

**Files:**
- Create: `docs/agent-playbook.md`

- [ ] **Step 1: Create `docs/agent-playbook.md`**

````markdown
# Agent Playbook — Personal Accounting System

You are an agent logging transactions or answering questions for Tommy's personal
accounting system. This document is the complete interface contract.

## Connection

Environment variables (never hardcode, never echo them back):
- `SUPABASE_URL` — e.g. `https://xxxx.supabase.co`
- `SUPABASE_SERVICE_KEY`

Every request carries:
```
apikey: <SUPABASE_SERVICE_KEY>
Authorization: Bearer <SUPABASE_SERVICE_KEY>
Content-Type: application/json
```

## Adding a transaction

`POST {SUPABASE_URL}/rest/v1/rpc/add_transaction`

| param | required | notes |
|---|---|---|
| `p_occurred_on` | yes | `YYYY-MM-DD`. Default to *today in America/Argentina/Buenos_Aires* if the user doesn't say. |
| `p_type` | yes | `outflow` (gasto) or `inflow` (ingreso) |
| `p_description` | yes | short, as the user phrased it |
| `p_category` | yes | one of the lists below (case-insensitive) |
| `p_amount` | yes | positive number, original currency |
| `p_source` | yes | your agent name: `claude`, `hermes`, `statement`, ... |
| `p_currency` | no | `ARS` (default) or `USD` |
| `p_fiscal` | no | default `true` (Blanco). Set `false` only if the user says en negro / sin factura. |
| `p_tags` | no | kebab-case strings, e.g. `["viaje-brasil"]` |
| `p_payment_method` | no | one of the list below; omit if not mentioned |
| `p_parent_id` | no | reimbursements only: uuid of the group outflow being repaid |
| `p_external_ref` | no | statement imports only, see below |
| `p_notes` | no | anything extra worth keeping |

Success returns the inserted row (JSON) including `id`, `usd_rate`, `amount_usd`.
Errors are descriptive (e.g. list the valid categories) — fix the input and retry;
do not ask the user unless genuinely ambiguous.

**Categories — outflow:** Comida, Salidas, Transporte, Compras, Regalos,
Suscripciones, Impuestos, Hogar, Servicios, Salud, Viajes, Otros gastos
**Categories — inflow:** Sueldo, Freelance, Reintegros, Regalos recibidos,
Rendimientos, Otros ingresos
**Payment methods:** BBVA Crédito, BBVA Débito, MercadoPago Crédito, Gnosis Pay,
Exa, Lemon, Ripio, Brubank, Belo Lux, Belo, Peanut, Cash, USD Cash

(The lists above are seeds; when in doubt fetch live:
`GET /rest/v1/categories?active=eq.true&select=name,kind` and
`GET /rest/v1/payment_methods?active=eq.true&select=name`.)

## Group payments

When Tommy pays for a group: log the **full amount** as one outflow.
When someone pays him back: log an **inflow, category Reintegros, with
`p_parent_id`** set to the original outflow's id. Find it if needed:
`GET /rest/v1/v_transactions?type=eq.outflow&order=occurred_on.desc&description=ilike.*<keyword>*`
Reports automatically net reimbursements against the parent expense.

## Querying

Views (read with GET + PostgREST filters):
- `v_transactions` — everything, joined names, `net_amount_usd`
- `v_monthly_summary` — month × type × category × fiscal → `total_usd`, `tx_count`
- `v_group_expenses` — group outflows: `gross_usd`, `reimbursed_usd`, `net_usd`

Examples:
```
GET /rest/v1/v_monthly_summary?month=eq.2026-06-01&type=eq.outflow
GET /rest/v1/v_transactions?occurred_on=gte.2026-06-01&category=eq.Salidas
GET /rest/v1/v_transactions?tags=cs.{viaje-brasil}
```

Anything the views can't express — ad-hoc SQL (single SELECT only):
`POST /rest/v1/rpc/run_report` with `{"p_sql": "select ... from v_transactions ..."}`

**Report conventions:** amounts in USD (`net_amount_usd` / `total_usd`);
reimbursement rows (`parent_id` set) are already excluded from summaries and
netted into their parent's month.

## Statement import workflow

Input: a bank/card statement (PDF, CSV, screenshot) + which payment method it is.

1. Parse every line: date, amount, merchant/description.
2. Compute `external_ref` per line: `<method-slug>:<YYYY-MM-DD>:<amount>:<merchant-slug>`
   (lowercase, kebab-case, e.g. `bbva-credito:2026-07-03:15230.50:carrefour`).
3. Fetch what's already logged for that window:
   `GET /rest/v1/v_transactions?payment_method=eq.<Method>&occurred_on=gte.<from>&occurred_on=lte.<to>`
4. Match statement lines to existing rows by date (±1 day) and amount.
5. Show the user ONLY the unmatched lines with proposed categories; wait for approval.
6. Insert approved lines via `add_transaction` with `p_source="statement"` and
   `p_external_ref` set. A duplicate `external_ref` error means it was already
   imported — skip it silently.

## Sanity checks (curl)

```bash
curl -s "$SUPABASE_URL/rest/v1/categories?select=name,kind" \
  -H "apikey: $SUPABASE_SERVICE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY"

curl -s -X POST "$SUPABASE_URL/rest/v1/rpc/add_transaction" \
  -H "apikey: $SUPABASE_SERVICE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"p_occurred_on":"2026-07-12","p_type":"outflow","p_description":"prueba",
       "p_category":"Comida","p_amount":100,"p_source":"claude"}'
```
(PowerShell: use `curl.exe`, or `Invoke-RestMethod` with a `-Headers` hashtable.)
````

- [ ] **Step 2: Verify the playbook is sufficient, live**

From a fresh context (or as literally as possible: using ONLY the playbook, not this
plan), perform: add an ARS outflow → add a linked Reintegro → query
`v_group_expenses` for it → run one `run_report` query → delete the test rows:
```
DELETE /rest/v1/transactions?source=eq.playbook-check&parent_id=not.is.null
DELETE /rest/v1/transactions?source=eq.playbook-check
```
(using `p_source="playbook-check"` for all rows). If any step required knowledge
not in the playbook, fix the playbook now.

- [ ] **Step 3: Commit**

```powershell
git add docs/agent-playbook.md
git commit -m "docs: agent playbook — the agent-agnostic interface contract"
```

---

### Task 7: Google Form bridge

**Files:**
- Create: `apps-script/form-sync.gs`

- [ ] **Step 1: Create `apps-script/form-sync.gs`**

```javascript
/**
 * Google Form -> Supabase bridge. Lives in the Apps Script project of the
 * spreadsheet LINKED to the expense Form (Extensions -> Apps Script).
 *
 * Script Properties required (File -> Project properties -> Script properties):
 *   SUPABASE_URL          e.g. https://xxxx.supabase.co
 *   SUPABASE_SERVICE_KEY  the service_role key
 *
 * Triggers required (clock icon -> Add trigger):
 *   onFormSubmitHandler  - From spreadsheet - On form submit
 *   retryErrors          - Time-driven - Day timer (also the keep-alive ping)
 */
var PROPS = PropertiesService.getScriptProperties();
var SUPABASE_URL = PROPS.getProperty('SUPABASE_URL');
var SERVICE_KEY = PROPS.getProperty('SUPABASE_SERVICE_KEY');

// Column headers in the responses sheet — must match the Form question titles.
var Q_NAME = 'Name';
var Q_CATEGORY = 'Category';
var Q_ARCA = 'ARCA';
var Q_CURRENCY = 'Currency';
var Q_AMOUNT = 'Amount';
var Q_PAYMENT = 'Payment Method';
var ARCA_NEGRO = 'Negro'; // the option meaning fiscal = false

function onFormSubmitHandler(e) {
  var payload = buildPayload(e.namedValues, new Date(e.values[0]));
  try {
    postTransaction(payload);
  } catch (err) {
    logError(payload, err);
  }
}

function buildPayload(v, submittedAt) {
  function first(key) {
    return (v[key] && v[key][0]) ? String(v[key][0]).trim() : '';
  }
  var arca = first(Q_ARCA);
  return {
    p_occurred_on: Utilities.formatDate(
      submittedAt, 'America/Argentina/Buenos_Aires', 'yyyy-MM-dd'),
    p_type: 'outflow', // the Form is outflow-only by design (spec §8)
    p_description: first(Q_NAME),
    p_category: first(Q_CATEGORY),
    p_amount: parseFloat(first(Q_AMOUNT).replace(',', '.')),
    p_currency: first(Q_CURRENCY) || 'ARS',
    p_fiscal: arca === '' ? true : arca !== ARCA_NEGRO, // blank -> Blanco
    p_payment_method: first(Q_PAYMENT) || null,
    p_source: 'form'
  };
}

function postTransaction(payload) {
  var res = UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/rpc/add_transaction', {
    method: 'post',
    contentType: 'application/json',
    headers: { apikey: SERVICE_KEY, Authorization: 'Bearer ' + SERVICE_KEY },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() >= 300) {
    throw new Error(res.getResponseCode() + ': ' + res.getContentText());
  }
}

function logError(payload, err) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tab = ss.getSheetByName('errors') || ss.insertSheet('errors');
  tab.appendRow([new Date(), JSON.stringify(payload), String(err)]);
}

/** Daily: retry failed posts, and ping Supabase so the free tier never pauses. */
function retryErrors() {
  keepAlive();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tab = ss.getSheetByName('errors');
  if (!tab || tab.getLastRow() === 0) return;
  var rows = tab.getDataRange().getValues();
  for (var i = rows.length - 1; i >= 0; i--) {
    try {
      postTransaction(JSON.parse(rows[i][1]));
      tab.deleteRow(i + 1);
    } catch (err) {
      // stays in the tab for the next daily retry
    }
  }
}

function keepAlive() {
  UrlFetchApp.fetch(SUPABASE_URL + '/rest/v1/categories?select=id&limit=1', {
    headers: { apikey: SERVICE_KEY, Authorization: 'Bearer ' + SERVICE_KEY }
  });
}
```

- [ ] **Step 2: Commit**

```powershell
git add apps-script/form-sync.gs
git commit -m "feat: Apps Script Form-to-Supabase bridge with error tab and keep-alive"
```

- [ ] **Step 3: USER CHECKPOINT — update the Form and install the script**

Ask Tommy to (with the executor guiding):
1. **Edit the Form:** Category question → change from checkboxes to *single choice*
   (dropdown), options = the 12 outflow categories. Add a *Payment Method* dropdown
   (13 options, exact names from the playbook). Make *ARCA* optional; its options
   must include `Negro` spelled exactly (blank or anything else = Blanco). If his
   ARCA options differ (e.g. Sí/No), update `ARCA_NEGRO` in the script to match the
   option that means *not registered*.
2. **Install the script:** open the *linked responses spreadsheet* → Extensions →
   Apps Script → paste `apps-script/form-sync.gs` → set the two Script Properties →
   add the two triggers listed in the file header.
3. Confirm the Form's question titles match the `Q_*` constants (or edit constants).

- [ ] **Step 4: Verify end-to-end (happy path + error path)**

1. Tommy submits a real test expense through the Form.
   Run: `python -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; print(rest_get('v_transactions', {'source': 'eq.form', 'order': 'created_at.desc', 'limit': '1'}))"`
   Expected: the just-submitted row, with correct category/fiscal/payment method.
2. Error path: in Script Properties, temporarily break `SUPABASE_SERVICE_KEY`
   (add an `x`), submit another test expense → an `errors` tab row appears.
   Restore the key, run `retryErrors` manually from the editor → the errors tab
   empties and the row appears in Supabase.
3. Delete both test rows:
   `python -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_delete; rest_delete('transactions', {'source': 'eq.form', 'description': 'eq.<the test description used>'})"`

---

### Task 8: Migration from the old Google Sheet

**Files:**
- Create: `scripts/migrate_from_sheets.py`

- [ ] **Step 1: USER CHECKPOINT — get the CSV export**

Ask Tommy to export the responses/data sheet as CSV (File → Download → CSV) and
drop it in the repo root as `sheet-export.csv` (gitignored by adding a line
`sheet-export.csv` to `.gitignore` — personal data stays out of git).

- [ ] **Step 2: Inspect the CSV header and adjust the CONFIG block**

Run: `python -c "import csv; print(next(csv.reader(open('sheet-export.csv', encoding='utf-8-sig'))))"`
Compare against the `COL_*` constants in the script (next step) and fix any that
differ (column names, ARCA values, date format). This is expected configuration,
not a deviation.

- [ ] **Step 3: Create `scripts/migrate_from_sheets.py`**

```python
"""Migrate the old Google Sheet into Supabase.

Usage:
  python scripts/migrate_from_sheets.py sheet-export.csv            # dry run
  python scripts/migrate_from_sheets.py sheet-export.csv --execute  # insert + reconcile
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime

from lib import db_conn

# ---- CONFIG: adjust to the CSV's actual headers/values (Task 8 Step 2) ------
COL_TIMESTAMP = "Timestamp"
COL_DESCRIPTION = "Name"
COL_CATEGORY = "Category"       # comma-separated when multiple boxes were ticked
COL_ARCA = "ARCA"
COL_CURRENCY = "Currency"
COL_AMOUNT = "Amount"
COL_USD = "USD"                 # the sheet's per-row USD conversion column
ARCA_NEGRO_VALUES = {"negro", "no"}          # lowercase values meaning fiscal=False
DATE_FORMATS = ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
CATEGORY_MAP = {                # old sheet name -> new category name
    "Comida": "Comida", "Salidas": "Salidas", "Transporte": "Transporte",
    "Compras": "Compras", "Regalos": "Regalos", "Suscripciones": "Suscripciones",
    "Impuestos": "Impuestos",
}
COMBO_RULES = {                 # frozenset of NEW names -> winning category
    frozenset({"Comida", "Salidas"}): "Salidas",
}
# -----------------------------------------------------------------------------


def parse_date(ts):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(ts.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable timestamp: {ts!r}")


def resolve_category(raw):
    names = [CATEGORY_MAP[c.strip()] for c in raw.split(",") if c.strip()]
    if not names:
        raise ValueError("row has no category")
    if len(names) == 1:
        return names[0], []
    winner = COMBO_RULES.get(frozenset(names), names[0])
    tags = [n.lower() for n in names if n != winner]
    return winner, tags


def load_rows(path):
    rows, problems = [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for lineno, r in enumerate(csv.DictReader(f), start=2):
            try:
                amount = float(str(r[COL_AMOUNT]).replace(",", ""))
                usd = float(str(r[COL_USD]).replace(",", ""))
                currency = (r[COL_CURRENCY] or "ARS").strip().upper()
                category, tags = resolve_category(r[COL_CATEGORY])
                fiscal = str(r[COL_ARCA]).strip().lower() not in ARCA_NEGRO_VALUES
                usd_rate = 1.0 if currency == "USD" else amount / usd
                rows.append(dict(
                    occurred_on=parse_date(r[COL_TIMESTAMP]),
                    description=r[COL_DESCRIPTION].strip() or "(sin descripción)",
                    category=category, tags=tags, amount=amount, currency=currency,
                    fiscal=fiscal, usd=usd, usd_rate=usd_rate, line=lineno,
                ))
            except Exception as e:  # noqa: BLE001 — report every bad row, don't die
                problems.append((lineno, str(e)))
    return rows, problems


def dry_run_report(rows, problems):
    multi = [r for r in rows if r["tags"]]
    print(f"\n=== DRY RUN — {len(rows)} importable rows, {len(problems)} problems ===")
    if problems:
        print("\n-- Problem rows (fix in CSV or CONFIG, then rerun) --")
        for lineno, msg in problems:
            print(f"  line {lineno}: {msg}")
    if multi:
        print(f"\n-- Multi-category rows ({len(multi)}) — winner + tags --")
        for r in multi:
            print(f"  line {r['line']:5} {r['occurred_on']} "
                  f"{r['description'][:40]:40} -> {r['category']} tags={r['tags']}")
    per_month = defaultdict(float)
    for r in rows:
        per_month[r["occurred_on"].strftime("%Y-%m")] += r["usd"]
    print("\n-- Per-month USD totals (from the sheet's own conversion) --")
    for m in sorted(per_month):
        print(f"  {m}  {per_month[m]:12.2f}")
    print("\nRe-run with --execute to insert.")


def insert_rows(rows):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select name, id from categories")
        cat_ids = dict(cur.fetchall())
        cur.executemany(
            """insert into transactions
                 (occurred_on, type, description, category_id, amount, currency,
                  fiscal, tags, usd_rate, source)
               values (%(occurred_on)s, 'outflow', %(description)s, %(category_id)s,
                       %(amount)s, %(currency)s, %(fiscal)s, %(tags)s,
                       %(usd_rate)s, 'migration')""",
            [dict(r, category_id=cat_ids[r["category"]]) for r in rows],
        )
        conn.commit()
    print(f"inserted {len(rows)} rows with source='migration'")


def reconcile(rows):
    per_month_sheet = defaultdict(float)
    for r in rows:
        per_month_sheet[r["occurred_on"].strftime("%Y-%m")] += r["usd"]
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select to_char(occurred_on, 'YYYY-MM'), sum(amount_usd)
                 from transactions where source = 'migration' group by 1"""
        )
        per_month_db = {m: float(t) for m, t in cur.fetchall()}
    print(f"\n{'month':8} {'sheet_usd':>12} {'db_usd':>12} {'diff%':>8}")
    worst = 0.0
    for m in sorted(per_month_sheet):
        s, d = per_month_sheet[m], per_month_db.get(m, 0.0)
        diff = abs(s - d) / s * 100 if s else 0.0
        worst = max(worst, diff)
        print(f"{m:8} {s:12.2f} {d:12.2f} {diff:8.3f}")
    verdict = "PASS" if worst < 0.5 else "FAIL"
    print(f"\nRECONCILIATION {verdict} — worst monthly diff {worst:.3f}% (limit 0.5%)")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rows, problems = load_rows(sys.argv[1])
    if "--execute" not in sys.argv:
        dry_run_report(rows, problems)
        return
    if problems:
        sys.exit(f"refusing to execute with {len(problems)} problem rows — "
                 "run the dry run and fix them first")
    insert_rows(rows)
    reconcile(rows)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Dry run**

Run: `python scripts/migrate_from_sheets.py sheet-export.csv`
Expected: zero problem rows (fix CONFIG/CSV and rerun until so), the multi-category
table, and per-month totals that eyeball-match the old sheet.

- [ ] **Step 5: USER CHECKPOINT — Tommy reviews the dry run**

Show Tommy the multi-category rows and the problems list. Adjust `COMBO_RULES`
per his answers and rerun the dry run until he approves.

- [ ] **Step 6: Execute and reconcile**

Run: `python scripts/migrate_from_sheets.py sheet-export.csv --execute`
Expected: `inserted N rows`, then the reconciliation table ending in
`RECONCILIATION PASS`. If FAIL: investigate the worst month (usually a row the
sheet's USD formula had errored on), fix, and — since this must stay re-runnable —
delete and redo: `delete from transactions where source = 'migration'` via
`python -c "import sys; sys.path.insert(0,'scripts'); from lib import db_conn; c=db_conn(); c.execute('delete from transactions where source=%s', ('migration',)); c.commit()"`
then `--execute` again.

- [ ] **Step 7: Verify history through the agent surface**

Run: `python -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; [print(r) for r in rest_get('v_monthly_summary', {'order': 'month.desc', 'limit': '5'})]"`
Expected: recent months with sane USD totals.

- [ ] **Step 8: Commit**

```powershell
git add scripts/migrate_from_sheets.py .gitignore
git commit -m "feat: sheet migration with combo rules, dry-run report, reconciliation"
```

---

### Task 9: Cutover

**Files:**
- Modify: `README.md` (status note)

- [ ] **Step 1: Verify the cron job ran unattended**

At least one calendar day after Task 3, run:
`python -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; print(rest_get('fx_rates', {'order': 'rate_date.desc', 'limit': '2'}))"`
Expected: a row dated within the last few days that was NOT inserted by the
backfill/tests (check `cron.job_run_details` via `run_report` if unsure:
`select * from cron.job_run_details order by start_time desc limit 5` — status
`succeeded`).

- [ ] **Step 2: USER CHECKPOINT — archive the old system**

Ask Tommy to: rename the old spreadsheet to `[ARCHIVED] ...`, stop using its
analysis tabs, and confirm the Form now feeds Supabase only (the Form → Sheet →
Apps Script → Supabase chain keeps the sheet as a raw receipt log; that's fine).

- [ ] **Step 3: Final smoke pass**

Run: `python -m pytest scripts/test_smoke.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Update README status and commit**

Append to `README.md`:
```markdown
## Status
Live since 2026-07. Source of truth: Supabase. Old sheet archived read-only.
Entry paths: Google Form (interim), any agent via docs/agent-playbook.md.
```

```powershell
git add README.md
git commit -m "docs: mark system live, old sheet archived"
```
