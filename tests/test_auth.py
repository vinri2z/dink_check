import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from dink_check.auth import AuthSession, _first_str_nested, jwt_expiry


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

    def test_first_str_nested(self):
        data = {
            "data": {"access_token": "nested-token", "refreshToken": "rt-nested"},
        }
        self.assertEqual(
            _first_str_nested(data, "access_token", "accessToken", "token"),
            "nested-token",
        )
        self.assertEqual(
            _first_str_nested(data, "refresh_token", "refreshToken"),
            "rt-nested",
        )

    @patch("dink_check.auth.requests.post")
    def test_refresh_via_api_nested_response(self, mock_post: MagicMock):
        new_token = _make_jwt(exp=datetime.now() + timedelta(minutes=30))
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"data": {"access_token": new_token, "refresh_token": "rt-new"}},
        )
        session = AuthSession(
            access_token=_make_jwt(exp=datetime.now() - timedelta(minutes=5)),
            fingerprint="fp",
            refresh_token="rt-old",
            refresh_url="https://dink.social/api/example/refresh",
        )
        self.assertTrue(session._refresh_via_api())
        self.assertEqual(session.access_token, new_token)
        self.assertEqual(session.refresh_token, "rt-new")

    @patch("dink_check.auth.requests.post")
    def test_refresh_sends_refresh_token_with_bearer(self, mock_post: MagicMock):
        new_token = _make_jwt(exp=datetime.now() + timedelta(minutes=30))
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "accessToken": new_token,
                "refreshToken": "rt-new",
                "fingerprint": "fp-new",
            },
        )
        session = AuthSession(
            access_token=_make_jwt(exp=datetime.now() - timedelta(minutes=5)),
            fingerprint="fp",
            refresh_token="rt-old",
        )
        self.assertTrue(session._refresh_via_api())
        call = mock_post.call_args
        self.assertEqual(call.args[0], session.refresh_url)
        self.assertEqual(call.kwargs["json"], {"refreshToken": "rt-old"})
        self.assertIn("Authorization", call.kwargs["headers"])
        self.assertEqual(session.fingerprint, "fp-new")

    @patch("dink_check.auth.requests.post")
    def test_signin_with_email_password_no_bearer(self, mock_post: MagicMock):
        new_token = _make_jwt(exp=datetime.now() + timedelta(minutes=30))
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "accessToken": new_token,
                "refreshToken": "rt-new",
                "fingerprint": "fp-new",
            },
        )
        session = AuthSession(email="user@example.com", password="secret")
        self.assertTrue(session._refresh_via_api())
        call = mock_post.call_args
        self.assertEqual(call.args[0], session.signin_url)
        self.assertEqual(
            call.kwargs["json"], {"email": "user@example.com", "password": "secret"}
        )
        self.assertNotIn("Authorization", call.kwargs["headers"])
        self.assertNotIn("x-fingerprint", call.kwargs["headers"])
        self.assertEqual(session.access_token, new_token)
        self.assertEqual(session.refresh_token, "rt-new")

    @patch("dink_check.auth.requests.post")
    def test_refresh_falls_back_to_signin_on_failure(self, mock_post: MagicMock):
        new_token = _make_jwt(exp=datetime.now() + timedelta(minutes=30))
        bad = MagicMock(status_code=401)
        bad.raise_for_status.side_effect = requests.HTTPError("401")
        good = MagicMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"accessToken": new_token, "refreshToken": "rt-new"},
        )
        mock_post.side_effect = [bad, good]
        session = AuthSession(
            refresh_token="rt-old", email="user@example.com", password="secret"
        )
        self.assertTrue(session._refresh_via_api())
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_post.call_args_list[1].args[0], session.signin_url)

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

    @patch("dink_check.auth.requests.post")
    def test_probe_valid(self, mock_post: MagicMock):
        mock_post.return_value = MagicMock(status_code=200)
        session = AuthSession(
            access_token=_make_jwt(exp=datetime.now() + timedelta(minutes=10)),
            fingerprint="fp",
            push_token="push-token-value",
        )
        self.assertEqual(session.probe(), "valid")
        mock_post.assert_called_once_with(
            "https://dink.social/api/users/push-token",
            headers=session.headers() | {"content-type": "application/json"},
            json={"pushToken": "push-token-value"},
            timeout=15,
        )

    def test_probe_skipped_without_push_token(self):
        session = AuthSession(
            access_token=_make_jwt(exp=datetime.now() + timedelta(minutes=10)),
            fingerprint="fp",
        )
        self.assertEqual(session.probe(), "unknown")

    @patch("dink_check.auth.requests.post")
    def test_probe_unknown_on_network_error(self, mock_post: MagicMock):
        mock_post.side_effect = requests.RequestException("network down")
        session = AuthSession(
            access_token=_make_jwt(exp=datetime.now() + timedelta(minutes=10)),
            fingerprint="fp",
            push_token="push-token-value",
        )
        self.assertEqual(session.probe(), "unknown")
        self.assertTrue(session.keepalive())

    @patch("dink_check.auth.requests.post")
    def test_probe_invalid_triggers_refresh(self, mock_post: MagicMock):
        invalid_response = MagicMock(
            status_code=401,
            text='{"message":"INVALID_TOKEN"}',
            json=lambda: {"message": "INVALID_TOKEN"},
        )
        valid_response = MagicMock(status_code=200)
        mock_post.side_effect = [invalid_response, valid_response]

        session = AuthSession(
            access_token=_make_jwt(exp=datetime.now() + timedelta(minutes=10)),
            fingerprint="fp",
            push_token="push-token-value",
        )
        with patch.object(session, "refresh", return_value=True) as mock_refresh:
            self.assertTrue(session.keepalive())
            mock_refresh.assert_called_once()
        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
