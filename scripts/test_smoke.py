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


# --- Task 2: schema + seeds -------------------------------------------------

def test_seed_categories():
    cats = rest_get("categories", {"select": "name,kind"})
    assert len(cats) == 18
    assert {"name": "Salidas", "kind": "outflow"} in cats
    assert {"name": "Reintegros", "kind": "inflow"} in cats
    assert not any(c["name"] == "Educación" for c in cats)


def test_seed_payment_methods():
    pms = rest_get("payment_methods", {"select": "name"})
    assert len(pms) == 15  # 13 payment methods + BBVA & Buenbit (conversion platforms)
    assert {"name": "Gnosis Pay"} in pms
    assert {"name": "BBVA"} in pms


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
    r = add(p_category="salidas", p_payment_method="lemon")
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
    r = add(p_payment_method="Visa Galicia")
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
