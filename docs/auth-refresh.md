# Dink auth refresh (recon notes)

## JWT access token

Captured tokens are RS256 JWTs issued by `dink` with ~30 minute lifetime (`exp - iat ≈ 1800s`).

Example payload fields:

- `sub` — user id
- `fgp` — SHA-256 hash of the raw `x-fingerprint` header (not the header value itself)
- `aud` — `mobile-app`
- `iss` — `dink`

Auth for booking APIs requires **both**:

- `Authorization: Bearer <jwt>`
- `x-fingerprint: <64-char hex>` (raw value from the same app request as the token)

## Refresh endpoint recon (automated probing)

Common paths were probed on `https://dink.social` (`/api/auth/refresh`, `/api/v2/auth/*`, `/api/oauth/token`, etc.). All returned **404**.

No public OpenID or mobile config endpoint was found. The login/refresh flow used by the iOS app must be captured with **mitmweb**.

## How to discover the real refresh/login call

1. Run `mitmweb` and proxy the phone.
2. Filter flows to `dink.social`.
3. Trigger token renewal:
   - Kill the app, reopen after the JWT expires, log in again.
   - Background the app 15+ minutes, foreground and open reservations.
4. Look for `POST` requests whose response contains `token`, `access_token`, or `refresh_token`.
5. Copy into `.env`:
   - `DINK_REFRESH_URL` — full URL
   - `REFRESH_TOKEN` — if the login response includes one
   - Or `DINK_EMAIL` / `DINK_PASSWORD` if the endpoint is a credential login

Then set `DINK_REFRESH_BODY_STYLE` if needed (`refresh_token`, `refreshToken`, or `login`).

## Session file fallback (no refresh API)

If no reusable refresh endpoint exists, run the mitmproxy addon while the Dink app is open on your phone:

```bash
mitmweb -s scripts/capture_dink_session.py
```

It writes `.dink_session.json` whenever the app calls `dink.social` with auth headers. The bot reloads that file when the token is expiring or after a `401 INVALID_TOKEN`.
