# Dink auth refresh (recon notes)

## JWT access token

Captured tokens are RS256 JWTs issued by `dink` with ~30 minute lifetime (`exp - iat ≈ 1800s`).

Example payload fields:

- `sub` — user id
- `fgp` — fingerprint claim embedded in the JWT; **not** a plain `SHA256(x-fingerprint)` of the header value (the transform is unknown), so `x-fingerprint` must be captured from the app, not synthesized
- `aud` — `mobile-app`
- `iss` — `dink`

Auth for booking APIs requires **both**:

- `Authorization: Bearer <jwt>`
- `x-fingerprint: <64-char hex>` — **returned by the server** in the sign-in /
  refresh response (it is not synthesized client-side; the bot just echoes the
  value it was given back on every request).

## Confirmed auth flow (captured via mitmweb)

The iOS app uses Dink's own identity API — **not** Firebase. Two endpoints
matter:

### Sign-in (email + password)

```
POST https://dink.social/api/identity/auth/sign-in
Content-Type: application/json
(no Authorization, no x-fingerprint)

{"email": "<email>", "password": "<password>"}
```

Response:

```json
{"accessToken": "<jwt>", "refreshToken": "<jwt>", "fingerprint": "<64hex>", "clientType": "mobile"}
```

### Refresh (rotating refresh token)

```
POST https://dink.social/api/identity/auth/refresh
Content-Type: application/json
Authorization: Bearer <current jwt>   # may be expired; refreshToken is what counts
x-fingerprint: <64hex>

{"refreshToken": "<refresh jwt>"}
```

Response is the same shape as sign-in (a **new** `refreshToken` each time — the
token rotates, so always persist the latest one).

`/api/identity/auth/sign-out` revokes a refresh token (`{"refreshToken": ...}`).

### How the bot keeps the session alive

`AuthSession.refresh()` (in `src/dink_check/auth.py`):

1. If a `REFRESH_TOKEN` is known → `POST /api/identity/auth/refresh`.
2. On failure (or no refresh token) → `POST /api/identity/auth/sign-in` with
   `DINK_EMAIL` / `DINK_PASSWORD`.

So with just email + password the bot bootstraps and self-heals indefinitely;
no periodic mitmweb capture is needed.

### Configure `.env`

```env
DINK_EMAIL=you@example.com
DINK_PASSWORD=...
# endpoints default to the values below — override only if Dink changes them
DINK_SIGNIN_URL=https://dink.social/api/identity/auth/sign-in
DINK_REFRESH_URL=https://dink.social/api/identity/auth/refresh
```

### Verify refresh without the booking loop

```bash
uv run dink-check-refresh
```

The command logs current/new token expiry, renews via refresh→sign-in, then
runs the keepalive probe (`DINK_PUSH_TOKEN` required for a definitive
`valid`/`invalid` result).

## Session file fallback (no refresh API)

If no reusable refresh endpoint exists, run the mitmproxy addon while the Dink app is open on your phone:

```bash
mitmweb -s scripts/capture_dink_session.py
```

It writes `.dink_session.json` whenever the app calls `dink.social` with auth headers. The bot reloads that file when the token is expiring or after a `401 INVALID_TOKEN`.

## Keepalive / validity probe

`POST /api/users/push-token` registers the device's FCM push token. It is **not** an auth endpoint — it requires a valid `Authorization: Bearer` + `x-fingerprint` and does not return new tokens. The bot uses it as a lightweight server-side validity check.

Configure in `.env`:

- `DINK_PUSH_TOKEN` — the `pushToken` value from a captured app request (body field)
- `DINK_PROBE_URL` — defaults to `https://dink.social/api/users/push-token`
- `DINK_KEEPALIVE_INTERVAL` — seconds between probes during idle waits (default `300`)

At startup the bot probes once; if the server rejects credentials it attempts refresh (session file / `DINK_REFRESH_URL`) before entering the booking loop. During long idle waits it re-probes periodically so server-side revocation is caught before the next availability poll.
