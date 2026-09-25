import json

from fastapi.testclient import TestClient

from jotpy.app import create_app
from jotpy.auth import OWNER_COOKIE_MAX_AGE, hash_secret
from tests.conftest import setup_owner

SCRYPT_SALT = "0123456789abcdef0123456789abcdef"
SCRYPT_PASSWORD = "jotpy-test-password"
SCRYPT_HASH = (
    "5dfb19f8eedaeb1ac7eea0bc8c28eda5470940a25d77216150fb940b5a607455"
    "97d0364d6096157d4f5dcc8f37f7b2a4578b72c304d1523c365df31006a02693"
)


def test_scrypt_matches_node_vector():
    assert hash_secret(SCRYPT_PASSWORD, SCRYPT_SALT) == SCRYPT_HASH


def test_setup_login_and_api_key(client, app):
    short = client.post("/api/auth/setup", json={"password": "short", "confirmPassword": "short"})
    assert short.status_code == 400
    assert short.json()["error"] == "Use at least 8 characters."

    mismatch = client.post(
        "/api/auth/setup",
        json={"password": "password1", "confirmPassword": "password2"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"] == "Passwords do not match."

    token = setup_owner(client)
    assert client.cookies.get("md_owner_session") == token
    cookie = client.cookies.get("md_owner_session")
    assert cookie
    set_cookie = client.post("/api/auth/token", json={"token": token})
    assert f"Max-Age={OWNER_COOKIE_MAX_AGE}" in set_cookie.headers["set-cookie"]

    again = client.post(
        "/api/auth/setup",
        json={"password": "password1", "confirmPassword": "password1"},
    )
    assert again.status_code == 400

    outsider = TestClient(app, follow_redirects=False)
    wrong = outsider.post("/api/auth/login", json={"password": "not-the-password"})
    assert wrong.status_code == 401
    assert wrong.json()["error"] == "Wrong password."

    logged_in = outsider.post("/api/auth/login", json={"password": "password1"})
    assert logged_in.status_code == 200
    assert logged_in.json()["ownerLocalStorageTokenKey"] == "md_owner_token"

    created = client.post("/api/keys", json={"label": "cli"})
    assert created.status_code == 200
    key = created.json()["key"]
    with TestClient(app, follow_redirects=False) as keyed:
        denied = keyed.get("/api/notes")
        assert denied.status_code == 401
        allowed = keyed.get("/api/notes", headers={"Authorization": f"Bearer {key}"})
        assert allowed.status_code == 200
        assert allowed.json()["ok"] is True


def test_existing_node_hash_logs_in(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "auth.json").write_text(
        json.dumps(
            {
                "passwordSalt": SCRYPT_SALT,
                "passwordHash": SCRYPT_HASH,
                "tokens": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    app = create_app(data)
    with TestClient(app, follow_redirects=False) as test_client:
        ok = test_client.post("/api/auth/login", json={"password": SCRYPT_PASSWORD})
        assert ok.status_code == 200
        bad = test_client.post("/api/auth/login", json={"password": "other-password"})
        assert bad.status_code == 401
