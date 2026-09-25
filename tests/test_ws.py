from tests.conftest import setup_owner


def test_owner_websocket_hello_and_insert(client):
    setup_owner(client)
    created = client.post("/api/notes")
    note_id = created.json()["note"]["id"]

    with client.websocket_connect(f"/?noteId={note_id}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"
        assert hello["noteId"] == note_id
        assert hello["markdown"] == ""
        assert hello["clientId"] == "c1"
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
                            "id": {"bunchId": "bunch-1", "counter": 0},
                            "content": "Hi",
                            "isInWord": False,
                        },
                    }
                ],
            }
        )
        message = socket.receive_json()
        assert message["type"] == "mutation"
        assert message["markdown"] == "Hi"
        assert message["serverCounter"] == 1
        assert message["idListUpdates"][0]["type"] == "insertAfter"
        assert message["idListUpdates"][0]["count"] == 2

    stored = client.get(f"/api/notes/{note_id}")
    assert stored.json()["note"]["markdown"] == "Hi"


def test_closed_editor_drops_its_cursor(client, app):
    setup_owner(client)
    created = client.post("/api/notes")
    note_id = created.json()["note"]["id"]

    with client.websocket_connect(f"/?noteId={note_id}") as first:
        hello = first.receive_json()
        first.send_json(
            {
                "type": "presence",
                "clientId": hello["clientId"],
                "selection": {"type": "cursor", "cursor": {"bunchId": "bunch-1", "counter": 0}},
            }
        )
        assert len(app.state.runtime.clients) == 1

    assert app.state.runtime.clients == []

    with client.websocket_connect(f"/?noteId={note_id}") as second:
        hello = second.receive_json()
        assert hello["type"] == "hello"
        assert hello["clientId"] == "c2"
        assert len(app.state.runtime.clients) == 1
