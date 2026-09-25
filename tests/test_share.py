from fastapi.testclient import TestClient

from tests.conftest import setup_owner


def test_share_access_levels(client, app):
    setup_owner(client)
    created = client.post("/api/notes")
    note_id = created.json()["note"]["id"]
    client.put(f"/api/notes/{note_id}", json={"markdown": "hello world"})
    hidden = client.get(f"/api/notes/{note_id}").json()["note"]
    assert hidden["shareId"] is None
    assert hidden["shareUrl"] == ""

    anon = TestClient(app, follow_redirects=False)
    missing = anon.get("/api/share/not-a-ticket")
    assert missing.status_code == 404
    assert missing.json()["error"] == "Shared note not found."
    assert anon.get("/s/not-a-ticket").status_code == 404

    share_id = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view"}).json()["shareId"]
    viewed = anon.get(f"/api/share/{share_id}")
    assert viewed.status_code == 200
    assert viewed.json()["note"]["markdown"] == "hello world"
    page = anon.get(f"/s/{share_id}")
    assert page.status_code == 200
    assert 'data-share-access="view"' in page.text
    assert f'data-share-id="{share_id}"' in page.text
    assert anon.post(f"/api/share/{share_id}/threads", json={"body": "x"}).status_code == 404
    assert anon.post(f"/api/share/{share_id}/edit", json={"edits": [{"oldText": "hello", "newText": "hi"}]}).status_code == 404

    previous = share_id
    share_id = client.put(f"/api/notes/{note_id}", json={"shareAccess": "comment"}).json()["shareId"]
    assert share_id != previous
    assert anon.get(f"/api/share/{previous}").status_code == 404
    unnamed = anon.post(
        f"/api/share/{share_id}/threads",
        json={"anchor": {"quote": "hello", "prefix": "", "suffix": "", "start": 0, "end": 5}, "body": "note"},
    )
    assert unnamed.status_code == 400
    assert unnamed.json()["error"] == "Set your name first."
    named = anon.post(f"/api/share/{share_id}/identity", json={"name": "Ada"})
    assert named.status_code == 200
    assert "Max-Age=31536000" in named.headers["set-cookie"]
    thread = anon.post(
        f"/api/share/{share_id}/threads",
        json={"anchor": {"quote": "hello", "prefix": "", "suffix": "", "start": 0, "end": 5}, "body": "note"},
    )
    assert thread.status_code == 200
    thread_id = thread.json()["threads"][0]["id"]
    forbidden = anon.delete(f"/api/share/{share_id}/threads/{thread_id}")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "Only the owner can delete a whole thread."
    assert anon.post(
        f"/api/share/{share_id}/edit",
        json={"edits": [{"oldText": "hello", "newText": "hi"}]},
    ).status_code == 404

    share_id = client.put(f"/api/notes/{note_id}", json={"shareAccess": "edit"}).json()["shareId"]
    edited = anon.post(
        f"/api/share/{share_id}/edit",
        json={"edits": [{"oldText": "hello", "newText": "hi"}]},
    )
    assert edited.status_code == 200
    assert anon.get(f"/api/share/{share_id}/note").json()["note"]["markdown"] == "hi world"
    assert anon.get(f"/api/share/{share_id}/collab").status_code == 200
