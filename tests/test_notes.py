import json

from fastapi.testclient import TestClient

from jotpy.app import create_app
from jotpy.markdown_html import render_markdown
from tests.conftest import setup_owner


def test_note_roundtrip_and_second_start(client, data_dir):
    setup_owner(client)
    created = client.post("/api/notes")
    assert created.status_code == 200
    note_id = created.json()["note"]["id"]
    assert created.json()["note"]["title"] == "untitled"

    saved = client.put(f"/api/notes/{note_id}", json={"title": "Poznámka", "markdown": "hello\n"})
    assert saved.status_code == 200
    assert saved.json()["shareAccess"] == "none"

    read = client.get(f"/api/notes/{note_id}")
    assert read.status_code == 200
    body = read.json()
    assert body["note"]["title"] == "Poznámka"
    assert body["note"]["markdown"] == "hello\n"
    assert "renderedHtml" in body["note"]

    meta_path = data_dir / "notes" / f"{note_id}.json"
    raw = meta_path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert "Poznámka" in raw
    meta = json.loads(raw)
    assert list(meta) == [
        "id",
        "title",
        "shareId",
        "shareAccess",
        "createdAt",
        "updatedAt",
        "threads",
        "collab",
    ]
    assert meta["collab"]["chars"][0]["chars"] == "hello\n"
    assert (data_dir / "notes" / f"{note_id}.md").read_text(encoding="utf-8") == "hello\n"

    reloaded = create_app(data_dir)
    with TestClient(reloaded, follow_redirects=False) as second:
        second.cookies.update(client.cookies)
        again = second.get(f"/api/notes/{note_id}")
        assert again.status_code == 200
        assert again.json()["note"]["markdown"] == "hello\n"
        assert again.json()["note"]["title"] == "Poznámka"

    deleted = client.delete(f"/api/notes/{note_id}")
    assert deleted.status_code == 200
    missing = client.get(f"/api/notes/{note_id}")
    assert missing.status_code == 404
    assert missing.json()["error"] == "Note not found."
    assert not meta_path.exists()


def test_markdown_keeps_mermaid_and_hljs_classes():
    html = render_markdown("```mermaid\ngraph TD\n  A-->B\n```\n\n```python\nprint(1)\n```\n")
    assert '<pre class="mermaid">' in html
    assert "graph TD" in html
    assert 'class="hljs language-python"' in html
    assert "hljs-built_in" in html or "hljs-keyword" in html or "hljs-title" in html
    assert "<script>" not in render_markdown("<script>alert(1)</script>")


def test_static_assets(client):
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    mermaid = client.get("/static/mermaid/mermaid.esm.min.mjs")
    assert mermaid.status_code == 200
    assert "mermaid" in mermaid.text[:200] or mermaid.text.startswith("import")
