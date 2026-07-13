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
