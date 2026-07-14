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

-- Verified caps from the legacy Overview sheet (D–G, 2025 and 2026 scales).
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

revoke execute on function add_invoice(date, int, numeric, text, text, text, text)
  from public, anon, authenticated;
revoke execute on function add_conversion(
  date, text, numeric, text, text, numeric, numeric, text) from public, anon, authenticated;

grant execute on function add_invoice(date, int, numeric, text, text, text, text)
  to service_role;
grant execute on function add_conversion(
  date, text, numeric, text, text, numeric, numeric, text) to service_role;
