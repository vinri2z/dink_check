"""mitmproxy addon: capture Dink bearer token + fingerprint into .dink_session.json.

Usage:
    mitmweb -s scripts/capture_dink_session.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

SESSION_FILE = Path(".dink_session.json")


def _extract_bearer(authorization: str) -> str:
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return ""


def response(flow) -> None:
    if "dink.social" not in flow.request.pretty_host:
        return

    authorization = flow.request.headers.get("authorization", "")
    fingerprint = flow.request.headers.get("x-fingerprint", "")
    access_token = _extract_bearer(authorization)
    if not access_token or not fingerprint:
        return

    payload = {
        "access_token": access_token,
        "fingerprint": fingerprint,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    SESSION_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    flow.comment = "saved dink session"
