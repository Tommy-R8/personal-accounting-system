-- Credit card installments, part 1: flag cards + real cycle dates
-- (spec: docs/superpowers/specs/2026-10-13-card-installments-design.md §5.1–5.2)

-- Default days are only the fallback for months with no real cycle.
alter table payment_methods
  add column is_credit_card      boolean not null default false,
  add column default_closing_day int check (default_closing_day between 1 and 31),
  add column default_due_day     int check (default_due_day between 1 and 31),
  add constraint payment_methods_card_days_check
    check (not is_credit_card
           or (default_closing_day is not null and default_due_day is not null));

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

-- One real cycle per card per calendar month: a later statement's actual date
-- replaces the "próximo cierre" forecast recorded from the previous one.
create or replace function add_card_cycle(
  p_payment_method text,
  p_closing_date date,
  p_due_date date,
  p_source text
) returns card_cycles
language plpgsql as $$
declare
  v_pm_id int;
  v_valid text;
  v_row card_cycles%rowtype;
begin
  select id into v_pm_id
    from payment_methods
   where lower(name) = lower(trim(p_payment_method)) and active and is_credit_card;
  if not found then
    select string_agg(name, ', ' order by id) into v_valid
      from payment_methods where active and is_credit_card;
    raise exception 'unknown credit card "%" — valid credit cards: %',
      p_payment_method, coalesce(v_valid, '(none flagged)');
  end if;
  if p_due_date <= p_closing_date then
    raise exception 'due_date (%) must be after closing_date (%)',
      p_due_date, p_closing_date;
  end if;

  delete from card_cycles
   where payment_method_id = v_pm_id
     and date_trunc('month', closing_date) = date_trunc('month', p_closing_date);
  insert into card_cycles (payment_method_id, closing_date, due_date, source)
  values (v_pm_id, p_closing_date, p_due_date, p_source)
  returning * into v_row;
  return v_row;
end $$;

revoke execute on function add_card_cycle(text, date, date, text)
  from public, anon, authenticated;
grant execute on function add_card_cycle(text, date, date, text) to service_role;
