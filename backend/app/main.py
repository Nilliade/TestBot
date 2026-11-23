"""FastAPI application (Step 1.2): starts offline, strictly read-only, MarketHub + Indicators + WS.

Security (bootstrap/session/CSRF/Host/Origin/CSP) is Step 1.3 and is
NOT IMPLEMENTED here. This app exposes only read-only GET endpoints and a
read-only WebSocket stream (/ws/stream); it binds to 127.0.0.1 by default.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import html

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from backend.app.config.settings import Settings, load_instruments
from backend.app.schemas.http_models import (
    CandlesResponse,
    FeedHealthResponse,
    HealthResponse,
    IndicatorsResponse,
    InstrumentsResponse,
    QuotesResponse,
)
from backend.app.schemas.ws_models import ResyncRequest
from backend.app.services.market_hub import MarketHub
from backend.app.sessions.calendar import load_session_calendars

VERSION = "0.2.0"


def create_default_hub(settings: Settings | None = None) -> MarketHub:
    cfg = settings or Settings()
    instruments = load_instruments(cfg.instruments_path)
    calendars = load_session_calendars(cfg.sessions_path)
    return MarketHub(
        instruments=instruments,
        calendars=calendars,
        ws_client_queue_limit=cfg.ws_client_queue_limit,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Offline-safe startup: no external network calls, no live exchange connections.
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.hub = create_default_hub(settings)
    yield
    app.state.hub.flush_closed_candles()


app = FastAPI(title="Trading Platform (Stage 1, read-only)", version=VERSION, lifespan=lifespan)


def _get_hub() -> MarketHub:
    hub = getattr(app.state, "hub", None)
    if hub is None:
        hub = create_default_hub()
        app.state.hub = hub
    return hub


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    hub = _get_hub()
    instruments = list(hub.instruments.values())
    fhs = {(fh.source_id, fh.instrument_id): fh.status.value for fh in hub.get_feed_health()}
    rows_html = []
    for inst in instruments:
        tfs = ", ".join(inst.supported_timeframes)
        status_str = fhs.get((inst.source_id, inst.instrument_id), "OFFLINE")
        rows_html.append(
            "<tr>"
            f"<td><code>{html.escape(inst.alias)}</code></td>"
            f"<td><code>{html.escape(inst.source_id)}</code></td>"
            f"<td><code>{html.escape(inst.venue_id)}</code></td>"
            f"<td><code>{html.escape(inst.market_type.value)}</code></td>"
            f"<td><code>{html.escape(inst.price_basis.value)}</code></td>"
            f"<td><code>{html.escape(inst.volume_type.value)}</code></td>"
            f"<td><code>{html.escape(tfs)}</code></td>"
            f"<td><span class='badge warn'>{html.escape(status_str)}</span></td>"
            "</tr>"
        )
    table_body = "\n".join(rows_html)
    page = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>TestBot — Этап 1 (Шаг 1.2, Read-Only)</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }}
    .container {{ max-width: 1040px; margin: 0 auto; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 18px 20px; margin-bottom: 16px; }}
    h1 {{ margin: 0 0 8px 0; font-size: 22px; }}
    h2 {{ margin: 0 0 12px 0; font-size: 16px; color: #94a3b8; }}
    .badges {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
    .ok {{ background: #065f46; color: #a7f3d0; }}
    .ro {{ background: #1e3a8a; color: #bfdbfe; }}
    .warn {{ background: #78350f; color: #fde68a; }}
    a {{ color: #38bdf8; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .links {{ display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #334155; }}
    th {{ color: #94a3b8; font-weight: 600; }}
    code {{ background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
    ul {{ margin: 6px 0 0 18px; padding: 0; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1>TestBot — Локальная платформа анализа рынка (v{html.escape(VERSION)})</h1>
      <div class="badges">
        <span class="badge ro">MODE: STRICT READ-ONLY (ЭТАП 1)</span>
        <span class="badge ok">SERVER: ONLINE (127.0.0.1)</span>
        <span class="badge ok">STEP 1.2: MARKETHUB &amp; INDICATORS READY</span>
      </div>
      <p>Локальный сервер Шага 1.2 запущен в офлайн-режиме. Торговые функции строго отключены.</p>
      <div class="links">
        <a href="/docs">Интерактивная документация API (/docs)</a>
        <a href="/healthz">Статус здоровья (/healthz)</a>
        <a href="/api/v1/instruments">Инструменты (/api/v1/instruments)</a>
        <a href="/api/v1/quotes">Котировки (/api/v1/quotes)</a>
        <a href="/api/v1/feed-health">Диагностика фидов (/api/v1/feed-health)</a>
        <a href="/api/v1/candles?source_id=okx-spot&amp;instrument_id=BTC-USDT&amp;timeframe=1h">Свечи BTC/USDT 1h</a>
        <a href="/api/v1/indicators?source_id=okx-spot&amp;instrument_id=BTC-USDT&amp;timeframe=1h">Индикаторы BTC/USDT 1h</a>
      </div>
    </div>

    <div class="card">
      <h2>Сконфигурированный каталог инструментов (backend/app/config/instruments.yaml)</h2>
      <table>
        <thead>
          <tr>
            <th>Пара</th>
            <th>Источник</th>
            <th>Площадка</th>
            <th>Тип рынка</th>
            <th>Базис цены</th>
            <th>Тип объёма</th>
            <th>Таймфреймы</th>
            <th>Статус фида</th>
          </tr>
        </thead>
        <tbody>
          {table_body}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h2>Статус компонентов Этапа 1</h2>
      <ul>
        <li><strong>Шаг 1.1 (PASS):</strong> Доменные контракты Pydantic, хранилище SQLite WAL (миграции, backup/restore), ParquetStore (атомарная запись, reconcile), генератор WS JSON Schema, <code>preflight_check</code>.</li>
        <li><strong>Шаг 1.2 (реализован):</strong> <code>SessionCalendar</code>, <code>CandleSeriesBuilder</code> (детекция <code>CONFIRMED_GAP</code> / <code>POSSIBLE_GAP</code> без интерполяции), <code>IndicatorSuite</code> (<code>EMA</code>, <code>RSI Wilder</code>, <code>ATR Wilder</code>, <code>Donchian</code>, <code>Bollinger Bands</code>), <code>MarketHub</code> и WebSocket-поток <code>/ws/stream</code>.</li>
        <li><strong>Шаг 1.3 (NOT IMPLEMENTED):</strong> Живые адаптеры <code>OKX spot</code> и <code>MT5 demo</code>, защита лаунчера (CSRF/Origin/ACL).</li>
        <li><strong>Шаг 1.4 (NOT IMPLEMENTED):</strong> Интерактивный фронтенд с графиками свечей (<code>frontend/</code>).</li>
      </ul>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=page)


@app.get("/healthz", response_model=HealthResponse, tags=["health"])
def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION, network="offline")


@app.get("/api/v1/instruments", response_model=InstrumentsResponse, tags=["instruments"])
def list_instruments() -> InstrumentsResponse:
    instruments = load_instruments()
    return InstrumentsResponse(instruments=instruments)


@app.get("/api/v1/quotes", response_model=QuotesResponse, tags=["market-data"])
def list_quotes() -> QuotesResponse:
    hub = _get_hub()
    return QuotesResponse(quotes=hub.get_quotes())


@app.get("/api/v1/candles", response_model=CandlesResponse, tags=["market-data"])
def list_candles(
    source_id: str = Query(..., min_length=1),
    instrument_id: str = Query(..., min_length=1),
    timeframe: str = Query("1h", min_length=2),
    limit: int = Query(500, ge=1, le=5000),
) -> CandlesResponse:
    hub = _get_hub()
    closed, open_c = hub.get_candles(source_id, instrument_id, timeframe, limit=limit)
    return CandlesResponse(
        source_id=source_id,
        instrument_id=instrument_id,
        timeframe=timeframe,
        closed_candles=closed,
        open_candle=open_c,
    )


@app.get("/api/v1/indicators", response_model=IndicatorsResponse, tags=["indicators"])
def get_indicators(
    source_id: str = Query(..., min_length=1),
    instrument_id: str = Query(..., min_length=1),
    timeframe: str = Query("1h", min_length=2),
    include_preview: bool = Query(False),
) -> IndicatorsResponse:
    hub = _get_hub()
    snap = hub.get_indicators(source_id, instrument_id, timeframe, include_preview=include_preview)
    return IndicatorsResponse(indicators=snap)


@app.get("/api/v1/feed-health", response_model=FeedHealthResponse, tags=["diagnostics"])
def list_feed_health() -> FeedHealthResponse:
    hub = _get_hub()
    return FeedHealthResponse(feed_health=hub.get_feed_health())


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    """Read-only WebSocket stream (`snapshot -> updates -> resync`, SPEC §6)."""
    await websocket.accept()
    hub = _get_hub()
    stream = hub.subscribe_client()

    async def _receiver() -> None:
        while not stream.closed:
            raw = await websocket.receive_json()
            req = ResyncRequest.model_validate(raw)
            hub.handle_client_resync(stream, req)

    recv_task = asyncio.create_task(_receiver())
    try:
        while not stream.closed:
            msg = await stream.wait_next_message(timeout=0.05)
            if msg is not None:
                await websocket.send_json(msg.model_dump(mode="json"))
            if recv_task.done():
                exc = recv_task.exception()
                if exc is not None:
                    raise exc
    except WebSocketDisconnect:
        pass
    finally:
        recv_task.cancel()
        hub.unsubscribe_client(stream)


@app.get("/openapi.json", include_in_schema=False)
def openapi_json():
    return app.openapi()


if __name__ == "__main__":
    import uvicorn

    settings = Settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
