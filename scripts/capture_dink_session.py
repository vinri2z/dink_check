"""mitmproxy addon: capture the full Dink auth flow.

Watches the Dink API plus the Firebase Identity Toolkit / SecureToken hosts the
iOS app uses for email/password login, so we can learn the exact
login + token-refresh contract.

Usage:
    mitmweb -s scripts/capture_dink_session.py

Outputs:
    .dink_session.json       latest dink bearer token + fingerprint (as before)
    .dink_auth_capture.jsonl  full request/response of every auth-relevant flow
                              (local, git-ignored — used to build the refresher)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

SESSION_FILE = Path(".dink_session.json")
CAPTURE_FILE = Path(".dink_auth_capture.jsonl")

TOKEN_KEYS = ("access_token", "accessToken", "token", "bearer_token")
REFRESH_KEYS = ("refresh_token", "refreshToken")
FINGERPRINT_KEYS = ("fingerprint", "x_fingerprint")

AUTH_HOSTS = (
    "dink.social",
    "identitytoolkit.googleapis.com",
    "securetoken.googleapis.com",
)


def _extract_bearer(authorization: str) -> str:
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return ""


def _first_str(data: dict, *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_str_nested(data: dict, *keys: str) -> str:
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


def _parse_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _response_tokens(data) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    tokens: dict[str, str] = {}
    access = _first_str_nested(data, *TOKEN_KEYS)
    if access:
        tokens["access_token"] = access
    refresh = _first_str_nested(data, *REFRESH_KEYS)
    if refresh:
        tokens["refresh_token"] = refresh
    fingerprint = _first_str_nested(data, *FINGERPRINT_KEYS)
    if fingerprint:
        tokens["fingerprint"] = fingerprint
    return tokens


def _write_session(payload: dict) -> None:
    SESSION_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _append_capture(record: dict) -> None:
    with CAPTURE_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _is_auth_host(host: str) -> bool:
    return any(h in host for h in AUTH_HOSTS)


def response(flow) -> None:
    host = flow.request.pretty_host
    if not _is_auth_host(host):
        return

    req_text = flow.request.get_text(strict=False) or ""
    resp_text = ""
    status = None
    if flow.response is not None:
        resp_text = flow.response.get_text(strict=False) or ""
        status = flow.response.status_code

    req_json = _parse_json(req_text)
    resp_json = _parse_json(resp_text)

    # Full capture (real values) -> local git-ignored file for building refresher.
    _append_capture(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "host": host,
            "path": flow.request.path,
            "req_headers": dict(flow.request.headers),
            "req_body": req_json if req_json is not None else req_text[:2000],
            "status": status,
            "resp_headers": dict(flow.response.headers) if flow.response else {},
            "resp_body": resp_json if resp_json is not None else resp_text[:2000],
        }
    )

    # Maintain .dink_session.json for the bot (dink.social tokens only).
    if "dink.social" not in host:
        return

    authorization = flow.request.headers.get("authorization", "")
    fingerprint = flow.request.headers.get("x-fingerprint", "")
    access_token = _extract_bearer(authorization)
    response_tokens = _response_tokens(resp_json)

    if not access_token and not response_tokens.get("access_token"):
        return

    payload: dict[str, str] = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if access_token:
        payload["access_token"] = access_token
    elif response_tokens.get("access_token"):
        payload["access_token"] = response_tokens["access_token"]

    if fingerprint:
        payload["fingerprint"] = fingerprint
    elif response_tokens.get("fingerprint"):
        payload["fingerprint"] = response_tokens["fingerprint"]

    if response_tokens.get("refresh_token"):
        payload["refresh_token"] = response_tokens["refresh_token"]

    if (
        flow.request.method == "POST"
        and response_tokens.get("access_token")
        and "dink.social" in flow.request.pretty_url
    ):
        payload["refresh_url"] = flow.request.pretty_url

    _write_session(payload)
    flow.comment = "saved dink session"
