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
