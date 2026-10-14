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
