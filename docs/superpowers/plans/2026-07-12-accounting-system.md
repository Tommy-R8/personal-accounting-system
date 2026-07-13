# Personal Accounting System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the agent-agnostic accounting backend specified in `docs/superpowers/specs/2026-07-12-accounting-system-design.md` — Supabase database with validating RPCs, report views, daily Blue-rate job, the fiscal module (invoices, conversions, monotributo brackets), Google Form bridge, and full migration from both old workbooks (expense tracker + Facturación y Cobranzas).

**Architecture:** All logic lives inside a free-tier Supabase Postgres (tables, triggers, RPCs, views, pg_cron job). Local Python scripts handle migrations-apply, rate backfill, workbook migrations, and smoke tests over the REST API. An Apps Script bridges the Google Form. `docs/agent-playbook.md` is the agent-facing contract.

**Tech Stack:** Supabase (Postgres 15+, PostgREST, pg_cron, `http` extension), Python 3.11+ (psycopg 3, requests, openpyxl, pytest, python-dotenv), Google Apps Script.

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

- [ ] **Step 1: Extend `.gitignore`** (it already exists with `data/` and `.env*` entries — append what's missing)

```gitignore
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
openpyxl>=3.1
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


def try_delete(path, params):
    """Teardown helper: tolerate 404s while tables don't exist yet (red phases)."""
    try:
        rest_delete(path, params)
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="session")
def cleanup():
    yield
    # children first: reimbursements reference their parent via FK
    try_delete("transactions", {"source": "eq.test", "parent_id": "not.is.null"})
    try_delete("transactions", {"source": "eq.test"})


# --- Task 2: schema + seeds -------------------------------------------------

def test_seed_categories():
    cats = rest_get("categories", {"select": "name,kind"})
    assert len(cats) == 18
    assert {"name": "Salidas", "kind": "outflow"} in cats
    assert {"name": "Reintegros", "kind": "inflow"} in cats
    assert not any(c["name"] == "Educación" for c in cats)


def test_seed_payment_methods():
    pms = rest_get("payment_methods", {"select": "name"})
    assert len(pms) == 15  # 13 payment methods + BBVA & Buenbit (conversion platforms)
    assert {"name": "Gnosis Pay"} in pms
    assert {"name": "BBVA"} in pms


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
Expected: all 3 FAIL — the two seed tests with 404 from `rest_get`, the trigger
test with `psycopg.errors.UndefinedTable` (it queries via SQL, not REST). Tables
don't exist yet.

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

-- The last two (BBVA = the bank account, Buenbit) exist as conversion platforms
-- (spec §5.2/§5.7); the table doubles as the platform list.
insert into payment_methods (name) values
  ('BBVA Crédito'), ('BBVA Débito'), ('MercadoPago Crédito'), ('Gnosis Pay'),
  ('Exa'), ('Lemon'), ('Ripio'), ('Brubank'), ('Belo Lux'), ('Belo'),
  ('Peanut'), ('Cash'), ('USD Cash'), ('BBVA'), ('Buenbit');
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

Run: `python -m pytest scripts/test_smoke.py -v`
Expected: the 11 new Task-4 tests FAIL with 404 (`add_transaction` not found); the
6 earlier tests still PASS.

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

### Task 6: Fiscal schema + RPCs — invoices, conversions, brackets

**Files:**
- Create: `supabase/migrations/005_fiscal_schema.sql`
- Modify: `scripts/test_smoke.py` (extend cleanup fixture + append tests)

- [ ] **Step 1: Extend the cleanup fixture in `scripts/test_smoke.py`**

Replace the existing `cleanup` fixture body with:

```python
@pytest.fixture(autouse=True, scope="session")
def cleanup():
    yield
    # children first: reimbursements reference their parent via FK
    try_delete("transactions", {"source": "eq.test", "parent_id": "not.is.null"})
    try_delete("transactions", {"source": "eq.test"})
    try_delete("invoices", {"source": "eq.test"})
    try_delete("conversions", {"source": "eq.test"})
```

- [ ] **Step 2: Append failing tests to `scripts/test_smoke.py`**

```python
# --- Task 6: fiscal schema + RPCs ---------------------------------------------

def add_invoice(**kw):
    payload = {
        "p_issued_on": "2026-07-06",
        "p_punto_venta": "9999",  # test PV — never collides with real 0001 numbers
        "p_invoice_number": kw.pop("n", 1),
        "p_amount_ars": 100000,
        "p_source": "test",
    }
    payload.update(kw)
    return rpc("add_invoice", payload)


def add_conversion(**kw):
    payload = {
        "p_occurred_on": "2026-07-06",
        "p_kind": "crypto_sale",
        "p_ars_amount": 150000,
        "p_usd_amount": 100,
        "p_platform": "Belo",
        "p_source": "test",
    }
    payload.update(kw)
    return rpc("add_conversion", payload)


def test_add_invoice_and_duplicate_rejected():
    r1 = add_invoice(n=901)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "issued"
    r2 = add_invoice(n=901)
    assert r2.status_code == 400
    assert "already exists" in r2.json()["message"]


def test_conversion_rate_effective_generated():
    r = add_conversion(p_ars_amount=150000, p_usd_amount=100)
    assert r.status_code == 200, r.text
    assert abs(float(r.json()["rate_effective"]) - 1500.0) < 0.01


def test_crypto_sale_requires_usd():
    r = add_conversion(p_usd_amount=None)
    assert r.status_code == 400
    assert "usd_amount" in r.json()["message"]


def test_bank_deposit_rejects_usd():
    r = add_conversion(p_kind="bank_deposit", p_usd_amount=100)
    assert r.status_code == 400
    assert "usd_amount" in r.json()["message"]


def test_bank_deposit_ok_without_usd():
    r = add_conversion(p_kind="bank_deposit", p_usd_amount=None, p_platform="BBVA")
    assert r.status_code == 200, r.text
    assert r.json()["rate_effective"] is None


def test_unknown_platform_lists_valid_ones():
    r = add_conversion(p_platform="Binance")
    assert r.status_code == 400
    assert "valid platforms" in r.json()["message"]


def test_brackets_seeded_current_scale():
    rows = rest_get("monotributo_brackets", {"select": "category,annual_cap",
                                             "valid_from": "eq.2026-01-01"})
    assert len(rows) == 11  # A..K
    d = next(r for r in rows if r["category"] == "D")
    assert abs(float(d["annual_cap"]) - 26212853.42) < 0.01


def test_suggest_invoices_properties():
    r = rpc("suggest_invoices", {"p_target_ars": 1044320})
    assert r.status_code == 200, r.text
    amounts = [float(a) for a in r.json()]
    assert all(a % 1000 == 0 for a in amounts)
    assert all(75000 <= a <= 200000 for a in amounts)  # spec §13: EVERY amount
    assert abs(sum(amounts) - 1044320) <= 500  # = target rounded to nearest 1000


def test_suggest_invoices_small_target_single_invoice():
    r = rpc("suggest_invoices", {"p_target_ars": 120000})
    assert r.status_code == 200, r.text
    assert [float(a) for a in r.json()] == [120000.0]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -v`
Expected: the 9 new Task-6 tests FAIL with 404 (tables/RPCs don't exist); all
earlier tests still PASS.

- [ ] **Step 4: Create `supabase/migrations/005_fiscal_schema.sql`**

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

create table conversions (
  id             uuid primary key default gen_random_uuid(),
  occurred_on    date not null,
  kind           text not null
                   check (kind in ('crypto_sale','cash_sale','bank_deposit')),
  ars_amount     numeric(14,2) not null check (ars_amount > 0),
  usd_amount     numeric(14,2) check (usd_amount > 0),
  rate_quoted    numeric,
  rate_effective numeric generated always as
                   (case when usd_amount is not null
                         then ars_amount / usd_amount end) stored,
  platform_id    int not null references payment_methods(id),
  notes          text,
  source         text not null,
  created_at     timestamptz not null default now(),
  check ((kind = 'bank_deposit') = (usd_amount is null))
);

create table monotributo_brackets (
  category    text not null check (category in
                ('A','B','C','D','E','F','G','H','I','J','K')),
  valid_from  date not null,
  annual_cap  numeric(14,2) not null check (annual_cap > 0),
  primary key (category, valid_from)
);

alter table invoices enable row level security;
alter table conversions enable row level security;
alter table monotributo_brackets enable row level security;

-- Verified caps from Tommy's Overview sheet (D–G, 2025 and 2026 scales).
-- A–C and H–K for the current scale are filled at Step 5 (USER CHECKPOINT).
insert into monotributo_brackets (category, valid_from, annual_cap) values
  ('D', '2025-01-01', 22934610.05), ('E', '2025-01-01', 26977793.60),
  ('F', '2025-01-01', 33809379.57), ('G', '2025-01-01', 40431835.35),
  ('D', '2026-01-01', 26212853.42), ('E', '2026-01-01', 30833964.37),
  ('F', '2026-01-01', 38642048.36), ('G', '2026-01-01', 46211109.37);

create or replace function add_invoice(
  p_issued_on date,
  p_invoice_number int,
  p_amount_ars numeric,
  p_source text,
  p_punto_venta text default '0001',
  p_status text default 'issued',
  p_notes text default null
) returns invoices
language plpgsql as $$
declare
  v_row invoices%rowtype;
begin
  if exists (select 1 from invoices
              where punto_venta = p_punto_venta
                and invoice_number = p_invoice_number) then
    raise exception 'invoice %-% already exists',
      p_punto_venta, lpad(p_invoice_number::text, 8, '0');
  end if;
  insert into invoices (issued_on, punto_venta, invoice_number, amount_ars,
                        status, notes, source)
  values (p_issued_on, p_punto_venta, p_invoice_number, p_amount_ars,
          p_status, p_notes, p_source)
  returning * into v_row;
  return v_row;
end $$;

create or replace function add_conversion(
  p_occurred_on date,
  p_kind text,
  p_ars_amount numeric,
  p_platform text,
  p_source text,
  p_usd_amount numeric default null,
  p_rate_quoted numeric default null,
  p_notes text default null
) returns conversions
language plpgsql as $$
declare
  v_platform_id int;
  v_valid text;
  v_row conversions%rowtype;
begin
  if p_kind not in ('crypto_sale','cash_sale','bank_deposit') then
    raise exception 'unknown kind "%" — valid kinds: crypto_sale, cash_sale, bank_deposit', p_kind;
  end if;
  if p_kind in ('crypto_sale','cash_sale') and p_usd_amount is null then
    raise exception '% requires usd_amount (the USD/USDC sold)', p_kind;
  end if;
  if p_kind = 'bank_deposit' and p_usd_amount is not null then
    raise exception 'bank_deposit must not carry usd_amount (no USD leg)';
  end if;

  select id into v_platform_id
    from payment_methods
   where lower(name) = lower(trim(p_platform)) and active;
  if not found then
    select string_agg(name, ', ' order by id) into v_valid
      from payment_methods where active;
    raise exception 'unknown platform "%" — valid platforms: %', p_platform, v_valid;
  end if;

  insert into conversions (occurred_on, kind, ars_amount, usd_amount,
                           rate_quoted, platform_id, notes, source)
  values (p_occurred_on, p_kind, p_ars_amount, p_usd_amount,
          p_rate_quoted, v_platform_id, p_notes, p_source)
  returning * into v_row;
  return v_row;
end $$;

-- Replaces the sheet's ROUND(RANDBETWEEN(75000;200000);-3) workflow: a set of
-- invoice amounts covering a deposit target. Suggestion only — writes nothing.
-- Contract (spec §6.2/§13): sum = target rounded to nearest 1,000; every amount a
-- multiple of 1,000; for targets > p_max every amount is within [p_min, p_max]
-- (guaranteed by picking each amount from the range that keeps the remainder
-- coverable by the invoices still to come); target <= p_max returns one invoice.
create or replace function suggest_invoices(
  p_target_ars numeric,
  p_min numeric default 75000,
  p_max numeric default 200000
) returns numeric[]
language plpgsql volatile as $$
declare
  v_target numeric := round(p_target_ars / 1000) * 1000;
  v_n int;
  v_remaining numeric;
  v_amounts numeric[] := '{}';
  v_lo numeric;
  v_hi numeric;
  v_pick numeric;
begin
  if p_target_ars <= 0 then
    raise exception 'target must be positive';
  end if;
  if p_min <= 0 or p_max < p_min
     or p_min % 1000 <> 0 or p_max % 1000 <> 0 then
    raise exception 'need 0 < p_min <= p_max, both multiples of 1000';
  end if;
  if v_target <= p_max then
    return array[v_target];  -- may be below p_min: cannot invoice above the target
  end if;
  v_n := ceil(v_target / p_max);  -- fewest invoices that can carry the target
  if v_target / v_n < p_min then
    raise exception 'target % cannot be split into invoices within [%, %]',
      p_target_ars, p_min, p_max;
  end if;
  v_remaining := v_target;
  for i in reverse v_n .. 2 loop  -- after this pick, i-1 invoices remain
    v_lo := greatest(p_min, v_remaining - (i - 1) * p_max);
    v_hi := least(p_max, v_remaining - (i - 1) * p_min);
    v_pick := round((v_lo + random() * (v_hi - v_lo)) / 1000) * 1000;
    v_pick := least(greatest(v_pick, v_lo), v_hi);
    v_amounts := v_amounts || v_pick;
    v_remaining := v_remaining - v_pick;
  end loop;
  v_amounts := v_amounts || v_remaining;  -- in [p_min, p_max] by construction
  return v_amounts;
end $$;

grant execute on function add_invoice(date, int, numeric, text, text, text, text)
  to service_role;
grant execute on function add_conversion(
  date, text, numeric, text, text, numeric, numeric, text) to service_role;
grant execute on function suggest_invoices(numeric, numeric, numeric) to service_role;
```

- [ ] **Step 5: USER CHECKPOINT — complete the bracket seeds**

The migration seeds only D–G (the categories visible in Tommy's Overview sheet,
values verified). Ask Tommy for the full current ARCA scale (categories A–K with
their annual caps — he offered to provide the whole table), or look it up on
ARCA's monotributo page and have him confirm. Add the missing rows as
`supabase/migrations/006_bracket_seeds.sql` with `valid_from = '2026-01-01'`
(adjust if the current scale's effective date differs — confirm with Tommy).
The `test_brackets_seeded_current_scale` test expects 11 categories for that date.

- [ ] **Step 6: Apply and run tests**

Run:
```powershell
python scripts/apply_migrations.py
python -m pytest scripts/test_smoke.py -v
```
Expected: `apply 005_fiscal_schema.sql`, `apply 006_bracket_seeds.sql`; all tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add supabase/migrations/005_fiscal_schema.sql supabase/migrations/006_bracket_seeds.sql scripts/test_smoke.py
git commit -m "feat: fiscal module schema and RPCs — invoices, conversions, brackets, suggest_invoices"
```

---

### Task 7: Fiscal views — deposit status, monotributo status, fiscal balance

**Files:**
- Create: `supabase/migrations/007_fiscal_views.sql`
- Modify: `scripts/test_smoke.py` (append tests)

- [ ] **Step 1: Append failing tests to `scripts/test_smoke.py`**

```python
# --- Task 7: fiscal views -------------------------------------------------------

def _deposit_status():
    return {k: float(v) for k, v in rest_get("v_deposit_status")[0].items()}


def test_deposit_status_metrics_move_correctly():
    """Insert a known fixture and assert the five §5.9 metric deltas."""
    before = _deposit_status()
    add_invoice(n=902, p_amount_ars=300000)
    add_conversion(p_kind="crypto_sale", p_ars_amount=100000, p_usd_amount=70)
    add_conversion(p_kind="cash_sale", p_ars_amount=50000, p_usd_amount=35,
                   p_platform="Cash")
    add_conversion(p_kind="bank_deposit", p_ars_amount=20000, p_usd_amount=None,
                   p_platform="BBVA")
    after = _deposit_status()
    assert after["total_facturado"] - before["total_facturado"] == 300000
    assert after["total_depositado"] - before["total_depositado"] == 120000  # crypto+deposit
    assert after["total_cobrado"] - before["total_cobrado"] == 150000        # crypto+cash
    assert after["restante_por_depositar"] - before["restante_por_depositar"] == 180000
    assert after["disponible_para_depositar"] - before["disponible_para_depositar"] == 30000


def test_monotributo_status_shape_and_math():
    rows = rest_get("v_monotributo_status")
    assert len(rows) == 11  # one per category of the current scale
    d = next(r for r in rows if r["category"] == "D")
    assert float(d["max_monthly_avg"]) == round(float(d["annual_cap"]) / 12, 2)
    assert isinstance(d["cap_exceeded"], bool)


def test_fiscal_balance_view_shape():
    rows = rest_get("v_fiscal_balance", {"order": "month.desc", "limit": "1"})
    assert set(rows[0].keys()) >= {"month", "invoiced_ars", "blanco_spend_ars",
                                   "invoiced_cum", "blanco_cum"}


def test_fiscal_balance_converts_usd_outflows_at_blue_sell():
    """A 100 USD Blanco outflow must add ~100*blue_sell ARS, not 100 ARS."""
    def blanco():
        rows = rest_get("v_fiscal_balance", {"month": "eq.2026-07-01"})
        return float(rows[0]["blanco_spend_ars"]) if rows else 0.0
    before = blanco()
    add(p_amount=100, p_currency="USD", p_occurred_on="2026-07-06")
    rate = float(rest_get("fx_rates", {"select": "blue_sell",
                                       "rate_date": "lte.2026-07-06",
                                       "order": "rate_date.desc",
                                       "limit": "1"})[0]["blue_sell"])
    assert abs(blanco() - before - 100 * rate) < 0.05


def test_monotributo_view_uses_latest_applicable_scale():
    d = next(r for r in rest_get("v_monotributo_status") if r["category"] == "D")
    assert float(d["annual_cap"]) == 26212853.42  # 2026 scale, not 2025's 22934610.05


def test_monotributo_view_ignores_future_scales():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into monotributo_brackets values ('D', '2099-01-01', 1)")
        cur.execute("select annual_cap from v_monotributo_status where category = 'D'")
        cap = float(cur.fetchone()[0])
        conn.rollback()  # discard the future-dated row — no cleanup needed
    assert cap == 26212853.42  # a scale that hasn't taken effect must be ignored
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -v -k "deposit_status or monotributo or fiscal_balance"`
Expected: new tests FAIL (views missing).

- [ ] **Step 3: Create `supabase/migrations/007_fiscal_views.sql`**

```sql
-- The five §5.9 metrics, cumulative over all time. Formulas verified against
-- Tommy's Overview sheet to the peso (spec §5.9).
create view v_deposit_status as
with inv as (
  select coalesce(sum(amount_ars), 0) as facturado
    from invoices where status = 'issued'
), conv as (
  select coalesce(sum(ars_amount) filter
           (where kind in ('crypto_sale','bank_deposit')), 0) as depositado,
         coalesce(sum(ars_amount) filter
           (where kind in ('crypto_sale','cash_sale')), 0) as cobrado
    from conversions
)
select inv.facturado               as total_facturado,
       conv.depositado             as total_depositado,
       conv.cobrado                as total_cobrado,
       inv.facturado - conv.depositado as restante_por_depositar,
       conv.cobrado - conv.depositado  as disponible_para_depositar
from inv, conv;

-- One row per category of the CURRENT scale (latest valid_from <= today),
-- compared against the 12-month-rolling invoiced total.
create view v_monotributo_status as
with current_scale as (
  select category, annual_cap
    from monotributo_brackets
   where valid_from = (select max(valid_from) from monotributo_brackets
                        where valid_from <= current_date)
), rolling as (
  select coalesce(sum(amount_ars), 0) as invoiced_12m
    from invoices
   where status = 'issued'
     and issued_on > current_date - interval '12 months'
)
select cs.category,
       cs.annual_cap,
       r.invoiced_12m,
       cs.annual_cap - r.invoiced_12m       as headroom,
       round(cs.annual_cap / 12, 2)         as max_monthly_avg,
       round(r.invoiced_12m / 12, 2)        as actual_monthly_avg,
       r.invoiced_12m > cs.annual_cap       as cap_exceeded
from current_scale cs cross join rolling r
order by cs.annual_cap;

-- Invoiced vs spent en Blanco, per month + cumulative, in ARS. Outflows are
-- GROSS (no reimbursement netting): this view tracks registered money movement,
-- not personal cost. USD outflows convert to ARS at the Blue sell rate for their
-- date from fx_rates (spec §6.3) — NOT the row's usd_rate, which is 1.0 for USD
-- rows by construction and cannot convert USD to ARS.
create view v_fiscal_balance as
with inv as (
  select date_trunc('month', issued_on)::date as m, sum(amount_ars) as invoiced
    from invoices where status = 'issued' group by 1
), sp as (
  select date_trunc('month', t.occurred_on)::date as m,
         sum(case when t.currency = 'ARS' then t.amount
                  else t.amount * (select f.blue_sell from fx_rates f
                                    where f.rate_date <= t.occurred_on
                                    order by f.rate_date desc limit 1)
             end) as blanco
    from transactions t
   where t.type = 'outflow' and t.fiscal
   group by 1
)
select coalesce(inv.m, sp.m)                       as month,
       coalesce(inv.invoiced, 0)                   as invoiced_ars,
       round(coalesce(sp.blanco, 0), 2)            as blanco_spend_ars,
       sum(coalesce(inv.invoiced, 0))
         over (order by coalesce(inv.m, sp.m))     as invoiced_cum,
       round(sum(coalesce(sp.blanco, 0))
         over (order by coalesce(inv.m, sp.m)), 2) as blanco_cum
from inv full outer join sp on inv.m = sp.m
order by 1;

alter view v_deposit_status set (security_invoker = true);
alter view v_monotributo_status set (security_invoker = true);
alter view v_fiscal_balance set (security_invoker = true);
```

- [ ] **Step 4: Apply and run the full suite**

Run:
```powershell
python scripts/apply_migrations.py
python -m pytest scripts/test_smoke.py -v
```
Expected: `apply 007_fiscal_views.sql`; **all** tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add supabase/migrations/007_fiscal_views.sql scripts/test_smoke.py
git commit -m "feat: fiscal views — deposit status, monotributo brackets, invoiced vs blanco"
```

---

### Task 8: Agent playbook

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
(The table also contains `BBVA` and `Buenbit` — those are conversion PLATFORMS
(§Fiscal module), not expense payment methods; never tag a transaction with them.)

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
- `v_deposit_status` — single row: Total Facturado / Depositado / Cobrado,
  Restante por Depositar, Disponible para Depositar (all ARS, cumulative)
- `v_monotributo_status` — one row per bracket category: cap, 12-month invoiced,
  headroom, monthly averages, `cap_exceeded`
- `v_fiscal_balance` — per month: `invoiced_ars` vs `blanco_spend_ars` + cumulatives

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

## Fiscal module (invoices & conversions)

**Recording an invoice** (Tommy issued a factura in ARCA and tells you about it):
`POST /rest/v1/rpc/add_invoice` with `p_issued_on`, `p_invoice_number` (int, e.g. 218),
`p_amount_ars`, `p_source`. `p_punto_venta` defaults to `"0001"`.

**Recording a conversion** — three kinds (`p_kind`):
- `crypto_sale`: sold USDC for ARS on an exchange → needs `p_usd_amount` and
  `p_platform` (Belo, Ripio, Buenbit...). The ARS is en blanco immediately.
- `cash_sale`: sold USD/USDC for ARS *cash* via dealer → `p_usd_amount`,
  `p_platform: "Cash"`.
- `bank_deposit`: deposited ARS cash into a bank → NO `p_usd_amount`,
  `p_platform` = the bank (e.g. "BBVA").
`POST /rest/v1/rpc/add_conversion` with `p_occurred_on`, `p_ars_amount`, plus the
above. Optional `p_rate_quoted` if Tommy mentions the quoted rate.

**Conversions are NOT income.** Salary arriving in USDC is an inflow transaction
(`add_transaction`, category Sueldo, currency USD); selling that USDC later is a
conversion. Never create both from one event.

**Suggesting invoices to cover a deposit target:**
`POST /rest/v1/rpc/suggest_invoices` with `{"p_target_ars": <amount>}`
(optional `p_min`/`p_max`, defaults 75,000/200,000). Returns an array of amounts.
Typical flow: read `v_deposit_status` → propose covering `disponible_para_depositar`
or a user-given target → show Tommy the suggested amounts → he issues them in ARCA →
record each with `add_invoice`. **Never record an invoice he didn't confirm as
actually issued.**

**Typical fiscal questions** → which surface:
- "¿Cuánto me queda por depositar?" → `v_deposit_status.restante_por_depositar`
- "¿Cómo vengo con el monotributo?" → `v_monotributo_status` (watch `cap_exceeded`
  and `headroom` of his current category)
- "¿Facturé más de lo que gasté en blanco?" → `v_fiscal_balance`

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
`v_group_expenses` for it → add an invoice (punto de venta 9999) → add a
crypto_sale conversion → read `v_deposit_status` → call `suggest_invoices` for
500,000 → run one `run_report` query → delete the test rows:
```
DELETE /rest/v1/transactions?source=eq.playbook-check&parent_id=not.is.null
DELETE /rest/v1/transactions?source=eq.playbook-check
DELETE /rest/v1/invoices?source=eq.playbook-check
DELETE /rest/v1/conversions?source=eq.playbook-check
```
(using `p_source="playbook-check"` for all rows). If any step required knowledge
not in the playbook, fix the playbook now.

- [ ] **Step 3: Commit**

```powershell
git add docs/agent-playbook.md
git commit -m "docs: agent playbook — the agent-agnostic interface contract"
```

---

### Task 9: Google Form bridge

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
  var payload = null;
  try {
    // NOTE: never parse e.values[0] with new Date() — on an es-AR sheet it is a
    // dd/MM/yyyy string that V8 misparses. The handler runs within seconds of
    // submission, so "now" IS the submission time.
    payload = buildPayload(e.namedValues, new Date());
    postTransaction(payload);
  } catch (err) {
    logError(payload || { raw: JSON.stringify(e && e.namedValues) }, err);
  }
}

/** Amount parser tolerant of es-AR formats; throws (-> errors tab) on garbage. */
function parseAmount(raw) {
  raw = String(raw).replace(/\s/g, '');
  var lastComma = raw.lastIndexOf(','), lastDot = raw.lastIndexOf('.');
  if (lastComma > -1 && lastDot > -1) {
    // both present: the later one is the decimal separator ('1.234,56' -> 1234.56)
    var thousands = lastComma > lastDot ? '.' : ',';
    raw = raw.split(thousands).join('').replace(',', '.');
  } else if (lastComma > -1) {
    raw = raw.replace(',', '.'); // decimal comma ('1500,50' -> 1500.50)
  }
  var n = parseFloat(raw);
  if (!isFinite(n) || n <= 0) throw new Error('unparseable amount: ' + raw);
  return n;
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
    p_amount: parseAmount(first(Q_AMOUNT)),
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
   (13 options, exact names from the playbook — NOT the two platform-only entries
   BBVA/Buenbit). Make *ARCA* optional; its options must include `Negro` spelled
   exactly (blank or anything else = Blanco). If his ARCA options differ (e.g.
   Sí/No), update `ARCA_NEGRO` in the script to match the option that means *not
   registered*. On the *Amount* question add response validation → Number, so
   thousands-separator strings can't be submitted.
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

### Task 10: Migration — expense tracker workbook

**Files:**
- Create: `scripts/migrate_from_sheets.py`

The source structure was inspected on 2026-07-13 (spec §10.1): sheet
`Respuestas de formulario 1`, columns `Marca temporal, Name, Category, Currency,
Amount, ARCA, Blue USD Rate, USD Amount, Month`. Known data facts the script
handles: 90 multi-category rows (9 pair combos), 4 rows without category, 19 rows
with broken USD conversions (`#VALUE!` strings), 206 blank ARCA cells (→ Blanco).

- [ ] **Step 1: USER CHECKPOINT — get a fresh export**

Ask Tommy to re-download the responses workbook as `.xlsx` (File → Download →
Microsoft Excel) into `data/` (gitignored), replacing
`data/Personal Expenses Tracker (responses).xlsx` — the sheet is in daily use, so
migrate from a same-day snapshot.

- [ ] **Step 2: Verify the header still matches**

Run: `python -c "import openpyxl; ws = openpyxl.load_workbook(r'data/Personal Expenses Tracker (responses).xlsx', read_only=True)['Respuestas de formulario 1']; print([c.value for c in next(ws.iter_rows(max_row=1))])"`
Expected: the columns listed above. If they differ, update the `COL_*` indices in
the script — expected configuration, not a deviation.

- [ ] **Step 3: Create `scripts/migrate_from_sheets.py`**

```python
"""Migrate the old expense tracker workbook into Supabase (spec §10.1).

Usage:
  python scripts/migrate_from_sheets.py                # dry run
  python scripts/migrate_from_sheets.py --execute      # insert + reconcile
"""
import sys
from collections import defaultdict
from datetime import datetime

import openpyxl

from lib import db_conn

XLSX = "data/Personal Expenses Tracker (responses).xlsx"
SHEET = "Respuestas de formulario 1"

# ---- CONFIG (verified against the workbook on 2026-07-13) --------------------
# 0-based column indices: Marca temporal, Name, Category, Currency, Amount,
#                         ARCA, Blue USD Rate, USD Amount, Month
I_TS, I_NAME, I_CAT, I_CUR, I_AMOUNT, I_ARCA, I_RATE, I_USD, I_MONTH = range(9)
ARCA_NEGRO_VALUES = {"negro"}   # blank/None/'Blanco' -> fiscal=True
CATEGORY_MAP = {                # old sheet name -> new category name (1:1 today)
    "Comida": "Comida", "Salidas": "Salidas", "Transporte": "Transporte",
    "Compras": "Compras", "Regalos": "Regalos", "Suscripciones": "Suscripciones",
    "Impuestos": "Impuestos",
}
COMBO_RULES = {                 # frozenset of NEW names -> winning category.
    # Pre-seeded from the data (44 + 27 rows); the remaining 7 combos (19 rows)
    # get rules here during the dry-run review with Tommy.
    frozenset({"Comida", "Salidas"}): "Salidas",
    frozenset({"Compras", "Regalos"}): "Regalos",
}
# -------------------------------------------------------------------------------


def resolve_category(raw):
    if not raw or not str(raw).strip():
        raise ValueError("row has no category — assign one manually")
    names = [CATEGORY_MAP[c.strip()] for c in str(raw).split(",") if c.strip()]
    if len(names) == 1:
        return names[0], []
    winner = COMBO_RULES.get(frozenset(names), names[0])
    tags = [n.lower() for n in names if n != winner]
    return winner, tags


def fetch_rates():
    """Backfilled Blue sell rates, for rows whose sheet conversion is broken."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select rate_date, blue_sell from fx_rates order by rate_date")
        return cur.fetchall()  # ascending [(date, rate), ...]


def sell_rate_on(rates, day):
    """Latest rate on or before `day` (same fallback rule as add_transaction)."""
    best = None
    for rate_date, rate in rates:
        if rate_date > day:
            break
        best = rate
    if best is None:
        raise ValueError(f"no Blue rate on or before {day}")
    return float(best)


def load_rows():
    rates = fetch_rates()
    ws = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)[SHEET]
    rows, problems = [], []
    for lineno, r in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if r[I_TS] is None:
            continue  # trailing blank rows
        try:
            occurred_on = r[I_TS].date()
            amount = float(r[I_AMOUNT])
            currency = (str(r[I_CUR] or "ARS")).strip().upper()
            category, tags = resolve_category(r[I_CAT])
            arca = str(r[I_ARCA] or "").strip().lower()
            fiscal = arca not in ARCA_NEGRO_VALUES
            repaired = False
            if currency == "USD":
                usd, usd_rate = amount, 1.0
            elif isinstance(r[I_USD], (int, float)):
                usd = float(r[I_USD])           # the sheet's own conversion
                usd_rate = amount / usd
            else:
                # 19 known rows: Bluelytics 429 error text / #VALUE! in the sheet.
                usd_rate = sell_rate_on(rates, occurred_on)
                usd = amount / usd_rate
                repaired = True
            rows.append(dict(
                occurred_on=occurred_on,
                description=str(r[I_NAME] or "").strip() or "(sin descripción)",
                category=category, tags=tags, amount=amount, currency=currency,
                fiscal=fiscal, usd=usd, usd_rate=usd_rate, repaired=repaired,
                line=lineno,
            ))
        except Exception as e:  # noqa: BLE001 — report every bad row, don't die
            problems.append((lineno, f"{r[I_NAME]!r}: {e}"))
    return rows, problems


def dry_run_report(rows, problems):
    multi = [r for r in rows if r["tags"]]
    repaired = [r for r in rows if r["repaired"]]
    print(f"\n=== DRY RUN — {len(rows)} importable rows, {len(problems)} problems ===")
    if problems:
        print("\n-- Problem rows (fix in workbook or CONFIG, then rerun) --")
        for lineno, msg in problems:
            print(f"  line {lineno}: {msg}")
    if multi:
        print(f"\n-- Multi-category rows ({len(multi)}) — winner + tags --")
        for r in multi:
            print(f"  line {r['line']:5} {r['occurred_on']} "
                  f"{r['description'][:40]:40} -> {r['category']} tags={r['tags']}")
    if repaired:
        print(f"\n-- Repaired-rate rows ({len(repaired)}) — sheet conversion was "
              "broken; recomputed from fx_rates --")
        for r in repaired:
            print(f"  line {r['line']:5} {r['occurred_on']} "
                  f"{r['description'][:40]:40} rate={r['usd_rate']:.2f}")
    per_month = defaultdict(float)
    for r in rows:
        per_month[r["occurred_on"].strftime("%Y-%m")] += r["usd"]
    print("\n-- Per-month USD totals --")
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


def sheet_side():
    """Independent raw pass over the worksheet — deliberately NOT reusing the
    parsing pipeline, so a row load_rows() drops or mangles cannot cancel out
    of the comparison. Sums only the sheet's own numeric USD values."""
    ws = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)[SHEET]
    per_month, n_rows, n_broken = defaultdict(float), 0, 0
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[I_TS] is None:
            continue
        n_rows += 1
        if isinstance(r[I_USD], (int, float)):
            per_month[r[I_TS].strftime("%Y-%m")] += float(r[I_USD])
        else:
            n_broken += 1  # broken conversions: repaired rows, excluded here
    return per_month, n_rows, n_broken


def reconcile(rows):
    per_month_sheet, n_sheet_rows, n_broken = sheet_side()
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from transactions where source = 'migration'")
        n_db = cur.fetchone()[0]
        cur.execute(
            """select to_char(t.occurred_on, 'YYYY-MM'), sum(t.amount_usd)
                 from transactions t
                where t.source = 'migration'
                group by 1"""
        )
        per_month_db = {m: float(t) for m, t in cur.fetchall()}
    # db totals include repaired rows; subtract their recomputed USD per month
    # so both sides cover the same clean-row population (spec §10.1).
    repaired_by_month = defaultdict(float)
    for r in rows:
        if r["repaired"]:
            repaired_by_month[r["occurred_on"].strftime("%Y-%m")] += r["usd"]
    print(f"\nrow counts: sheet={n_sheet_rows} db={n_db} "
          f"(sheet rows with broken USD: {n_broken})")
    print(f"{'month':8} {'sheet_usd':>12} {'db_usd':>12} {'diff%':>8}")
    worst = 0.0
    for m in sorted(per_month_sheet):
        s = per_month_sheet[m]
        d = per_month_db.get(m, 0.0) - repaired_by_month.get(m, 0.0)
        diff = abs(s - d) / s * 100 if s else 0.0
        worst = max(worst, diff)
        print(f"{m:8} {s:12.2f} {d:12.2f} {diff:8.3f}")
    counts_ok = n_db == n_sheet_rows
    if not counts_ok:
        print(f"\nROW COUNT MISMATCH: sheet has {n_sheet_rows}, db has {n_db}")
    verdict = "PASS" if worst < 0.5 and counts_ok else "FAIL"
    print(f"\nRECONCILIATION {verdict} — worst monthly diff {worst:.3f}% (limit 0.5%)")


def main():
    rows, problems = load_rows()
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

Run: `python scripts/migrate_from_sheets.py`
Expected: ~4 problem rows (the known no-category rows — resolved at Step 5), the
multi-category table (~90 rows), ~19 repaired-rate rows, and per-month totals that
eyeball-match the old sheet.

- [ ] **Step 5: USER CHECKPOINT — Tommy reviews the dry run**

Show Tommy: (a) the multi-category rows — agree `COMBO_RULES` for the 7 undecided
combos; (b) the no-category rows — he assigns categories (fix the cells in the
workbook or hardcode per-line overrides); (c) the repaired-rate list, for
awareness. Rerun the dry run until he approves and problems reach zero.

- [ ] **Step 6: Execute and reconcile**

Run: `python scripts/migrate_from_sheets.py --execute`
Expected: `inserted N rows`, then matching row counts and the reconciliation table
ending in `RECONCILIATION PASS`. If FAIL: check the row counts first (a dropped or
double-read row), then the worst month, fix, and — since this must stay re-runnable —
delete and redo: `delete from transactions where source = 'migration'` via
`python -c "import sys; sys.path.insert(0,'scripts'); from lib import db_conn; c=db_conn(); c.execute('delete from transactions where source=%s', ('migration',)); c.commit()"`
then `--execute` again.

- [ ] **Step 7: Verify history through the agent surface**

Run: `python -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; [print(r) for r in rest_get('v_monthly_summary', {'order': 'month.desc', 'limit': '5'})]"`
Expected: recent months with sane USD totals.

- [ ] **Step 8: Commit**

```powershell
git add scripts/migrate_from_sheets.py
git commit -m "feat: expense sheet migration with combo rules, rate repair, dry-run report"
```

---

### Task 11: Migration — Facturación y Cobranzas workbook

**Files:**
- Create: `scripts/migrate_facturacion.py`

Source structure (inspected 2026-07-13, spec §10.2): sheet `Facturas` with
`Fecha de Emisión, Mes, Año, Número de Factura ('0001-00000001'), Importe Total`
(217 rows, clean); sheet `Cobranzas y Depósitos` with `Fecha, Mes, Año,
Cantidad [ARS], Plataforma de Ingreso, Cantidad [USD], Tipo de Cambio,
Precio Final, Notas` (22 rows). One known quirk: the Buenbit row's
`Tipo de Cambio` holds the ARS amount, not a rate.

- [ ] **Step 1: USER CHECKPOINT — get a fresh export**

Ask Tommy to re-download the workbook as `.xlsx` into `data/`, replacing
`data/Facturación y Cobranzas.xlsx`, and to confirm the Overview values at that
moment (Total Facturado / Depositado / Cobrado, Restante, Disponible) — they are
the reconciliation targets.

- [ ] **Step 2: Create `scripts/migrate_facturacion.py`**

```python
"""Migrate the Facturación y Cobranzas workbook into Supabase (spec §10.2).

Usage:
  python scripts/migrate_facturacion.py                # dry run
  python scripts/migrate_facturacion.py --execute      # insert + reconcile
"""
import sys
from datetime import datetime

import openpyxl

from lib import db_conn

XLSX = "data/Facturación y Cobranzas.xlsx"


def load_invoices(wb):
    rows = []
    for r in wb["Facturas"].iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            continue
        pv, num = str(r[3]).split("-")
        rows.append(dict(issued_on=r[0].date(), punto_venta=pv,
                         invoice_number=int(num), amount_ars=float(r[4])))
    return rows


def classify(platform, usd):
    """Kind rule, verified against every row of the sheet (spec §10.2)."""
    if usd is None:
        return "bank_deposit"
    return "cash_sale" if platform == "Cash" else "crypto_sale"


def load_conversions(wb):
    rows, problems = [], []
    for lineno, r in enumerate(
            wb["Cobranzas y Depósitos"].iter_rows(min_row=2, values_only=True),
            start=2):
        if not isinstance(r[0], datetime):
            continue  # trailing formula/blank rows
        ars, platform, usd, tc, notas = r[3], str(r[4]).strip(), r[5], r[6], r[8]
        try:
            usd = float(usd) if isinstance(usd, (int, float)) else None
            kind = classify(platform, usd)
            # rate_quoted only when plausible (the Buenbit row holds the ARS
            # amount in Tipo de Cambio -> import as null)
            quoted = float(tc) if isinstance(tc, (int, float)) and tc < 100000 else None
            rows.append(dict(occurred_on=r[0].date(), kind=kind,
                             ars_amount=float(ars), usd_amount=usd,
                             rate_quoted=quoted, platform=platform,
                             notes=str(notas) if notas else None, line=lineno))
        except Exception as e:  # noqa: BLE001
            problems.append((lineno, str(e)))
    return rows, problems


def metrics(conversions, invoices):
    dep = sum(c["ars_amount"] for c in conversions
              if c["kind"] in ("crypto_sale", "bank_deposit"))
    cob = sum(c["ars_amount"] for c in conversions
              if c["kind"] in ("crypto_sale", "cash_sale"))
    fac = sum(i["amount_ars"] for i in invoices)
    return dict(facturado=fac, depositado=dep, cobrado=cob,
                restante=fac - dep, disponible=cob - dep)


def dry_run(invoices, conversions, problems):
    print(f"\n=== DRY RUN — {len(invoices)} invoices, "
          f"{len(conversions)} conversions, {len(problems)} problems ===")
    for lineno, msg in problems:
        print(f"  line {lineno}: {msg}")
    print("\n-- Conversions classification --")
    for c in conversions:
        print(f"  line {c['line']:3} {c['occurred_on']} {c['kind']:12} "
              f"ARS {c['ars_amount']:>13,.2f} {c['platform']:8} "
              f"USD {c['usd_amount'] or 0:>9,.2f} quoted={c['rate_quoted']}")
    print("\n-- Metrics computed from the rows (compare with the Overview tab) --")
    for k, v in metrics(conversions, invoices).items():
        print(f"  {k:12} {v:>15,.2f}")
    print("\nRe-run with --execute to insert.")


def insert_all(invoices, conversions):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select name, id from payment_methods")
        pm_ids = dict(cur.fetchall())
        cur.executemany(
            """insert into invoices (issued_on, punto_venta, invoice_number,
                                     amount_ars, source)
               values (%(issued_on)s, %(punto_venta)s, %(invoice_number)s,
                       %(amount_ars)s, 'migration')""",
            invoices,
        )
        cur.executemany(
            """insert into conversions (occurred_on, kind, ars_amount, usd_amount,
                                        rate_quoted, platform_id, notes, source)
               values (%(occurred_on)s, %(kind)s, %(ars_amount)s, %(usd_amount)s,
                       %(rate_quoted)s, %(platform_id)s, %(notes)s, 'migration')""",
            [dict(c, platform_id=pm_ids[c["platform"]]) for c in conversions],
        )
        conn.commit()
    print(f"inserted {len(invoices)} invoices + {len(conversions)} conversions")


def check_platforms(conversions):
    """Resolve platform names at dry-run time (not mid-insert): unknown spellings
    in a fresh export become reviewable problems instead of a KeyError."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select name from payment_methods where active")
        known = {n for (n,) in cur.fetchall()}
    return [(c["line"], f"unknown platform {c['platform']!r} — "
             f"add it to payment_methods or fix the cell")
            for c in conversions if c["platform"] not in known]


def reconcile(invoices, conversions):
    """Spec §10.2: sheet-derived metrics must equal v_deposit_status to the peso.
    Valid because at this point only migrated rows exist in the fiscal tables."""
    expected = metrics(conversions, invoices)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from v_deposit_status")
        cols = [d[0] for d in cur.description]
        actual = dict(zip(cols, (float(v) for v in cur.fetchone())))
    mapping = {  # sheet-side key -> view column
        "facturado": "total_facturado", "depositado": "total_depositado",
        "cobrado": "total_cobrado", "restante": "restante_por_depositar",
        "disponible": "disponible_para_depositar",
    }
    print(f"\n{'metric':12} {'sheet':>16} {'db':>16}")
    ok = True
    for k, col in mapping.items():
        s, d = expected[k], actual[col]
        match = abs(s - d) < 0.01  # to the peso
        ok = ok and match
        print(f"{k:12} {s:>16,.2f} {d:>16,.2f}{'' if match else '  <-- MISMATCH'}")
    print(f"\nRECONCILIATION {'PASS' if ok else 'FAIL'}")


def main():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    invoices = load_invoices(wb)
    conversions, problems = load_conversions(wb)
    problems += check_platforms(conversions)
    if "--execute" not in sys.argv:
        dry_run(invoices, conversions, problems)
        return
    if problems:
        sys.exit(f"refusing to execute with {len(problems)} problem rows")
    insert_all(invoices, conversions)
    reconcile(invoices, conversions)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Dry run and review**

Run: `python scripts/migrate_facturacion.py`
Expected: 217 invoices, ~22+ conversions, zero problems; every row classified;
the printed metrics match the workbook's Overview tab. Show Tommy the
classification table for a quick eyeball.

- [ ] **Step 4: Execute and reconcile**

Run: `python scripts/migrate_facturacion.py --execute`
Expected: insert counts, then the sheet-vs-db metric table ending in
`RECONCILIATION PASS`, and the sheet-side column matching the Overview values
Tommy confirmed at Step 1. If FAIL: a misclassified kind is the likely cause
(it shifts ARS between Depositado and Cobrado); fix, then delete and redo —
`delete from invoices where source='migration'` and same for `conversions`.

- [ ] **Step 5: Also verify the bracket view against the Overview**

Run: `python -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; [print(r) for r in rest_get('v_monotributo_status')]"`
Expected: for the categories shown in the Overview tab (D–G), `invoiced_12m` and
the averages line up with the sheet's numbers (12-month window differences are
expected if the sheet's period differs from rolling-12-months — eyeball, don't
assert equality).

- [ ] **Step 6: Commit**

```powershell
git add scripts/migrate_facturacion.py
git commit -m "feat: facturación workbook migration with kind classification and metric reconciliation"
```

---

### Task 12: Cutover

**Files:**
- Modify: `README.md` (status note)

- [ ] **Step 1: Verify the cron job ran unattended**

At least one calendar day after Task 3, run:
`python -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; print(rest_get('fx_rates', {'order': 'rate_date.desc', 'limit': '2'}))"`
Expected: a row dated within the last few days that was NOT inserted by the
backfill/tests. If unsure, check the cron log **via the direct DB connection**
(the `cron` schema is not readable by `service_role`, so `run_report` cannot
see it):
`python -c "import sys; sys.path.insert(0,'scripts'); from lib import db_conn; c=db_conn(); cur=c.cursor(); cur.execute('select start_time, status from cron.job_run_details order by start_time desc limit 5'); [print(r) for r in cur.fetchall()]"`
Expected: recent runs with status `succeeded`.

- [ ] **Step 2: USER CHECKPOINT — archive the old system**

Ask Tommy to: rename BOTH old spreadsheets to `[ARCHIVED] ...` (the expense
tracker and Facturación y Cobranzas), stop using their analysis/Overview tabs,
and confirm the Form now feeds Supabase only (the Form → Sheet → Apps Script →
Supabase chain keeps the sheet as a raw receipt log; that's fine). From now on
invoices and conversions are logged through an agent (playbook §Fiscal module).

- [ ] **Step 3: Final smoke pass**

Run: `python -m pytest scripts/test_smoke.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Update README status and commit**

Append to `README.md`:
```markdown
## Status
Live since 2026-07. Source of truth: Supabase. Both old workbooks archived
read-only. Entry paths: Google Form (interim, expenses), any agent via
docs/agent-playbook.md (expenses, income, invoices, conversions).
```

```powershell
git add README.md
git commit -m "docs: mark system live, old sheet archived"
```
