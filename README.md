# Personal Accounting System

Agent-agnostic accounting on Supabase. Spec:
`docs/superpowers/specs/2026-07-12-accounting-system-design.md`.
Agents: read `docs/agent-playbook.md` — it is the entire interface contract.

## Layout
- `supabase/migrations/` — numbered SQL, applied by `scripts/apply_migrations.py`
- `scripts/` — migrations applier, rate backfill, sheet migration, smoke tests
- `apps-script/` — Google Form bridge (pasted into Apps Script editor)

## Setup
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill in real values
python scripts/apply_migrations.py

## Tests
python -m pytest scripts/test_smoke.py -v
