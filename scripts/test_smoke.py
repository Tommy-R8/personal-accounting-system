"""Smoke tests against the LIVE Supabase project. All test rows use source='test'
and are cleaned up by the session fixture. Run from repo root:
    python -m pytest scripts/test_smoke.py -v
"""
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
    assert len(cats) == 18
    assert {"name": "Salidas", "kind": "outflow"} in cats
    assert {"name": "Reintegros", "kind": "inflow"} in cats
    assert not any(c["name"] == "Educación" for c in cats)


def test_seed_payment_methods():
    # Only Cash / USD Cash are seeded; owners add their own rows, so no exact count.
    pms = rest_get("payment_methods", {"select": "name"})
    assert {"name": "Cash"} in pms
    assert {"name": "USD Cash"} in pms


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
    r = add(p_category="salidas", p_payment_method="cash")
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
        "p_platform": "Cash",  # any active payment_methods row works as a platform
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
    r = add_conversion(p_kind="bank_deposit", p_usd_amount=None, p_platform="Cash")
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
                   p_platform="Cash")
    add_conversion(p_kind="bank_deposit", p_ars_amount=20000, p_usd_amount=None,
                   p_platform="Cash")
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
