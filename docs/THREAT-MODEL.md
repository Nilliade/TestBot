# THREAT-MODEL — Stage 1 (local read-only platform)

> Step 1.1 scope: this document records the threat model and the
> **planned** controls. Security implementation (bootstrap, session, CSRF,
> Host/Origin, CSP, launcher) is Step 1.3 and is marked `NOT IMPLEMENTED`
> until then. No trading keys exist in Stage 1.

## 1. Assets & trust boundaries

- **Assets:** local market data (SQLite + Parquet), configs, logs, bootstrap
  secret (short-lived), session cookie, CSRF token (in-memory only).
- **Trust boundaries:** browser ↔ backend (loopback only), launcher ↔ backend
  (local OS channel), backend ↔ external feeds (DNS/TLS/WS/REST), MT5
  subprocess ↔ backend (bounded queue + timeouts).
- **Out of scope (Stage 1):** trading keys, order execution, strategy plugins,
  cloud anchoring of journal hashes (future stages).

## 2. Network binding & access matrix (planned, Step 1.3)

- Bind strictly to `127.0.0.1` (never `0.0.0.0` by default).
- Exact `Host` match including port (no "contains 127.0.0.1" checks);
  reject duplicate/ambiguous `Host` within server capabilities.
- Static bootstrap-shell + local JS/CSS: exact `Host` match only.
- `POST /bootstrap`: no session, but exact `Host` + `Origin`,
  `Content-Type: application/json`, body size limit, rate-limit, valid
  one-time `bootstrap_secret` (atomic redemption).
- CSRF-token endpoint: valid session + exact `Host` + `Origin`.
- Market data, settings, diagnostics, all other GET APIs: valid session +
  exact `Host`; same-origin GET without `Origin` is allowed, but if `Origin`
  is present it must exactly match the allowed origin.
- WebSocket: valid session + exact `Host` + `Origin` at handshake
  (no custom headers required from browser `WebSocket` API).
- Mutating HTTP APIs (`POST/PUT/PATCH/DELETE` except `/bootstrap`):
  valid session + exact `Host` + `Origin` + valid CSRF token.
- `CORS` closed by default. Fetch Metadata headers are hardening only,
  never the sole mechanism.

## 3. Bootstrap & secrets (planned, Step 1.3)

- On backend start, create a cryptographically strong one-time
  `bootstrap_secret` with short TTL (e.g. 60 s) in a file created atomically
  with current-OS-user-only permissions. On Windows, ACL is explicitly
  verified (no reliance on POSIX `chmod`); if permissions cannot be
  restricted, secure mode refuses to start with a clear error.
- Secret is allowed **only** in the fragment of a one-time local URL
  (`#...`) opened by the trusted `launcher`. Never in query string,
  HTTP logs, app logs, diagnostic export or static files.
- Bootstrap-shell loads only the minimal local external script needed to
  handle the fragment. That script immediately removes the fragment via
  `history.replaceState` before loading the main app, other resources or
  sending other requests, then sends the code in the JSON body of
  `POST /bootstrap`. All resources local; shell CSP without
  `unsafe-inline`/`unsafe-eval`.
- If the secret expired or was redeemed (including first launch on a slow
  machine), GUI shows a clear error and offers relaunch via `launcher`;
  issuing a new code works on first launch and on GUI reopen.
- **Residual risk:** fragment exposure via browser history, OS or same-user
  processes is documented here; it cannot be fully eliminated on a local
  machine and is accepted with short TTL + one-time use.

## 4. Session, CSRF, headers, XSS (planned, Step 1.3)

- Session token only in `HttpOnly; Path=/; SameSite=Strict` cookie
  (no `Domain` where possible; `Secure` on plain `http://127.0.0.1` is
  verified and documented on the target config, not promised).
- Separate CSRF token returned in the response body to the authorized
  frontend, kept only in page memory, sent in a header on mutating
  requests, verified server-side (does not replace `Origin` checks).
- Page reload / multiple tabs: protected CSRF refresh endpoint for a valid
  session (requires session cookie + exact `Host` + `Origin`). Policy for
  multiple tabs and token rotation is fixed and tested; reload must not
  leave the authorized UI broken.
- Production CSP without `unsafe-inline`/`unsafe-eval` (unless documented
  technical reason), `frame-ancestors 'none'`, no `innerHTML` insertion of
  unchecked provider strings.

## 5. Launcher lifecycle (planned, Step 1.3)

- Launcher discovers an already-running trusted backend via a local
  mechanism and **does not** start a second writer on the same storage.
- Issuing a new one-time bootstrap code (first launch after timeout or GUI
  reopen) uses a local channel available only to the current OS user, or a
  documented backend-restart procedure. **No new bootstrap code via an
  unauthenticated network endpoint.**
- Closing the GUI does not stop the backend; a separate launcher/CLI command
  stops it. Behavior on terminal close and OS reboot is documented
  (no autostart without user decision).

## 6. Data integrity & local journal (future stages)

- Stage 1: SQLite + Parquet manifest/reconciliation (§5 of SPEC).
- Future paper-trading journal (Stage 4): append-only with hash chain,
  integrity checks, backups and export — explicitly documented as **not**
  absolutely immutable and not guaranteeing detection of full substitution
  if the attacker controls the machine (external hash anchoring optional,
  not a mandatory cloud dependency).

## 7. Residual risks accepted for Stage 1

- Same-user malware/processes can read local files and memory; OS-level
  isolation is out of scope for Stage 1.
- Browser history/OS may retain the bootstrap fragment; mitigated by TTL +
  one-time use + immediate `replaceState`.
- External feeds (OKX, MT5 broker) are untrusted data; provider strings are
  never rendered as HTML.
- NTP/clock skew is best-effort; errors are reported, never masked.
