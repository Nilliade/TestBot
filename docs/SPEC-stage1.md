# SPEC — Stage 1 (v1.0, read-only prototype)

> Status: Step 1.1 — contracts & storage. This document is the normative
> contract for Stage 1. Any deviation must be recorded as a spec change.
> Stage 1 is **strictly read-only**: no trading endpoints, no order
> creation/cancellation, no trading API keys, no runtime order models,
> no RiskGuardian, no strategy plugins. Stages 2–5 are roadmap only
> (see §9) and are **not implemented** in Stage 1.

## 0. Environment & reproducibility

| Item | Pinned / tested value | Notes |
|---|---|---|
| Python | `3.11.2` (`requires-python ==3.11.*`) | Tested in sandbox (Linux x86_64, Debian 12). Target is Windows x86_64 — **not yet verified**; Windows verification is `NOT RUN` until executed on the target. |
| Node.js | `22.22.3` | Tested in sandbox. Frontend (Step 1.4) must pin exact `lightweight-charts` version + `LICENSE`/`NOTICE`. |
| Backend deps | `backend/requirements.lock` (exact pins, generated from `pyproject.toml`) | **Single reproducible install procedure:** `pip install -r backend/requirements.lock` (see `README.md`). `pyproject.toml` declares ranges; the lock file is the source of truth. No separate `requirements.txt` is maintained. |
| SQLite | WAL mode, `PRAGMA journal_mode=WAL`, `synchronous=NORMAL` (documented; `FULL` for backup verification) | Backup/restore procedure in §6 accounts for WAL. |
| Parquet | PyArrow, schema in §5 | One writer per partition; tmp → fsync → atomic replace. |
| Network | Local compute/GUI/storage are local; quotes/DNS/NTP/exchanges are external deps. App must start and run offline with explicit status. | Sandbox allows only `github.com`/`registry.npmjs.org`/`pypi.org`; OKX/MT5 live checks are `NOT RUN` in sandbox. |

### 0.1 Scope decisions (Section 0, confirmed by user)

- Target machine: **Windows x86_64**. RAM / free disk / install permission / proxy — **not confirmed**; not recorded as facts. Work proceeds in sandbox (Linux); before any install/check on Windows, missing data and permission must be requested.
- Crypto: **OKX spot, public data only** (no account, no trading keys). Availability and terms of use for the user's connection — **requiring verification** via `preflight_check` on the target; not assumed.
- Forex/XAU: **included in scope via MT5** (Windows + demo account, subprocess worker). Presence of terminal / suitable broker / demo account — **not confirmed**; if absent, corresponding checks are `BLOCKED`, feed is never substituted.
- Stage 1: **no trading, no trade journal, no feature snapshots**. Paper bots, broker demo execution and real execution are distinct later-stage modes (Stages 4–5), not implemented now.

## 1. Instrument catalog (`instruments.yaml`)

Configurable catalog (guideline up to 20 pairs: 10 crypto spot + 10 Forex/XAU).
Default active core: 4 crypto (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `XRP/USDT`,
optionally `PAXG/USDT`) + 3 Forex (`EUR/USD`, `USD/JPY`, `XAU/USD` when the
MT5 adapter is enabled). At startup, capability discovery marks unavailable
instruments as `UNAVAILABLE` without breaking startup.

`supported_timeframes` is determined per adapter+instrument. An unavailable
interval is shown as `UNAVAILABLE`, never generated without a confirmed
methodology. If a timeframe is locally aggregated, the source interval is
stated explicitly and completeness of source candles is verified.

### 1.1 `Instrument` fields

```text
schema_version: str            # e.g. "1.0"
source_id: str                 # stable source identifier, e.g. "okx-spot", "mt5-demo"
instrument_id: str             # stable internal id, e.g. "BTC-USDT"
instrument_storage_key: str    # safe key: [A-Za-z0-9_-]+ only; no /, :, spaces, path separators
alias: str                     # human-readable, e.g. "BTC/USDT"
venue_id: str                  # e.g. "okx"
account_or_feed_id: str        # feed/account identifier (public feed id; no secrets)
provider_symbol: str           # venue-native symbol, e.g. "BTC-USDT"
market_type: str               # "spot" | "cfd" | ... (enum MarketType)
base_ccy: str
quote_ccy: str
settlement_ccy: str
expiry: datetime | null        # UTC, only if applicable
contract_variant: str | null
price_basis: str               # "bid" | "ask" | "mid" | "last" | "unknown" (enum PriceBasis)
volume_type: str               # "exchange" | "tick" | "none" (enum VolumeType)
supported_timeframes: list[str]  # subset of {"1m","5m","15m","1h","4h","1d"}
specs: InstrumentSpecs         # min_qty, qty_step, price_step, min_notional, contract_size (Decimal-compatible strings)
supports_short: bool
session_calendar_id: str
```

Validation rules:
- `instrument_storage_key` must match `^[A-Za-z0-9_-]+$`, must not be `.`/`..`,
  must not contain `/`, `\`, `:`, whitespace or control chars. This prevents
  path traversal (`../`) and normalization collisions. Uniqueness is enforced
  per `(source_id, instrument_id)` and storage key must be unique per
  `(venue_slug, market_type)` partition.
- `supported_timeframes` must be non-empty and a subset of the allowed set.
- Specs are serialized as strings (Decimal-compatible, no float loss);
  parsing uses `Decimal`, never `float`, for price/qty steps.
- `market_type`, `price_basis`, `volume_type` are closed enums; unknown
  values are rejected at validation time.

## 2. Quotes (`Quote`) and market metrics

```text
schema_version: str
source_id: str
instrument_id: str
bid: Decimal | null
ask: Decimal | null
last: Decimal | null
exchange_ts_utc: datetime | null   # venue timestamp, UTC
received_ts_utc: datetime          # local receipt time, UTC
bid_age_ms: int | null             # optional separate ages
ask_age_ms: int | null
quality_flags: set[str]            # e.g. {"CROSSED", "MISSING_BID", "STALE", ...}
```

- Internal timeouts use monotonic clocks (`time.monotonic_ns()`); monotonic
  values are **never** persisted as wall-clock labels.
- `spread_bps = (ask - bid) / mid * 10_000`, `mid = (ask + bid) / 2` — computed
  **only** for fresh, valid, time-consistent sides. If `ask < bid` or a side
  is missing, `spread_bps` is `null` (`N/A` in UI) and a quality flag is set.
- Last price is explicitly labelled by basis (`last`, `mid`, etc.); `bid`
  must never be presented as `last` without labelling.
- 24h change: for crypto only the provider value with known semantics or an
  explicit window calculation (otherwise `N/A`); for Forex the change from
  trading-day close must **not** be labelled "24h" — UI must show the exact
  semantics.

## 3. Candles (`Candle`), gaps and revisions

Interval semantics: **`[open_time_utc, close_time_utc)`** in UTC.
For `1d`, the session/timezone binding of the source is fixed and recorded.

```text
schema_version: str
source_id: str
instrument_id: str
timeframe: str                     # "1m" | "5m" | "15m" | "1h" | "4h" | "1d"
open_time_utc: datetime            # UTC
close_time_utc: datetime           # UTC, exclusive; close_time > open_time
open: Decimal
high: Decimal
low: Decimal
close: Decimal
volume: Decimal | null             # null if source does not provide it
volume_type: str                   # "exchange" | "tick" | "none"
price_basis: str                   # "bid" | "ask" | "mid" | "last" | "unknown"
is_closed: bool
quality_flags: set[str]            # e.g. {"CONFIRMED_GAP","POSSIBLE_GAP","REVISED",...}
revision: int                      # >=1; increments on provider correction
received_at_utc: datetime
updated_at_utc: datetime
```

- **Primary key:** `(source_id, instrument_id, timeframe, open_time_utc)`.
- **Origin:** each adapter declares `provider_native` or `aggregated` +
  `price_basis` + `volume_type`. For the first crypto adapter, prefer native
  exchange candles (history + stream with close confirmation); never present
  aggregation of an incomplete stream as native candles. If the provider does
  not send an explicit close flag, the close rule, delay and subsequent
  history reconciliation are fixed and documented. For MT5, broker bar price
  basis or `price_basis=unknown` is fixed.
- **Closed vs current:** only closed candles (`is_closed=True`) are written
  to Parquet. The current candle lives in memory (`MarketHub`), is sent to
  GUI with `is_closed=False`; indicator values on it are either not drawn or
  explicitly marked `PREVIEW / UNCONFIRMED`.
- **Revisions:** on provider correction of a closed candle, `revision`
  increments and affected indicators are recomputed. Stage 1 implements
  option (a): store the current candle + revision journal with hashes,
  explicitly documenting that reproducing an old snapshot is impossible in
  Stage 1. (Option (b) append-only archive is a future extension.)
- **Gaps:** `CONFIRMED_GAP` only when by source/session rules the bar must
  have existed and its absence is confirmed; otherwise `POSSIBLE_GAP` /
  `UNKNOWN` with reason. Zero candles are never synthesized, gaps are never
  interpolated, and a closed session is never counted as a gap.

## 4. Feed health (`FeedHealth`)

Tracked at venue level **and** instrument/stream level:

```text
schema_version: str
source_id: str
instrument_id: str | null          # null = venue-level
status: str                        # CONNECTED | DEGRADED | STALE | MARKET_CLOSED | OFFLINE | RATE_LIMITED
last_quote_age_ms: int | null
last_heartbeat_age_ms: int | null
subscription_state: str | null
reconnect_count: int
gaps_detected: int
clock_skew_ms: int | null
rate_limit_state: str | null
```

`MARKET_CLOSED` is set **only** by a confirmed session calendar.

## 5. Storage: SQLite + Parquet

### 5.1 Paths

```text
data/market/{venue_slug}/{market_type}/{instrument_storage_key}/{timeframe}/{YYYY-MM}.parquet
```

`venue_slug` and `market_type` are derived from validated `Instrument`
fields; `instrument_storage_key` is validated per §1.1 (no traversal).

### 5.2 SQLite (WAL, migrations, backup/restore)

- `PRAGMA journal_mode=WAL` on open; verify with `PRAGMA journal_mode`
  (returns `wal`). `PRAGMA synchronous=NORMAL` for runtime;
  backup verification uses `FULL`.
- Migrations are versioned (`schema_migrations` table: `version`, `applied_at_utc`,
  `description`). Each migration is transactional and idempotent
  (re-applying is a no-op). Downgrades are not supported in Stage 1.
- Tables (Step 1.1):
  - `schema_migrations(version INTEGER PK, applied_at_utc TEXT, description TEXT)`
  - `candle_meta(source_id, instrument_id, timeframe, open_time_utc, close_time_utc,
    revision, received_at_utc, updated_at_utc, quality_flags, parquet_path, file_hash, PRIMARY KEY(source_id, instrument_id, timeframe, open_time_utc))`
  - `parquet_manifest(partition_key, file_path, file_hash, file_version, candle_count, updated_at_utc, PRIMARY KEY(partition_key, file_path))`
  - `revision_journal(source_id, instrument_id, timeframe, open_time_utc, revision, candle_hash, recorded_at_utc, PRIMARY KEY(..., revision))`
- **Backup/restore (WAL-aware):** use `sqlite3.Connection.backup()` (online
  backup API) which correctly handles WAL, or file copy **only** after
  `PRAGMA wal_checkpoint(TRUNCATE)` + `PRAGMA journal_mode=DELETE` on a
  quiesced connection. Never copy `-wal`/`-shm` files as a backup.
  Restore: stop writer, replace DB file, reopen, verify
  `PRAGMA integrity_check` and `PRAGMA journal_mode=WAL`, then run
  reconciliation (§5.4). Procedure is tested in `test_backup_restore.py`.

### 5.3 Parquet (batch, atomic, one writer per partition)

- Closed candles are buffered and published in batches (configurable interval
  + mandatory flush on clean shutdown; docs state the loss window on crash
  and the reload-from-source procedure).
- One writer per partition. Write to temp file **on the same filesystem**
  → `fsync` → atomic replace (`os.replace`). Behavior is verified on the
  target OS (Linux in sandbox; Windows verification is `NOT RUN`).
- Parquet schema (PyArrow): all `Candle` fields; decimals stored as
  `decimal128` or string (exact); timestamps as `timestamp[us, tz=UTC]`;
  `quality_flags` as `list<string>`. Schema version is stored in file
  metadata (`schema_version`).

### 5.4 Manifest & reconciliation

Parquet and SQLite are not in a shared transaction. Therefore:
- Each Parquet file has a manifest entry (file version + SHA-256 hash).
- At startup, **idempotent reconciliation** runs:
  1. Scan `data/market/**/*.parquet`, compute hashes.
  2. Compare with `parquet_manifest` and `candle_meta`.
  3. Missing manifest entries are created; hash mismatches are flagged
     (`DEGRADED`, requires manual review — never silently overwritten);
     orphan SQLite rows without Parquet data are flagged for reload.
- Tests cover failure during file write **and** failure between Parquet
  publication and SQLite metadata commit (see `test_parquet_reconcile.py`).

## 6. WebSocket protocol (`snapshot → updates → resync`)

- `sequence_id` belongs to a specific client stream and is assigned **after**
  coalescing, immediately before sending.
- Each reconnect starts a new `stream_epoch`. `snapshot` contains
  `stream_epoch`, `sequence_id` and a consistent state (changes during
  snapshot assembly must not be lost between `snapshot` and first `update`).
- On `stream_epoch` mismatch or a gap in delivered `sequence_id`, the client
  requests a new `snapshot`.
- Coalescing is allowed only for replaceable visual states (last quote,
  current open candle). Candle-close events, history revisions, data-quality
  changes and feed-state changes must not be silently lost: on overflow of a
  bounded per-client queue (configurable, e.g. 100 messages), the server
  marks the stream for resync and sends a new `snapshot` or closes the
  connection with an explicit code.
- WS message JSON Schemas are versioned separately from HTTP OpenAPI
  (`backend/app/schemas/ws/*.schema.json`, generated from Pydantic models).

## 7. Indicators (Stage 1: EMA, RSI, ATR, Donchian, Bollinger)

**All formulas are fixed here before code (implementation in Step 1.2).**
Inputs are closed candles only, in chronological order. `Decimal` prices are
converted to `float` for computation with documented tolerance; batch and
incremental results must match within `1e-9` (relative) in tests.

### 7.1 EMA (period `n`)

- `EMA_0 = SMA(first n closes)` (seed = mean of first `n` closes).
- `EMA_t = close_t * k + EMA_{t-1} * (1 - k)`, `k = 2 / (n + 1)`.
- Warm-up: first `n-1` values are `null` (not drawn). Output starts at index `n-1`.
- Gaps: after `CONFIRMED_GAP` (if not restored), state is reset and warm-up
  restarts (see §7.6).

### 7.2 RSI (period `n`, Wilder smoothing)

- `gain_0 = max(close_0 - close_{-1}, 0)`, `loss_0 = max(close_{-1} - close_0, 0)`
  for `t >= 1`.
- Initial `avg_gain = mean(first n gains)`, `avg_loss = mean(first n losses)`
  (Wilder initialization).
- `RSI = 100 - 100 / (1 + avg_gain / avg_loss)`; if `avg_loss == 0` and
  `avg_gain > 0` → `100`; if both `0` → `50` (documented edge).
- Subsequent: `avg_gain = (avg_gain * (n-1) + gain_t) / n` (Wilder),
  same for `avg_loss`.
- Warm-up: first `n` values are `null`; first output at index `n`.

### 7.3 ATR (period `n`, Wilder smoothing)

- `TR_t = max(high_t - low_t, |high_t - close_{t-1}|, |low_t - close_{t-1}|)`,
  `t >= 1`; `TR_0 = high_0 - low_0`.
- Initial `ATR = mean(first n TR)` (Wilder initialization).
- Subsequent: `ATR_t = (ATR_{t-1} * (n-1) + TR_t) / n`.
- Warm-up: first `n-1` values are `null`; first output at index `n-1`.

### 7.4 Donchian (period `n`)

- Upper `= max(high_{t-n+1..t})`, Lower `= min(low_{t-n+1..t})` over the
  **current closed candle inclusive** (displayed channel).
- For future breakout signals, the channel of the **previous `n` candles
  excluding current** is used separately (no look-ahead).
- Warm-up: first `n-1` values are `null`.

### 7.5 Bollinger (period `n`, `k` sigma)

- Middle `= SMA(closes, n)`.
- StdDev: **population** (`ddof=0`): `sqrt(mean((close - middle)^2))`.
- Upper `= middle + k * std`, Lower `= middle - k * std`.
- Warm-up: first `n-1` values are `null`.

### 7.6 Gaps & resets

After `CONFIRMED_GAP` (if the gap is not restored), indicator state is reset
and warm-up restarts. `POSSIBLE_GAP`/`UNKNOWN` do not reset but are flagged;
indicators spanning a possible gap are marked `UNCONFIRMED` in UI.

### 7.7 Reference vectors

Expected values must be independently verifiable (hand-calculated small
examples and/or a verified library with fixed parameters). Generating
expected values from the implementation under test is forbidden. Batch vs
incremental equality is checked within the tolerance above.

## 8. GUI requirements (Stage 1, Step 1.4)

1. **Watchlist:** active and `UNAVAILABLE` instruments, last price (with basis),
   bid/ask/spread (`N/A` when missing), price change (with exact window label),
   ATR, freshness and feed-status indicators.
2. **Interactive chart (Lightweight Charts):** candles, supported-timeframe
   switching (`1m, 5m, 15m, 1h, 4h, 1d` — unavailable shown as `UNAVAILABLE`),
   volumes only when type is known (`Exchange Vol` / `Tick Vol`, `none` → `N/A`),
   backend-indicator toggles, explicit `GAP` marking and current-candle status
   (`PREVIEW / UNCONFIRMED`).
3. **Health & diagnostics (`HealthBar` + `DiagnosticsDrawer`):** venue and
   instrument statuses, data age, reconnect count, `clock_skew_ms`, REST/rate-limit
   budget, `preflight_check` report.

## 9. Roadmap Stages 2–5 (architecture only, NOT implemented in Stage 1)

- **Stage 2:** modular `BaseStrategy` + `StrategyRegistry`, versioned JSON
  parameter schemas, reproducible backtests (code/config/data/env hashes, seed),
  event-driven engine with bid/ask, fees, slippage, swap/funding, walk-forward +
  holdout + block bootstrap, look-ahead tests.
- **Stage 3:** economic-calendar adapter (license-checked, fail-closed
  `NO_NEW_ENTRIES`), isolated local NLP (untrusted input, can only block/flag).
- **Stage 4:** independent `RiskGuardian` (0.25% equity research cap, daily/weekly
  limits, `Pause` vs `Panic`, `UNKNOWN` order state machine, `RECOVERY /
  NO_NEW_ENTRIES` after restart), paper bots (`bot_id`/`run_id`, isolated
  virtual balances/journals), honest execution modelling (post-signal quotes,
  ask for buy / bid for sell, slippage/fees/liquidity, SL/TP intra-candle),
  append-only decision/trade journal with hash chain, trade feature snapshots
  (no future leak, including rejected signals).
- **Stage 5:** isolated `Execution Gateway` (keys only there via OS secret store),
  Micro-Live gate (WFO/holdout + paper journal + failure tests + manual confirm +
  absolute loss limit), controlled self-learning (`Champion vs Challenger`,
  offline training, purge/embargo, two-stage OOS → Shadow/Paper, hard
  activation/rollback fuses, RiskGuardian supremacy).

## 10. Non-goals & prohibitions (Stage 1)

- No trading HTTP/WS endpoints, no order create/cancel calls, no trading keys.
- No fake/synthetic substitution of unavailable feeds in production.
  Synthetic streams only via isolated test fixtures (`SIMULATED` marking).
- No silent source switching/mixing for one instrument.
- No `try/except: pass`, no silent infinite retries, no tests that only check
  function existence.
- No external CDN/analytics in frontend; all resources local.

## 11. Acceptance gates (summary)

Full table in `docs/ACCEPTANCE.md`. Step 1.1 gate: contracts, migrations,
backup/restore, write-failure and Parquet/SQLite desync tests `PASS`;
offline start `PASS`. Windows-specific checks are `NOT RUN` until run on
Windows. Live OKX/MT5 checks are `NOT RUN` in sandbox.
