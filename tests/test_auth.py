import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from dink_check.auth import AuthSession, jwt_expiry


def _make_jwt(*, exp: datetime) -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(exp.timestamp()), "iss": "dink"}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


class AuthSessionTests(unittest.TestCase):
    def test_jwt_expiry(self):
        expires = datetime.now() + timedelta(minutes=10)
        token = _make_jwt(exp=expires)
        parsed = jwt_expiry(token)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(int(parsed.timestamp()), int(expires.timestamp()))

    def test_is_expiring_soon(self):
        session = AuthSession(
            access_token=_make_jwt(exp=datetime.now() + timedelta(minutes=1)),
            fingerprint="abc",
        )
        self.assertTrue(session.is_expiring_soon(timedelta(minutes=2)))
        self.assertFalse(session.is_expiring_soon(timedelta(seconds=30)))

    def test_reload_session_file_prefers_newer_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_file = Path(tmp) / ".dink_session.json"
            old_token = _make_jwt(exp=datetime.now() + timedelta(minutes=5))
            new_token = _make_jwt(exp=datetime.now() + timedelta(minutes=20))
            session = AuthSession(
                access_token=old_token,
                fingerprint="old-fp",
                session_file=session_file,
            )
            session_file.write_text(
                json.dumps(
                    {
                        "access_token": new_token,
                        "fingerprint": "new-fp",
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(session.reload_session_file(force=True))
            self.assertEqual(session.access_token, new_token)
            self.assertEqual(session.fingerprint, "new-fp")

    @patch("dink_check.auth.requests.post")
    def test_refresh_via_api(self, mock_post: MagicMock):
        new_token = _make_jwt(exp=datetime.now() + timedelta(minutes=30))
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "access_token": new_token,
                "refresh_token": "rt-new",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            session_file = Path(tmp) / ".dink_session.json"
            session = AuthSession(
                access_token=_make_jwt(exp=datetime.now() + timedelta(minutes=1)),
                fingerprint="fp",
                refresh_token="rt-old",
                refresh_url="https://dink.social/api/example/refresh",
                session_file=session_file,
            )
            self.assertTrue(session.refresh())
            self.assertEqual(session.access_token, new_token)
            self.assertEqual(session.refresh_token, "rt-new")
            self.assertTrue(session_file.exists())


if __name__ == "__main__":
    unittest.main()
