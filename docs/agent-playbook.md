# Agent Playbook — Personal Accounting System

You are an agent logging transactions or answering questions for the owner's
personal accounting system. This document is the complete interface contract.

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
| `p_source` | yes | your agent name: `claude`, `telegram-bot`, `statement`, ... |
| `p_currency` | no | `ARS` (default) or `USD` |
| `p_fiscal` | no | default `true` (Blanco). Set `false` only if the user says en negro / sin factura. |
| `p_tags` | no | kebab-case strings, e.g. `["viaje-enero"]` |
| `p_payment_method` | no | one of the list below; omit if not mentioned |
| `p_parent_id` | no | reimbursements only: uuid of the group outflow being repaid |
| `p_external_ref` | no | statement imports only, see below |
| `p_notes` | no | anything extra worth keeping |
| `p_installments` | no | credit card purchases in cuotas only: number of cuotas (2–60). The amount is the **total** paid (sum of all cuotas). Omit for 1 payment. |

Success returns the inserted row (JSON) including `id`, `usd_rate`, `amount_usd`.
Errors are descriptive (e.g. list the valid categories) — fix the input and retry;
do not ask the user unless genuinely ambiguous.

**Categories — outflow:** Comida, Salidas, Transporte, Compras, Regalos,
Suscripciones, Impuestos, Hogar, Servicios, Salud, Viajes, Otros gastos
**Categories — inflow:** Sueldo, Freelance, Reintegros, Regalos recibidos,
Rendimientos, Otros ingresos
**Payment methods:** only `Cash ARS` and `Cash USD` are seeded — the owner adds
their own banks, cards, and wallets. ALWAYS fetch the live list. Note the same
table also holds conversion PLATFORMS (§Fiscal module: banks and exchanges used
in `add_conversion`) — never tag a transaction with a platform-only entry.

(The category list above is the seed set; when in doubt fetch live:
`GET /rest/v1/categories?active=eq.true&select=name,kind` and
`GET /rest/v1/payment_methods?active=eq.true&select=name`.)

## Group payments

When the user pays for a group: log the **full amount** as one outflow.
When someone pays them back: log an **inflow, category Reintegros, with
`p_parent_id`** set to the original outflow's id. Find it if needed:
`GET /rest/v1/v_transactions?type=eq.outflow&order=occurred_on.desc&description=ilike.*<keyword>*`
Reports automatically net reimbursements against the parent expense.

## Querying

Views (read with GET + PostgREST filters):
- `v_transactions` — everything, joined names, `net_amount_usd`
- `v_monthly_summary` — month × type × category × fiscal → `total_usd`, `tx_count`
- `v_group_expenses` — group outflows, one row per outflow (filter by `id` =
  the outflow transaction's id): `gross_usd`, `reimbursed_usd`, `net_usd`,
  `reimbursement_count`
- `v_deposit_status` — single row: Total Facturado / Depositado / Cobrado,
  Restante por Depositar, Disponible para Depositar (all ARS, cumulative)
- `v_monotributo_status` — one row per bracket category: cap, 12-month invoiced,
  headroom, monthly averages, `cap_exceeded`
- `v_fiscal_balance` — per month: `invoiced_ars` vs `blanco_spend_ars` + cumulatives

Examples:
```
GET /rest/v1/v_monthly_summary?month=eq.2026-06-01&type=eq.outflow
GET /rest/v1/v_transactions?occurred_on=gte.2026-06-01&category=eq.Salidas
GET /rest/v1/v_transactions?tags=cs.{viaje-enero}
```

Anything the views can't express — ad-hoc SQL (single SELECT only):
`POST /rest/v1/rpc/run_report` with `{"p_sql": "select ... from v_transactions ..."}`

**Report conventions:** amounts in USD (`net_amount_usd` / `total_usd`);
reimbursement rows (`parent_id` set) are already excluded from summaries and
netted into their parent's month.

## Fiscal module (invoices & conversions)

**Recording an invoice** (the user issued a factura in ARCA and tells you about
it): `POST /rest/v1/rpc/add_invoice` with `p_issued_on`, `p_invoice_number` (int),
`p_amount_ars`, `p_source`, and `p_concept` (the description printed on the
factura, e.g. `"Servicios Varios"`) when known. `p_punto_venta` defaults to
`"0001"`. **Never record an invoice the user didn't confirm as actually issued.**

**Recording a conversion** — three kinds (`p_kind`):
- `crypto_sale`: sold USDC for ARS on an exchange → needs `p_usd_amount` and
  `p_platform` (the exchange's `payment_methods` entry). The ARS is en blanco
  immediately.
- `cash_sale`: sold USD/USDC for ARS *cash* via dealer → `p_usd_amount`,
  `p_platform: "Cash ARS"`.
- `bank_deposit`: deposited ARS cash into a bank → NO `p_usd_amount`,
  `p_platform` = the bank's `payment_methods` entry.
`POST /rest/v1/rpc/add_conversion` with `p_occurred_on`, `p_ars_amount`, plus the
above. Optional `p_rate_quoted` if the user mentions the quoted rate.

**Conversions are NOT income.** Salary arriving in USDC is an inflow transaction
(`add_transaction`, category Sueldo, currency USD); selling that USDC later is a
conversion. Never create both from one event.

**Typical fiscal questions** → which surface:
- "¿Cuánto me queda por depositar?" → `v_deposit_status.restante_por_depositar`
- "¿Cómo vengo con el monotributo?" → `v_monotributo_status` (watch `cap_exceeded`
  and `headroom` of his current category)
- "¿Facturé más de lo que gasté en blanco?" → `v_fiscal_balance`

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

## Credit card statements

- **Never record the payment of a card statement** ("pagué la Visa"). Every
  purchase on it is already recorded; tell the owner so.
- "¿Cuánto pago de la Visa?" → the statement being paid is the one with the
  earliest `due_date` on or after today:
  `GET /rest/v1/v_card_statements?payment_method=eq.<Card>&due_date=gte.<today>&order=due_date,currency`
  Keep only the rows whose `closing_date` equals the first row's (one row per
  currency). Report ARS and USD separately — never add them. "¿Y el mes que
  viene?" = the rows of the next `closing_date`. `is_estimated = true` means the
  cycle dates are the card's default days, not yet confirmed by a real statement.
- "¿Cuánto tengo comprometido en cuotas?" → `run_report` with
  `select currency, sum(installment_amount) as total from v_card_schedule where due_date >= current_date and installments > 1 group by currency`.
- The owner corrects the cuotas of a purchase already logged ("era en 6
  cuotas") → `POST /rest/v1/rpc/set_installments` with
  `{"p_transaction_id": "<id>", "p_installments": 6}`; `1` removes the plan.
- Purchase with interest: `p_amount` is the total of the cuotas; put the list
  price in `p_notes`.

## Statement import workflow

Input: a bank/card statement (PDF, CSV, screenshot) + which payment method it is.

1. Parse every line: date, amount, merchant/description.
2. Compute `external_ref` per line: `<method-slug>:<YYYY-MM-DD>:<amount>:<merchant-slug>`
   (lowercase, kebab-case, e.g. `mybank-credito:2026-07-03:15230.50:carrefour`).
3. **Card statements only:** record the statement's cycle and the next one with
   `add_card_cycle` (`p_payment_method`, `p_closing_date`, `p_due_date`,
   `p_source="statement"`) — "cierre/vencimiento actual" and "próximo
   cierre/vencimiento". Re-recording a month replaces it; that is expected.
4. **Cuota lines** (`CUOTA k/n`, or `k/n` next to the merchant): the purchase
   total is `cuota amount × n`, its date is the original purchase date printed
   on the line, and its `external_ref` is built from the **purchase**
   (`<method-slug>:<original date>:<total>:<merchant-slug>`), so every month's
   cuota line maps to the same ref. The purchase usually predates the statement
   window, so look it up separately, by original date, any method or none:
   `GET /rest/v1/v_transactions?type=eq.outflow&occurred_on=gte.<date−1>&occurred_on=lte.<date+1>`
   and match by total (± n pesos for rounding).
   - Matched → if the row has no plan (`GET /rest/v1/installment_plans?transaction_id=eq.<id>`
     is empty), propose `set_installments` with `n`; if it has no method, also
     complete it as in step 9.
   - Unmatched and `k > 1` → the purchase was probably logged with another
     amount or date. Show it to the owner as "probably already logged" — do not
     propose it as new.
   - Unmatched and `k = 1` → propose it like any new line and insert it with
     `p_installments = n`.
5. Fetch what's already logged for that window — rows of this method **and**
   rows logged without a method (the owner often omits it):
   `GET /rest/v1/v_transactions?or=(payment_method.eq."<Method>",payment_method.is.null)&occurred_on=gte.<from>&occurred_on=lte.<to>`
6. Match statement lines to existing rows by date (±1 day) and amount.
7. Show the user the unmatched lines with proposed categories, plus the lines
   that matched a row **without** a method, as "complete with <Method>"; wait
   for approval.
8. Insert approved unmatched lines via `add_transaction` with
   `p_source="statement"` and `p_external_ref` set. A duplicate `external_ref`
   error means it was already imported — skip it silently.
9. For each approved "complete" line, set the method and the line's ref on the
   existing row, so the next import matches it by ref instead of re-proposing it
   (`id` from `GET /rest/v1/payment_methods?name=eq.<Method>&select=id`):
   `PATCH /rest/v1/transactions?id=eq.<row id>&payment_method_id=is.null`
   body `{"payment_method_id": <id>, "external_ref": "<ref>"}`.
   The `is.null` filter makes the PATCH a no-op on a row that already got a method.

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
