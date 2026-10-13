# Credit Card Installments (Cuotas) — Design

Date: 2026-10-13
Status: approved design, pending implementation plan

## 1. Problem

A credit card purchase is one economic fact on the day it is made, but the cash
leaves later: in the next statement, or spread over N monthly installments
("cuotas"). Today the system only records the fact. It cannot answer "how much
will the Visa statement charge me next month?" or "how much do I owe in
installments over the next 6 months?", and nothing stops an agent from logging
the statement payment as a second expense.

## 2. Goal

The owner says one thing — "compré una heladera a $600.000 en 6 cuotas con la
Visa" — and the system can project, per card, what each upcoming statement
charges. No extra reporting is required from the owner: card cycle dates come
from statements the owner already hands to the agent for import.

## 3. Decisions (agreed 2026-10-13)

- **The purchase stays one transaction**, full amount, on the purchase date.
  Monthly summaries (`v_monthly_summary`, `v_fiscal_balance`) are unchanged.
- **USD conversion uses the purchase-date rate** for the whole amount (option A).
  This overstates the real cost of ARS installments without interest by roughly
  the inflation over the plan (≈5% for 6 cuotas at 2%/month). Accepted for
  simplicity and because it keeps "fact" and "cash" cleanly separate.
- **Installments with interest:** the transaction amount is the total actually
  paid (sum of all cuotas). The list price, if relevant, goes in `p_notes`.
- **The statement payment is never recorded as a transaction.** It is cash
  settling debt, not a new expense.
- **The cuota calendar is computed, not stored.** Only the plan (how many cuotas)
  is stored; which statement each cuota lands in is derived from cycle dates, so
  it corrects itself when real cycle dates arrive.

## 4. Non-goals

- Account balances, internal transfers, balance snapshots. Deferred until the
  system models accounts (see conversation 2026-10-13: statements of every
  platform would be needed; not worth the friction now).
- Investments, asset valuation, stablecoins as currencies.
- Promotions with a deferred first cuota ("empezás a pagar en 3 meses").
- Card credits/refunds and their effect on a statement.
- Recording statement payments or modelling card debt as a balance.

Statement fees and taxes (comisión, IVA, sellos) are ordinary lines: the existing
statement import records them as normal outflows. Nothing new here.

## 5. Components

All-in-Supabase, matching the existing system. Next migration number: `011`.

### 5.1 Cards in `payment_methods`

```sql
alter table payment_methods
  add column is_credit_card       boolean not null default false,
  add column default_closing_day  int check (default_closing_day between 1 and 31),
  add column default_due_day      int check (default_due_day between 1 and 31),
  add check (not is_credit_card
             or (default_closing_day is not null and default_due_day is not null));
```

The default days are only a fallback for months with no known real cycle. The
owner (or an agent) flags existing cards once; `docs/configuration.md` documents
it.

### 5.2 `card_cycles` table — real closing/due dates

```sql
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
```

RPC `add_card_cycle(p_payment_method text, p_closing_date date, p_due_date date,
p_source text)`:
- Looks up the card by name (case-insensitive, like `add_transaction`); errors
  with the list of valid credit cards if not found or not `is_credit_card`.
- Keeps **one real cycle per card per calendar month**: if that card already has
  a row whose `closing_date` is in the same month, it is replaced (latest
  information wins); otherwise a new row is inserted.

A real statement shows the current cycle and the next one ("próximo cierre /
próximo vencimiento"); the agent records both on every import. A printed "next
closing" often moves by a day or two; the following statement then replaces it
with the actual date, with no error and no owner involvement.

Known simplification: cycles are keyed by calendar month, so a card that closes
twice in one month (e.g. the 2nd and the 30th) cannot be represented. Accepted;
revisit only if it happens.

### 5.3 `installment_plans` table

```sql
create table installment_plans (
  transaction_id uuid primary key references transactions(id) on delete cascade,
  installments   int  not null check (installments between 2 and 60)
);
alter table installment_plans enable row level security;
```

Only plans with 2+ cuotas get a row. A credit card outflow without a plan row
is a single payment (1 cuota).

Cuota amounts: `round(amount / n, 2)` for cuotas 1…n-1; cuota n absorbs the
remainder so the cuotas sum exactly to the transaction amount.

### 5.4 Views

**Cycles (internal, `v_card_cycles`):** per credit card, one row per calendar
month from the card's first outflow to 60 months ahead:
- if `card_cycles` has a row whose `closing_date` falls in that month → use it,
  `is_estimated = false`;
- else → `closing_date = make_date(y, m, least(default_closing_day, last day of
  month))`, `due_date` = first date after it whose day is `default_due_day`
  (clamped to month end), `is_estimated = true`.

**`v_card_schedule`:** one row per cuota of every credit card outflow
(`parent_id is null`):

| column | meaning |
|---|---|
| `transaction_id`, `occurred_on`, `description`, `payment_method`, `currency` | from the purchase |
| `installment_no`, `installments` | k of n (1 of 1 when no plan) |
| `installment_amount` | per §5.3 |
| `closing_date`, `due_date`, `is_estimated` | the statement it lands in |

Rule: cuota k lands in the k-th cycle with `closing_date >= occurred_on`.
Purchases on the closing day itself count in that cycle; a real statement that
disagrees by a day is accepted as a known edge (the import compares by amount,
not by cycle, so it causes no duplicates).

**`v_card_statements`:** per card, `closing_date`, `due_date`, `currency`:
sum of `installment_amount`, count of cuotas, `is_estimated`. Answers "what does
the next Visa statement charge?" (ARS and USD separately, as on the real
statement).

All views `security_invoker = true`.

### 5.5 `add_transaction` gains `p_installments`

New optional last parameter `p_installments int default null`. The old signature
is dropped first (same PostgREST overload issue as migration `009`).

- `null` or `1` → no plan row (unchanged behaviour).
- `2..60` → requires `p_type = 'outflow'` and a payment method with
  `is_credit_card`; inserts the plan row in the same call.
- Anything else → descriptive error (e.g. "p_installments needs a credit card
  payment method — valid cards: Visa, Mastercard").

### 5.6 Playbook (`docs/agent-playbook.md`)

- `p_installments` row in the `add_transaction` table.
- "Paying a card statement": never record it; tell the owner it is already
  counted. Answer "¿cuánto pago de la Visa?" from `v_card_statements`.
- Statement import, additions:
  1. Record the current and next cycle with `add_card_cycle`.
  2. Lines shaped like `CUOTA k/n` (or `k/n` next to the merchant): the purchase
     total is `cuota × n` and its date is the original purchase date printed on
     the line. Its `external_ref` is built from the **purchase** (method, original
     date, total, merchant), so every month's cuota line maps to the same ref.
  3. Match against existing rows by original date (±1 day) and total
     (± n pesos for rounding). Matched → skip. Unmatched → propose it to the owner
     like any new line and insert with `p_installments = n`.

### 5.7 `docs/configuration.md`

How to flag a card: set `is_credit_card`, `default_closing_day`,
`default_due_day` on its `payment_methods` row.

## 6. Error handling

- `add_card_cycle` with a non-card method or `due_date <= closing_date` → error.
- `p_installments` on an inflow, without a payment method, or on a non-card
  method → error listing valid cards.
- A card flagged without default days → blocked by the table check.

## 7. Testing (`scripts/test_smoke.py`, live Supabase)

Each test states why the rule matters:
- 6-cuota purchase on a card creates a plan; cuotas sum exactly to the amount
  (no peso lost to rounding).
- `v_monthly_summary` shows the full amount in the purchase month (decision A —
  the fact is not spread).
- Cuota k lands in the k-th cycle after the purchase, using estimated cycles when
  no real ones exist.
- Adding a real cycle moves the affected cuotas to the real dates (the calendar
  self-corrects, nothing stored goes stale).
- Re-recording a cycle with a moved closing date in the same month replaces the
  old one (a statement's "próximo cierre" is only a forecast).
- Purchase on the closing day lands in that cycle.
- `p_installments` with a non-card method or an inflow errors.
- A card outflow with no plan appears once, as 1 of 1.
- `v_card_statements` totals ARS and USD separately.

## 8. Out of scope but prepared

- When accounts with balances arrive, a statement payment can become a transfer
  from a bank account to the card's debt — the schedule views already say how
  much each payment should be.
- Per-cuota USD valuation (option B) can be added later as a separate view over
  `v_card_schedule` joined to `fx_rates` by `due_date`, without touching stored
  data.
