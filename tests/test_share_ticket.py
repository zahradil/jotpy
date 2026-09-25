import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jotpy.app import create_app
from jotpy.tickets import PERMANENT_DAY, day_number, sign_ticket_fields, ticket_for, today_utc
from tests.conftest import setup_owner

NOT_FOUND = "Shared note not found."


def _decode(token: str) -> bytes:
    pad = "=" * ((4 - len(token) % 4) % 4)
    return base64.urlsafe_b64decode(token + pad)


def _flip_mac(token: str) -> str:
    raw = bytearray(_decode(token))
    raw[-1] ^= 0x01
    return base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")


def _with_access_code(token: str, key: bytes, code: int) -> str:
    value = int.from_bytes(_decode(token)[:6], "big")
    value = (value & ~(0b11 << 20)) | (code << 20)
    return sign_ticket_fields(key, value.to_bytes(6, "big"))


def _meta(data_dir, note_id: str) -> dict:
    return json.loads((data_dir / "notes" / f"{note_id}.json").read_text(encoding="utf-8"))


def _assert_missing(response):
    assert response.status_code == 404
    assert response.json()["error"] == NOT_FOUND


def test_same_content_same_ticket_and_bit_flip():
    key = bytes(range(32))
    token = ticket_for(key, "a0b1c", "comment", 9, 400)
    assert token == ticket_for(key, "a0b1c", "comment", 9, 400)
    assert token is not None and len(token) == 14
    changed = ticket_for(key, "a0b1c", "comment", 9, 401)
    assert changed != token

    raw = _decode(token)
    assert len(raw) == 10
    value = int.from_bytes(raw[:6], "big")
    assert value & 0xFFF == 400
    assert (value >> 12) & 0xFF == 9
    assert (value >> 20) & 0x3 == 1
    note_bits = value >> 22
    chars = []
    for _ in range(5):
        note_bits, index = divmod(note_bits, 36)
        chars.append("0123456789abcdefghijklmnopqrstuvwxyz"[index])
    assert "".join(reversed(chars)) == "a0b1c"
    assert note_bits == 0
    assert raw[6:] == hmac.new(key, raw[:6], hashlib.sha256).digest()[:4]
    assert day_number(datetime(2037, 3, 19, tzinfo=timezone.utc)) == 4095
    moment = datetime(2026, 9, 25, 23, 0, tzinfo=timezone.utc)
    assert day_number(moment + timedelta(hours=24)) == day_number(datetime(2026, 9, 26, tzinfo=timezone.utc))


def test_invalid_tickets_are_the_same_404(client, app, data_dir):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    key = (data_dir / "link.key").read_bytes()
    anon = TestClient(app, follow_redirects=False)
    today = today_utc()
    assert today >= 1

    while_off = ticket_for(key, note_id, "view", 0, today)
    assert while_off is not None
    _assert_missing(anon.get(f"/api/share/{while_off}"))

    saved = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view", "markdown": "hello"})
    body = saved.json()
    meta = _meta(data_dir, note_id)
    ticket = ticket_for(key, note_id, "view", meta["shareGeneration"], meta["shareExpiresDay"])
    assert body["shareId"] == ticket
    assert body["shareUrl"].endswith(f"/s/{ticket}")
    assert anon.get(f"/api/share/{ticket}").status_code == 200

    other_id = "00000" if note_id != "00000" else "00001"
    cases = {
        "signature": _flip_mac(ticket),
        "expired": ticket_for(key, note_id, "view", meta["shareGeneration"], today - 1),
        "generation": ticket_for(key, note_id, "view", (meta["shareGeneration"] + 1) % 256, meta["shareExpiresDay"]),
        "unknown": ticket_for(key, other_id, "view", meta["shareGeneration"], meta["shareExpiresDay"]),
        "level": ticket_for(key, note_id, "edit", meta["shareGeneration"], meta["shareExpiresDay"]),
        "access3": _with_access_code(ticket, key, 3),
    }
    for name, forged in cases.items():
        assert forged and forged != ticket, name
        _assert_missing(anon.get(f"/api/share/{forged}"))
    page = anon.get(f"/s/{cases['signature']}")
    assert page.status_code == 404
    assert NOT_FOUND in page.text
    assert anon.get(f"/api/share/{ticket}").status_code == 200


def test_access_change_replaces_ticket(client, app):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    first = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view"}).json()
    second = client.put(f"/api/notes/{note_id}", json={"shareAccess": "comment"}).json()
    assert first["shareId"] != second["shareId"]
    anon = TestClient(app, follow_redirects=False)
    _assert_missing(anon.get(f"/api/share/{first['shareId']}"))
    assert anon.get(f"/api/share/{second['shareId']}").status_code == 200


def test_rotate_share_same_access(client, app, data_dir):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    first = client.put(f"/api/notes/{note_id}", json={"shareAccess": "edit"}).json()
    before = _meta(data_dir, note_id)["shareGeneration"]
    second = client.put(
        f"/api/notes/{note_id}",
        json={"shareAccess": "edit", "rotateShare": True},
    ).json()
    assert first["shareAccess"] == "edit"
    assert second["shareAccess"] == "edit"
    assert first["shareId"] != second["shareId"]
    assert _meta(data_dir, note_id)["shareGeneration"] == before + 1
    anon = TestClient(app, follow_redirects=False)
    _assert_missing(anon.get(f"/api/share/{first['shareId']}"))
    assert anon.get(f"/api/share/{second['shareId']}").status_code == 200

    off = client.put(f"/api/notes/{note_id}", json={"shareAccess": "none", "rotateShare": True}).json()
    assert off["shareId"] is None
    assert off["shareUrl"] == ""
    held = _meta(data_dir, note_id)["shareGeneration"]
    again = client.put(f"/api/notes/{note_id}", json={"shareAccess": "none", "rotateShare": True}).json()
    assert again["shareId"] is None
    assert _meta(data_dir, note_id)["shareGeneration"] == held
    assert _meta(data_dir, note_id)["shareExpiresDay"] is None


def test_disable_share_invalidates_immediately(client, app, data_dir):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    issued = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view"}).json()
    generation = _meta(data_dir, note_id)["shareGeneration"]
    off = client.put(f"/api/notes/{note_id}", json={"shareAccess": "none"}).json()
    assert off["shareAccess"] == "none"
    assert off["shareId"] is None
    assert off["shareUrl"] == ""
    meta = _meta(data_dir, note_id)
    assert meta["shareGeneration"] == generation
    assert meta["shareExpiresDay"] is None
    assert "shareId" not in meta
    anon = TestClient(app, follow_redirects=False)
    _assert_missing(anon.get(f"/api/share/{issued['shareId']}"))
    assert anon.get(f"/s/{issued['shareId']}").status_code == 404


def test_new_note_id_is_five_base36_chars(client):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    assert re.fullmatch(r"[0-9a-z]{5}", note_id)


def test_link_key_created_once(data_dir, client):
    path = data_dir / "link.key"
    first = path.read_bytes()
    assert len(first) == 32
    assert path.stat().st_mode & 0o777 == 0o600
    create_app(data_dir)
    assert path.read_bytes() == first


def test_skill_is_public_markdown(client):
    response = client.get("/skill/jot/SKILL.md")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    skill = Path(__file__).resolve().parents[1] / "skill-jot" / "SKILL.md"
    assert response.text == skill.read_text(encoding="utf-8")
    assert "A–Z" in response.text
    assert "curl -sS" in response.text


def test_websocket_rejects_bad_ticket_without_hello(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/?shareId=not-a-ticket") as socket:
            socket.receive_json()


def test_websocket_rechecks_ticket_on_message(client):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    client.put(f"/api/notes/{note_id}", json={"markdown": "hello"})
    ticket = client.put(f"/api/notes/{note_id}", json={"shareAccess": "edit"}).json()["shareId"]
    with client.websocket_connect(f"/?shareId={ticket}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"
        assert hello["shareId"] == ticket
        client.put(f"/api/notes/{note_id}", json={"shareAccess": "none"})
        with pytest.raises(WebSocketDisconnect):
            socket.send_json(
                {
                    "type": "mutation",
                    "clientId": hello["clientId"],
                    "mutations": [
                        {
                            "name": "insert",
                            "clientCounter": 1,
                            "args": {
                                "before": None,
                                "id": {"bunchId": "bunch-x", "counter": 0},
                                "content": "Z",
                                "isInWord": False,
                            },
                        }
                    ],
                }
            )
            socket.receive_json()
    assert client.get(f"/api/notes/{note_id}").json()["note"]["markdown"] == "hello"


def _end_day(token: str) -> int:
    return int.from_bytes(_decode(token)[:6], "big") & 0xFFF


def _ticket(url: str) -> str:
    return url.rsplit("/s/", 1)[1]


def test_permalink_outlives_daily_link(client, app, monkeypatch):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    shared = client.put(f"/api/notes/{note_id}", json={"shareAccess": "edit"}).json()
    daily = shared["shareId"]
    permanent = _ticket(shared["sharePermalink"])
    assert permanent != daily
    assert _end_day(permanent) == PERMANENT_DAY
    assert shared["shareExpires"] == (datetime(2026, 1, 1) + timedelta(days=_end_day(daily))).date().isoformat()

    anon = TestClient(app, follow_redirects=False)
    assert anon.get(f"/api/share/{daily}").json()["linkExpires"] == shared["shareExpires"]
    assert anon.get(f"/api/share/{permanent}").json()["linkExpires"] is None
    assert 'data-link-expires="never"' in anon.get(f"/s/{permanent}").text
    assert f'data-link-expires="{shared["shareExpires"]}"' in anon.get(f"/s/{daily}").text

    # Years later the daily link is gone and the permalink still works.
    monkeypatch.setattr("jotpy.notes.today_utc", lambda: PERMANENT_DAY - 1)
    _assert_missing(anon.get(f"/api/share/{daily}"))
    assert anon.get(f"/api/share/{permanent}").status_code == 200
    # The shared payload hands out the daily link only, never the permalink.
    assert shared["sharePermalink"] not in anon.get(f"/api/share/{permanent}").text

    monkeypatch.undo()
    rotated = client.put(f"/api/notes/{note_id}", json={"shareAccess": "edit", "rotateShare": True}).json()
    _assert_missing(anon.get(f"/api/share/{permanent}"))
    assert anon.get(f"/api/share/{_ticket(rotated['sharePermalink'])}").status_code == 200


def test_renew_moves_daily_link_without_revoking(client, app, data_dir, monkeypatch):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    monkeypatch.setattr("jotpy.notes.expiry_day", lambda: today_utc())
    first = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view"}).json()
    monkeypatch.undo()
    generation = _meta(data_dir, note_id)["shareGeneration"]
    renewed = client.put(f"/api/notes/{note_id}", json={"renewLink": True}).json()
    assert renewed["shareId"] != first["shareId"]
    assert _end_day(renewed["shareId"]) == _end_day(first["shareId"]) + 1
    assert _meta(data_dir, note_id)["shareGeneration"] == generation
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(f"/api/share/{first['shareId']}").status_code == 200
    assert anon.get(f"/api/share/{renewed['shareId']}").status_code == 200

    off = client.put(f"/api/notes/{note_id}", json={"shareAccess": "none", "renewLink": True}).json()
    assert off["shareId"] is None
    assert off["sharePermalink"] == ""
    assert off["shareExpires"] is None


def test_generation_stops_at_limit(client, app, data_dir):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    shared = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view"}).json()
    meta = _meta(data_dir, note_id)
    meta["shareGeneration"] = 254
    (data_dir / "notes" / f"{note_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    app.state.runtime.notes[note_id].share_generation = 254

    last = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view", "rotateShare": True})
    assert last.status_code == 200
    assert _meta(data_dir, note_id)["shareGeneration"] == 255
    refused = client.put(f"/api/notes/{note_id}", json={"title": "kept", "shareAccess": "edit"})
    assert refused.status_code == 409
    assert _meta(data_dir, note_id)["shareGeneration"] == 255
    assert _meta(data_dir, note_id)["title"] != "kept"
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(f"/api/share/{last.json()['shareId']}").status_code == 200
    # Turning sharing off still works; turning it back on would need a new generation.
    assert client.put(f"/api/notes/{note_id}", json={"shareAccess": "none"}).status_code == 200
    assert client.put(f"/api/notes/{note_id}", json={"shareAccess": "view"}).status_code == 409
