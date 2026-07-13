create table invoices (
  id              uuid primary key default gen_random_uuid(),
  issued_on       date not null,
  punto_venta     text not null default '0001',
  invoice_number  int not null,
  amount_ars      numeric(14,2) not null check (amount_ars > 0),
  status          text not null default 'issued'
                    check (status in ('draft','issued','voided')),
  cae             text,            -- ARCA-ready: null until WSFE integration
  cae_expires_on  date,
  notes           text,
  source          text not null,
  created_at      timestamptz not null default now(),
  unique (punto_venta, invoice_number)
);

create table conversions (
  id             uuid primary key default gen_random_uuid(),
  occurred_on    date not null,
  kind           text not null
                   check (kind in ('crypto_sale','cash_sale','bank_deposit')),
  ars_amount     numeric(14,2) not null check (ars_amount > 0),
  usd_amount     numeric(14,2) check (usd_amount > 0),
  rate_quoted    numeric,
  rate_effective numeric generated always as
                   (case when usd_amount is not null
                         then ars_amount / usd_amount end) stored,
  platform_id    int not null references payment_methods(id),
  notes          text,
  source         text not null,
  created_at     timestamptz not null default now(),
  check ((kind = 'bank_deposit') = (usd_amount is null))
);

create table monotributo_brackets (
  category    text not null check (category in
                ('A','B','C','D','E','F','G','H','I','J','K')),
  valid_from  date not null,
  annual_cap  numeric(14,2) not null check (annual_cap > 0),
  primary key (category, valid_from)
);

alter table invoices enable row level security;
alter table conversions enable row level security;
alter table monotributo_brackets enable row level security;

-- Verified caps from Tommy's Overview sheet (D–G, 2025 and 2026 scales).
-- A–C and H–K for the current scale are filled at Step 5 (USER CHECKPOINT).
insert into monotributo_brackets (category, valid_from, annual_cap) values
  ('D', '2025-01-01', 22934610.05), ('E', '2025-01-01', 26977793.60),
  ('F', '2025-01-01', 33809379.57), ('G', '2025-01-01', 40431835.35),
  ('D', '2026-01-01', 26212853.42), ('E', '2026-01-01', 30833964.37),
  ('F', '2026-01-01', 38642048.36), ('G', '2026-01-01', 46211109.37);

create or replace function add_invoice(
  p_issued_on date,
  p_invoice_number int,
  p_amount_ars numeric,
  p_source text,
  p_punto_venta text default '0001',
  p_status text default 'issued',
  p_notes text default null
) returns invoices
language plpgsql as $$
declare
  v_row invoices%rowtype;
begin
  if exists (select 1 from invoices
              where punto_venta = p_punto_venta
                and invoice_number = p_invoice_number) then
    raise exception 'invoice %-% already exists',
      p_punto_venta, lpad(p_invoice_number::text, 8, '0');
  end if;
  insert into invoices (issued_on, punto_venta, invoice_number, amount_ars,
                        status, notes, source)
  values (p_issued_on, p_punto_venta, p_invoice_number, p_amount_ars,
          p_status, p_notes, p_source)
  returning * into v_row;
  return v_row;
end $$;

create or replace function add_conversion(
  p_occurred_on date,
  p_kind text,
  p_ars_amount numeric,
  p_platform text,
  p_source text,
  p_usd_amount numeric default null,
  p_rate_quoted numeric default null,
  p_notes text default null
) returns conversions
language plpgsql as $$
declare
  v_platform_id int;
  v_valid text;
  v_row conversions%rowtype;
begin
  if p_kind not in ('crypto_sale','cash_sale','bank_deposit') then
    raise exception 'unknown kind "%" — valid kinds: crypto_sale, cash_sale, bank_deposit', p_kind;
  end if;
  if p_kind in ('crypto_sale','cash_sale') and p_usd_amount is null then
    raise exception '% requires usd_amount (the USD/USDC sold)', p_kind;
  end if;
  if p_kind = 'bank_deposit' and p_usd_amount is not null then
    raise exception 'bank_deposit must not carry usd_amount (no USD leg)';
  end if;

  select id into v_platform_id
    from payment_methods
   where lower(name) = lower(trim(p_platform)) and active;
  if not found then
    select string_agg(name, ', ' order by id) into v_valid
      from payment_methods where active;
    raise exception 'unknown platform "%" — valid platforms: %', p_platform, v_valid;
  end if;

  insert into conversions (occurred_on, kind, ars_amount, usd_amount,
                           rate_quoted, platform_id, notes, source)
  values (p_occurred_on, p_kind, p_ars_amount, p_usd_amount,
          p_rate_quoted, v_platform_id, p_notes, p_source)
  returning * into v_row;
  return v_row;
end $$;

-- Replaces the sheet's ROUND(RANDBETWEEN(75000;200000);-3) workflow: a set of
-- invoice amounts covering a deposit target. Suggestion only — writes nothing.
-- Contract (spec §6.2/§13): sum = target rounded to nearest 1,000; every amount a
-- multiple of 1,000; for targets > p_max every amount is within [p_min, p_max]
-- (guaranteed by picking each amount from the range that keeps the remainder
-- coverable by the invoices still to come); target <= p_max returns one invoice.
create or replace function suggest_invoices(
  p_target_ars numeric,
  p_min numeric default 75000,
  p_max numeric default 200000
) returns numeric[]
language plpgsql volatile as $$
declare
  v_target numeric := round(p_target_ars / 1000) * 1000;
  v_n int;
  v_remaining numeric;
  v_amounts numeric[] := '{}';
  v_lo numeric;
  v_hi numeric;
  v_pick numeric;
begin
  if p_target_ars <= 0 then
    raise exception 'target must be positive';
  end if;
  if p_min <= 0 or p_max < p_min
     or p_min % 1000 <> 0 or p_max % 1000 <> 0 then
    raise exception 'need 0 < p_min <= p_max, both multiples of 1000';
  end if;
  if v_target <= p_max then
    return array[v_target];  -- may be below p_min: cannot invoice above the target
  end if;
  v_n := ceil(v_target / p_max);  -- fewest invoices that can carry the target
  if v_target / v_n < p_min then
    raise exception 'target % cannot be split into invoices within [%, %]',
      p_target_ars, p_min, p_max;
  end if;
  v_remaining := v_target;
  for i in reverse v_n .. 2 loop  -- after this pick, i-1 invoices remain
    v_lo := greatest(p_min, v_remaining - (i - 1) * p_max);
    v_hi := least(p_max, v_remaining - (i - 1) * p_min);
    v_pick := round((v_lo + random() * (v_hi - v_lo)) / 1000) * 1000;
    v_pick := least(greatest(v_pick, v_lo), v_hi);
    v_amounts := v_amounts || v_pick;
    v_remaining := v_remaining - v_pick;
  end loop;
  v_amounts := v_amounts || v_remaining;  -- in [p_min, p_max] by construction
  return v_amounts;
end $$;

revoke execute on function add_invoice(date, int, numeric, text, text, text, text)
  from public, anon, authenticated;
revoke execute on function add_conversion(
  date, text, numeric, text, text, numeric, numeric, text) from public, anon, authenticated;
revoke execute on function suggest_invoices(numeric, numeric, numeric)
  from public, anon, authenticated;

grant execute on function add_invoice(date, int, numeric, text, text, text, text)
  to service_role;
grant execute on function add_conversion(
  date, text, numeric, text, text, numeric, numeric, text) to service_role;
grant execute on function suggest_invoices(numeric, numeric, numeric) to service_role;
