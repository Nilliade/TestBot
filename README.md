# Trading Platform — Stage 1 (read-only prototype)

Local platform for crypto (OKX spot, public) + Forex/XAU (MT5 demo) market-data
analysis. **Stage 1 is strictly read-only**: no trading, no order endpoints,
no trading API keys. See `docs/SPEC-stage1.md`, `docs/THREAT-MODEL.md`,
`docs/ACCEPTANCE.md`.

## Environment (pinned)

- Python `3.11.2` (`requires-python ==3.11.*`, tested in Linux sandbox).
  Target is Windows x86_64 — Windows verification is `NOT RUN` until executed
  on the target.
- Node.js `22.22.3` (tested in sandbox; frontend is Step 1.4).
- Backend dependencies: `backend/requirements.lock` (exact pins).

## Reproducible install (single procedure, from lock file)

`pyproject.toml` declares version ranges; **`backend/requirements.lock` is the
single source of truth** for reproducible installs. There is no separate
`requirements.txt` — do not use one.

```bash
# 1. Python 3.11.x (exact patch 3.11.2 tested)
python --version  # must be 3.11.2

# 2. Create and activate a virtual environment
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

# 3. Install ONLY from the lock file (reproducible)
pip install -r backend/requirements.lock

# 4. Generate WS JSON Schemas (from Pydantic models)
python -m backend.app.schemas.generate_ws_schemas

# 5. Run Step 1.1 gates
pytest backend/tests/ -v

# 6. Offline start check (no network required)
python -m backend.app.tools.preflight_check --offline
python -c "from backend.app.main import app; print([r.path for r in app.routes])"
```

To regenerate the lock file after changing `pyproject.toml` (maintainers only):

```bash
pip install -e ".[test]"
pip freeze > backend/requirements.lock
```

## Offline behavior

The app starts without network. `preflight_check --offline` reports network
sources as `NOT RUN` (never faked). Live OKX/MT5 verification requires the
Windows target machine and is `NOT RUN` in the sandbox.

## Repository layout

```text
backend/app/    FastAPI, config, domain, schemas, storage, tools
backend/tests/  Step 1.1 gates (contracts, migrations, backup/restore, reconcile, audit)
docs/           SPEC-stage1.md, THREAT-MODEL.md, ACCEPTANCE.md, NOTICE
frontend/       NOT IMPLEMENTED until Step 1.4
launcher/       NOT IMPLEMENTED until Step 1.3
data/           gitignored: sqlite, parquet, logs, backups
```
