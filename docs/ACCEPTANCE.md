# ACCEPTANCE — Stage 1 Definition of Done

> Format: `Criterion | PASS / FAIL / NOT RUN / NOT APPLICABLE | Command or proof | Limitation`
> `NOT APPLICABLE` is allowed only for genuinely inapplicable checks
> (e.g. `RATE_LIMITED` when no REST adapter is in agreed scope).
> Linux sandbox results and Windows target checks are reported separately.
> Windows-specific checks are NEVER marked `PASS` without execution on Windows.

## A. Automatic deterministic checks (no external network — mandatory gates)

| # | Criterion | Status (Linux sandbox `Python 3.11.2`) | Status (Windows target `win32` `Python 3.11.9`) | Command / proof | Limitation |
|---|---|---|---|---|---|
| A1 | Read-only audit: route list, adapter method allowlist, static search for forbidden trading calls, config tests | `PASS` (7/7 tests) | `PASS` (7/7 tests) | `pytest backend/tests/test_readonly_audit.py -v` | Static audit reduces risk, not absolute proof; adapter allowlist expands in 1.3 |
| A2 | Contracts & candles: `Instrument`/`Quote`/`Candle`/`FeedHealth` validation, `[open, close)` UTC, nullable, `supported_timeframes`, PK, revisions, `CONFIRMED_GAP` vs `POSSIBLE_GAP` without interpolation | `PASS` (27/27 tests) | `PASS` (27/27 tests) | `pytest backend/tests/test_contracts.py -v` | — |
| A3 | Reconnect/recovery/WS queue on fake server (break, reconnect, history reload without dupes, gap preservation, overflow resync) | `PASS` (7/7 tests) | `PASS` (7/7 tests) | `pytest backend/tests/test_ws_reconnect_recovery.py -v` | Tested against deterministic fake upstream server; live OKX/MT5 is Step 1.3 |
| A4 | Indicators: independent reference vectors (warm-up, edges, reset after `CONFIRMED_GAP`), batch vs incremental within tolerance `<= 1e-9` | `PASS` (8/8 tests) | `PASS` (8/8 tests) | `pytest backend/tests/test_indicators.py -v` | Hand-calculated reference vectors per SPEC §7.1–7.7 |
| A5 | Storage: Parquet write failure, failure **between** Parquet publication and SQLite commit + reconciliation, revision dedup, SQLite migrations, backup/restore | `PASS` (14/14 tests) | `PASS` (14/14 tests: migrations 3/3, backup/restore 4/4, parquet_reconcile 7/7) | `pytest backend/tests/test_parquet_reconcile.py backend/tests/test_backup_restore.py backend/tests/test_migrations.py -v` | Verified on both Linux ext4 and Windows NTFS (`63 passed in 2.73s`) |
| A6 | API & Launcher security: CSRF/Origin/auth, same-origin GET without `Origin`, CSRF refresh on reload/tabs, bootstrap one-time/expiry (incl. first-launch recovery), safe GUI reopen, unsafe ACL refusal, WS handshake, no secrets in logs/errors/static | `NOT RUN` — Step 1.3 | `NOT RUN` — Step 1.3 | — | Not in Step 1.2 scope |
| A7 | Feed states: `CONNECTED`, `DEGRADED`, `STALE`, `MARKET_CLOSED`, `OFFLINE`, plus `RATE_LIMITED` if REST adapter in scope | `PASS` (state machine in `MarketHub` + `SessionCalendar`, tested in `test_ws_reconnect_recovery.py`) | `PASS` (tested in `test_ws_reconnect_recovery.py` on Windows) | `pytest backend/tests/test_ws_reconnect_recovery.py -v` | REST reserve not in scope (MT5 chosen); `RATE_LIMITED` `NOT APPLICABLE`; live adapter wiring is Step 1.3 |
| A8 | GUI tests: instrument/timeframe switching, history load, `GAP`/`N/A`/feed statuses, `PREVIEW/UNCONFIRMED`, `SIMULATED` marking | `NOT RUN` — Step 1.4 | `NOT RUN` — Step 1.4 | — | Not in Step 1.2 scope |

## B. Checks on the user's machine (separate from CI; without them Stage 1 is not fully accepted)

| # | Criterion | Status | Command / proof | Limitation |
|---|---|---|---|---|
| B1 | `preflight_check`: per-source report (success/error/disabled), DNS/TLS, REST, WS, MT5 (if included), clock comparison (best effort, no NTP masking) | Partial `PASS` on Windows target (`config`=`OK`, `storage`=`OK`, `clock`=`OK` 3ms drift, `okx-spot` DNS+TCP=`OK`; REST/WS live & `mt5-demo`=`NOT_RUN`) | `.venv\Scripts\python.exe -m backend.app.tools.preflight_check` on Windows | Full REST/WS OKX & MT5 subprocess checks belong to Step 1.3 |
| B2 | Real OKX spot stream observation ≥ 30 min + forced network break and recovery check | `NOT RUN` | — | Requires Step 1.3 OKX adapter + target Windows execution |
| B3 | GUI stability on isolated synthetic stream (`SIMULATED`) 30 min with report: OS/browser/versions, instrument count, event rate, warm-up duration, instrument/interval of measurements; no unbounded growth of queues/subscriptions/DOM; memory growth > 50 MB in last 20 min after warm-up triggers investigation (not auto-fail/pass) | `NOT RUN` — Step 1.4 | — | Not in Step 1.2 scope |
| B4 | After clean restart: history, settings and correct source statuses preserved | `NOT RUN` | — | Requires full stack (Steps 1.2–1.4) |

## Step 1.1 & Step 1.2 gate status

### 1. Linux sandbox checks (`Debian GNU/Linux 12 x86_64`, Python `3.11.2`)

| Check | Result (Linux) | Command | Evidence |
|---|---|---|---|
| `test_contracts.py` (domain models, WS schemas, catalog, offline FastAPI, offline preflight) | `PASS` (27 passed) | `pytest backend/tests/test_contracts.py -v` | 27/27 passed |
| `test_migrations.py` (WAL, idempotent v1 schema, integrity_check) | `PASS` (3 passed) | `pytest backend/tests/test_migrations.py -v` | 3/3 passed |
| `test_backup_restore.py` (SQLite backup API without manual WAL checkpoint, restore, revision conflict/regression) | `PASS` (4 passed) | `pytest backend/tests/test_backup_restore.py -v` | 4/4 passed |
| `test_parquet_reconcile.py` (atomic write, unclosed rejection, crash between Parquet & SQLite + idempotent reconcile, hash mismatch reporting, orphan SQLite rows) | `PASS` (7 passed) | `pytest backend/tests/test_parquet_reconcile.py -v` | 7/7 passed |
| `test_readonly_audit.py` (no mutating routes, no trading keywords/calls/keys, loopback default, `.bat` lockfile check) | `PASS` (7 passed) | `pytest backend/tests/test_readonly_audit.py -v` | 7/7 passed |
| `test_indicators.py` (`EMA`, `RSI Wilder`, `ATR Wilder`, `Donchian`, `Bollinger ddof=0`, hand-calculated reference vectors, `CONFIRMED_GAP` reset, `POSSIBLE_GAP` `UNCONFIRMED`, batch vs incremental `<= 1e-9`, `PREVIEW_UNCONFIRMED`) | `PASS` (8 passed) | `pytest backend/tests/test_indicators.py -v` | 8/8 passed |
| `test_ws_reconnect_recovery.py` (fake server break/reconnect, history backfill without dupes, `CONFIRMED_GAP`/`POSSIBLE_GAP` without interpolation, confirmed Forex weekend `MARKET_CLOSED`, WS coalescing, post-coalesce `sequence_id`, overflow resync, `/ws/stream` + `ResyncRequest`) | `PASS` (7 passed) | `pytest backend/tests/test_ws_reconnect_recovery.py -v` | 7/7 passed |

### 2. Windows x86_64 target machine checks (`C:\Users\Thema\Desktop\TestBot-main`, Python `3.11.9`)

| Check | Result (Windows) | Evidence / Limitation |
|---|---|---|
| Full Step 1.1 + Step 1.2 deterministic suite (`test_backup_restore`, `test_contracts`, `test_indicators`, `test_migrations`, `test_parquet_reconcile`, `test_readonly_audit`, `test_ws_reconnect_recovery` — 63 tests) | `PASS` (`63 passed in 2.73s`) | Verified on `platform win32 -- Python 3.11.9, pytest-8.4.2, pluggy-1.7.0` |

### Step 1.2 gate verdict

- **Linux sandbox deterministic suite (`A1`, `A2`, `A3`, `A4`, `A5`, `A7`)**: `PASS` (`63 passed in 0.65s`).
- **Windows x86_64 target deterministic suite (`A1`, `A2`, `A3`, `A4`, `A5`, `A7`)**: `PASS` (`63 passed in 2.73s`).
- **All mandatory Step 1.1 and Step 1.2 gates are PASSED** on both Linux and Windows x86_64.
