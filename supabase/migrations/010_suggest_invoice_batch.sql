-- Invoice batch suggestion, part 2: the RPC (needs 009: settings + invoices.concept)

-- Lowest (p_upper = false) or highest (p_upper = true) sum that p_n invoices can
-- reach: mandatory count_min slots first (skipped when p_relaxed), then the free
-- slots greedily from the cheapest / priciest band up to each band's count_max.
create or replace function _batch_bound(
  p_bands jsonb, p_n int, p_relaxed boolean, p_upper boolean
) returns numeric
language plpgsql immutable as $$
declare
  v_total numeric := 0;
  v_slots int := p_n;
  v_cmin  int;
  v_cap   int;
  v_take  int;
  b       jsonb;
begin
  if not p_relaxed then
    for b in select e from jsonb_array_elements(p_bands) e loop
      v_cmin  := (b->>'count_min')::int;
      v_total := v_total + v_cmin *
                 (case when p_upper then b->>'max' else b->>'min' end)::numeric;
      v_slots := v_slots - v_cmin;
    end loop;
  end if;
  for b in select e from jsonb_array_elements(p_bands) e
            order by case when p_upper then -(e->>'max')::numeric
                          else (e->>'min')::numeric end loop
    exit when v_slots <= 0;
    v_cap  := (b->>'count_max')::int
              - case when p_relaxed then 0 else (b->>'count_min')::int end;
    v_take := least(v_cap, v_slots);
    v_total := v_total + v_take *
               (case when p_upper then b->>'max' else b->>'min' end)::numeric;
    v_slots := v_slots - v_take;
  end loop;
  return v_total;
end $$;

-- Weekly batch of invoice amounts. Reads settings.invoicing + this month's issued
-- invoices; writes nothing. Sizing and drawing rules: spec §5.3.
create or replace function suggest_invoice_batch()
returns jsonb
language plpgsql volatile as $$
declare
  s              jsonb;
  v_target       numeric;
  v_batch_size   int;
  v_weekday      int;
  v_round        numeric;
  v_bands        jsonb;
  v_nb           int;
  v_today        date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
  v_month_start  date;
  v_month_end    date;
  v_invoiced     numeric;
  v_used         numeric[];
  v_batches_left int;
  v_remaining    numeric;
  v_batch_target numeric;
  v_n            int;
  v_sum_cmin     int := 0;
  v_sum_cmax     int := 0;
  v_relaxed      boolean;
  v_floor        numeric;
  v_ceil         numeric;
  v_warning      text := null;
  v_counts       int[];
  v_amounts      numeric[];
  v_band_idx     int[];
  v_best_amounts numeric[];
  v_best_idx     int[];
  v_best_diff    numeric := null;
  v_ok           boolean;
  v_sum          numeric;
  v_pick         numeric;
  v_cmin         int;
  v_cmax         int;
  v_min          numeric;
  v_max          numeric;
  v_tries        int;
  v_out          jsonb;
begin
  -- 1. settings
  select value into s from settings where key = 'invoicing';
  if not found then
    raise exception 'settings row "invoicing" not found';
  end if;
  v_target     := (s->>'monthly_target_ars')::numeric;
  v_batch_size := (s->>'batch_size')::int;
  v_weekday    := (s->>'batch_weekday')::int;
  v_round      := (s->>'round_to')::numeric;
  v_bands      := s->'bands';
  if v_target is null or v_batch_size is null or v_weekday is null or v_round is null
     or v_bands is null or jsonb_typeof(v_bands) <> 'array' or jsonb_array_length(v_bands) = 0 then
    raise exception 'settings.invoicing needs monthly_target_ars, batch_size, batch_weekday, round_to and a non-empty bands array';
  end if;
  if v_weekday not between 1 and 7 or v_round <= 0 or v_batch_size < 1 then
    raise exception 'settings.invoicing: batch_weekday must be 1-7 (ISO), round_to > 0, batch_size >= 1';
  end if;
  v_nb := jsonb_array_length(v_bands);
  for i in 0 .. v_nb - 1 loop
    if (v_bands->i->>'concept') is null or (v_bands->i->>'min') is null
       or (v_bands->i->>'max') is null or (v_bands->i->>'count_min') is null
       or (v_bands->i->>'count_max') is null then
      raise exception 'settings.invoicing.bands[%] needs concept, min, max, count_min, count_max', i;
    end if;
    if (v_bands->i->>'min')::numeric < 0
       or (v_bands->i->>'min')::numeric > (v_bands->i->>'max')::numeric
       or (v_bands->i->>'count_min')::int < 0
       or (v_bands->i->>'count_min')::int > (v_bands->i->>'count_max')::int then
      raise exception 'settings.invoicing.bands[%]: need 0 <= min <= max and 0 <= count_min <= count_max', i;
    end if;
    v_sum_cmin := v_sum_cmin + (v_bands->i->>'count_min')::int;
    v_sum_cmax := v_sum_cmax + (v_bands->i->>'count_max')::int;
  end loop;
  if v_batch_size < v_sum_cmin or v_batch_size > v_sum_cmax then
    raise exception 'batch_size % must be between the sum of bands.count_min (%) and the sum of bands.count_max (%)',
      v_batch_size, v_sum_cmin, v_sum_cmax;
  end if;

  -- 2. month state
  v_month_start := date_trunc('month', v_today)::date;
  v_month_end   := (v_month_start + interval '1 month' - interval '1 day')::date;
  select coalesce(sum(amount_ars), 0),
         coalesce(array_agg(distinct amount_ars), '{}')
    into v_invoiced, v_used
    from invoices
   where status = 'issued'
     and issued_on between v_month_start and v_month_end;
  select count(*) into v_batches_left
    from generate_series(v_today, v_month_end, interval '1 day') d
   where extract(isodow from d) = v_weekday;
  v_batches_left := greatest(v_batches_left, 1);
  v_remaining    := v_target - v_invoiced;
  v_batch_target := round(v_remaining / v_batches_left / v_round) * v_round;

  -- 3. sizing: shrink n until the batch can be that small
  v_n := v_batch_size;
  loop
    v_relaxed := v_n < v_sum_cmin;
    v_floor   := _batch_bound(v_bands, v_n, v_relaxed, false);
    exit when v_batch_target >= v_floor or v_n = 1;
    v_n := v_n - 1;
  end loop;
  if v_batch_target < v_floor then
    return jsonb_build_object(
      'month', to_char(v_today, 'YYYY-MM'),
      'monthly_target_ars', v_target,
      'invoiced_so_far', v_invoiced,
      'remaining', v_remaining,
      'batches_left', v_batches_left,
      'batch_target', v_batch_target,
      'warning', null,
      'reason', 'monthly target reached',
      'invoices', '[]'::jsonb);
  end if;
  v_ceil := _batch_bound(v_bands, v_n, v_relaxed, true);
  if v_batch_target > v_ceil then
    v_warning := format('batch capped at %s; monthly target cannot be reached at this cadence', v_ceil);
    v_batch_target := v_ceil;
  end if;

  -- 4. drawing: rejection sampling, keep the closest acceptable set as fallback
  for attempt in 1 .. 500 loop
    -- per-band counts summing to v_n
    v_tries := 0;
    loop
      v_counts := '{}';
      for i in 0 .. v_nb - 1 loop
        v_cmin := case when v_relaxed then 0 else (v_bands->i->>'count_min')::int end;
        v_cmax := (v_bands->i->>'count_max')::int;
        v_counts := v_counts || (v_cmin + floor(random() * (v_cmax - v_cmin + 1)))::int;
      end loop;
      exit when (select sum(c) from unnest(v_counts) c) = v_n;
      v_tries := v_tries + 1;
      if v_tries > 10000 then
        raise exception 'could not pick per-band counts summing to % — check bands.count_min/count_max', v_n;
      end if;
    end loop;

    -- amounts
    v_amounts  := '{}';
    v_band_idx := '{}';
    v_ok := true;
    for i in 0 .. v_nb - 1 loop
      v_min := (v_bands->i->>'min')::numeric;
      v_max := (v_bands->i->>'max')::numeric;
      for j in 1 .. v_counts[i + 1] loop
        v_pick := round((v_min + random() * (v_max - v_min)) / v_round) * v_round;
        v_pick := least(greatest(v_pick, v_min), v_max);
        if v_pick = any(v_amounts) or v_pick = any(v_used) then
          v_ok := false;
        end if;
        v_amounts  := v_amounts || v_pick;
        v_band_idx := v_band_idx || i;
      end loop;
    end loop;
    continue when not v_ok;

    v_sum := (select sum(a) from unnest(v_amounts) a);
    if v_best_diff is null or abs(v_sum - v_batch_target) < v_best_diff then
      v_best_diff    := abs(v_sum - v_batch_target);
      v_best_amounts := v_amounts;
      v_best_idx     := v_band_idx;
    end if;
    exit when v_best_diff <= v_round * v_n / 2;
  end loop;

  if v_best_amounts is null then
    raise exception 'could not draw % distinct amounts not already used this month — widen the bands', v_n;
  end if;
  if v_best_diff > v_round * v_n / 2 then
    v_warning := concat_ws('; ', v_warning, 'tolerance not met after 500 draws');
  end if;

  -- 5. output: band order (as in settings), then amount desc
  select jsonb_agg(jsonb_build_object('concept', v_bands->bi->>'concept', 'amount_ars', a)
                   order by bi, a desc)
    into v_out
    from unnest(v_best_amounts, v_best_idx) as t(a, bi);

  return jsonb_build_object(
    'month', to_char(v_today, 'YYYY-MM'),
    'monthly_target_ars', v_target,
    'invoiced_so_far', v_invoiced,
    'remaining', v_remaining,
    'batches_left', v_batches_left,
    'batch_target', v_batch_target,
    'warning', v_warning,
    'reason', null,
    'invoices', v_out);
end $$;

revoke execute on function _batch_bound(jsonb, int, boolean, boolean)
  from public, anon, authenticated;
grant execute on function _batch_bound(jsonb, int, boolean, boolean) to service_role;
revoke execute on function suggest_invoice_batch() from public, anon, authenticated;
grant execute on function suggest_invoice_batch() to service_role;
