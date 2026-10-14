-- Credit card installments, part 4: change the cuotas of an existing purchase.
-- Needed when the owner logged a card purchase without saying "en N cuotas"
-- and the statement later shows "CUOTA 1/6" (final review of the 011–013 work).

create or replace function set_installments(
  p_transaction_id uuid,
  p_installments int
) returns void
language plpgsql as $$
declare
  v_tx transactions%rowtype;
  v_valid text;
begin
  select * into v_tx from transactions where id = p_transaction_id;
  if not found then
    raise exception 'transaction % not found', p_transaction_id;
  end if;
  if p_installments is null or p_installments < 1 or p_installments > 60 then
    raise exception 'p_installments must be between 1 and 60, got %', p_installments;
  end if;
  if v_tx.type <> 'outflow'
     or not exists (select 1 from payment_methods
                     where id = v_tx.payment_method_id and is_credit_card) then
    select string_agg(name, ', ' order by id) into v_valid
      from payment_methods where active and is_credit_card;
    raise exception 'installments need a credit card outflow — valid credit cards: %',
      coalesce(v_valid, '(none flagged)');
  end if;

  if p_installments = 1 then
    delete from installment_plans where transaction_id = p_transaction_id;
  else
    insert into installment_plans (transaction_id, installments)
    values (p_transaction_id, p_installments)
    on conflict (transaction_id) do update set installments = excluded.installments;
  end if;
end $$;

revoke execute on function set_installments(uuid, int) from public, anon, authenticated;
grant execute on function set_installments(uuid, int) to service_role;
