"""FastAPI skeleton (Step 1.1): starts offline, read-only, no trading routes.

Security (bootstrap/session/CSRF/Host/Origin/CSP) is Step 1.3 and is
NOT IMPLEMENTED here. This skeleton exposes only read-only GET endpoints
(/, /healthz, /api/v1/instruments, /docs) for contract verification; it binds
to 127.0.0.1 by default.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import html

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from backend.app.config.settings import Settings, load_instruments
from backend.app.schemas.http_models import HealthResponse, InstrumentsResponse

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Offline-safe startup: no network calls, no feed connections.
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Trading Platform (Stage 1, read-only)", version=VERSION, lifespan=lifespan)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    instruments = load_instruments()
    rows_html = []
    for inst in instruments:
        tfs = ", ".join(inst.supported_timeframes)
        rows_html.append(
            "<tr>"
            f"<td><code>{html.escape(inst.alias)}</code></td>"
            f"<td><code>{html.escape(inst.source_id)}</code></td>"
            f"<td><code>{html.escape(inst.venue_id)}</code></td>"
            f"<td><code>{html.escape(inst.market_type.value)}</code></td>"
            f"<td><code>{html.escape(inst.price_basis.value)}</code></td>"
            f"<td><code>{html.escape(inst.volume_type.value)}</code></td>"
            f"<td><code>{html.escape(tfs)}</code></td>"
            "<td><span class='badge warn'>OFFLINE (Step 1.1 skeleton)</span></td>"
            "</tr>"
        )
    table_body = "\n".join(rows_html)
    page = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>TestBot — Этап 1 (Шаг 1.1, Read-Only)</title>
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
        <span class="badge warn">STEP 1.1: SKELETON &amp; STORAGE</span>
      </div>
      <p>Локальный сервер Шага 1.1 запущен в офлайн-режиме. Торговые функции строго отключены.</p>
      <div class="links">
        <a href="/docs">Интерактивная документация API (/docs)</a>
        <a href="/healthz">Статус здоровья JSON (/healthz)</a>
        <a href="/api/v1/instruments">Каталог инструментов JSON (/api/v1/instruments)</a>
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
        <li><strong>Шаг 1.1 (реализован):</strong> Доменные контракты Pydantic, хранилище SQLite WAL (миграции, backup/restore), ParquetStore (атомарная запись, reconcile), генератор WS JSON Schema, <code>preflight_check</code>.</li>
        <li><strong>Шаг 1.2 (NOT IMPLEMENTED):</strong> <code>MarketHub</code>, <code>CandleBuilder</code>, расчёт индикаторов (<code>EMA</code>, <code>RSI</code>, <code>ATR</code>, <code>Donchian</code>, <code>Bollinger</code>).</li>
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


@app.get("/openapi.json", include_in_schema=False)
def openapi_json():
    return app.openapi()


if __name__ == "__main__":
    import uvicorn

    settings = Settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
