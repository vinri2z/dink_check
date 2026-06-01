import base64
import json
import logging
from datetime import datetime, timedelta
from os import environ
from pathlib import Path
from typing import Any, Literal, Optional

import requests

DEFAULT_SESSION_FILE = Path(".dink_session.json")
DEFAULT_PROBE_URL = "https://dink.social/api/users/push-token"
DEFAULT_SIGNIN_URL = "https://dink.social/api/identity/auth/sign-in"
DEFAULT_REFRESH_URL = "https://dink.social/api/identity/auth/refresh"
REFRESH_THRESHOLD = timedelta(minutes=2)
AUTH_ERROR_MESSAGES = frozenset(
    {"INVALID_TOKEN", "INVALID_FINGERPRINT", "MISSING_TOKEN", "MISSING_FINGERPRINT"}
)
ProbeResult = Literal["valid", "invalid", "unknown"]
MOBILE_HEADERS: dict[str, str] = {
    "accept": "*/*",
    "accept-language": "es",
    "x-application": "mobile",
    "x-app-bundle-id": "it.dink.www",
    "x-platform": "ios",
    "origin": "capacitor://localhost",
    "user-agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
}


def jwt_expiry(token: str) -> Optional[datetime]:
    try:
        payload_segment = token.split(".")[1]
        payload_segment += "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        exp = payload.get("exp")
        return datetime.fromtimestamp(exp) if isinstance(exp, (int, float)) else None
    except (IndexError, ValueError, json.JSONDecodeError, OSError):
        return None


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_str_nested(data: dict[str, Any], *keys: str) -> str:
    """Return the first matching string from ``data`` or common nested wrappers."""
    direct = _first_str(data, *keys)
    if direct:
        return direct
    for wrapper in ("data", "tokens", "result", "auth", "session"):
        nested = data.get(wrapper)
        if isinstance(nested, dict):
            found = _first_str(nested, *keys)
            if found:
                return found
    return ""


def _api_message(response_body: Any, fallback: str = "") -> str:
    if isinstance(response_body, dict):
        message = response_body.get("message", fallback)
        return message if isinstance(message, str) else fallback
    return fallback


class AuthSession:
    """Mutable Dink credentials with optional API refresh and session-file reload."""

    def __init__(
        self,
        *,
        access_token: str = "",
        fingerprint: str = "",
        refresh_token: str = "",
        refresh_url: str = DEFAULT_REFRESH_URL,
        signin_url: str = DEFAULT_SIGNIN_URL,
        email: str = "",
        password: str = "",
        push_token: str = "",
        probe_url: str = DEFAULT_PROBE_URL,
        session_file: Path = DEFAULT_SESSION_FILE,
    ) -> None:
        self.access_token = access_token.strip()
        self.fingerprint = fingerprint.strip()
        self.refresh_token = refresh_token.strip()
        self.refresh_url = (refresh_url or DEFAULT_REFRESH_URL).strip()
        self.signin_url = (signin_url or DEFAULT_SIGNIN_URL).strip()
        self.email = email.strip()
        self.password = password.strip()
        self.push_token = push_token.strip()
        self.probe_url = (probe_url or DEFAULT_PROBE_URL).strip()
        self.session_file = session_file
        self._session_file_mtime: float = 0.0

    @classmethod
    def from_env(cls) -> "AuthSession":
        session_file = Path(
            (environ.get("DINK_SESSION_FILE") or str(DEFAULT_SESSION_FILE)).strip()
        )
        session = cls(
            access_token=(environ.get("BEARER_TOKEN") or "").strip(),
            fingerprint=(environ.get("FINGERPRINT") or "").strip(),
            refresh_token=(environ.get("REFRESH_TOKEN") or "").strip(),
            refresh_url=(environ.get("DINK_REFRESH_URL") or DEFAULT_REFRESH_URL).strip(),
            signin_url=(environ.get("DINK_SIGNIN_URL") or DEFAULT_SIGNIN_URL).strip(),
            email=(environ.get("DINK_EMAIL") or "").strip(),
            password=(environ.get("DINK_PASSWORD") or "").strip(),
            push_token=(environ.get("DINK_PUSH_TOKEN") or "").strip(),
            probe_url=(environ.get("DINK_PROBE_URL") or DEFAULT_PROBE_URL).strip(),
            session_file=session_file,
        )
        session.reload_session_file(force=True)
        return session

    @property
    def expires_at(self) -> Optional[datetime]:
        if not self.access_token:
            return None
        return jwt_expiry(self.access_token)

    def headers(self) -> dict[str, str]:
        headers = dict(MOBILE_HEADERS)
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if self.fingerprint:
            headers["x-fingerprint"] = self.fingerprint
        return headers

    def is_expired(self) -> bool:
        expires_at = self.expires_at
        return expires_at is not None and datetime.now() >= expires_at

    def is_expiring_soon(self, threshold: timedelta = REFRESH_THRESHOLD) -> bool:
        expires_at = self.expires_at
        if expires_at is None:
            return False
        return datetime.now() >= expires_at - threshold

    def has_bootstrap_credentials(self) -> bool:
        if self.access_token and self.fingerprint:
            return True
        if self.refresh_url and self.refresh_token:
            return True
        if self.signin_url and self.email and self.password:
            return True
        return self.session_file.exists()

    def validate(self) -> bool:
        ok = True
        if not self.has_bootstrap_credentials():
            logging.error(
                "Missing auth credentials. Set BEARER_TOKEN + FINGERPRINT, or configure "
                "DINK_REFRESH_URL with REFRESH_TOKEN (or DINK_EMAIL/DINK_PASSWORD), or run "
                "mitmweb with scripts/capture_dink_session.py."
            )
            ok = False

        if self.access_token and self.is_expired():
            logging.warning(
                "Access token expired at %s.",
                self.expires_at.strftime("%Y-%m-%d %H:%M:%S")
                if self.expires_at
                else "unknown",
            )
            if not self._can_refresh():
                logging.error(
                    "Configure refresh (DINK_REFRESH_URL + REFRESH_TOKEN) or keep the Dink "
                    "app open with mitmweb capturing to %s.",
                    self.session_file,
                )
                ok = False

        expires_at = self.expires_at
        if expires_at and not self.is_expired():
            remaining = expires_at - datetime.now()
            logging.info(
                "Access token valid for %dm %ds (expires %s).",
                int(remaining.total_seconds()) // 60,
                int(remaining.total_seconds()) % 60,
                expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
        return ok

    def ensure_valid(self) -> bool:
        if not self.is_expiring_soon() and not self.is_expired():
            return bool(self.access_token and self.fingerprint)
        return self.refresh()

    def refresh(self) -> bool:
        if self._refresh_via_api():
            return True
        if self.reload_session_file(force=True):
            if self.access_token and self.fingerprint and not self.is_expired():
                logging.info("Loaded fresh credentials from %s.", self.session_file)
                return True
        logging.error(
            "Token refresh failed. Update .env, configure DINK_REFRESH_URL, or capture a "
            "new session via mitmweb into %s.",
            self.session_file,
        )
        return False

    def probe(self) -> ProbeResult:
        if not self.push_token or not self.probe_url:
            return "unknown"
        if not self.access_token or not self.fingerprint:
            return "unknown"

        headers = self.headers()
        headers["content-type"] = "application/json"
        try:
            response = requests.post(
                self.probe_url,
                headers=headers,
                json={"pushToken": self.push_token},
                timeout=15,
            )
        except requests.RequestException as error:
            logging.warning("Keepalive probe to %s failed: %s", self.probe_url, error)
            return "unknown"

        if 200 <= response.status_code < 300:
            return "valid"

        try:
            response_body = response.json()
        except ValueError:
            response_body = {}

        message = _api_message(response_body, response.text)
        if response.status_code == 401 or message in AUTH_ERROR_MESSAGES:
            logging.warning(
                "Keepalive probe rejected credentials (%s): %s",
                response.status_code,
                message or response.text[:200],
            )
            return "invalid"

        logging.warning(
            "Keepalive probe returned unexpected status %s: %s",
            response.status_code,
            message or response.text[:200],
        )
        return "unknown"

    def keepalive(self) -> bool:
        result = self.probe()
        if result == "valid":
            return True
        if result == "unknown":
            return True

        logging.warning("Credentials rejected by keepalive probe; attempting refresh.")
        if not self.refresh():
            return False
        return self.probe() == "valid"

    def reload_session_file(self, *, force: bool = False) -> bool:
        if not self.session_file.exists():
            return False

        mtime = self.session_file.stat().st_mtime
        if not force and mtime <= self._session_file_mtime:
            return False

        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logging.warning("Could not read %s: %s", self.session_file, error)
            return False

        if not isinstance(data, dict):
            return False

        candidate_token = _first_str(data, "access_token", "bearer_token", "token")
        candidate_fingerprint = _first_str(data, "fingerprint", "x_fingerprint")
        candidate_refresh = _first_str(data, "refresh_token", "refreshToken")
        candidate_refresh_url = _first_str(data, "refresh_url")

        if not candidate_token:
            return False

        current_exp = jwt_expiry(self.access_token) if self.access_token else None
        candidate_exp = jwt_expiry(candidate_token)
        if (
            not force
            and current_exp
            and candidate_exp
            and candidate_exp <= current_exp
        ):
            self._session_file_mtime = mtime
            return False

        self.access_token = candidate_token
        if candidate_fingerprint:
            self.fingerprint = candidate_fingerprint
        if candidate_refresh:
            self.refresh_token = candidate_refresh
        if candidate_refresh_url and not self.refresh_url:
            self.refresh_url = candidate_refresh_url
        self._session_file_mtime = mtime
        self.persist_session_file()
        return True

    def persist_session_file(self) -> None:
        payload: dict[str, str] = {
            "access_token": self.access_token,
            "fingerprint": self.fingerprint,
            "refresh_token": self.refresh_token,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        if self.refresh_url:
            payload["refresh_url"] = self.refresh_url
        try:
            self.session_file.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            self._session_file_mtime = self.session_file.stat().st_mtime
        except OSError as error:
            logging.warning("Could not write %s: %s", self.session_file, error)

    def _can_refresh(self) -> bool:
        if self.refresh_url and self.refresh_token:
            return True
        if self.signin_url and self.email and self.password:
            return True
        return self.session_file.exists()

    def _refresh_via_api(self) -> bool:
        """Renew credentials against the Dink identity API.

        Prefers the rotating refresh-token flow
        (``POST /api/identity/auth/refresh`` with ``{"refreshToken": ...}``),
        then falls back to an email/password sign-in
        (``POST /api/identity/auth/sign-in`` with ``{"email", "password"}``).
        Both responses carry ``accessToken``, ``refreshToken`` and
        ``fingerprint``.
        """
        if self.refresh_url and self.refresh_token:
            if self._post_auth(
                self.refresh_url,
                {"refreshToken": self.refresh_token},
                use_bearer=True,
                what="refresh",
            ):
                return True
            logging.info("Refresh token rejected; trying email/password sign-in.")

        if self.signin_url and self.email and self.password:
            return self._post_auth(
                self.signin_url,
                {"email": self.email, "password": self.password},
                use_bearer=False,
                what="sign-in",
            )
        return False

    def _post_auth(
        self, url: str, body: dict[str, str], *, use_bearer: bool, what: str
    ) -> bool:
        headers = dict(MOBILE_HEADERS)
        headers["content-type"] = "application/json"
        if use_bearer:
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            if self.fingerprint:
                headers["x-fingerprint"] = self.fingerprint
        try:
            response = requests.post(url, headers=headers, json=body, timeout=15)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            logging.warning("%s request to %s failed: %s", what, url, error)
            return False
        return self._apply_token_response(data, what)

    def _apply_token_response(self, data: Any, what: str) -> bool:
        if not isinstance(data, dict):
            logging.warning("%s response was not a JSON object.", what)
            return False

        new_token = _first_str_nested(data, "accessToken", "access_token", "token")
        if not new_token:
            logging.warning("%s response did not include an access token.", what)
            return False

        self.access_token = new_token
        new_fingerprint = _first_str_nested(data, "fingerprint", "x_fingerprint")
        if new_fingerprint:
            self.fingerprint = new_fingerprint
        new_refresh = _first_str_nested(data, "refreshToken", "refresh_token")
        if new_refresh:
            self.refresh_token = new_refresh

        self.persist_session_file()
        expires_at = self.expires_at
        logging.info(
            "Renewed access token via %s (expires %s).",
            what,
            expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else "unknown",
        )
        return True
