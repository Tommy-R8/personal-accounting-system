"""Shared helpers: DB connection + Supabase REST access. Load .env from repo root."""
import os

import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
DB_URL = os.environ["SUPABASE_DB_URL"]

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def db_conn():
    return psycopg.connect(DB_URL)


def rpc(name, payload):
    """POST /rest/v1/rpc/<name>. Returns the raw response (callers assert status)."""
    return requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/{name}", json=payload, headers=HEADERS, timeout=30
    )


def rest_get(path, params=None):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{path}", params=params or {}, headers=HEADERS, timeout=30
    )
    r.raise_for_status()
    return r.json()


def rest_delete(path, params):
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/{path}", params=params, headers=HEADERS, timeout=30
    )
    r.raise_for_status()
