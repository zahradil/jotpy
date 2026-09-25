import json
import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from jotpy.app import create_app
from tests.conftest import setup_owner

SOURCE = Path("/home/zahradil/Work/jot/jot/data/notes")


def test_loads_existing_note_markdown_from_collab(tmp_path):
    notes = tmp_path / "data" / "notes"
    notes.mkdir(parents=True)
    shutil.copy(SOURCE / "2xh2vthv.json", notes / "2xh2vthv.json")
    shutil.copy(SOURCE / "2xh2vthv.md", notes / "2xh2vthv.md")
    expected = (notes / "2xh2vthv.md").read_text(encoding="utf-8")

    app = create_app(tmp_path / "data")
    migrated = sorted(path.stem for path in notes.glob("*.md"))
    assert migrated != ["2xh2vthv"]
    assert len(migrated) == 1
    note_id = migrated[0]
    assert re.fullmatch(r"[0-9a-z]{5}", note_id)
    assert not (notes / "2xh2vthv.md").exists()
    assert not (notes / "2xh2vthv.json").exists()
    assert (notes / f"{note_id}.md").read_text(encoding="utf-8") == expected

    with TestClient(app, follow_redirects=False) as client:
        setup_owner(client)
        missing = client.get("/api/notes/2xh2vthv")
        assert missing.status_code == 404
        response = client.get(f"/api/notes/{note_id}")
        assert response.status_code == 200
        note = response.json()["note"]
        assert note["id"] == note_id
        assert note["title"] == "První123"
        assert note["markdown"] == expected
        assert note["shareAccess"] == "edit"
        assert response.json()["threads"][0]["anchor"]["quote"] == "Grok"

    assert (SOURCE / "2xh2vthv.json").exists()
    assert (SOURCE / "2xh2vthv.md").exists()
    assert json.loads((SOURCE / "2xh2vthv.json").read_text(encoding="utf-8"))["id"] == "2xh2vthv"


def _write_legacy(notes: Path, note_id: str, title: str, access: str, markdown: str) -> None:
    (notes / f"{note_id}.md").write_text(markdown, encoding="utf-8")
    payload = {
        "id": note_id,
        "title": title,
        "shareId": "oldshareidvalue",
        "shareAccess": access,
        "createdAt": "2026-02-01T00:00:00.000Z",
        "updatedAt": "2026-02-01T00:00:00.000Z",
        "threads": [],
    }
    (notes / f"{note_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_migrates_eight_char_id_in_tmp(tmp_path):
    notes = tmp_path / "data" / "notes"
    notes.mkdir(parents=True)
    kept = "migrated body\n"
    _write_legacy(notes, "abcd1234", "Kept", "comment", kept)
    _write_legacy(notes, "zzzz8888", "Off", "none", "off\n")

    create_app(tmp_path / "data")
    files = sorted(path.stem for path in notes.glob("*.md"))
    assert len(files) == 2
    assert "abcd1234" not in files
    assert "zzzz8888" not in files
    seen = set()
    for name in files:
        assert re.fullmatch(r"[0-9a-z]{5}", name)
        meta = json.loads((notes / f"{name}.json").read_text(encoding="utf-8"))
        assert "shareId" not in meta
        assert meta["id"] == name
        seen.add(meta["title"])
        if meta["title"] == "Kept":
            assert (notes / f"{name}.md").read_text(encoding="utf-8") == kept
            assert meta["shareAccess"] == "comment"
            assert meta["shareGeneration"] == 0
            assert isinstance(meta["shareExpiresDay"], int)
        else:
            assert (notes / f"{name}.md").read_text(encoding="utf-8") == "off\n"
            assert meta["shareAccess"] == "none"
            assert meta["shareGeneration"] == 0
            assert meta["shareExpiresDay"] is None
    assert seen == {"Kept", "Off"}
