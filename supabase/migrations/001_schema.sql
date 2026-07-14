-- Extensions (no-ops if already enabled from the dashboard)
create extension if not exists pg_cron;
create extension if not exists http with schema extensions;

-- NOTE: default-privileges revoke alone does NOT strip the built-in PUBLIC
-- execute grant on later functions (verified live) — every migration that
-- creates a function must also explicitly:
--   revoke execute on function <fn>(<args>) from public, anon, authenticated;
alter default privileges in schema public
  revoke execute on functions from public, anon, authenticated;

create table categories (
  id     serial primary key,
  name   text not null unique,
  kind   text not null check (kind in ('inflow','outflow')),
  active boolean not null default true
);

create table payment_methods (
  id     serial primary key,
  name   text not null unique,
  active boolean not null default true
);

create table fx_rates (
  rate_date  date primary key,
  blue_buy   numeric not null,
  blue_sell  numeric not null,
  blue_avg   numeric not null
);

create table transactions (
  id                uuid primary key default gen_random_uuid(),
  occurred_on       date not null,
  type              text not null check (type in ('inflow','outflow')),
  description       text not null,
  category_id       int not null references categories(id),
  amount            numeric(14,2) not null check (amount > 0),
  currency          text not null check (currency in ('ARS','USD')),
  fiscal            boolean not null default true,   -- Blanco by default
  tags              text[] not null default '{}',
  payment_method_id int references payment_methods(id),
  parent_id         uuid references transactions(id),
  usd_rate          numeric not null check (usd_rate > 0),
  amount_usd        numeric generated always as (amount / usd_rate) stored,
  external_ref      text,
  notes             text,
  source            text not null,
  created_at        timestamptz not null default now()
);

create unique index transactions_external_ref_key
  on transactions (external_ref) where external_ref is not null;

create or replace function trg_validate_transaction() returns trigger
language plpgsql as $$
declare
  v_kind text;
  v_parent_type text;
begin
  select kind into v_kind from categories where id = new.category_id;
  if v_kind is distinct from new.type then
    raise exception 'category kind (%) does not match transaction type (%)',
      v_kind, new.type;
  end if;
  if new.parent_id is not null then
    if new.type <> 'inflow' then
      raise exception 'only inflows (reimbursements) can have a parent_id';
    end if;
    select type into v_parent_type from transactions where id = new.parent_id;
    if v_parent_type is null then
      raise exception 'parent transaction % not found', new.parent_id;
    end if;
    if v_parent_type <> 'outflow' then
      raise exception 'parent of a reimbursement must be an outflow';
    end if;
  end if;
  return new;
end $$;

-- Residual risk: the trigger validates the NEW row only — an UPDATE flipping a
-- parent outflow's type to inflow after children exist is not caught
-- (unreachable via the planned RPCs, only via direct service_role SQL).
create trigger validate_transaction
  before insert or update on transactions
  for each row execute function trg_validate_transaction();

revoke execute on function trg_validate_transaction() from public, anon, authenticated;

-- RLS: no policies. anon/authenticated are blocked; service_role bypasses RLS.
alter table categories enable row level security;
alter table payment_methods enable row level security;
alter table fx_rates enable row level security;
alter table transactions enable row level security;
alter table _migrations enable row level security;

insert into categories (name, kind) values
  ('Comida', 'outflow'), ('Salidas', 'outflow'), ('Transporte', 'outflow'),
  ('Compras', 'outflow'), ('Regalos', 'outflow'), ('Suscripciones', 'outflow'),
  ('Impuestos', 'outflow'), ('Hogar', 'outflow'), ('Servicios', 'outflow'),
  ('Salud', 'outflow'), ('Viajes', 'outflow'), ('Otros gastos', 'outflow'),
  ('Sueldo', 'inflow'), ('Freelance', 'inflow'), ('Reintegros', 'inflow'),
  ('Regalos recibidos', 'inflow'), ('Rendimientos', 'inflow'),
  ('Otros ingresos', 'inflow');

-- Payment methods are personal — only the two universal ones are seeded.
-- Uncomment/adapt the examples below, or insert rows later; agents fetch the
-- live list. NOTE: this table doubles as the conversion-platform list for the
-- fiscal module (banks and exchanges used in add_conversion go here too, but
-- should never be tagged on expenses — see docs/configuration.md).
insert into payment_methods (name) values
  ('Cash'), ('USD Cash');
-- , ('MyBank Débito'), ('MyBank Crédito')     -- bank cards
-- , ('MercadoPago'), ('PayPal')               -- fintech wallets
-- , ('MyBank'), ('MyExchange')                -- conversion platforms (fiscal module)
