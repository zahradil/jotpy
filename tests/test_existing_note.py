import shutil

from fastapi.testclient import TestClient

from jotpy.app import create_app
from tests.conftest import setup_owner

SOURCE = "/home/zahradil/Work/jot/jot/data/notes"


def test_loads_existing_note_markdown_from_collab(tmp_path):
    notes = tmp_path / "data" / "notes"
    notes.mkdir(parents=True)
    shutil.copy(f"{SOURCE}/2xh2vthv.json", notes / "2xh2vthv.json")
    shutil.copy(f"{SOURCE}/2xh2vthv.md", notes / "2xh2vthv.md")
    expected = (notes / "2xh2vthv.md").read_text(encoding="utf-8")

    app = create_app(tmp_path / "data")
    with TestClient(app, follow_redirects=False) as client:
        setup_owner(client)
        response = client.get("/api/notes/2xh2vthv")
        assert response.status_code == 200
        note = response.json()["note"]
        assert note["title"] == "První123"
        assert note["markdown"] == expected
        assert note["shareAccess"] == "edit"
        assert response.json()["threads"][0]["anchor"]["quote"] == "Grok"
