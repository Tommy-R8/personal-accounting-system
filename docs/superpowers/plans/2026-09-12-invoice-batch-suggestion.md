# Invoice Batch Suggestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Supabase RPC, `suggest_invoice_batch()`, that returns a weekly batch of realistic-looking invoice amounts (with descriptions) sized so the month lands on a configurable target; plus the `settings` table it reads, a `concept` column on `invoices`, tests, and playbook docs.

**Architecture:** All-in-Supabase like the rest of the repo. Migration `009_invoice_settings.sql` adds a key/value `settings` table (seeded with the `invoicing` config), `invoices.concept` and a re-created `add_invoice` with `p_concept`; migration `010_suggest_invoice_batch.sql` adds the private helper `_batch_bound` and the RPC. Tests hit the live project via REST (`scripts/test_smoke.py`), the same way every other feature is tested.

**Tech Stack:** PostgreSQL / plpgsql (Supabase), PostgREST, Python 3.12 + pytest + requests for tests.

**Spec:** `docs/superpowers/specs/2026-09-12-invoice-batch-suggestion-design.md`

**Conventions to know (from the existing repo):**
- Migrations live in `supabase/migrations/NNN_name.sql` and are applied in filename order by `python scripts/apply_migrations.py` (tracked in `_migrations`; an already-applied file is skipped, so **never edit an applied migration — add a new one**).
- Tests: `python -m pytest scripts/test_smoke.py -v` from repo root, needs `.env` with `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL`. Test rows use `source='test'` and invoices use `p_punto_venta='9999'`; the session fixture deletes them.
- RPCs are revoked from `public, anon, authenticated` and granted to `service_role`.
- `supabase/migrations/008_bracket_update_2026_07.sql` is currently **untracked** in git. Leave it alone; do not include it in this feature's commits.

---

## File map

| File | Change |
|---|---|
| `supabase/migrations/009_invoice_settings.sql` | **Create.** `settings` table + seed, `invoices.concept`, new `add_invoice`, grants. |
| `supabase/migrations/010_suggest_invoice_batch.sql` | **Create.** `_batch_bound` helper, `suggest_invoice_batch` RPC, grants. |
| `scripts/lib.py` | **Modify.** Add `rest_patch` helper. |
| `scripts/test_smoke.py` | **Modify.** Append a "Task 9: invoice batch" section. |
| `docs/agent-playbook.md` | **Modify.** Add "Weekly invoice batch" section + `p_concept` in the invoice-recording paragraph. |
| `docs/configuration.md` | **Modify.** Add the `settings.invoicing` entry. |

Two migrations so each task applies once and is never edited afterwards (`apply_migrations.py` skips applied files). `010` depends on `009` (the RPC reads `settings`). Tasks below are ordered so each one leaves the tests green.

---

### Task 1: `rest_patch` helper

**Files:**
- Modify: `scripts/lib.py`

- [ ] **Step 1: Add the helper after `rest_delete`**

```python
def rest_patch(path, params, body):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{path}", params=params, json=body, headers=HEADERS, timeout=30
    )
    r.raise_for_status()
    return r.json()
```

- [ ] **Step 2: Sanity import**

Run: `python -c "import sys; sys.path.insert(0,'scripts'); import lib; print(lib.rest_patch)"`
Expected: `<function rest_patch at 0x...>`

- [ ] **Step 3: Commit**

```bash
git add scripts/lib.py
git commit -m "chore: rest_patch helper for tests"
```

---

### Task 2: Migration — settings table, `concept` column, `add_invoice` with `p_concept`

**Files:**
- Create: `supabase/migrations/009_invoice_settings.sql`
- Modify: `scripts/test_smoke.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to the end of `scripts/test_smoke.py`:

```python
# --- Task 9: invoice batch suggestion -----------------------------------------

from lib import rest_patch  # noqa: E402  (kept next to the tests that use it)

INVOICING_TEST_CFG = {
    "monthly_target_ars": 1000000,
    "batch_size": 5,
    "batch_weekday": 5,
    "round_to": 1000,
    "bands": [
        {"concept": "Asesoramiento Comercial", "min": 110000, "max": 200000,
         "count_min": 2, "count_max": 3},
        {"concept": "Servicios Varios", "min": 75000, "max": 120000,
         "count_min": 2, "count_max": 3},
    ],
}


def set_invoicing(cfg):
    rest_patch("settings", {"key": "eq.invoicing"}, {"value": cfg})


@pytest.fixture
def invoicing_cfg():
    """Swap in a known config for one test, restore the owner's row afterwards."""
    original = rest_get("settings", {"key": "eq.invoicing", "select": "value"})[0]["value"]
    set_invoicing(INVOICING_TEST_CFG)
    yield dict(INVOICING_TEST_CFG)
    set_invoicing(original)


def test_settings_seeded():
    rows = rest_get("settings", {"key": "eq.invoicing", "select": "value"})
    assert len(rows) == 1
    v = rows[0]["value"]
    assert {"monthly_target_ars", "batch_size", "batch_weekday", "round_to", "bands"} <= set(v)


def test_add_invoice_stores_concept():
    r = add_invoice(n=902, p_concept="Servicios Varios")
    assert r.status_code == 200, r.text
    assert r.json()["concept"] == "Servicios Varios"
    r = add_invoice(n=903)
    assert r.status_code == 200, r.text
    assert r.json()["concept"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -k "settings_seeded or stores_concept" -v`
Expected: both FAIL (`settings` 404 / `concept` KeyError).

- [ ] **Step 3: Write the migration**

Create `supabase/migrations/009_invoice_settings.sql`:

```sql
-- Invoice batch suggestion, part 1: settings + concept
-- (spec: docs/superpowers/specs/2026-09-12-invoice-batch-suggestion-design.md)

-- Generic key/value config. Agents edit rows with PATCH /rest/v1/settings?key=eq.<key>
create table settings (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
alter table settings enable row level security;

-- monthly_target_ars is a placeholder: the owner sets it before the first run
-- (docs/configuration.md). batch_weekday is ISO (1 = Monday … 7 = Sunday).
insert into settings (key, value) values ('invoicing', '{
  "monthly_target_ars": 2200000,
  "batch_size": 5,
  "batch_weekday": 5,
  "round_to": 1000,
  "bands": [
    {"concept": "Asesoramiento Comercial", "min": 110000, "max": 200000,
     "count_min": 2, "count_max": 3},
    {"concept": "Servicios Varios",        "min":  75000, "max": 120000,
     "count_min": 2, "count_max": 3}
  ]
}');

-- Description printed on the factura. Null for rows recorded before this migration.
alter table invoices add column concept text;

-- Re-create add_invoice with p_concept. The old signature must go: PostgREST
-- cannot pick between two overloads when the call omits p_concept.
drop function add_invoice(date, int, numeric, text, text, text, text);

create or replace function add_invoice(
  p_issued_on date,
  p_invoice_number int,
  p_amount_ars numeric,
  p_source text,
  p_punto_venta text default '0001',
  p_status text default 'issued',
  p_notes text default null,
  p_concept text default null
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
                        status, notes, concept, source)
  values (p_issued_on, p_punto_venta, p_invoice_number, p_amount_ars,
          p_status, p_notes, p_concept, p_source)
  returning * into v_row;
  return v_row;
end $$;

revoke execute on function add_invoice(date, int, numeric, text, text, text, text, text)
  from public, anon, authenticated;
grant execute on function add_invoice(date, int, numeric, text, text, text, text, text)
  to service_role;
```

- [ ] **Step 4: Apply and run the tests**

Run: `python scripts/apply_migrations.py`
Expected: `apply 009_invoice_settings.sql` then `done`.

Run: `python -m pytest scripts/test_smoke.py -k "settings_seeded or stores_concept or add_invoice_and_duplicate" -v`
Expected: 3 PASS (the pre-existing `add_invoice` test still works without `p_concept`).

If the migration errored halfway: nothing was recorded in `_migrations` (single transaction), fix the file and re-run `apply_migrations.py`.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/009_invoice_settings.sql scripts/test_smoke.py
git commit -m "feat: settings table, invoices.concept, add_invoice p_concept"
```

---

### Task 3: `_batch_bound` helper + `suggest_invoice_batch` RPC

**Files:**
- Create: `supabase/migrations/010_suggest_invoice_batch.sql`
- Modify: `scripts/test_smoke.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_smoke.py`:

```python
def suggest():
    r = rpc("suggest_invoice_batch", {})
    assert r.status_code == 200, r.text
    return r.json()


def band_of(cfg, amount, concept):
    (b,) = [b for b in cfg["bands"] if b["concept"] == concept]
    return b["min"] <= amount <= b["max"]


def set_target_per_batch(cfg, per_batch):
    """Point monthly_target so that this batch's target is exactly per_batch,
    whatever is already invoiced this month (real invoices count too)."""
    state = suggest()
    cfg["monthly_target_ars"] = float(state["invoiced_so_far"]) + state["batches_left"] * per_batch
    set_invoicing(cfg)
    return cfg


def test_batch_properties(invoicing_cfg):
    cfg = set_target_per_batch(invoicing_cfg, 600000)
    for _ in range(200):
        out = suggest()
        assert out["warning"] is None, out
        assert out["reason"] is None
        assert float(out["batch_target"]) == 600000
        inv = out["invoices"]
        assert len(inv) == cfg["batch_size"]
        amounts = [float(i["amount_ars"]) for i in inv]
        assert len(set(amounts)) == len(amounts), "repeated amount"
        for i in inv:
            a = float(i["amount_ars"])
            assert a % cfg["round_to"] == 0
            assert band_of(cfg, a, i["concept"]), i
        for b in cfg["bands"]:
            n = sum(1 for i in inv if i["concept"] == b["concept"])
            assert b["count_min"] <= n <= b["count_max"], inv
        assert abs(sum(amounts) - 600000) <= cfg["round_to"] * len(inv) / 2
        # ordered by band (settings order) then amount desc
        concepts = [i["concept"] for i in inv]
        order = [b["concept"] for b in cfg["bands"]]
        assert concepts == sorted(concepts, key=order.index)
        for b in cfg["bands"]:
            xs = [float(i["amount_ars"]) for i in inv if i["concept"] == b["concept"]]
            assert xs == sorted(xs, reverse=True)


def test_batch_self_corrects_and_avoids_used_amounts(invoicing_cfg):
    from datetime import date
    cfg = set_target_per_batch(invoicing_cfg, 600000)
    before = suggest()
    today = date.today().isoformat()
    r1 = add_invoice(n=951, p_issued_on=today, p_amount_ars=133000, p_concept="Asesoramiento Comercial")
    r2 = add_invoice(n=952, p_issued_on=today, p_amount_ars=91000, p_concept="Servicios Varios")
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    after = suggest()
    assert float(after["invoiced_so_far"]) == float(before["invoiced_so_far"]) + 224000
    expected_target = round((float(after["remaining"]) / after["batches_left"]) / 1000) * 1000
    assert float(after["batch_target"]) == expected_target
    for _ in range(50):
        amounts = {float(i["amount_ars"]) for i in suggest()["invoices"]}
        assert not amounts & {133000.0, 91000.0}


def test_batch_empty_when_target_reached(invoicing_cfg):
    cfg = set_target_per_batch(invoicing_cfg, 0)
    out = suggest()
    assert out["invoices"] == []
    assert out["reason"] == "monthly target reached"
    assert out["warning"] is None


def test_batch_shrinks_when_target_small(invoicing_cfg):
    # 300k fits 3 invoices at the 75k floor but not 5 (floor 445k) or 4 (370k).
    cfg = set_target_per_batch(invoicing_cfg, 300000)
    out = suggest()
    assert out["reason"] is None
    assert len(out["invoices"]) == 3
    amounts = [float(i["amount_ars"]) for i in out["invoices"]]
    assert abs(sum(amounts) - 300000) <= cfg["round_to"] * 3 / 2
    for i in out["invoices"]:
        assert band_of(cfg, float(i["amount_ars"]), i["concept"])


def test_batch_capped_when_target_huge(invoicing_cfg):
    # ceil for 5 with the test bands = 3*200k + 2*120k = 840k.
    set_target_per_batch(invoicing_cfg, 5000000)
    out = suggest()
    assert float(out["batch_target"]) == 840000
    assert "capped at 840000" in out["warning"]
    assert len(out["invoices"]) == 5


def test_batch_size_outside_band_envelope_is_rejected(invoicing_cfg):
    cfg = dict(invoicing_cfg, batch_size=10)
    set_invoicing(cfg)
    r = rpc("suggest_invoice_batch", {})
    assert r.status_code == 400
    assert "batch_size" in r.json()["message"]
```

Notes for the engineer:
- `set_target_per_batch` reads `invoiced_so_far` from a first call so the test works in any month, even with the owner's real invoices present.
- Test invoices 951/952 are dated *today* on PV `9999`; the session fixture deletes them (source `test`).
- The "capped" case can never meet tolerance (three 200 000s would repeat), so `warning` will also mention tolerance — the assertion only checks the `capped` part.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest scripts/test_smoke.py -k "batch" -v`
Expected: all FAIL with 404 (`suggest_invoice_batch` not found).

- [ ] **Step 3: Write the migration**

Create `supabase/migrations/010_suggest_invoice_batch.sql`:

```sql
-- Invoice batch suggestion, part 2: the RPC (needs 009: settings + invoices.concept)

-- Lowest (p_upper = false) or highest (p_upper = true) sum that p_n invoices can
-- reach: mandatory count_min slots first (skipped when p_relaxed), then the free
-- slots greedily from the cheapest / priciest band up to each band's count_max.
create or replace function _batch_bound(
  p_bands jsonb, p_n int, p_relaxed boolean, p_upper boolean
) returns numeric
language plpgsql immutable as $$
declare
  v_total numeric := 0;
  v_slots int := p_n;
  v_cmin  int;
  v_cap   int;
  v_take  int;
  b       jsonb;
begin
  if not p_relaxed then
    for b in select e from jsonb_array_elements(p_bands) e loop
      v_cmin  := (b->>'count_min')::int;
      v_total := v_total + v_cmin *
                 (case when p_upper then b->>'max' else b->>'min' end)::numeric;
      v_slots := v_slots - v_cmin;
    end loop;
  end if;
  for b in select e from jsonb_array_elements(p_bands) e
            order by case when p_upper then -(e->>'max')::numeric
                          else (e->>'min')::numeric end loop
    exit when v_slots <= 0;
    v_cap  := (b->>'count_max')::int
              - case when p_relaxed then 0 else (b->>'count_min')::int end;
    v_take := least(v_cap, v_slots);
    v_total := v_total + v_take *
               (case when p_upper then b->>'max' else b->>'min' end)::numeric;
    v_slots := v_slots - v_take;
  end loop;
  return v_total;
end $$;

-- Weekly batch of invoice amounts. Reads settings.invoicing + this month's issued
-- invoices; writes nothing. Sizing and drawing rules: spec §5.3.
create or replace function suggest_invoice_batch()
returns jsonb
language plpgsql volatile as $$
declare
  s              jsonb;
  v_target       numeric;
  v_batch_size   int;
  v_weekday      int;
  v_round        numeric;
  v_bands        jsonb;
  v_nb           int;
  v_today        date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
  v_month_start  date;
  v_month_end    date;
  v_invoiced     numeric;
  v_used         numeric[];
  v_batches_left int;
  v_remaining    numeric;
  v_batch_target numeric;
  v_n            int;
  v_sum_cmin     int := 0;
  v_sum_cmax     int := 0;
  v_relaxed      boolean;
  v_floor        numeric;
  v_ceil         numeric;
  v_warning      text := null;
  v_counts       int[];
  v_amounts      numeric[];
  v_band_idx     int[];
  v_best_amounts numeric[];
  v_best_idx     int[];
  v_best_diff    numeric := null;
  v_ok           boolean;
  v_sum          numeric;
  v_pick         numeric;
  v_cmin         int;
  v_cmax         int;
  v_min          numeric;
  v_max          numeric;
  v_tries        int;
  v_out          jsonb;
  i int; j int;
begin
  -- 1. settings
  select value into s from settings where key = 'invoicing';
  if not found then
    raise exception 'settings row "invoicing" not found';
  end if;
  v_target     := (s->>'monthly_target_ars')::numeric;
  v_batch_size := (s->>'batch_size')::int;
  v_weekday    := (s->>'batch_weekday')::int;
  v_round      := (s->>'round_to')::numeric;
  v_bands      := s->'bands';
  if v_target is null or v_batch_size is null or v_weekday is null or v_round is null
     or jsonb_typeof(v_bands) <> 'array' or jsonb_array_length(v_bands) = 0 then
    raise exception 'settings.invoicing needs monthly_target_ars, batch_size, batch_weekday, round_to and a non-empty bands array';
  end if;
  if v_weekday not between 1 and 7 or v_round <= 0 or v_batch_size < 1 then
    raise exception 'settings.invoicing: batch_weekday must be 1-7 (ISO), round_to > 0, batch_size >= 1';
  end if;
  v_nb := jsonb_array_length(v_bands);
  for i in 0 .. v_nb - 1 loop
    if (v_bands->i->>'concept') is null or (v_bands->i->>'min') is null
       or (v_bands->i->>'max') is null or (v_bands->i->>'count_min') is null
       or (v_bands->i->>'count_max') is null then
      raise exception 'settings.invoicing.bands[%] needs concept, min, max, count_min, count_max', i;
    end if;
    if (v_bands->i->>'min')::numeric > (v_bands->i->>'max')::numeric
       or (v_bands->i->>'count_min')::int < 0
       or (v_bands->i->>'count_min')::int > (v_bands->i->>'count_max')::int then
      raise exception 'settings.invoicing.bands[%]: need 0 <= min <= max and 0 <= count_min <= count_max', i;
    end if;
    v_sum_cmin := v_sum_cmin + (v_bands->i->>'count_min')::int;
    v_sum_cmax := v_sum_cmax + (v_bands->i->>'count_max')::int;
  end loop;
  if v_batch_size < v_sum_cmin or v_batch_size > v_sum_cmax then
    raise exception 'batch_size % must be between the sum of bands.count_min (%) and the sum of bands.count_max (%)',
      v_batch_size, v_sum_cmin, v_sum_cmax;
  end if;

  -- 2. month state
  v_month_start := date_trunc('month', v_today)::date;
  v_month_end   := (v_month_start + interval '1 month' - interval '1 day')::date;
  select coalesce(sum(amount_ars), 0),
         coalesce(array_agg(distinct amount_ars), '{}')
    into v_invoiced, v_used
    from invoices
   where status = 'issued'
     and issued_on between v_month_start and v_month_end;
  select count(*) into v_batches_left
    from generate_series(v_today, v_month_end, interval '1 day') d
   where extract(isodow from d) = v_weekday;
  v_batches_left := greatest(v_batches_left, 1);
  v_remaining    := v_target - v_invoiced;
  v_batch_target := round(v_remaining / v_batches_left / v_round) * v_round;

  -- 3. sizing: shrink n until the batch can be that small
  v_n := v_batch_size;
  loop
    v_relaxed := v_n < v_sum_cmin;
    v_floor   := _batch_bound(v_bands, v_n, v_relaxed, false);
    exit when v_batch_target >= v_floor or v_n = 1;
    v_n := v_n - 1;
  end loop;
  if v_batch_target < v_floor then
    return jsonb_build_object(
      'month', to_char(v_today, 'YYYY-MM'),
      'monthly_target_ars', v_target,
      'invoiced_so_far', v_invoiced,
      'remaining', v_remaining,
      'batches_left', v_batches_left,
      'batch_target', v_batch_target,
      'warning', null,
      'reason', 'monthly target reached',
      'invoices', '[]'::jsonb);
  end if;
  v_ceil := _batch_bound(v_bands, v_n, v_relaxed, true);
  if v_batch_target > v_ceil then
    v_warning := format('batch capped at %s; monthly target cannot be reached at this cadence', v_ceil);
    v_batch_target := v_ceil;
  end if;

  -- 4. drawing: rejection sampling, keep the closest acceptable set as fallback
  for attempt in 1 .. 500 loop
    -- per-band counts summing to v_n
    v_tries := 0;
    loop
      v_counts := '{}';
      for i in 0 .. v_nb - 1 loop
        v_cmin := case when v_relaxed then 0 else (v_bands->i->>'count_min')::int end;
        v_cmax := (v_bands->i->>'count_max')::int;
        v_counts := v_counts || (v_cmin + floor(random() * (v_cmax - v_cmin + 1)))::int;
      end loop;
      exit when (select sum(c) from unnest(v_counts) c) = v_n;
      v_tries := v_tries + 1;
      if v_tries > 10000 then
        raise exception 'could not pick per-band counts summing to % — check bands.count_min/count_max', v_n;
      end if;
    end loop;

    -- amounts
    v_amounts  := '{}';
    v_band_idx := '{}';
    v_ok := true;
    for i in 0 .. v_nb - 1 loop
      v_min := (v_bands->i->>'min')::numeric;
      v_max := (v_bands->i->>'max')::numeric;
      for j in 1 .. v_counts[i + 1] loop
        v_pick := round((v_min + random() * (v_max - v_min)) / v_round) * v_round;
        v_pick := least(greatest(v_pick, v_min), v_max);
        if v_pick = any(v_amounts) or v_pick = any(v_used) then
          v_ok := false;
        end if;
        v_amounts  := v_amounts || v_pick;
        v_band_idx := v_band_idx || i;
      end loop;
    end loop;
    continue when not v_ok;

    v_sum := (select sum(a) from unnest(v_amounts) a);
    if v_best_diff is null or abs(v_sum - v_batch_target) < v_best_diff then
      v_best_diff    := abs(v_sum - v_batch_target);
      v_best_amounts := v_amounts;
      v_best_idx     := v_band_idx;
    end if;
    exit when v_best_diff <= v_round * v_n / 2;
  end loop;

  if v_best_amounts is null then
    raise exception 'could not draw % distinct amounts not already used this month — widen the bands', v_n;
  end if;
  if v_best_diff > v_round * v_n / 2 then
    v_warning := concat_ws('; ', v_warning, 'tolerance not met after 500 draws');
  end if;

  -- 5. output: band order (as in settings), then amount desc
  select jsonb_agg(jsonb_build_object('concept', v_bands->bi->>'concept', 'amount_ars', a)
                   order by bi, a desc)
    into v_out
    from unnest(v_best_amounts, v_best_idx) as t(a, bi);

  return jsonb_build_object(
    'month', to_char(v_today, 'YYYY-MM'),
    'monthly_target_ars', v_target,
    'invoiced_so_far', v_invoiced,
    'remaining', v_remaining,
    'batches_left', v_batches_left,
    'batch_target', v_batch_target,
    'warning', v_warning,
    'reason', null,
    'invoices', v_out);
end $$;

revoke execute on function _batch_bound(jsonb, int, boolean, boolean)
  from public, anon, authenticated;
revoke execute on function suggest_invoice_batch() from public, anon, authenticated;
grant execute on function suggest_invoice_batch() to service_role;
```

- [ ] **Step 4: Apply the migration**

Run: `python scripts/apply_migrations.py`
Expected: `skip  009_invoice_settings.sql`, `apply 010_suggest_invoice_batch.sql`, `done`.

- [ ] **Step 5: Run the batch tests**

Run: `python -m pytest scripts/test_smoke.py -k "batch or settings or concept" -v`
Expected: all PASS. `test_batch_properties` takes ~200 HTTP calls (≈20–40 s).

If `test_batch_properties` fails on tolerance: check `v_round * v_n / 2` — with `round_to` 1000 and 5 invoices the sum may differ from the target by at most 2 500, i.e. ±2 000 in practice.

- [ ] **Step 6: Full suite**

Run: `python -m pytest scripts/test_smoke.py -v`
Expected: every test PASS, including the older fiscal tests (the `add_invoice` re-create must not break them).

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/010_suggest_invoice_batch.sql scripts/test_smoke.py
git commit -m "feat: suggest_invoice_batch RPC — weekly batch sized to the monthly target"
```

---

### Task 4: Playbook — "Weekly invoice batch" + `p_concept`

**Files:**
- Modify: `docs/agent-playbook.md` (Fiscal module section)

- [ ] **Step 1: Update the invoice-recording paragraph**

Replace:

```
**Recording an invoice** (the user issued a factura in ARCA and tells you about
it): `POST /rest/v1/rpc/add_invoice` with `p_issued_on`, `p_invoice_number` (int),
`p_amount_ars`, `p_source`. `p_punto_venta` defaults to `"0001"`. **Never record
an invoice the user didn't confirm as actually issued.**
```

with:

```
**Recording an invoice** (the user issued a factura in ARCA and tells you about
it): `POST /rest/v1/rpc/add_invoice` with `p_issued_on`, `p_invoice_number` (int),
`p_amount_ars`, `p_source`, and `p_concept` (the description printed on the
factura, e.g. `"Servicios Varios"`) when known. `p_punto_venta` defaults to
`"0001"`. **Never record an invoice the user didn't confirm as actually issued.**
```

- [ ] **Step 2: Add the new section right before `## Statement import workflow`**

```markdown
## Weekly invoice batch

The owner invoices in fixed weekly batches so the month lands on a target. A
cron (yours to set up — Telegram, email, whatever the owner uses) should run
this flow once per `batch_weekday`:

1. `POST /rest/v1/rpc/suggest_invoice_batch` with body `{}`. Response:
   ```json
   {"month": "2026-09", "monthly_target_ars": 2200000, "invoiced_so_far": 1075000,
    "remaining": 1125000, "batches_left": 2, "batch_target": 563000,
    "warning": null, "reason": null,
    "invoices": [{"concept": "Asesoramiento Comercial", "amount_ars": 176000}, ...]}
   ```
2. Show the owner the batch as a numbered list — `1. Asesoramiento Comercial — $176.000`
   — then one line: *invoiced so far $X of $Y, N batches left this month*.
   If `warning` or `reason` is set, quote it verbatim. An empty `invoices` list
   means the target is already covered: say so and stop.
3. Wait. The owner issues the facturas in ARCA and replies with the comprobante
   numbers (and any amount they changed).
4. Record each one with `add_invoice` (`p_issued_on`, `p_invoice_number`,
   `p_amount_ars`, `p_concept`, `p_source`). Never record an invoice the owner
   did not confirm; never re-run step 1 to "refresh" a batch that was already
   partly issued — record what was issued first.

The RPC writes nothing and is safe to call any number of times. It sizes each
batch as `(monthly_target − invoiced this month) ÷ batches left`, so a heavy
week is followed by lighter ones automatically.

**Changing the settings** (the owner asks to raise the target, change bands,
etc.): read the row, edit the JSON, write the whole value back:

```
GET   /rest/v1/settings?key=eq.invoicing&select=value
PATCH /rest/v1/settings?key=eq.invoicing     body: {"value": { ...edited json... }}
```

Fields: `monthly_target_ars`, `batch_size`, `batch_weekday` (ISO, 1 = Monday …
7 = Sunday), `round_to`, and `bands` — each with `concept`, `min`, `max`,
`count_min`, `count_max`. `batch_size` must lie between the sum of the bands'
`count_min` and the sum of their `count_max`; the RPC tells you if it doesn't.
```

- [ ] **Step 3: Read it back once for formatting**

Run: `sed -n '/## Weekly invoice batch/,/## Statement import workflow/p' docs/agent-playbook.md`
Expected: the section renders with the fenced blocks closed and the next heading intact.

- [ ] **Step 4: Commit**

```bash
git add docs/agent-playbook.md
git commit -m "docs: weekly invoice batch flow in the agent playbook"
```

---

### Task 5: `docs/configuration.md` entry

**Files:**
- Modify: `docs/configuration.md` (Fiscal module list)

- [ ] **Step 1: Add a bullet after the "Punto de venta" bullet**

```markdown
- **Invoice batch settings** (data): the `settings` row with key `invoicing`
  (seeded in `009`). Set `monthly_target_ars` to your own number before the
  first `suggest_invoice_batch` call — the seed value is a placeholder. The
  two seeded `bands` (descriptions + amount ranges) are examples; edit them to
  match what you actually invoice. Field reference: `docs/agent-playbook.md`
  → "Weekly invoice batch".
```

- [ ] **Step 2: Commit**

```bash
git add docs/configuration.md
git commit -m "docs: configuration entry for invoice batch settings"
```

---

### Task 6: Owner setup (not code — the owner does this once)

- [ ] Set the real `monthly_target_ars` on the live project (the seed is a placeholder), e.g. via any agent following the playbook's "Changing the settings" steps, or in the Supabase table editor.
- [ ] Call `suggest_invoice_batch` once by hand and eyeball the numbers.
- [ ] Wire the cron in the agent of choice (Hermes → Telegram for the owner).

---

## Self-review

- **Spec coverage:** §5.1 settings → Task 2; §5.2 concept/add_invoice → Task 2; §5.3 RPC (sizing, shrinking, capping, rejection sampling, used-amount avoidance, ordering, output shape) → Task 3; §5.4 playbook → Task 4; §5.5 configuration → Task 5; §6 errors → Task 3 validation block + tests; §7 tests → Tasks 2–3 (properties ×200, self-correction, target reached, shrinking, capped, validation, concept).
- **Placeholders:** none.
- **Type consistency:** `add_invoice` signature `(date, int, numeric, text, text, text, text, text)` used identically in drop/revoke/grant; `_batch_bound(jsonb, int, boolean, boolean)` identical in create/revoke/calls; output keys match between RPC, tests and playbook.
