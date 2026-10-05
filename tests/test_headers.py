import base64
import hashlib
import re

from tests.conftest import setup_owner


def test_pages_send_security_headers(client):
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    assert "'unsafe-inline'" not in policy.split("script-src", 1)[1].split(";", 1)[0]


def test_policy_allows_every_inline_script(client):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    for path in ("/", f"/notes/{note_id}"):
        response = client.get(path)
        assert response.status_code == 200, path
        policy = response.headers["content-security-policy"]
        inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", response.text, flags=re.DOTALL)
        assert inline, path
        for body in inline:
            digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
            assert f"'sha256-{digest}'" in policy, (path, body[:60])
