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
