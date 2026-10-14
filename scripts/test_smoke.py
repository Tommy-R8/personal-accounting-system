"""Smoke tests against the LIVE Supabase project. All test rows use source='test'
and are cleaned up by the session fixture. Run from repo root:
    python -m pytest scripts/test_smoke.py -v
"""
from datetime import date
import uuid

import psycopg
import pytest

from lib import db_conn, rest_delete, rest_get, rpc


def try_delete(path, params):
    """Teardown helper: tolerate 404s while tables don't exist yet (red phases)."""
    try:
        rest_delete(path, params)
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="session")
def cleanup():
    yield
    # children first: reimbursements reference their parent via FK
    try_delete("transactions", {"source": "eq.test", "parent_id": "not.is.null"})
    try_delete("transactions", {"source": "eq.test"})
    try_delete("invoices", {"source": "eq.test"})
    try_delete("conversions", {"source": "eq.test"})


# --- Task 2: schema + seeds -------------------------------------------------

def test_seed_categories():
    cats = rest_get("categories", {"select": "name,kind"})
    # Reintegros is load-bearing (reimbursement flow); owners may add more rows.
    assert {"name": "Salidas", "kind": "outflow"} in cats
    assert {"name": "Reintegros", "kind": "inflow"} in cats


def test_seed_payment_methods():
    # Only Cash ARS / Cash USD are seeded; owners add their own rows, so no exact count.
    pms = rest_get("payment_methods", {"select": "name"})
    assert {"name": "Cash ARS"} in pms
    assert {"name": "Cash USD"} in pms


def test_trigger_blocks_kind_mismatch_on_direct_insert():
    # Direct inserts (the migration path) must be validated too, not just the RPC.
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from categories where name = 'Sueldo'")
        inflow_cat = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                """insert into transactions
                     (occurred_on, type, description, category_id, amount,
                      currency, usd_rate, source)
                   values ('2026-07-06', 'outflow', 'x', %s, 1, 'ARS', 1000, 'test')""",
                (inflow_cat,),
            )


# --- Task 3: fx rates ---------------------------------------------------------

def test_backfill_populated_history():
    oldest = rest_get("fx_rates", {"select": "rate_date", "order": "rate_date.asc", "limit": "1"})
    assert oldest and oldest[0]["rate_date"] <= "2015-01-01"


def test_fetch_blue_rate_upserts_recent_row():
    from datetime import date
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select fetch_blue_rate()")
        cur.execute("select max(rate_date), max(blue_sell) from fx_rates")
        max_date, sell = cur.fetchone()
        conn.commit()
    assert (date.today() - max_date).days <= 4  # Fri rate on a long weekend at worst
    assert sell > 0


def test_daily_cron_job_registered():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("select schedule from cron.job where jobname = 'daily-blue-rate'")
        row = cur.fetchone()
    assert row is not None and row[0] == "30 13 * * *"


# --- Task 4: add_transaction RPC ----------------------------------------------

def add(**kw):
    """Call add_transaction with test defaults; individual kwargs override."""
    payload = {
        "p_occurred_on": "2026-07-06",
        "p_type": "outflow",
        "p_description": "smoke test row",
        "p_category": "Comida",
        "p_amount": 100,
        "p_source": "test",
    }
    payload.update(kw)
    return rpc("add_transaction", payload)


def test_add_ars_outflow_defaults():
    r = add(p_description="test asado", p_category="Salidas", p_amount=80000)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["currency"] == "ARS"
    assert row["fiscal"] is True  # Blanco default
    assert float(row["usd_rate"]) > 0
    assert abs(float(row["amount_usd"]) - 80000 / float(row["usd_rate"])) < 0.01


def test_usd_rate_is_one():
    r = add(p_type="inflow", p_category="Sueldo", p_amount=1000, p_currency="USD")
    assert r.status_code == 200, r.text
    row = r.json()
    assert float(row["usd_rate"]) == 1.0
    assert float(row["amount_usd"]) == 1000.0


def test_rate_snapshot_uses_latest_available_day():
    latest = rest_get(
        "fx_rates",
        {"select": "blue_sell", "rate_date": "lte.2026-07-06",
         "order": "rate_date.desc", "limit": "1"},
    )[0]
    r = add()
    assert r.status_code == 200, r.text
    assert float(r.json()["usd_rate"]) == float(latest["blue_sell"])


def test_names_resolve_case_insensitively():
    r = add(p_category="salidas", p_payment_method="cash ars")
    assert r.status_code == 200, r.text
    assert r.json()["category_id"] is not None


def test_unknown_category_lists_valid_ones():
    r = add(p_category="Comidaa")
    assert r.status_code == 400
    msg = r.json()["message"]
    assert "valid outflow categories" in msg and "Comida" in msg


def test_category_kind_mismatch_is_friendly():
    r = add(p_category="Sueldo")  # inflow category on an outflow
    assert r.status_code == 400
    assert "valid outflow categories" in r.json()["message"]


def test_unknown_payment_method_lists_valid_ones():
    r = add(p_payment_method="Visa Bank B")
    assert r.status_code == 400
    assert "valid payment methods" in r.json()["message"]


def test_reimbursement_links_to_outflow():
    dinner = add(p_category="Salidas", p_amount=80000).json()
    r = add(p_type="inflow", p_category="Reintegros", p_amount=20000,
            p_parent_id=dinner["id"])
    assert r.status_code == 200, r.text
    assert r.json()["parent_id"] == dinner["id"]


def test_outflow_cannot_have_parent():
    dinner = add(p_category="Salidas").json()
    r = add(p_parent_id=dinner["id"])
    assert r.status_code == 400
    assert "reimbursements" in r.json()["message"]


def test_reimbursement_cannot_point_at_inflow():
    salary = add(p_type="inflow", p_category="Sueldo").json()
    r = add(p_type="inflow", p_category="Reintegros", p_parent_id=salary["id"])
    assert r.status_code == 400
    assert "outflow" in r.json()["message"]


def test_external_ref_blocks_duplicates():
    import uuid
    ref = f"test:2026-07-06:100:{uuid.uuid4()}"
    r1 = add(p_external_ref=ref)
    assert r1.status_code == 200, r1.text
    r2 = add(p_external_ref=ref)
    assert r2.status_code == 400
    assert "already imported" in r2.json()["message"]


def test_invalid_type_is_rejected():
    r = add(p_type="expense")
    assert r.status_code == 400
    assert "inflow or outflow" in r.json()["message"]


# --- Task 5: views + run_report -------------------------------------------------

def test_group_netting_in_views():
    dinner = add(p_category="Salidas", p_amount=80000,
                 p_description="test group dinner").json()
    for _ in range(3):
        add(p_type="inflow", p_category="Reintegros", p_amount=20000,
            p_parent_id=dinner["id"])

    v = rest_get("v_transactions", {"id": f"eq.{dinner['id']}"})[0]
    rate = float(dinner["usd_rate"])
    # same-date reimbursements share the rate: net = (80000 - 60000) / rate
    assert abs(float(v["net_amount_usd"]) - 20000 / rate) < 0.05

    g = rest_get("v_group_expenses", {"id": f"eq.{dinner['id']}"})[0]
    assert g["reimbursement_count"] == 3
    assert abs(float(g["net_usd"]) - 20000 / rate) < 0.05


def test_summary_excludes_reimbursements_and_nets_parents():
    # via run_report so we can filter to source='test' (v_monthly_summary can't)
    r = rpc("run_report", {"p_sql": (
        "select round(sum(net_amount_usd)::numeric, 2) as total "
        "from v_transactions where source = 'test' "
        "and type = 'outflow' and parent_id is null"
    )})
    assert r.status_code == 200, r.text
    assert float(r.json()[0]["total"]) > 0


def test_monthly_summary_view_shape():
    rows = rest_get("v_monthly_summary", {"limit": "1"})
    assert set(rows[0].keys()) == {"month", "type", "category", "fiscal",
                                   "total_usd", "tx_count"}


def test_run_report_rejects_non_select():
    r = rpc("run_report", {"p_sql": "delete from transactions"})
    assert r.status_code == 400
    assert "SELECT" in r.json()["message"]


def test_run_report_rejects_multi_statement():
    r = rpc("run_report", {"p_sql": "select 1; select 2"})
    assert r.status_code == 400


# --- Task 6: fiscal schema + RPCs ---------------------------------------------

def add_invoice(**kw):
    payload = {
        "p_issued_on": "2026-07-06",
        "p_punto_venta": "9999",  # test PV — never collides with real 0001 numbers
        "p_invoice_number": kw.pop("n", 1),
        "p_amount_ars": 100000,
        "p_source": "test",
    }
    payload.update(kw)
    return rpc("add_invoice", payload)


def add_conversion(**kw):
    payload = {
        "p_occurred_on": "2026-07-06",
        "p_kind": "crypto_sale",
        "p_ars_amount": 150000,
        "p_usd_amount": 100,
        "p_platform": "Cash ARS",  # any active payment_methods row works as a platform
        "p_source": "test",
    }
    payload.update(kw)
    return rpc("add_conversion", payload)


def test_add_invoice_and_duplicate_rejected():
    r1 = add_invoice(n=901)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "issued"
    r2 = add_invoice(n=901)
    assert r2.status_code == 400
    assert "already exists" in r2.json()["message"]


def test_conversion_rate_effective_generated():
    r = add_conversion(p_ars_amount=150000, p_usd_amount=100)
    assert r.status_code == 200, r.text
    assert abs(float(r.json()["rate_effective"]) - 1500.0) < 0.01


def test_crypto_sale_requires_usd():
    r = add_conversion(p_usd_amount=None)
    assert r.status_code == 400
    assert "usd_amount" in r.json()["message"]


def test_bank_deposit_rejects_usd():
    r = add_conversion(p_kind="bank_deposit", p_usd_amount=100)
    assert r.status_code == 400
    assert "usd_amount" in r.json()["message"]


def test_bank_deposit_ok_without_usd():
    r = add_conversion(p_kind="bank_deposit", p_usd_amount=None, p_platform="Cash ARS")
    assert r.status_code == 200, r.text
    assert r.json()["rate_effective"] is None


def test_unknown_platform_lists_valid_ones():
    r = add_conversion(p_platform="Binance")
    assert r.status_code == 400
    assert "valid platforms" in r.json()["message"]


def test_brackets_seeded_current_scale():
    rows = rest_get("monotributo_brackets", {"select": "category,annual_cap",
                                             "valid_from": "eq.2026-01-01"})
    assert len(rows) == 11  # A..K
    d = next(r for r in rows if r["category"] == "D")
    assert abs(float(d["annual_cap"]) - 26212853.42) < 0.01


# --- Task 7: fiscal views -------------------------------------------------------

def _deposit_status():
    return {k: float(v) for k, v in rest_get("v_deposit_status")[0].items()}


def test_deposit_status_metrics_move_correctly():
    """Insert a known fixture and assert the five §5.9 metric deltas."""
    before = _deposit_status()
    add_invoice(n=902, p_amount_ars=300000)
    add_conversion(p_kind="crypto_sale", p_ars_amount=100000, p_usd_amount=70)
    add_conversion(p_kind="cash_sale", p_ars_amount=50000, p_usd_amount=35,
                   p_platform="Cash ARS")
    add_conversion(p_kind="bank_deposit", p_ars_amount=20000, p_usd_amount=None,
                   p_platform="Cash ARS")
    after = _deposit_status()
    assert after["total_facturado"] - before["total_facturado"] == 300000
    assert after["total_depositado"] - before["total_depositado"] == 120000  # crypto+deposit
    assert after["total_cobrado"] - before["total_cobrado"] == 150000        # crypto+cash
    assert after["restante_por_depositar"] - before["restante_por_depositar"] == 180000
    assert after["disponible_para_depositar"] - before["disponible_para_depositar"] == 30000


def test_monotributo_status_shape_and_math():
    rows = rest_get("v_monotributo_status")
    assert len(rows) == 11  # one per category of the current scale
    d = next(r for r in rows if r["category"] == "D")
    assert float(d["max_monthly_avg"]) == round(float(d["annual_cap"]) / 12, 2)
    assert isinstance(d["cap_exceeded"], bool)


def test_fiscal_balance_view_shape():
    rows = rest_get("v_fiscal_balance", {"order": "month.desc", "limit": "1"})
    assert set(rows[0].keys()) >= {"month", "invoiced_ars", "blanco_spend_ars",
                                   "invoiced_cum", "blanco_cum"}


def test_fiscal_balance_converts_usd_outflows_at_blue_sell():
    """A 100 USD Blanco outflow must add ~100*blue_sell ARS, not 100 ARS."""
    def blanco():
        rows = rest_get("v_fiscal_balance", {"month": "eq.2026-07-01"})
        return float(rows[0]["blanco_spend_ars"]) if rows else 0.0
    before = blanco()
    add(p_amount=100, p_currency="USD", p_occurred_on="2026-07-06")
    rate = float(rest_get("fx_rates", {"select": "blue_sell",
                                       "rate_date": "lte.2026-07-06",
                                       "order": "rate_date.desc",
                                       "limit": "1"})[0]["blue_sell"])
    assert abs(blanco() - before - 100 * rate) < 0.05


def test_monotributo_view_uses_latest_applicable_scale():
    d = next(r for r in rest_get("v_monotributo_status") if r["category"] == "D")
    assert float(d["annual_cap"]) == 26212853.42  # 2026 scale, not 2025's 22934610.05


def test_monotributo_view_ignores_future_scales():
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into monotributo_brackets values ('D', '2099-01-01', 1)")
        cur.execute("select annual_cap from v_monotributo_status where category = 'D'")
        cap = float(cur.fetchone()[0])
        conn.rollback()  # discard the future-dated row — no cleanup needed
    assert cap == 26212853.42  # a scale that hasn't taken effect must be ignored


# --- Task 9: invoice batch suggestion -----------------------------------------

from lib import rest_patch  # noqa: E402  (kept next to the tests that use it)

INVOICING_TEST_CFG = {
    "monthly_target_ars": 1000000,
    "batch_size": 5,
    "batch_weekday": 5,
    "round_to": 1000,
    "bands": [
        {"concept": "Asesoramiento Comercial", "min": 110000, "max": 200000,
         "count_min": 2, "count_max": 3},
        {"concept": "Servicios Varios", "min": 75000, "max": 120000,
         "count_min": 2, "count_max": 3},
    ],
}


def set_invoicing(cfg):
    rest_patch("settings", {"key": "eq.invoicing"}, {"value": cfg})


@pytest.fixture
def invoicing_cfg():
    """Swap in a known config for one test, restore the owner's row afterwards."""
    original = rest_get("settings", {"key": "eq.invoicing", "select": "value"})[0]["value"]
    set_invoicing(INVOICING_TEST_CFG)
    yield dict(INVOICING_TEST_CFG)
    set_invoicing(original)


def test_settings_seeded():
    rows = rest_get("settings", {"key": "eq.invoicing", "select": "value"})
    assert len(rows) == 1
    v = rows[0]["value"]
    assert {"monthly_target_ars", "batch_size", "batch_weekday", "round_to", "bands"} <= set(v)


def test_add_invoice_stores_concept():
    r = add_invoice(n=904, p_concept="Servicios Varios")
    assert r.status_code == 200, r.text
    assert r.json()["concept"] == "Servicios Varios"
    r = add_invoice(n=905)
    assert r.status_code == 200, r.text
    assert r.json()["concept"] is None


def suggest():
    r = rpc("suggest_invoice_batch", {})
    assert r.status_code == 200, r.text
    return r.json()


def band_of(cfg, amount, concept):
    (b,) = [b for b in cfg["bands"] if b["concept"] == concept]
    return b["min"] <= amount <= b["max"]


def set_target_per_batch(cfg, per_batch):
    """Point monthly_target so that this batch's target is exactly per_batch,
    whatever is already invoiced this month (real invoices count too)."""
    state = suggest()
    cfg["monthly_target_ars"] = float(state["invoiced_so_far"]) + state["batches_left"] * per_batch
    set_invoicing(cfg)
    return cfg


def test_batch_properties(invoicing_cfg):
    cfg = set_target_per_batch(invoicing_cfg, 600000)
    for _ in range(200):
        out = suggest()
        assert out["warning"] is None, out
        assert out["reason"] is None
        assert float(out["batch_target"]) == 600000
        inv = out["invoices"]
        assert len(inv) == cfg["batch_size"]
        amounts = [float(i["amount_ars"]) for i in inv]
        assert len(set(amounts)) == len(amounts), "repeated amount"
        for i in inv:
            a = float(i["amount_ars"])
            assert a % cfg["round_to"] == 0
            assert band_of(cfg, a, i["concept"]), i
        for b in cfg["bands"]:
            n = sum(1 for i in inv if i["concept"] == b["concept"])
            assert b["count_min"] <= n <= b["count_max"], inv
        assert abs(sum(amounts) - 600000) <= cfg["round_to"] * len(inv) / 2
        # ordered by band (settings order) then amount desc
        concepts = [i["concept"] for i in inv]
        order = [b["concept"] for b in cfg["bands"]]
        assert concepts == sorted(concepts, key=order.index)
        for b in cfg["bands"]:
            xs = [float(i["amount_ars"]) for i in inv if i["concept"] == b["concept"]]
            assert xs == sorted(xs, reverse=True)


def test_batch_self_corrects_and_avoids_used_amounts(invoicing_cfg):
    set_target_per_batch(invoicing_cfg, 600000)
    before = suggest()
    today = date.today().isoformat()
    r1 = add_invoice(n=951, p_issued_on=today, p_amount_ars=133000, p_concept="Asesoramiento Comercial")
    r2 = add_invoice(n=952, p_issued_on=today, p_amount_ars=91000, p_concept="Servicios Varios")
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    after = suggest()
    assert float(after["invoiced_so_far"]) == float(before["invoiced_so_far"]) + 224000
    # Python's banker's rounding agrees with Postgres here: 224000 / batches_left never yields an exact .5 thousand.
    expected_target = round((float(after["remaining"]) / after["batches_left"]) / 1000) * 1000
    assert float(after["batch_target"]) == expected_target
    for _ in range(50):
        amounts = {float(i["amount_ars"]) for i in suggest()["invoices"]}
        assert not amounts & {133000.0, 91000.0}


def test_batch_empty_when_target_reached(invoicing_cfg):
    cfg = set_target_per_batch(invoicing_cfg, 0)
    out = suggest()
    assert out["invoices"] == []
    assert out["reason"] == "monthly target reached"
    assert out["warning"] is None


def test_batch_shrinks_when_target_small(invoicing_cfg):
    # 300k fits 3 invoices at the 75k floor but not 5 (floor 445k) or 4 (370k).
    cfg = set_target_per_batch(invoicing_cfg, 300000)
    out = suggest()
    assert out["reason"] is None
    assert len(out["invoices"]) == 3
    amounts = [float(i["amount_ars"]) for i in out["invoices"]]
    assert abs(sum(amounts) - 300000) <= cfg["round_to"] * 3 / 2
    for i in out["invoices"]:
        assert band_of(cfg, float(i["amount_ars"]), i["concept"])


def test_batch_capped_when_target_huge(invoicing_cfg):
    # ceil for 5 with the test bands = 3*200k + 2*120k = 840k.
    set_target_per_batch(invoicing_cfg, 5000000)
    out = suggest()
    assert float(out["batch_target"]) == 840000
    assert "capped at 840000" in out["warning"]
    assert len(out["invoices"]) == 5


def test_batch_size_outside_band_envelope_is_rejected(invoicing_cfg):
    cfg = dict(invoicing_cfg, batch_size=10)
    set_invoicing(cfg)
    r = rpc("suggest_invoice_batch", {})
    assert r.status_code == 400
    assert "batch_size" in r.json()["message"]

# --- Task 10: cards + real cycles ---------------------------------------------

@pytest.fixture
def make_card():
    """Factory for throwaway credit cards. Each test gets its own card so
    cycles and purchases of one test never shift another test's calendar."""
    ids = []

    def _make(closing_day=20, due_day=5):
        name = f"Test Card {uuid.uuid4().hex[:8]}"
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """insert into payment_methods
                     (name, is_credit_card, default_closing_day, default_due_day)
                   values (%s, true, %s, %s) returning id""",
                (name, closing_day, due_day),
            )
            ids.append(cur.fetchone()[0])
        return name

    yield _make
    with db_conn() as conn, conn.cursor() as cur:
        for pm_id in ids:
            cur.execute("delete from transactions where payment_method_id = %s", (pm_id,))
            cur.execute("delete from card_cycles where payment_method_id = %s", (pm_id,))
            cur.execute("delete from payment_methods where id = %s", (pm_id,))


def add_cycle(card, closing, due):
    return rpc("add_card_cycle", {"p_payment_method": card, "p_closing_date": closing,
                                  "p_due_date": due, "p_source": "test"})


def test_card_flag_requires_default_days():
    # Estimated cycles need both days; a card without them would silently vanish
    # from every projection.
    with db_conn() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("""insert into payment_methods (name, is_credit_card)
                           values ('Test Card no days', true)""")
        conn.rollback()


def test_add_card_cycle_rejects_non_card():
    r = add_cycle("Cash ARS", "2027-01-20", "2027-02-05")
    assert r.status_code == 400
    assert "valid credit cards" in r.json()["message"]


def test_add_card_cycle_rejects_due_not_after_closing(make_card):
    r = add_cycle(make_card(), "2027-01-20", "2027-01-20")
    assert r.status_code == 400
    assert "must be after" in r.json()["message"]


def test_add_card_cycle_resolves_name_case_insensitively(make_card):
    # Agents type card names as the owner says them ("Bank A crédito").
    card = make_card()
    r = add_cycle(card.upper(), "2027-01-20", "2027-02-05")
    assert r.status_code == 200, r.text


def test_add_card_cycle_replaces_same_month(make_card):
    # A statement's "próximo cierre" is a forecast; the next statement's actual
    # date must replace it, not sit next to it.
    card = make_card()
    assert add_cycle(card, "2027-01-28", "2027-02-10").status_code == 200
    r = add_cycle(card, "2027-01-29", "2027-02-11")
    assert r.status_code == 200, r.text
    pm_id = r.json()["payment_method_id"]
    rows = rest_get("card_cycles", {"payment_method_id": f"eq.{pm_id}",
                                    "select": "closing_date,due_date"})
    assert rows == [{"closing_date": "2027-01-29", "due_date": "2027-02-11"}]


def test_add_card_cycle_keeps_other_months(make_card):
    card = make_card()
    add_cycle(card, "2027-01-28", "2027-02-10")
    r = add_cycle(card, "2027-02-25", "2027-03-10")
    pm_id = r.json()["payment_method_id"]
    rows = rest_get("card_cycles", {"payment_method_id": f"eq.{pm_id}", "select": "id"})
    assert len(rows) == 2

# --- Task 11: installment plans ---------------------------------------------

def plan_of(tx_id):
    return rest_get("installment_plans", {"transaction_id": f"eq.{tx_id}",
                                          "select": "installments"})


def test_installments_create_plan_on_one_transaction(make_card):
    r = add(p_amount=600000, p_payment_method=make_card(), p_installments=6)
    assert r.status_code == 200, r.text
    tx = r.json()
    assert plan_of(tx["id"]) == [{"installments": 6}]


def test_installment_purchase_is_full_amount_on_purchase_date(make_card):
    # Decision A: the purchase is the economic fact. The whole amount counts in
    # the purchase month at that day's rate — cuotas are cash, not spending.
    tx = add(p_amount=600000, p_payment_method=make_card(), p_installments=6).json()
    row = rest_get("v_transactions", {"id": f"eq.{tx['id']}"})[0]
    assert row["occurred_on"] == "2026-07-06"
    assert abs(float(row["net_amount_usd"]) - 600000 / float(row["usd_rate"])) < 0.01


def test_one_installment_stores_no_plan(make_card):
    tx = add(p_payment_method=make_card(), p_installments=1).json()
    assert plan_of(tx["id"]) == []


def test_add_transaction_without_installments_unchanged(make_card):
    # Hermes and the form never send p_installments; they must keep working.
    r = add(p_payment_method=make_card())
    assert r.status_code == 200, r.text
    assert plan_of(r.json()["id"]) == []


def test_installments_need_credit_card():
    r = add(p_payment_method="Cash ARS", p_installments=3)
    assert r.status_code == 400
    assert "valid credit cards" in r.json()["message"]


def test_installments_need_payment_method():
    r = add(p_installments=3)
    assert r.status_code == 400
    assert "valid credit cards" in r.json()["message"]


def test_installments_only_on_outflows(make_card):
    r = add(p_type="inflow", p_category="Reintegros",
            p_payment_method=make_card(), p_installments=3)
    assert r.status_code == 400
    assert "only applies to outflows" in r.json()["message"]


@pytest.mark.parametrize("n", [0, 61])
def test_installments_out_of_range(make_card, n):
    r = add(p_payment_method=make_card(), p_installments=n)
    assert r.status_code == 400
    assert "between 1 and 60" in r.json()["message"]
