# Invoice Batch Suggestion — Design

Date: 2026-09-12
Status: approved design, pending implementation plan

## 1. Problem

The owner issues monotributo invoices (Factura C) by hand in ARCA, in fixed
batches of `batch_size` (currently 5) once a week, so that the month's total
lands on a chosen monthly amount. Amounts must look like real client work:
random, rounded to the thousand, spread across two bands with two recurring
descriptions. Today the numbers are produced in a Google Sheet with
`ROUND(RANDBETWEEN(...);-3)` and a few manual re-rolls until the total "looks
right".

The earlier `suggest_invoices(p_target, p_min, p_max)` RPC (2026-07 plan) was
rejected: it used the fewest invoices that could carry the target, which pushed
every amount toward the maximum. That was a math problem, not a Supabase one.

## 2. Goal

Any agent (Hermes on a cron, Claude Code, ...) can make **one HTTP call** and
get back a ready-to-issue batch: N amounts with their descriptions, plus enough
month context to tell the owner where they stand. The owner issues the invoices
manually, reports the comprobante numbers, and the agent records them.

## 3. Non-goals

- Scheduling / notification. Each user wires their own cron and channel
  (Telegram, etc.). The repo ships the RPC and the playbook section only.
- Issuing invoices through ARCA / AFIP SDK. Deferred; this design adds the
  `concept` field that integration will need.
- Any change to deposit/conversion logic. "Restante por depositar" stays a
  consequence of invoices and deposits, not an input to this feature.

## 4. Architecture

All-in-Supabase, matching the existing system: one new table, one new column,
one new RPC, one playbook section. No runtime is required on agent hosts.

## 5. Components

### 5.1 `settings` table (migration `009_invoice_batch.sql`)

```sql
create table settings (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
alter table settings enable row level security;
```

Seed row, key `invoicing`:

```json
{
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
}
```

- `monthly_target_ars` — what the month should total. Seed value is a
  placeholder; the owner sets it (e.g. near their category's cap ÷ 12).
- `batch_weekday` — ISO weekday, 1 = Monday … 7 = Sunday. Used only to count
  the batches left in the month.
- `bands` — any number of bands. Each has a description (`concept`), an
  inclusive amount range, and how many invoices of that band a batch may
  contain. The sum of all `count_min` must be ≤ `batch_size` ≤ the sum of all
  `count_max`; the RPC raises a descriptive error otherwise.

Agents change settings with PostgREST directly:
`PATCH /rest/v1/settings?key=eq.invoicing` with `{"value": {...full json...}}`.
(Whole-value replace; the agent reads first, edits, writes back.)

### 5.2 `invoices.concept`

`alter table invoices add column concept text;` — nullable, historic rows stay
null. `add_invoice` gains `p_concept text default null` (new signature; revoke
and grant updated accordingly).

### 5.3 RPC `suggest_invoice_batch()`

`returns jsonb`, `volatile`, no arguments, writes nothing, `service_role` only.

Inputs read at call time:

- `s` = `settings.value` where `key = 'invoicing'`. Missing row → error
  `settings row "invoicing" not found`.
- `invoiced_so_far` = `sum(amount_ars)` of `invoices` with `status = 'issued'`
  and `issued_on` in the current month (`America/Argentina/Buenos_Aires`).
- `used_amounts` = the distinct `amount_ars` values of those same rows.
- `batches_left` = number of dates from today (inclusive) to month end whose
  ISO weekday = `batch_weekday`; floored at 1.

Sizing:

- `remaining` = `monthly_target_ars − invoiced_so_far`.
- `batch_target` = `round(remaining / batches_left / round_to) × round_to`.
- `n` starts at `batch_size`. `floor_n` = the smallest sum achievable with `n`
  invoices honouring `count_min` (fill the rest from the cheapest band's min);
  `ceil_n` likewise with maxes.
- **Too small:** while `batch_target < floor_n` and `n > 1`, decrement `n`.
  Once `n` is below the sum of the bands' `count_min`, every band's
  `count_min` is treated as 0 (`count_max` unchanged), so `floor_n` becomes
  `n × (smallest band min)`. If `n = 1` and `batch_target` is still below the
  smallest band `min`, return an empty batch with
  `reason = "monthly target reached"`.
- **Too big:** if `batch_target > ceil_n`, set `batch_target = ceil_n` and
  `warning = "batch capped at <ceil_n>; monthly target cannot be reached at
  this cadence"`.

Drawing (rejection sampling, replaces the manual re-rolls):

1. Choose a per-band count vector: random integers within each band's
   `[count_min, count_max]` that sum to `n` (rejection until valid; with the
   seed config this is one of {2+3, 3+2}).
2. For each slot, draw uniform in `[min, max]`, round to `round_to`, clamp
   back into the band.
3. Accept the set if (a) no amount repeats inside the set, (b) no amount is in
   `used_amounts`, and (c) `|sum − batch_target| ≤ round_to × n / 2`.
4. Otherwise redraw. After 500 failed attempts, return the closest set seen
   (by `|sum − batch_target|`) with `warning = "tolerance not met after 500
   draws"`. Never raises for this.

Output:

```json
{
  "month": "2026-09",
  "monthly_target_ars": 2200000,
  "invoiced_so_far": 1075000,
  "remaining": 1125000,
  "batches_left": 2,
  "batch_target": 563000,
  "warning": null,
  "reason": null,
  "invoices": [
    {"concept": "Asesoramiento Comercial", "amount_ars": 176000},
    {"concept": "Asesoramiento Comercial", "amount_ars": 131000},
    {"concept": "Servicios Varios",        "amount_ars":  98000},
    {"concept": "Servicios Varios",        "amount_ars":  81000},
    {"concept": "Servicios Varios",        "amount_ars":  77000}
  ]
}
```

`invoices` is ordered by band (as in settings) then descending amount, so
the output reads the same way every time. Empty batch → `invoices: []`.

### 5.4 Playbook section "Weekly invoice batch"

Added to `docs/agent-playbook.md` under the Fiscal module:

1. `POST /rest/v1/rpc/suggest_invoice_batch` with `{}`.
2. Show the owner the batch as a numbered list (`concept — $amount`), then one
   line of context: invoiced so far / monthly target / batches left. Surface
   `warning` or `reason` verbatim if set.
3. Wait. The owner issues the invoices in ARCA and replies with the comprobante
   numbers (and any amount they changed).
4. Record each with `add_invoice` (`p_issued_on`, `p_invoice_number`,
   `p_amount_ars`, `p_concept`, `p_source`). Never record an invoice the owner
   did not confirm.
5. Changing settings: describe the PATCH on `settings`, with the JSON shape.

The existing "never record unconfirmed invoices" rule stays the guard rail.

### 5.5 `docs/configuration.md`

Short entry: the `settings.invoicing` row, what each field means, and that
`monthly_target_ars` must be set by the owner before the first run.

## 6. Error handling

- Missing settings row, malformed bands, or `batch_size` outside the
  `count_min`/`count_max` envelope → `raise exception` with the fix in the
  message (same style as `add_transaction`).
- Unreachable targets never error; they return `warning`/`reason` so the cron
  agent can relay them instead of failing silently.

## 7. Testing (`scripts/test_smoke.py`, live Supabase like the rest)

Setup helper writes a known `invoicing` settings row and restores the original
after the test.

- **Properties over 200 calls** (fresh month state or a temporary target large
  enough): every amount inside its band, multiple of `round_to`, no repeats
  within a batch, per-band counts within `[count_min, count_max]`, total count
  = `batch_size`, `|sum − batch_target| ≤ round_to × n / 2` when `warning` is
  null.
- **Self-correction:** insert issued invoices for this month (source `test`,
  cleaned up after), call again, assert `invoiced_so_far` and `batch_target`
  reflect them and that their amounts are not reused.
- **Target reached:** target ≤ `invoiced_so_far` → `invoices = []`,
  `reason` set.
- **Shrinking:** a remaining amount that fits 3 invoices but not 5 → batch of
  3, sum within tolerance.
- **Capped:** a huge target → `batch_target = ceil_n`, `warning` set.
- **Validation:** `batch_size` = 10 with the seed bands → descriptive error.
- **`add_invoice` with `p_concept`** stores the value; without it stores null.

## 8. Out of scope but prepared

`concept` on `invoices` is the text the future AFIP-SDK integration sends as
the item description. `settings` is generic key/value so later features
(e.g. current monotributo category) can reuse it.
