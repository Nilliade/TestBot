# Indicators Package (Stage 1, Step 1.2 — IMPLEMENTED)

Implements Stage 1 indicators per `docs/SPEC-stage1.md` §7:
- `IncrementalEMA` / `compute_ema_batch`
- `IncrementalRSI` / `compute_rsi_batch` (Wilder smoothing)
- `IncrementalATR` / `compute_atr_batch` (Wilder smoothing)
- `IncrementalDonchian` / `compute_donchian_batch`
- `IncrementalBollinger` / `compute_bollinger_batch` (population std `ddof=0`)
- `IndicatorSuite` (handles warm-up, `CONFIRMED_GAP` state reset, `POSSIBLE_GAP` `UNCONFIRMED` marking, and non-mutating `PREVIEW_UNCONFIRMED` preview on unclosed candles).
