# Configuration & Customization

What you can tweak, where it lives, and what depends on it. Two kinds of
knobs: **data** (rows in tables — change live with SQL, no redeploy) and
**code** (SQL functions — edit and reapply).

## Categories (data)

Rows in the `categories` table, seeded in `001_schema.sql`. Each has a `kind`
(`inflow` or `outflow`) and an `active` flag. `add_transaction` validates
against active categories case-insensitively and lists the valid ones in its
error message.

```sql
insert into categories (name, kind) values ('Mascotas', 'outflow');
update categories set active = false where name = 'Regalos';  -- hide, keep history
```

Never delete a category that has transactions — deactivate it instead.
Agents can always fetch the live list:
`GET /rest/v1/categories?active=eq.true&select=name,kind`.

## Payment methods & conversion platforms (data)

Rows in `payment_methods`, same pattern (`active` flag, live SQL). Only `Cash ARS`
and `Cash USD` are seeded — add your own banks / cards / wallets (commented
examples in `001_schema.sql`). One quirk: the table doubles as the
**conversion platform** list for the fiscal module — the banks and exchanges
you use in `add_conversion` also live here, but are not expense payment
methods; agents are told never to tag a transaction with a platform-only entry.

## Currencies (code — invasive)

`ARS` and `USD` are hard-wired: a check constraint on `transactions.currency`
(`001_schema.sql`) and rate resolution in `add_transaction` (`003`, where USD
is the base: `usd_rate = 1.0`, ARS looks up `fx_rates.blue_sell`). Adding a
third currency means: widen the check constraint, add a rate source +
column/table for it, extend the rate logic in `add_transaction`, and update
the playbook. Doable, but it touches everything
— the design assumes an ARS-earning, USD-thinking user.

## FX rate source & schedule (code)

`002_rate_job.sql` fetches the **Dólar Blue** rate from
[Bluelytics](https://api.bluelytics.com.ar) daily at 13:30 UTC (10:30 ART,
after the day's quotes publish) via `pg_cron`. Conversions use `blue_sell`.
To change source, rate side, or schedule, edit `fetch_blue_rate()` / the
`cron.schedule` line. If you're not in Argentina, replace the whole thing
with your currency's official rate API — everything downstream only reads the
`fx_rates` table. `scripts/backfill_rates.py` is the one-off history loader.

## Timezone

`America/Argentina/Buenos_Aires` is the "default to today" convention in
`docs/agent-playbook.md`.

## Fiscal module (Argentina-specific; ignore or rip out if N/A)

- **Monotributo brackets** (data): `monotributo_brackets` rows, seeded in
  `005`/`006` with the 2025/2026 ARCA scales. When ARCA publishes a new
  scale, insert new rows with the new `valid_from` — don't update old ones;
  `v_monotributo_status` picks the right scale by date.
- **Punto de venta** (code): invoice numbering defaults to `'0001'`
  (`add_invoice` in `005`).

Not in Argentina? The core ledger (transactions, categories, views) works
standalone — just never call `add_invoice`/`add_conversion`, or drop
migrations `005`–`007` before first apply.

## Agent playbook (the contract)

`docs/agent-playbook.md` is what agents actually read — it hardcodes the
seeded category/payment-method lists, the timezone, and the owner's habits.
After any change above, update the playbook to match; it also tells agents to
fetch live lists when in doubt.

## Reporting conventions (code)

Views live in `004` and `007`. Reports are USD-denominated
(`net_amount_usd`), reimbursements net into their parent outflow's month.
Custom one-off reports need no schema change: `run_report` accepts any single
read-only SELECT. Add a view only when a question recurs.

## Security posture (don't loosen casually)

Everything is locked to `service_role`: RLS enabled with **no policies** on
all tables, `security_invoker` views, execute revoked from
`public`/`anon`/`authenticated` on every function. There is deliberately no
anon-facing surface. If you add a function in a new migration, copy the
`revoke`/`grant` pattern from the existing ones — the default-privileges
revoke in `001` is not sufficient on its own (see the note there).
