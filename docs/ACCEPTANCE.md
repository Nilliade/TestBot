# ACCEPTANCE — Stage 1 Definition of Done

> Format: `Criterion | PASS / FAIL / NOT RUN / NOT APPLICABLE | Command or proof | Limitation`
> `NOT APPLICABLE` is allowed only for genuinely inapplicable checks
> (e.g. `RATE_LIMITED` when no REST adapter is in agreed scope).
> Linux sandbox results and Windows target checks are reported separately.
> Windows-specific checks are NEVER marked `PASS` without execution on Windows.

## A. Automatic deterministic checks (no external network — mandatory gates)

| # | Criterion | Status (Linux sandbox) | Status (Windows target) | Command / proof | Limitation |
|---|---|---|---|---|---|
| A1 | Read-only audit: route list, adapter method allowlist, static search for forbidden trading calls, config tests | `PASS` (6/6 tests) | `NOT RUN` | `pytest backend/tests/test_readonly_audit.py -v` | Static audit reduces risk, not absolute proof; adapter allowlist expands in 1.2/1.3 |
| A2 | Contracts & candles: `Instrument`/`Quote`/`Candle`/`FeedHealth` validation, `[open, close)` UTC, nullable, `supported_timeframes`, PK, revisions, `CONFIRMED_GAP` vs `POSSIBLE_GAP` without interpolation | `PASS` (27/27 tests) | `NOT RUN` | `pytest backend/tests/test_contracts.py -v` | — |
| A3 | Reconnect/recovery/WS queue on fake server (break, reconnect, history reload without dupes, gap preservation, overflow resync) | `NOT RUN` — Step 1.2 | `NOT RUN` — Step 1.2 | — | Not in Step 1.1 scope |
| A4 | Indicators: independent reference vectors (warm-up, edges, reset after `CONFIRMED_GAP`), batch vs incremental within tolerance | `NOT RUN` — Step 1.2 | `NOT RUN` — Step 1.2 | — | Formulas fixed in SPEC §7; code in Step 1.2 |
| A5 | Storage: Parquet write failure, failure **between** Parquet publication and SQLite commit + reconciliation, revision dedup, SQLite migrations, backup/restore | `PASS` (14/14 tests) | `NOT RUN` | `pytest backend/tests/test_parquet_reconcile.py backend/tests/test_backup_restore.py backend/tests/test_migrations.py -v` | Windows fsync/atomic-replace & NTFS semantics: `NOT RUN` until Windows |
| A6 | API & Launcher security: CSRF/Origin/auth, same-origin GET without `Origin`, CSRF refresh on reload/tabs, bootstrap one-time/expiry (incl. first-launch recovery), safe GUI reopen, unsafe ACL refusal, WS handshake, no secrets in logs/errors/static | `NOT RUN` — Step 1.3 | `NOT RUN` — Step 1.3 | — | Not in Step 1.1 scope |
| A7 | Feed states: `CONNECTED`, `DEGRADED`, `STALE`, `MARKET_CLOSED`, `OFFLINE`, plus `RATE_LIMITED` if REST adapter in scope | `NOT RUN` — Steps 1.2–1.3 | `NOT RUN` — Steps 1.2–1.3 | — | REST reserve not in scope (MT5 chosen); `RATE_LIMITED` `NOT APPLICABLE` |
| A8 | GUI tests: instrument/timeframe switching, history load, `GAP`/`N/A`/feed statuses, `PREVIEW/UNCONFIRMED`, `SIMULATED` marking | `NOT RUN` — Step 1.4 | `NOT RUN` — Step 1.4 | — | Not in Step 1.1 scope |

## B. Checks on the user's machine (separate from CI; without them Stage 1 is not fully accepted)

| # | Criterion | Status | Command / proof | Limitation |
|---|---|---|---|---|
| B1 | `preflight_check`: per-source report (success/error/disabled), DNS/TLS, REST, WS, MT5 (if included), clock comparison (best effort, no NTP masking) | `NOT RUN` on Windows target (`PASS` offline mode in Linux sandbox; `okx-spot`=`NOT_RUN`, `mt5-demo`=`BLOCKED` on Linux) | `python -m backend.app.tools.preflight_check --offline` | Sandbox cannot reach OKX/MT5; Windows target resources/permissions/OKX/MT5 unconfirmed |
| B2 | Real OKX spot stream observation ≥ 30 min + forced network break and recovery check | `NOT RUN` | — | Requires target Windows machine + verified OKX access |
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
| `test_readonly_audit.py` (no mutating routes, no trading keywords/calls/keys, loopback default) | `PASS` (6 passed) | `pytest backend/tests/test_readonly_audit.py -v` | 6/6 passed in Linux sandbox |
| WS JSON Schema generation | `PASS` | `python -m backend.app.schemas.generate_ws_schemas` | Generated `snapshot.schema.json`, `update.schema.json`, `resync_request.schema.json` |
| Offline start without network | `PASS` | `python -m backend.app.tools.preflight_check --offline` & `TestClient(app)` | Starts offline; `/healthz` & `/api/v1/instruments` respond 200 |

### 2. Windows x86_64 target machine checks

| Check | Result (Windows) | Reason / Blocker |
|---|---|---|
| Target hardware/OS verification (RAM, free disk, proxy/firewall, install permission) | `NOT RUN` | Unconfirmed by user; requires user permission and execution on Windows |
| Lockfile install (`pip install -r backend/requirements.lock`) on Windows Python 3.11 | `NOT RUN` | Not executed on Windows target (`backend/requirements.lock` includes PEP 508 `sys_platform` markers for `uvloop` and `colorama`, but real Windows execution is still required) |
| Windows NTFS atomic replace (`os.replace`) & SQLite WAL backup under Windows file locking | `NOT RUN` | Requires execution on Windows x86_64 |
| OKX public spot reachability & ToS verification on user connection | `NOT RUN` | Requires verification on user's network/account |
| MT5 terminal, suitable broker, and demo account availability (`EURUSD`, `USDJPY`, `XAUUSD`) | `NOT RUN` / `BLOCKED` | Unconfirmed; if absent on target, remains `BLOCKED` without substituting feed |

### Overall Step 1.1 gate verdict

- **Linux sandbox deterministic suite**: `PASS` (47/47 tests passed, offline startup verified).
- **Windows target verification**: `NOT RUN` (target checks have not been executed on Windows x86_64).
- **Gate status**: Per rule 7 ("если обязательная проверка Шага 1.1 не выполнена, пометь её `NOT RUN` и не объявляй ворота пройденными"), **Step 1.1 gates are NOT declared fully passed across target Windows** until the Windows checks above are executed, or the user explicitly authorizes moving to Step 1.2 in the sandbox with Windows checks recorded as `NOT RUN`.
