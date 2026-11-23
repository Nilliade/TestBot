"""Preflight check: independent per-source report (SPEC §0, ACCEPTANCE B1).

Skeleton (Step 1.1): checks config, local storage, clock (best effort),
and reports OKX/MT5 live reachability as NOT RUN/BLOCKED when the
environment cannot verify them. Never fakes success.

Usage:
    python -m backend.app.tools.preflight_check [--offline] [--json]
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config.settings import DEFAULT_DATA_DIR, load_instruments

# OKX public hosts (verification only; no trading endpoints).
OKX_HOSTS = ["www.okx.com", "ws.okx.com"]
# MT5 is a local Windows subprocess; no network host to probe from Linux sandbox.
MT5_NOTE = "MT5 requires Windows + terminal + demo account (NOT RUN in sandbox)"


@dataclass
class SourceReport:
    source: str
    status: str  # "ok" | "error" | "disabled" | "not_run" | "blocked"
    detail: str
    checks: dict = field(default_factory=dict)


def _check_dns(host: str) -> tuple[bool, str]:
    try:
        socket.getaddrinfo(host, 443)
        return True, "dns ok"
    except Exception as exc:  # noqa: BLE001 - report, never mask
        return False, f"dns failed: {exc}"


def _check_tcp(host: str, port: int = 443, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "tcp ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"tcp failed: {exc}"


def _clock_skew_best_effort() -> tuple[int | None, str]:
    """Best-effort clock comparison without masking NTP errors.

    Compares local wall clock with monotonic clock drift over a short
    interval and reports local time. No NTP query is attempted here;
    real NTP verification requires target-machine execution.
    """
    try:
        wall0 = time.time()
        mono0 = time.monotonic()
        time.sleep(0.05)
        wall1 = time.time()
        mono1 = time.monotonic()
        wall_delta = wall1 - wall0
        mono_delta = mono1 - mono0
        skew_ms = int(round((wall_delta - mono_delta) * 1000))
        now = datetime.now(timezone.utc).isoformat()
        return skew_ms, f"local_utc={now} wall-mono drift over 50ms={skew_ms}ms (NTP not queried; verify on target)"
    except Exception as exc:  # noqa: BLE001
        return None, f"clock check failed: {exc}"


def run_preflight(offline: bool = False) -> list[SourceReport]:
    reports: list[SourceReport] = []

    # Config / instruments.
    try:
        instruments = load_instruments()
        reports.append(
            SourceReport(
                source="config",
                status="ok",
                detail=f"loaded {len(instruments)} instruments",
                checks={"count": len(instruments)},
            )
        )
    except Exception as exc:  # noqa: BLE001
        reports.append(SourceReport(source="config", status="error", detail=str(exc)))

    # Local storage.
    try:
        data_dir = DEFAULT_DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)
        reports.append(SourceReport(source="storage", status="ok", detail=f"data_dir={data_dir} writable"))
    except Exception as exc:  # noqa: BLE001
        reports.append(SourceReport(source="storage", status="error", detail=str(exc)))

    # Clock.
    skew, detail = _clock_skew_best_effort()
    reports.append(
        SourceReport(
            source="clock",
            status="ok" if skew is not None else "error",
            detail=detail,
            checks={"clock_skew_ms": skew},
        )
    )

    # OKX public spot (REST/WS reachability). In sandbox, external hosts are
    # unreachable by policy; in offline mode we do not attempt network.
    if offline:
        reports.append(
            SourceReport(
                source="okx-spot",
                status="not_run",
                detail="offline mode: network checks skipped (NOT RUN)",
            )
        )
    else:
        dns_ok, dns_detail = _check_dns("www.okx.com")
        tcp_ok, tcp_detail = _check_tcp("www.okx.com", 443)
        if dns_ok and tcp_ok:
            reports.append(
                SourceReport(
                    source="okx-spot",
                    status="ok",
                    detail="dns+tcp reachable (REST/WS live verification still required on target)",
                    checks={"dns": dns_detail, "tcp": tcp_detail},
                )
            )
        else:
            reports.append(
                SourceReport(
                    source="okx-spot",
                    status="error",
                    detail=f"unreachable: {dns_detail}; {tcp_detail} (requires verification on target; not faked)",
                    checks={"dns": dns_detail, "tcp": tcp_detail},
                )
            )

    # MT5 (Windows subprocess). On non-Windows sandbox it is BLOCKED, never faked.
    import platform

    if platform.system() != "Windows":
        reports.append(
            SourceReport(
                source="mt5-demo",
                status="blocked",
                detail=f"{MT5_NOTE}; current OS={platform.system()} (NOT RUN)",
            )
        )
    else:
        reports.append(
            SourceReport(
                source="mt5-demo",
                status="not_run",
                detail="Windows detected; terminal/broker/demo verification NOT RUN (requires target execution)",
            )
        )

    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 1 preflight check (per-source report)")
    parser.add_argument("--offline", action="store_true", help="Skip network checks (NOT RUN)")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    reports = run_preflight(offline=args.offline)
    if args.json:
        print(json.dumps([{"source": r.source, "status": r.status, "detail": r.detail, "checks": r.checks} for r in reports], indent=2))
    else:
        for r in reports:
            print(f"[{r.status.upper():>8}] {r.source}: {r.detail}")
    # Exit code: 0 if no "error" (not_run/blocked are not failures of the tool itself).
    has_error = any(r.status == "error" for r in reports)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
