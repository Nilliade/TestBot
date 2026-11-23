# Sessions Package (Stage 1, Step 1.2 — IMPLEMENTED)

Implements `SessionCalendar` and `SessionCalendarRegistry` per `docs/SPEC-stage1.md` §3 and §4:
- `MARKET_CLOSED` and scheduled-close gap suppression are active **only** when `calendar.confirmed == True`.
- Unconfirmed calendars (`confirmed == False`) never emit `MARKET_CLOSED` or suppress gaps.
