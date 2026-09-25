import pytest
from fastapi.testclient import TestClient

from jotpy.app import create_app


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def app(data_dir):
    return create_app(data_dir)


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def setup_owner(test_client, password="password1"):
    created = test_client.post(
        "/api/auth/setup",
        json={"password": password, "confirmPassword": password},
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    accepted = test_client.post("/api/auth/token", json={"token": token})
    assert accepted.status_code == 200, accepted.text
    return token
