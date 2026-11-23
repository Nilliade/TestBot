"""Session calendars (SPEC §3, §4): MARKET_CLOSED only by confirmed calendar.

Rules:
- Crypto (`crypto-24x7`): `always_open=True`, `confirmed=True` -> always open.
- Forex (`forex-24x5`): weekly schedule in configured IANA timezone
  (default Sunday 17:00 America/New_York to Friday 17:00 America/New_York).
- `is_confirmed_closed(ts)` returns True ONLY when `confirmed=True` AND `ts`
  falls in a closed window. If `confirmed=False`, `is_confirmed_closed(ts)`
  always returns False (never emit MARKET_CLOSED or suppress gaps on an
  unconfirmed schedule).
- `is_confirmed_open(ts)` returns True ONLY when `confirmed=True` AND `ts`
  falls in an open window.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.config.settings import DEFAULT_SESSIONS_PATH

UTC = timezone.utc


class SessionCalendar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_id: str = Field(min_length=1)
    description: str = ""
    timezone: str = "UTC"
    always_open: bool = True
    confirmed: bool = False
    weekly_open_weekday: int = Field(default=6, ge=0, le=6)   # Sunday = 6
    weekly_open_hour: int = Field(default=17, ge=0, le=23)
    weekly_open_minute: int = Field(default=0, ge=0, le=59)
    weekly_close_weekday: int = Field(default=4, ge=0, le=6)  # Friday = 4
    weekly_close_hour: int = Field(default=17, ge=0, le=23)
    weekly_close_minute: int = Field(default=0, ge=0, le=59)
    note: str | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, v: str) -> str:
        ZoneInfo(v)
        return v

    def _is_schedule_open_at(self, ts_utc: datetime) -> bool:
        if ts_utc.tzinfo is None or ts_utc.utcoffset() != UTC.utcoffset(ts_utc):
            raise ValueError("ts_utc must be timezone-aware UTC")
        if self.always_open:
            return True
        tz = ZoneInfo(self.timezone)
        local_dt = ts_utc.astimezone(tz)
        wd = local_dt.weekday()  # Mon=0 .. Sun=6
        hm = (local_dt.hour, local_dt.minute)
        open_hm = (self.weekly_open_hour, self.weekly_open_minute)
        close_hm = (self.weekly_close_hour, self.weekly_close_minute)

        # Standard 24x5 Forex window: opens Sunday (6) at open_hm, closes Friday (4) at close_hm.
        if wd == self.weekly_open_weekday:
            return hm >= open_hm
        if wd == self.weekly_close_weekday:
            return hm < close_hm
        if wd == 5:  # Saturday
            return False
        # Monday (0) .. Thursday (3)
        return True

    def is_confirmed_closed(self, ts_utc: datetime) -> bool:
        """True ONLY if calendar is confirmed and market is closed at ts_utc."""
        if not self.confirmed:
            return False
        return not self._is_schedule_open_at(ts_utc)

    def is_confirmed_open(self, ts_utc: datetime) -> bool:
        """True ONLY if calendar is confirmed and market is open at ts_utc."""
        if not self.confirmed:
            return False
        return self._is_schedule_open_at(ts_utc)

    def is_interval_confirmed_closed(self, start_utc: datetime, end_utc: datetime) -> bool:
        """True if calendar is confirmed and start_utc falls in a closed session."""
        if end_utc <= start_utc:
            raise ValueError("end_utc must be > start_utc")
        return self.is_confirmed_closed(start_utc)


class SessionsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    calendars: list[SessionCalendar] = Field(default_factory=list)


class SessionCalendarRegistry:
    def __init__(self, calendars: list[SessionCalendar]) -> None:
        self._by_id: dict[str, SessionCalendar] = {}
        for cal in calendars:
            if cal.calendar_id in self._by_id:
                raise ValueError(f"duplicate calendar_id: {cal.calendar_id}")
            self._by_id[cal.calendar_id] = cal

    def get(self, calendar_id: str) -> SessionCalendar:
        if calendar_id not in self._by_id:
            raise KeyError(f"unknown session calendar: {calendar_id!r}")
        return self._by_id[calendar_id]

    def list_all(self) -> list[SessionCalendar]:
        return list(self._by_id.values())


def load_session_calendars(path: Path | None = None) -> SessionCalendarRegistry:
    p = Path(path) if path is not None else DEFAULT_SESSIONS_PATH
    if not p.exists():
        raise FileNotFoundError(f"sessions file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    parsed = SessionsFile.model_validate(raw)
    return SessionCalendarRegistry(parsed.calendars)
