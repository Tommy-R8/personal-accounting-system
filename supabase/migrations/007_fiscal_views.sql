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
