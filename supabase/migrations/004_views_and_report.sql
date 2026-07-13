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

-- The lateral reimbursement sum probes children by parent_id per parent row.
create index transactions_parent_id_idx
  on transactions (parent_id) where parent_id is not null;

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

revoke execute on function run_report(text) from public, anon, authenticated;
grant execute on function run_report(text) to service_role;
