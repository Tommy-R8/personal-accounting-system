# Credit Card Installments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project, per credit card, what each upcoming statement charges — including purchases in N cuotas — while keeping each purchase as one transaction at its purchase date.

**Architecture:** All-in-Supabase like the rest of the repo. `011` flags cards on `payment_methods` and adds `card_cycles` + `add_card_cycle`; `012` adds `installment_plans` and re-creates `add_transaction` with `p_installments`; `013` adds the computed views `v_card_cycles`, `v_card_schedule`, `v_card_statements`. Nothing about the calendar is stored — it is derived from cycle dates, so real cycles correct it automatically.

**Tech Stack:** PostgreSQL / plpgsql (Supabase), PostgREST, Python 3.12 + pytest + requests + psycopg for tests.

**Spec:** `docs/superpowers/specs/2026-10-13-card-installments-design.md`

**Conventions to know (from the existing repo):**
- Migrations live in `supabase/migrations/NNN_name.sql`, applied in filename order by `.venv/Scripts/python.exe scripts/apply_migrations.py` (tracked in `_migrations`; applied files are skipped — **never edit an applied migration, add a new one**).
- Tests: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v` from repo root. They hit the **live** project using `.env` (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL`). Test rows use `source='test'`.
- Every new function: `revoke execute ... from public, anon, authenticated;` then `grant execute ... to service_role;`. Every new table: `enable row level security` (no policies). Every new view: `security_invoker = true`.
- `supabase/migrations/008_bracket_update_2026_07.sql` is applied in the DB but **untracked** in git. Leave it alone; do not include it in this feature's commits.
- `add_transaction` is called by name (JSON params) from Hermes, the Google Form Apps Script (`data/apps-script/form-sync.gs`) and agents. Adding a defaulted parameter is backwards compatible **only** if the old signature is dropped (PostgREST cannot pick between overloads) — same pattern as `009_invoice_settings.sql`.

## Global Constraints

- Purchase stays one transaction, full amount, purchase date; USD conversion at the purchase-date rate (spec §3, option A). `v_monthly_summary` and `v_fiscal_balance` are not changed.
- `installment_plans` rows only for 2–60 cuotas; a card outflow without a plan is 1 cuota.
- Cuotas 1…n-1 = `round(amount / n, 2)`; cuota n absorbs the remainder (sum is exact).
- Cuota k lands in the k-th cycle with `closing_date >= occurred_on` (purchase on closing day counts in that cycle).
- One real cycle per card per calendar month; `add_card_cycle` replaces an existing one in the same month.
- Estimated cycle: closing = `default_closing_day` clamped to month end; due = first date after closing whose day is `default_due_day` (clamped to month end).
- Cycle horizon: from the card's first outflow month to current month + 60 months.
- The statement payment is never recorded as a transaction.

## Review Focus

1. **Existing callers of `add_transaction`** (Hermes, form-sync.gs) that never send `p_installments` must keep working → covered by the whole existing suite re-run in Task 2 Step 6 plus `test_add_transaction_without_installments_unchanged`.
2. **Card names with accents** ("Bank A Crédito") passed in a different case to `add_card_cycle` must resolve → `test_add_card_cycle_resolves_name_case_insensitively` (Task 1).
3. **Card refunds (inflows on a card)** must not appear as a charge in the schedule → `test_card_inflow_not_in_schedule` (Task 3).
4. **Closing day 29–31 in short months** must clamp to month end, not error → `test_closing_day_clamps_to_month_end` (Task 3).
5. **Due day after the closing day** (due in the same month) must not jump a month → `test_due_same_month_when_due_day_after_closing` (Task 3).

---

## File map

| File | Change |
|---|---|
| `supabase/migrations/011_cards.sql` | **Create.** Card columns on `payment_methods`, `card_cycles`, `add_card_cycle`. |
| `supabase/migrations/012_installment_plans.sql` | **Create.** `installment_plans`, `add_transaction` with `p_installments`. |
| `supabase/migrations/013_card_schedule_views.sql` | **Create.** `_card_day` helper, `v_card_cycles`, `v_card_schedule`, `v_card_statements`. |
| `scripts/test_smoke.py` | **Modify.** `make_card` fixture + "Task 10/11/12" sections. |
| `docs/agent-playbook.md` | **Modify.** `p_installments`, card statements section, statement import additions. |
| `docs/configuration.md` | **Modify.** How to flag a credit card. |

---

### Task 1: Cards and real cycles (`011_cards.sql`)

**Files:**
- Create: `supabase/migrations/011_cards.sql`
- Modify: `scripts/test_smoke.py` (append at end of file; add `import uuid` at the top imports)

**Interfaces:**
- Produces: columns `payment_methods.is_credit_card boolean`, `default_closing_day int`, `default_due_day int`; table `card_cycles(id, payment_method_id, closing_date, due_date, source, created_at)`; RPC `add_card_cycle(p_payment_method text, p_closing_date date, p_due_date date, p_source text) returns card_cycles`; pytest fixture `make_card(closing_day=20, due_day=5) -> str` (returns the card name).

- [ ] **Step 1: Write the failing tests**

Add `import uuid` next to `from datetime import date` at the top of `scripts/test_smoke.py`, then append:

```python
# --- Task 10: cards + real cycles ---------------------------------------------

@pytest.fixture
def make_card():
    """Factory for throwaway credit cards. Each test gets its own card so
    cycles and purchases of one test never shift another test's calendar."""
    ids = []

    def _make(closing_day=20, due_day=5):
        name = f"Test Card {uuid.uuid4().hex[:8]}"
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """insert into payment_methods
                     (name, is_credit_card, default_closing_day, default_due_day)
                   values (%s, true, %s, %s) returning id""",
                (name, closing_day, due_day),
            )
            ids.append(cur.fetchone()[0])
        return name

    yield _make
    with db_conn() as conn, conn.cursor() as cur:
        for pm_id in ids:
            cur.execute("delete from transactions where payment_method_id = %s", (pm_id,))
            cur.execute("delete from card_cycles where payment_method_id = %s", (pm_id,))
            cur.execute("delete from payment_methods where id = %s", (pm_id,))


def add_cycle(card, closing, due):
    return rpc("add_card_cycle", {"p_payment_method": card, "p_closing_date": closing,
                                  "p_due_date": due, "p_source": "test"})


def test_card_flag_requires_default_days():
    # Estimated cycles need both days; a card without them would silently vanish
    # from every projection.
    with db_conn() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("""insert into payment_methods (name, is_credit_card)
                           values ('Test Card no days', true)""")
        conn.rollback()


def test_add_card_cycle_rejects_non_card():
    r = add_cycle("Cash ARS", "2027-01-20", "2027-02-05")
    assert r.status_code == 400
    assert "valid credit cards" in r.json()["message"]


def test_add_card_cycle_rejects_due_not_after_closing(make_card):
    r = add_cycle(make_card(), "2027-01-20", "2027-01-20")
    assert r.status_code == 400
    assert "must be after" in r.json()["message"]


def test_add_card_cycle_resolves_name_case_insensitively(make_card):
    # Agents type card names as the owner says them ("Bank A crédito").
    card = make_card()
    r = add_cycle(card.upper(), "2027-01-20", "2027-02-05")
    assert r.status_code == 200, r.text


def test_add_card_cycle_replaces_same_month(make_card):
    # A statement's "próximo cierre" is a forecast; the next statement's actual
    # date must replace it, not sit next to it.
    card = make_card()
    assert add_cycle(card, "2027-01-28", "2027-02-10").status_code == 200
    r = add_cycle(card, "2027-01-29", "2027-02-11")
    assert r.status_code == 200, r.text
    pm_id = r.json()["payment_method_id"]
    rows = rest_get("card_cycles", {"payment_method_id": f"eq.{pm_id}",
                                    "select": "closing_date,due_date"})
    assert rows == [{"closing_date": "2027-01-29", "due_date": "2027-02-11"}]


def test_add_card_cycle_keeps_other_months(make_card):
    card = make_card()
    add_cycle(card, "2027-01-28", "2027-02-10")
    r = add_cycle(card, "2027-02-25", "2027-03-10")
    pm_id = r.json()["payment_method_id"]
    rows = rest_get("card_cycles", {"payment_method_id": f"eq.{pm_id}", "select": "id"})
    assert len(rows) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v -k "card"`
Expected: FAIL/ERROR — `column "is_credit_card" ... does not exist` and 404 for `add_card_cycle`.

- [ ] **Step 3: Write the migration**

Create `supabase/migrations/011_cards.sql`:

```sql
-- Credit card installments, part 1: flag cards + real cycle dates
-- (spec: docs/superpowers/specs/2026-10-13-card-installments-design.md §5.1–5.2)

-- Default days are only the fallback for months with no real cycle.
alter table payment_methods
  add column is_credit_card      boolean not null default false,
  add column default_closing_day int check (default_closing_day between 1 and 31),
  add column default_due_day     int check (default_due_day between 1 and 31),
  add constraint payment_methods_card_days_check
    check (not is_credit_card
           or (default_closing_day is not null and default_due_day is not null));

create table card_cycles (
  id                serial primary key,
  payment_method_id int  not null references payment_methods(id),
  closing_date      date not null,
  due_date          date not null check (due_date > closing_date),
  source            text not null,
  created_at        timestamptz not null default now(),
  unique (payment_method_id, closing_date)
);
alter table card_cycles enable row level security;

-- One real cycle per card per calendar month: a later statement's actual date
-- replaces the "próximo cierre" forecast recorded from the previous one.
create or replace function add_card_cycle(
  p_payment_method text,
  p_closing_date date,
  p_due_date date,
  p_source text
) returns card_cycles
language plpgsql as $$
declare
  v_pm_id int;
  v_valid text;
  v_row card_cycles%rowtype;
begin
  select id into v_pm_id
    from payment_methods
   where lower(name) = lower(trim(p_payment_method)) and active and is_credit_card;
  if not found then
    select string_agg(name, ', ' order by id) into v_valid
      from payment_methods where active and is_credit_card;
    raise exception 'unknown credit card "%" — valid credit cards: %',
      p_payment_method, coalesce(v_valid, '(none flagged)');
  end if;
  if p_due_date <= p_closing_date then
    raise exception 'due_date (%) must be after closing_date (%)',
      p_due_date, p_closing_date;
  end if;

  delete from card_cycles
   where payment_method_id = v_pm_id
     and date_trunc('month', closing_date) = date_trunc('month', p_closing_date);
  insert into card_cycles (payment_method_id, closing_date, due_date, source)
  values (v_pm_id, p_closing_date, p_due_date, p_source)
  returning * into v_row;
  return v_row;
end $$;

revoke execute on function add_card_cycle(text, date, date, text)
  from public, anon, authenticated;
grant execute on function add_card_cycle(text, date, date, text) to service_role;
```

- [ ] **Step 4: Apply and run tests**

Run: `.venv/Scripts/python.exe scripts/apply_migrations.py`
Expected: `apply 011_cards.sql` then `done`.

Run: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v -k "card"`
Expected: all Task 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/011_cards.sql scripts/test_smoke.py
git commit -m "feat: flag credit cards, card_cycles table, add_card_cycle RPC"
```

---

### Task 2: Installment plans in `add_transaction` (`012_installment_plans.sql`)

**Files:**
- Create: `supabase/migrations/012_installment_plans.sql`
- Modify: `scripts/test_smoke.py` (append)

**Interfaces:**
- Consumes: `payment_methods.is_credit_card` (Task 1), fixture `make_card` (Task 1), existing test helper `add(**kw)` (defaults: `2026-07-06`, outflow, `Comida`, amount 100, source `test`).
- Produces: table `installment_plans(transaction_id uuid pk, installments int)`; `add_transaction(..., p_installments int default null)` — signature `(date, text, text, text, numeric, text, text, boolean, text[], text, uuid, text, text, int)`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_smoke.py`:

```python
# --- Task 11: installment plans ---------------------------------------------

def plan_of(tx_id):
    return rest_get("installment_plans", {"transaction_id": f"eq.{tx_id}",
                                          "select": "installments"})


def test_installments_create_plan_on_one_transaction(make_card):
    r = add(p_amount=600000, p_payment_method=make_card(), p_installments=6)
    assert r.status_code == 200, r.text
    tx = r.json()
    assert plan_of(tx["id"]) == [{"installments": 6}]


def test_installment_purchase_is_full_amount_on_purchase_date(make_card):
    # Decision A: the purchase is the economic fact. The whole amount counts in
    # the purchase month at that day's rate — cuotas are cash, not spending.
    tx = add(p_amount=600000, p_payment_method=make_card(), p_installments=6).json()
    row = rest_get("v_transactions", {"id": f"eq.{tx['id']}"})[0]
    assert row["occurred_on"] == "2026-07-06"
    assert abs(float(row["net_amount_usd"]) - 600000 / float(row["usd_rate"])) < 0.01


def test_one_installment_stores_no_plan(make_card):
    tx = add(p_payment_method=make_card(), p_installments=1).json()
    assert plan_of(tx["id"]) == []


def test_add_transaction_without_installments_unchanged(make_card):
    # Hermes and the form never send p_installments; they must keep working.
    r = add(p_payment_method=make_card())
    assert r.status_code == 200, r.text
    assert plan_of(r.json()["id"]) == []


def test_installments_need_credit_card():
    r = add(p_payment_method="Cash ARS", p_installments=3)
    assert r.status_code == 400
    assert "valid credit cards" in r.json()["message"]


def test_installments_need_payment_method():
    r = add(p_installments=3)
    assert r.status_code == 400
    assert "valid credit cards" in r.json()["message"]


def test_installments_only_on_outflows(make_card):
    r = add(p_type="inflow", p_category="Reintegros",
            p_payment_method=make_card(), p_installments=3)
    assert r.status_code == 400
    assert "only applies to outflows" in r.json()["message"]


@pytest.mark.parametrize("n", [0, 61])
def test_installments_out_of_range(make_card, n):
    r = add(p_payment_method=make_card(), p_installments=n)
    assert r.status_code == 400
    assert "between 1 and 60" in r.json()["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v -k "installment"`
Expected: FAIL — PostgREST 404/`PGRST202` (no `add_transaction` with `p_installments`).

- [ ] **Step 3: Write the migration**

Create `supabase/migrations/012_installment_plans.sql`:

```sql
-- Credit card installments, part 2: plans + add_transaction p_installments
-- (spec: docs/superpowers/specs/2026-10-13-card-installments-design.md §5.3, §5.5)

-- Only plans with 2+ cuotas get a row; a card outflow without one is 1 cuota.
create table installment_plans (
  transaction_id uuid primary key references transactions(id) on delete cascade,
  installments   int  not null check (installments between 2 and 60)
);
alter table installment_plans enable row level security;

-- The old signature must go: PostgREST cannot pick between two overloads when
-- the call omits p_installments.
drop function add_transaction(
  date, text, text, text, numeric, text, text, boolean, text[], text, uuid, text, text);

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
  p_notes text default null,
  p_installments int default null
) returns transactions
language plpgsql as $$
declare
  v_cat categories%rowtype;
  v_pm_id int;
  v_rate numeric;
  v_valid text;
  v_row transactions%rowtype;
begin
  if p_type not in ('inflow','outflow') then
    raise exception 'invalid type "%" — must be inflow or outflow', p_type;
  end if;

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

  if p_installments is not null and p_installments <> 1 then
    if p_installments < 1 or p_installments > 60 then
      raise exception 'p_installments must be between 1 and 60, got %', p_installments;
    end if;
    if p_type <> 'outflow' then
      raise exception 'p_installments only applies to outflows';
    end if;
    if v_pm_id is null
       or not exists (select 1 from payment_methods
                       where id = v_pm_id and is_credit_card) then
      select string_agg(name, ', ' order by id) into v_valid
        from payment_methods where active and is_credit_card;
      raise exception 'p_installments needs a credit card payment method — valid credit cards: %',
        coalesce(v_valid, '(none flagged)');
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

  if p_installments > 1 then
    insert into installment_plans (transaction_id, installments)
    values (v_row.id, p_installments);
  end if;
  return v_row;
end $$;

revoke execute on function add_transaction(
  date, text, text, text, numeric, text, text, boolean, text[], text, uuid, text, text, int
) from public, anon, authenticated;
grant execute on function add_transaction(
  date, text, text, text, numeric, text, text, boolean, text[], text, uuid, text, text, int
) to service_role;
```

Note: the body before the `p_installments` block and after it is a verbatim copy of `003_add_transaction.sql`; only the new parameter, the validation block and the plan insert are new.

- [ ] **Step 4: Apply and run the new tests**

Run: `.venv/Scripts/python.exe scripts/apply_migrations.py`
Expected: `apply 012_installment_plans.sql`.

Run: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v -k "installment"`
Expected: all Task 11 tests PASS.

- [ ] **Step 5: (spot check) PostgREST schema cache**

If Step 4 shows `PGRST202` for a param that exists, the cache is stale. Run:
`.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'scripts'); from lib import db_conn; c=db_conn(); c.execute(\"notify pgrst, 'reload schema'\"); c.commit()"` and re-run Step 4.

- [ ] **Step 6: Run the whole suite (existing callers still work)**

Run: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v`
Expected: everything PASS — in particular all Task 4 `add_transaction` tests, which call without `p_installments`.

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/012_installment_plans.sql scripts/test_smoke.py
git commit -m "feat: installment_plans + add_transaction p_installments"
```

---

### Task 3: Schedule and statement views (`013_card_schedule_views.sql`)

**Files:**
- Create: `supabase/migrations/013_card_schedule_views.sql`
- Modify: `scripts/test_smoke.py` (append)

**Interfaces:**
- Consumes: `card_cycles`, `add_card_cycle` + test helper `add_cycle(card, closing, due)` (Task 1); `installment_plans`, `add_transaction(p_installments)` (Task 2); fixture `make_card(closing_day, due_day)`.
- Produces: function `_card_day(p_month date, p_day int) returns date`; views `v_card_cycles(payment_method_id, payment_method, month, closing_date, due_date, is_estimated)`, `v_card_schedule(transaction_id, occurred_on, description, payment_method, currency, installment_no, installments, installment_amount, closing_date, due_date, is_estimated)`, `v_card_statements(payment_method, closing_date, due_date, currency, total, installment_count, is_estimated)`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_smoke.py`:

```python
# --- Task 12: card schedule + statements --------------------------------------

def schedule(tx_id):
    return rest_get("v_card_schedule", {"transaction_id": f"eq.{tx_id}",
                                        "order": "installment_no"})


def test_cuotas_sum_exactly_to_purchase(make_card):
    # No peso may be lost or invented by rounding: the owner reconciles
    # these against real statements.
    tx = add(p_amount=100000, p_payment_method=make_card(), p_installments=3).json()
    amounts = [float(c["installment_amount"]) for c in schedule(tx["id"])]
    assert amounts == [33333.33, 33333.33, 33333.34]


def test_cuotas_land_in_consecutive_estimated_cycles(make_card):
    card = make_card(closing_day=20, due_day=5)
    tx = add(p_payment_method=card, p_installments=3).json()  # bought 2026-07-06
    rows = schedule(tx["id"])
    assert [(c["installment_no"], c["closing_date"], c["due_date"], c["is_estimated"])
            for c in rows] == [
        (1, "2026-07-20", "2026-08-05", True),
        (2, "2026-08-20", "2026-09-05", True),
        (3, "2026-09-20", "2026-10-05", True),
    ]


def test_purchase_after_closing_goes_to_next_cycle(make_card):
    tx = add(p_occurred_on="2026-07-21", p_payment_method=make_card()).json()
    assert schedule(tx["id"])[0]["closing_date"] == "2026-08-20"


def test_purchase_on_closing_day_lands_in_that_cycle(make_card):
    tx = add(p_occurred_on="2026-07-20", p_payment_method=make_card()).json()
    assert schedule(tx["id"])[0]["closing_date"] == "2026-07-20"


def test_one_payment_purchase_appears_once(make_card):
    tx = add(p_payment_method=make_card()).json()
    rows = schedule(tx["id"])
    assert len(rows) == 1
    assert (rows[0]["installment_no"], rows[0]["installments"]) == (1, 1)


def test_real_cycle_moves_cuotas_without_touching_stored_data(make_card):
    # The calendar is computed: recording the real cycle must fix it with no
    # migration of stored rows.
    card = make_card()
    tx = add(p_payment_method=card, p_installments=2).json()
    assert add_cycle(card, "2026-08-27", "2026-09-08").status_code == 200
    rows = schedule(tx["id"])
    assert (rows[0]["closing_date"], rows[0]["is_estimated"]) == ("2026-07-20", True)
    assert (rows[1]["closing_date"], rows[1]["due_date"], rows[1]["is_estimated"]) == \
        ("2026-08-27", "2026-09-08", False)


def test_closing_day_clamps_to_month_end(make_card):
    tx = add(p_occurred_on="2026-09-10", p_payment_method=make_card(closing_day=31)).json()
    assert schedule(tx["id"])[0]["closing_date"] == "2026-09-30"


def test_due_same_month_when_due_day_after_closing(make_card):
    tx = add(p_occurred_on="2026-07-01",
             p_payment_method=make_card(closing_day=5, due_day=15)).json()
    row = schedule(tx["id"])[0]
    assert (row["closing_date"], row["due_date"]) == ("2026-07-05", "2026-07-15")


def test_non_card_purchase_not_in_schedule():
    tx = add(p_payment_method="Cash ARS").json()
    assert schedule(tx["id"]) == []


def test_card_inflow_not_in_schedule(make_card):
    # A refund to the card is not a charge; it must not inflate the statement.
    tx = add(p_type="inflow", p_category="Reintegros", p_payment_method=make_card()).json()
    assert schedule(tx["id"]) == []


def test_statements_total_by_currency(make_card):
    card = make_card()
    add(p_payment_method=card, p_amount=1000)
    add(p_payment_method=card, p_amount=500)
    add(p_payment_method=card, p_amount=50, p_currency="USD")
    rows = rest_get("v_card_statements", {"payment_method": f"eq.{card}",
                                          "closing_date": "eq.2026-07-20",
                                          "order": "currency"})
    assert [(r["currency"], float(r["total"]), r["installment_count"]) for r in rows] == \
        [("ARS", 1500.0, 2), ("USD", 50.0, 1)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v -k "cuota or schedule or statement or closing or cycle or purchase"`
Expected: Task 12 tests FAIL with 404 (`v_card_schedule` does not exist); Task 10 tests still PASS.

- [ ] **Step 3: Write the migration**

Create `supabase/migrations/013_card_schedule_views.sql`:

```sql
-- Credit card installments, part 3: computed calendar
-- (spec: docs/superpowers/specs/2026-10-13-card-installments-design.md §5.4)

-- Day p_day of the month starting at p_month, clamped to the month's last day.
create or replace function _card_day(p_month date, p_day int) returns date
language sql immutable as $$
  select p_month + (least(p_day,
           extract(day from (p_month + interval '1 month' - interval '1 day'))::int) - 1)
$$;
revoke execute on function _card_day(date, int) from public, anon, authenticated;
grant execute on function _card_day(date, int) to service_role;

-- One cycle per card per month, from the card's first outflow to 60 months ahead.
-- A real card_cycles row for that month wins over the estimate.
create view v_card_cycles as
with cards as (
  select pm.id as payment_method_id, pm.name as payment_method,
         pm.default_closing_day, pm.default_due_day,
         date_trunc('month', coalesce(
           (select min(t.occurred_on) from transactions t
             where t.payment_method_id = pm.id and t.type = 'outflow'),
           current_date))::date as first_month
    from payment_methods pm
   where pm.is_credit_card
), months as (
  select c.*, gs::date as month
    from cards c,
         generate_series(c.first_month,
                         date_trunc('month', current_date) + interval '60 months',
                         interval '1 month') gs
), estimated as (
  select m.*, _card_day(m.month, m.default_closing_day) as est_closing
    from months m
)
select e.payment_method_id, e.payment_method, e.month,
       coalesce(cc.closing_date, e.est_closing) as closing_date,
       coalesce(cc.due_date,
         case when _card_day(e.month, e.default_due_day) > e.est_closing
              then _card_day(e.month, e.default_due_day)
              else _card_day((e.month + interval '1 month')::date, e.default_due_day)
         end) as due_date,
       cc.id is null as is_estimated
  from estimated e
  left join card_cycles cc
    on cc.payment_method_id = e.payment_method_id
   and date_trunc('month', cc.closing_date) = e.month;

-- One row per cuota of every credit card outflow. Cuota k lands in the k-th
-- cycle with closing_date >= occurred_on; cuota n absorbs the rounding remainder.
create view v_card_schedule as
with purchases as (
  select t.id as transaction_id, t.occurred_on, t.description,
         t.payment_method_id, t.currency, t.amount,
         coalesce(ip.installments, 1) as installments
    from transactions t
    join payment_methods pm on pm.id = t.payment_method_id and pm.is_credit_card
    left join installment_plans ip on ip.transaction_id = t.id
   where t.type = 'outflow'
), cycles as (
  select c.*, row_number() over (partition by c.payment_method_id
                                 order by c.closing_date) as seq
    from v_card_cycles c
), first_cycle as (
  select p.transaction_id, min(c.seq) as seq0
    from purchases p
    join cycles c on c.payment_method_id = p.payment_method_id
                 and c.closing_date >= p.occurred_on
   group by p.transaction_id
), cuotas as (
  select p.*, k as installment_no,
         case when k < p.installments
              then round(p.amount / p.installments, 2)
              else p.amount - round(p.amount / p.installments, 2) * (p.installments - 1)
         end as installment_amount
    from purchases p, generate_series(1, p.installments) k
)
select cu.transaction_id, cu.occurred_on, cu.description, cy.payment_method,
       cu.currency, cu.installment_no, cu.installments, cu.installment_amount,
       cy.closing_date, cy.due_date, cy.is_estimated
  from cuotas cu
  join first_cycle fc on fc.transaction_id = cu.transaction_id
  join cycles cy on cy.payment_method_id = cu.payment_method_id
                and cy.seq = fc.seq0 + cu.installment_no - 1;

-- What each statement charges, per currency (ARS and USD, as on the real one).
create view v_card_statements as
select payment_method, closing_date, due_date, currency,
       sum(installment_amount) as total,
       count(*) as installment_count,
       bool_or(is_estimated) as is_estimated
  from v_card_schedule
 group by payment_method, closing_date, due_date, currency;

alter view v_card_cycles set (security_invoker = true);
alter view v_card_schedule set (security_invoker = true);
alter view v_card_statements set (security_invoker = true);
```

- [ ] **Step 4: Apply and run tests**

Run: `.venv/Scripts/python.exe scripts/apply_migrations.py`
Expected: `apply 013_card_schedule_views.sql`.

Run: `.venv/Scripts/python.exe -m pytest scripts/test_smoke.py -v`
Expected: the whole suite PASS.

- [ ] **Step 5: Check the views against real data (read-only)**

Run: `.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; print(len(rest_get('v_card_cycles', {'select':'month','limit':'1'})))"`
Expected: `0` — no real card is flagged yet (Task 4 owner step), and the view still answers without error.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/013_card_schedule_views.sql scripts/test_smoke.py
git commit -m "feat: v_card_cycles, v_card_schedule, v_card_statements"
```

---

### Task 4: Docs + flag the owner's real cards

**Files:**
- Modify: `docs/agent-playbook.md` (`add_transaction` param table ~line 36; new section before `## Statement import workflow`; statement import steps)
- Modify: `docs/configuration.md` (section `## Payment methods & conversion platforms (data)`, ~line 23)

**Interfaces:**
- Consumes: `p_installments`, `add_card_cycle`, `v_card_statements`, `v_card_schedule`.

- [ ] **Step 1: Playbook — parameter row**

In the `add_transaction` table of `docs/agent-playbook.md`, after the `p_notes` row, add:

```markdown
| `p_installments` | no | credit card purchases in cuotas only: number of cuotas (2–60). The amount is the **total** paid (sum of all cuotas). Omit for 1 payment. |
```

- [ ] **Step 2: Playbook — card statements section**

Insert before `## Statement import workflow`:

```markdown
## Credit card statements

- **Never record the payment of a card statement** ("pagué la Visa"). Every
  purchase on it is already recorded; tell the owner so.
- "¿Cuánto pago de la Visa?" / "¿cuánto me viene el mes que viene?" →
  `GET /rest/v1/v_card_statements?payment_method=eq.<Card>&closing_date=gte.<today>&order=closing_date&limit=2`
  (one row per currency). `is_estimated = true` means the cycle dates are the
  card's default days, not yet confirmed by a real statement.
- "¿Cuánto tengo comprometido en cuotas?" → sum `installment_amount` from
  `v_card_schedule` with `closing_date=gte.<today>` and `installments=gt.1`.
- Purchase with interest: `p_amount` is the total of the cuotas; put the list
  price in `p_notes`.
```

- [ ] **Step 3: Playbook — statement import additions**

In `## Statement import workflow`, insert after step 2 (renumber the following steps so the list stays 1…N) these two steps:

```markdown
3. **Card statements only:** record the statement's cycle and the next one with
   `add_card_cycle` (`p_payment_method`, `p_closing_date`, `p_due_date`,
   `p_source="statement"`) — "cierre/vencimiento actual" and "próximo
   cierre/vencimiento". Re-recording a month replaces it; that is expected.
4. **Cuota lines** (`CUOTA k/n`, or `k/n` next to the merchant): the purchase
   total is `cuota amount × n`, its date is the original purchase date printed
   on the line, and its `external_ref` is built from the **purchase**
   (`<method-slug>:<original date>:<total>:<merchant-slug>`), so every month's
   cuota line maps to the same ref. Match it like any line but by original date
   and total (± n pesos for rounding). Unmatched → propose it and insert with
   `p_installments = n`.
```

- [ ] **Step 4: Configuration doc**

Append to the `## Payment methods & conversion platforms (data)` section of `docs/configuration.md`:

```markdown
**Credit cards** (installments / statement projection): flag a card row once.
Both default days are required; they are only used for months with no real
cycle recorded from a statement.

    update payment_methods
       set is_credit_card = true, default_closing_day = 24, default_due_day = 5
     where name = 'MyBank Crédito';
```

- [ ] **Step 5: Commit docs**

```bash
git add docs/agent-playbook.md docs/configuration.md
git commit -m "docs: card installments in the agent playbook and configuration"
```

- [ ] **Step 6: OWNER CHECKPOINT — flag the real cards**

Ask the owner for the usual closing day and due day of each card: `Bank A Crédito` (id 1), `Wallet A Crédito` (id 3), `Bank B Crédito` (id 19). Do not guess. With their answers, run the `update` from Step 4 once per card through `db_conn()`, then verify:

Run: `.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'scripts'); from lib import rest_get; print(rest_get('v_card_statements', {'closing_date':'gte.2026-10-01','order':'payment_method,closing_date','limit':'6'}))"`
Expected: rows for the flagged cards. Existing 2026 purchases on those cards appear in past cycles (expected — they were 1-payment purchases).

This is data, not code: nothing to commit.
