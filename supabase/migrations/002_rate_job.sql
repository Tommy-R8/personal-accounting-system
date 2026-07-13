create or replace function fetch_blue_rate() returns void
language plpgsql as $$
declare
  v_body jsonb;
begin
  select content::jsonb into v_body
    from extensions.http_get('https://api.bluelytics.com.ar/v2/latest');
  insert into fx_rates (rate_date, blue_buy, blue_sell, blue_avg)
  values (
    ((v_body->>'last_update')::timestamptz
       at time zone 'America/Argentina/Buenos_Aires')::date,
    (v_body->'blue'->>'value_buy')::numeric,
    (v_body->'blue'->>'value_sell')::numeric,
    (v_body->'blue'->>'value_avg')::numeric
  )
  on conflict (rate_date) do update set
    blue_buy = excluded.blue_buy,
    blue_sell = excluded.blue_sell,
    blue_avg = excluded.blue_avg;
end $$;

revoke execute on function fetch_blue_rate() from public, anon, authenticated;
grant execute on function fetch_blue_rate() to service_role;

-- 13:30 UTC = 10:30 ART, after the day's Blue quotes are published
select cron.schedule('daily-blue-rate', '30 13 * * *', $$select fetch_blue_rate()$$);
