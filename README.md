# Personal Accounting System

Agent-agnostic accounting on Supabase: the entire "app" is a Postgres schema
plus its REST API — any LLM agent (or a Google Form) is the frontend.

- **First-time setup:** `docs/setup.md`
- **What you can change** (categories, currencies, FX source, fiscal module...):
  `docs/configuration.md`
- **Agents:** read `docs/agent-playbook.md` — it is the entire interface contract.

Comments referencing "spec §…" point to the original design document, which is
kept out of the repo because it contains the owner's personal financial details.

## Layout
- `supabase/migrations/` — numbered SQL, applied by `scripts/apply_migrations.py`
- `scripts/` — migrations applier, rate backfill, one-time sheet migration, smoke tests
- `apps-script/` — Google Form bridge (pasted into Apps Script editor)

## Quick start
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill in real values (see docs/setup.md)
python scripts/apply_migrations.py
python scripts/backfill_rates.py
```

## Tests
```powershell
python -m pytest scripts/test_smoke.py -v
```
