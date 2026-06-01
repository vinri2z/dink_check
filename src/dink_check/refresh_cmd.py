"""CLI to verify DINK_REFRESH_URL without running the booking loop."""

from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

from .auth import AuthSession

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _format_expiry(auth: AuthSession) -> str:
    expires_at = auth.expires_at
    if expires_at is None:
        return "unknown"
    return expires_at.strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    auth = AuthSession.from_env()

    if auth.access_token:
        logging.info("Current access token expires at %s.", _format_expiry(auth))
    else:
        logging.warning("No access token loaded (BEARER_TOKEN or session file).")

    if not auth.refresh_url:
        logging.error(
            "DINK_REFRESH_URL is not set. Capture the app refresh/login POST via "
            "mitmweb — see docs/auth-refresh.md."
        )
        sys.exit(1)

    if not auth.refresh_token and not (auth.email and auth.password):
        logging.error(
            "Set REFRESH_TOKEN or DINK_EMAIL/DINK_PASSWORD for refresh at %s.",
            auth.refresh_url,
        )
        sys.exit(1)

    logging.info(
        "Renewing via refresh=%s / sign-in=%s.",
        auth.refresh_url,
        auth.signin_url,
    )

    if not auth._refresh_via_api():
        logging.error(
            "API renew failed. Check DINK_REFRESH_URL/REFRESH_TOKEN or "
            "DINK_SIGNIN_URL/DINK_EMAIL/DINK_PASSWORD."
        )
        sys.exit(1)

    logging.info("New access token expires at %s.", _format_expiry(auth))
    if auth.refresh_token:
        logging.info("Refresh token present (%d chars).", len(auth.refresh_token))
    if auth.fingerprint:
        logging.info("Fingerprint present (%d chars).", len(auth.fingerprint))

    probe_result = auth.probe()
    logging.info("Keepalive probe result: %s.", probe_result)

    if probe_result == "invalid":
        logging.error(
            "Server rejected refreshed credentials. Check fingerprint pairing or "
            "DINK_REFRESH_AUTH (try bearer vs none)."
        )
        sys.exit(1)

    if probe_result == "unknown":
        logging.warning(
            "Probe skipped or inconclusive (set DINK_PUSH_TOKEN to verify acceptance)."
        )

    logging.info("Refresh verification succeeded.")
    sys.exit(0)


if __name__ == "__main__":
    main()
