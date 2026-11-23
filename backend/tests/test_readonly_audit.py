"""Read-only audit (ACCEPTANCE A1): routes, forbidden calls, config.

Static audit reduces risk; it is not absolute proof (documented in SPEC).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.app.config.settings import Settings
from backend.app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"

FORBIDDEN_PATTERNS = [
    r"create_order",
    r"cancel_order",
    r"place_order",
    r"submit_order",
    r"fetch_order",
    r"create_limit_order",
    r"create_market_order",
    r"\.buy\s*\(",
    r"\.sell\s*\(",
    r"api_key\s*=\s*['\"][^'\"]+['\"]",  # hardcoded trading key literal
    r"secret_key\s*=\s*['\"][^'\"]+['\"]",
]

TRADING_ROUTE_KEYWORDS = ["order", "trade", "buy", "sell", "place", "cancel", "execute"]


def test_no_mutating_routes_in_skeleton():
    mutating = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        mutating_methods = methods & {"POST", "PUT", "PATCH", "DELETE"}
        if mutating_methods:
            mutating.append((route.path, sorted(mutating_methods)))
    # Step 1.1 skeleton: only GET routes (/healthz, /api/v1/instruments).
    assert mutating == [], f"mutating routes found (forbidden in Stage 1 skeleton): {mutating}"


def test_no_trading_route_paths():
    for route in app.routes:
        path = getattr(route, "path", "").lower()
        for kw in TRADING_ROUTE_KEYWORDS:
            assert kw not in path, f"trading-like route path found: {path}"


def test_no_forbidden_trading_calls_in_app_code():
    violations = []
    for py_file in sorted(APP_DIR.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            for m in re.finditer(pattern, text):
                violations.append(f"{py_file.relative_to(REPO_ROOT)}: {pattern} @ {m.group(0)!r}")
    assert violations == [], "forbidden trading calls found:\n" + "\n".join(violations)


def test_no_trading_keys_in_config():
    for cfg in [APP_DIR / "config" / "settings.py", APP_DIR / "config" / "instruments.yaml"]:
        text = cfg.read_text(encoding="utf-8").lower()
        # Stage 1 must not contain trading credential fields.
        for field in ["api_key", "api_secret", "secret_key", "password"]:
            assert field not in text, f"{cfg.name} contains forbidden field {field!r}"


def test_default_bind_is_loopback_only():
    settings = Settings()
    assert settings.host == "127.0.0.1", f"default host must be 127.0.0.1, got {settings.host!r}"


def test_adapters_workers_security_have_no_trading_code_yet():
    # Steps 1.2–1.3 will add adapters/security; in 1.1 they must be placeholders.
    for sub in ["adapters", "workers", "security"]:
        py_files = [p for p in (APP_DIR / sub).glob("*.py") if p.name != "__init__.py"]
        assert py_files == [], f"{sub}/ must be placeholder in 1.1, found {py_files}"


def test_windows_batch_launchers_use_lockfile_and_loopback():
    assert not (REPO_ROOT / "Stup.bat").exists()
    for bat_name in ["Setup.bat", "Start.bat"]:
        bat_path = REPO_ROOT / bat_name
        assert bat_path.exists(), f"missing {bat_name}"
        raw = bat_path.read_bytes()
        # Ensure pure ASCII so Windows cmd.exe never breaks across codepages.
        raw.decode("ascii")
        assert b"requirements.txt" not in raw, f"{bat_name} must not reference requirements.txt"

    setup_text = (REPO_ROOT / "Setup.bat").read_text(encoding="ascii")
    assert r"backend\requirements.lock" in setup_text
    assert "127.0.0.1:8765" in setup_text
    assert "0.0.0.0" not in setup_text

    gitattr = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in gitattr

    lock_text = (REPO_ROOT / "backend" / "requirements.lock").read_text(encoding="utf-8")
    assert "tzdata==" in lock_text
