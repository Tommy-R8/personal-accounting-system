-- Invoice batch suggestion, part 1: settings + concept
-- (spec: docs/superpowers/specs/2026-09-12-invoice-batch-suggestion-design.md)

-- Generic key/value config. Agents edit rows with PATCH /rest/v1/settings?key=eq.<key>
create table settings (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
alter table settings enable row level security;

-- monthly_target_ars is a placeholder: the owner sets it before the first run
-- (docs/configuration.md). batch_weekday is ISO (1 = Monday … 7 = Sunday).
insert into settings (key, value) values ('invoicing', '{
  "monthly_target_ars": 2200000,
  "batch_size": 5,
  "batch_weekday": 5,
  "round_to": 1000,
  "bands": [
    {"concept": "Asesoramiento Comercial", "min": 110000, "max": 200000,
     "count_min": 2, "count_max": 3},
    {"concept": "Servicios Varios",        "min":  75000, "max": 120000,
     "count_min": 2, "count_max": 3}
  ]
}');

-- Description printed on the factura. Null for rows recorded before this migration.
alter table invoices add column concept text;

-- Re-create add_invoice with p_concept. The old signature must go: PostgREST
-- cannot pick between two overloads when the call omits p_concept.
drop function add_invoice(date, int, numeric, text, text, text, text);

create or replace function add_invoice(
  p_issued_on date,
  p_invoice_number int,
  p_amount_ars numeric,
  p_source text,
  p_punto_venta text default '0001',
  p_status text default 'issued',
  p_notes text default null,
  p_concept text default null
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
                        status, notes, concept, source)
  values (p_issued_on, p_punto_venta, p_invoice_number, p_amount_ars,
          p_status, p_notes, p_concept, p_source)
  returning * into v_row;
  return v_row;
end $$;

revoke execute on function add_invoice(date, int, numeric, text, text, text, text, text)
  from public, anon, authenticated;
grant execute on function add_invoice(date, int, numeric, text, text, text, text, text)
  to service_role;
