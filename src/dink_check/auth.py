import base64
import json
import logging
from datetime import datetime, timedelta
from os import environ
from pathlib import Path
from typing import Any, Optional

import requests

DEFAULT_SESSION_FILE = Path(".dink_session.json")
REFRESH_THRESHOLD = timedelta(minutes=2)
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


class AuthSession:
    """Mutable Dink credentials with optional API refresh and session-file reload."""

    def __init__(
        self,
        *,
        access_token: str = "",
        fingerprint: str = "",
        refresh_token: str = "",
        refresh_url: str = "",
        refresh_body_style: str = "",
        email: str = "",
        password: str = "",
        session_file: Path = DEFAULT_SESSION_FILE,
    ) -> None:
        self.access_token = access_token.strip()
        self.fingerprint = fingerprint.strip()
        self.refresh_token = refresh_token.strip()
        self.refresh_url = refresh_url.strip()
        self.refresh_body_style = (refresh_body_style or "refresh_token").strip()
        self.email = email.strip()
        self.password = password.strip()
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
            refresh_url=(environ.get("DINK_REFRESH_URL") or "").strip(),
            refresh_body_style=(environ.get("DINK_REFRESH_BODY_STYLE") or "refresh_token").strip(),
            email=(environ.get("DINK_EMAIL") or "").strip(),
            password=(environ.get("DINK_PASSWORD") or "").strip(),
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
        if self.refresh_url and (self.refresh_token or (self.email and self.password)):
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
        self._session_file_mtime = mtime
        self.persist_session_file()
        return True

    def persist_session_file(self) -> None:
        payload = {
            "access_token": self.access_token,
            "fingerprint": self.fingerprint,
            "refresh_token": self.refresh_token,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self.session_file.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            self._session_file_mtime = self.session_file.stat().st_mtime
        except OSError as error:
            logging.warning("Could not write %s: %s", self.session_file, error)

    def _can_refresh(self) -> bool:
        return bool(self.refresh_url and (self.refresh_token or (self.email and self.password))) or self.session_file.exists()

    def _refresh_via_api(self) -> bool:
        if not self.refresh_url:
            return False
        if not self.refresh_token and not (self.email and self.password):
            return False

        headers = self.headers()
        headers["content-type"] = "application/json"
        body = self._build_refresh_body()
        try:
            response = requests.post(
                self.refresh_url, headers=headers, json=body, timeout=15
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as error:
            logging.warning("Refresh request to %s failed: %s", self.refresh_url, error)
            return False

        if not isinstance(data, dict):
            logging.warning("Refresh response was not a JSON object.")
            return False

        new_token = _first_str(data, "access_token", "accessToken", "token")
        if not new_token:
            logging.warning("Refresh response did not include an access token.")
            return False

        self.access_token = new_token
        new_fingerprint = _first_str(data, "fingerprint", "x_fingerprint")
        if new_fingerprint:
            self.fingerprint = new_fingerprint
        new_refresh = _first_str(data, "refresh_token", "refreshToken")
        if new_refresh:
            self.refresh_token = new_refresh

        self.persist_session_file()
        expires_at = self.expires_at
        logging.info(
            "Refreshed access token via API (expires %s).",
            expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else "unknown",
        )
        return True

    def _build_refresh_body(self) -> dict[str, str]:
        if self.refresh_body_style == "login":
            return {"email": self.email, "password": self.password}
        if self.refresh_body_style == "refreshToken":
            return {"refreshToken": self.refresh_token}
        if self.refresh_body_style == "refresh_token":
            return {"refresh_token": self.refresh_token, "grant_type": "refresh_token"}
        return {"refresh_token": self.refresh_token}
