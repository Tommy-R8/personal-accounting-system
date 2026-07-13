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
  p_notes text default null
) returns transactions
language plpgsql as $$
declare
  v_cat categories%rowtype;
  v_pm_id int;
  v_rate numeric;
  v_valid text;
  v_row transactions%rowtype;
begin
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
  return v_row;
end $$;

revoke execute on function add_transaction(
  date, text, text, text, numeric, text, text, boolean, text[], text, uuid, text, text
) from public, anon, authenticated;
grant execute on function add_transaction(
  date, text, text, text, numeric, text, text, boolean, text[], text, uuid, text, text
) to service_role;
