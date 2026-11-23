# ACCEPTANCE — Stage 1 Definition of Done

> Format: `Criterion | PASS / FAIL / NOT RUN / NOT APPLICABLE | Command or proof | Limitation`
> `NOT APPLICABLE` is allowed only for genuinely inapplicable checks
> (e.g. `RATE_LIMITED` when no REST adapter is in agreed scope).
> Linux sandbox results and Windows target checks are reported separately.
> Windows-specific checks are NEVER marked `PASS` without execution on Windows.

## A. Automatic deterministic checks (no external network — mandatory gates)

| # | Criterion | Status (Linux sandbox) | Status (Windows target `win32` Python `3.11.9`) | Command / proof | Limitation |
|---|---|---|---|---|---|
| A1 | Read-only audit: route list, adapter method allowlist, static search for forbidden trading calls, config tests | `PASS` (7/7 tests) | `PASS` (7/7 tests) | `pytest backend/tests/test_readonly_audit.py -v` | Static audit reduces risk, not absolute proof; adapter allowlist expands in 1.2/1.3 |
| A2 | Contracts & candles: `Instrument`/`Quote`/`Candle`/`FeedHealth` validation, `[open, close)` UTC, nullable, `supported_timeframes`, PK, revisions, `CONFIRMED_GAP` vs `POSSIBLE_GAP` without interpolation | `PASS` (27/27 tests) | `PASS` (27/27 tests) | `pytest backend/tests/test_contracts.py -v` | — |
| A3 | Reconnect/recovery/WS queue on fake server (break, reconnect, history reload without dupes, gap preservation, overflow resync) | `NOT RUN` — Step 1.2 | `NOT RUN` — Step 1.2 | — | Not in Step 1.1 scope |
| A4 | Indicators: independent reference vectors (warm-up, edges, reset after `CONFIRMED_GAP`), batch vs incremental within tolerance | `NOT RUN` — Step 1.2 | `NOT RUN` — Step 1.2 | — | Formulas fixed in SPEC §7; code in Step 1.2 |
| A5 | Storage: Parquet write failure, failure **between** Parquet publication and SQLite commit + reconciliation, revision dedup, SQLite migrations, backup/restore | `PASS` (14/14 tests) | `PASS` SQLite migrations (3/3) & backup/restore (4/4); Parquet `os.fsync` Win32 fix applied (re-verify 4/7 tests on Windows) | `pytest backend/tests/test_parquet_reconcile.py backend/tests/test_backup_restore.py backend/tests/test_migrations.py -v` | Win32 `FlushFileBuffers` requires `"wb"` handle (fixed in `parquet_store.py`; awaiting Windows re-run) |
| A6 | API & Launcher security: CSRF/Origin/auth, same-origin GET without `Origin`, CSRF refresh on reload/tabs, bootstrap one-time/expiry (incl. first-launch recovery), safe GUI reopen, unsafe ACL refusal, WS handshake, no secrets in logs/errors/static | `NOT RUN` — Step 1.3 | `NOT RUN` — Step 1.3 | — | Not in Step 1.1 scope |
| A7 | Feed states: `CONNECTED`, `DEGRADED`, `STALE`, `MARKET_CLOSED`, `OFFLINE`, plus `RATE_LIMITED` if REST adapter in scope | `NOT RUN` — Steps 1.2–1.3 | `NOT RUN` — Steps 1.2–1.3 | — | REST reserve not in scope (MT5 chosen); `RATE_LIMITED` `NOT APPLICABLE` |
| A8 | GUI tests: instrument/timeframe switching, history load, `GAP`/`N/A`/feed statuses, `PREVIEW/UNCONFIRMED`, `SIMULATED` marking | `NOT RUN` — Step 1.4 | `NOT RUN` — Step 1.4 | — | Not in Step 1.1 scope |

## B. Checks on the user's machine (separate from CI; without them Stage 1 is not fully accepted)

| # | Criterion | Status | Command / proof | Limitation |
|---|---|---|---|---|
| B1 | `preflight_check`: per-source report (success/error/disabled), DNS/TLS, REST, WS, MT5 (if included), clock comparison (best effort, no NTP masking) | Partial `PASS` on Windows target (`config`=`OK`, `storage`=`OK`, `clock`=`OK` 3ms drift, `okx-spot` DNS+TCP=`OK`; REST/WS live & `mt5-demo`=`NOT_RUN`) | `.venv\Scripts\python.exe -m backend.app.tools.preflight_check` on Windows | Full REST/WS OKX & MT5 subprocess checks belong to Step 1.3 |
| B2 | Real OKX spot stream observation ≥ 30 min + forced network break and recovery check | `NOT RUN` | — | Requires Step 1.3 OKX adapter + target Windows execution |
| B3 | GUI stability on isolated synthetic stream (`SIMULATED`) 30 min with report: OS/browser/versions, instrument count, event rate, warm-up duration, instrument/interval of measurements; no unbounded growth of queues/subscriptions/DOM; memory growth > 50 MB in last 20 min after warm-up triggers investigation (not auto-fail/pass) | `NOT RUN` — Step 1.4 | — | Not in Step 1.1 scope |
| B4 | After clean restart: history, settings and correct source statuses preserved | `NOT RUN` | — | Requires full stack (Steps 1.2–1.4) |

## Step 1.1 gate status

### 1. Linux sandbox checks (`Debian GNU/Linux 12 x86_64`, Python `3.11.2`)

| Check | Result (Linux) | Command | Evidence |
|---|---|---|---|
| `test_contracts.py` (domain models, WS schemas, catalog, offline FastAPI, offline preflight) | `PASS` (27 passed) | `pytest backend/tests/test_contracts.py -v` | 27/27 passed in Linux sandbox |
| `test_migrations.py` (WAL, idempotent v1 schema, integrity_check) | `PASS` (3 passed) | `pytest backend/tests/test_migrations.py -v` | 3/3 passed in Linux sandbox |
| `test_backup_restore.py` (SQLite backup API without manual WAL checkpoint, restore, revision conflict/regression) | `PASS` (4 passed) | `pytest backend/tests/test_backup_restore.py -v` | 4/4 passed in Linux sandbox |
| `test_parquet_reconcile.py` (atomic write, unclosed rejection, crash between Parquet & SQLite + idempotent reconcile, hash mismatch reporting, orphan SQLite rows) | `PASS` (7 passed) | `pytest backend/tests/test_parquet_reconcile.py -v` | 7/7 passed in Linux sandbox |
| `test_readonly_audit.py` (no mutating routes, no trading keywords/calls/keys, loopback default, `.bat` lockfile check) | `PASS` (7 passed) | `pytest backend/tests/test_readonly_audit.py -v` | 7/7 passed in Linux sandbox |
| WS JSON Schema generation | `PASS` | `python -m backend.app.schemas.generate_ws_schemas` | Generated `snapshot.schema.json`, `update.schema.json`, `resync_request.schema.json` |
| Offline start without network | `PASS` | `python -m backend.app.tools.preflight_check --offline` & `TestClient(app)` | Starts offline; `/`, `/healthz` & `/api/v1/instruments` respond 200 |

### 2. Windows x86_64 target machine checks (`C:\Users\Thema\Desktop\TestBot-main`, Python `3.11.9`)

| Check | Result (Windows) | Evidence / Limitation |
|---|---|---|
| Lockfile install (`backend\requirements.lock`) & `.venv` creation on Windows Python 3.11.9 | `PASS` | Verified via `Start.bat` / `.venv\Scripts\python.exe` on user's Windows machine |
| Offline & local server start (`127.0.0.1:8765`, `GET /`, `GET /docs`, `GET /openapi.json`) | `PASS` | Uvicorn process `[13772]` started cleanly; HTTP 200 OK on `/`, `/docs`, `/openapi.json` |
| `preflight_check` (online mode): `config`, `storage` (NTFS writable), `clock`, `okx-spot` DNS+TCP | `PASS` (`config`=`OK`, `storage`=`OK`, `clock`=`OK` 3ms, `okx-spot`=`OK` dns+tcp reachable) | Executed on Windows (`2026-11-23T08:16:08Z`); REST/WS live stream & ToS check still required in Step 1.3 |
| `test_contracts.py` (27/27), `test_migrations.py` (3/3), `test_backup_restore.py` (4/4), `test_readonly_audit.py` (7/7) on Windows NTFS | `PASS` (41/41 passed) | Verified on `win32 -- Python 3.11.9, pytest-8.4.2` |
| `test_parquet_reconcile.py` on Windows NTFS | 4/7 `PASS` (including `test_failure_between_parquet_and_sqlite_then_reconcile` after Win32 `os.fsync` fix); remaining 3/7 failed with `ZoneInfoNotFoundError: 'No time zone found with key UTC'` (`No module named 'tzdata'`); fixed by pinning `tzdata==2026.6` in `backend/requirements.lock` — **re-run pending on Windows** | Install `tzdata==2026.6` and re-run `.venv\Scripts\python.exe -m pytest backend/tests/ -v` on Windows |
| MT5 terminal, suitable broker, and demo account availability (`EURUSD`, `USDJPY`, `XAUUSD`) | `NOT RUN` / `BLOCKED` | Reported as `[NOT_RUN] mt5-demo` by `preflight_check`; terminal/broker/demo account still unconfirmed |
