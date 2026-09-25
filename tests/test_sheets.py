import json
import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jotpy.app import create_app
from jotpy.notes import allocate_note_id
from tests.conftest import setup_owner

ID_RE = re.compile(r"^[0-9a-z]{5}$")


def _ops(client, sheet_id, base_version, ops):
    return client.post(
        f"/api/sheets/{sheet_id}/ops",
        json={"baseVersion": base_version, "ops": ops},
    )


def _meta(data_dir, sheet_id):
    return json.loads((data_dir / "sheets" / f"{sheet_id}.json").read_text(encoding="utf-8"))


def test_empty_sheet(client, app, data_dir):
    setup_owner(client)
    created = client.post("/api/sheets")
    assert created.status_code == 200
    sheet = created.json()["sheet"]
    sheet_id = sheet["id"]
    assert ID_RE.fullmatch(sheet_id)
    assert sheet["title"] == "untitled"
    assert isinstance(sheet["shareId"], str) and sheet["shareId"]
    assert sheet["columnCount"] == 0
    assert sheet["rowCount"] == 0

    data = client.get(f"/api/sheets/{sheet_id}/data")
    assert data.status_code == 200
    body = data.json()
    assert body["version"] == 0
    assert body["columns"] == []
    assert body["rows"] == []
    assert body["truncated"] is False

    raw = (data_dir / "sheets" / f"{sheet_id}.json").read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert "shareId" not in raw
    meta = json.loads(raw)
    assert meta["shareGeneration"] == 1
    assert meta["shareAccess"] == "edit"
    assert isinstance(meta["shareExpiresDay"], int)
    assert meta["columns"] == []
    assert meta["rows"] == []
    assert (data_dir / "sheets" / f"{sheet_id}.csv").read_text(encoding="utf-8") == "_id\n"

    csv_body = client.get(f"/api/sheets/{sheet_id}/data", params={"format": "csv"})
    assert csv_body.status_code == 200
    assert "text/csv" in csv_body.headers["content-type"]
    assert csv_body.headers["X-Jot-Version"] == "0"
    assert csv_body.text.startswith("_id")

    page = client.get(f"/sheets/{sheet_id}")
    assert page.status_code == 200
    assert 'data-page="sheet"' in page.text
    assert f'data-sheet-id="{sheet_id}"' in page.text
    assert "data-too-large" not in page.text
    anon = TestClient(app, follow_redirects=False)
    assert anon.get(f"/sheets/{sheet_id}").status_code == 302
    assert anon.get(f"/api/sheets/{sheet_id}/data").status_code == 401

    note_id = client.post("/api/notes").json()["note"]["id"]
    assert note_id != sheet_id
    assert client.get(f"/api/sheets/{note_id}").status_code == 404
    assert client.get(f"/api/notes/{sheet_id}").status_code == 404

    renamed = client.put(f"/api/sheets/{sheet_id}", json={"title": "Objednavky"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Objednavky"
    assert client.get("/api/sheets", params={"q": "obj"}).json()["sheets"][0]["id"] == sheet_id
    assert client.get("/api/sheets", params={"q": "zzz"}).json()["sheets"] == []

    key = client.post("/api/keys", json={"label": "cli"}).json()["key"]
    with TestClient(app, follow_redirects=False) as keyed:
        assert keyed.get("/api/sheets").status_code == 401
        allowed = keyed.get("/api/sheets", headers={"Authorization": f"Bearer {key}"})
        assert allowed.status_code == 200
        assert any(item["id"] == sheet_id for item in allowed.json()["sheets"])
        assert keyed.get(f"/api/sheets/{sheet_id}/data", headers={"Authorization": f"Bearer {key}"}).status_code == 200

    assert client.delete(f"/api/sheets/{sheet_id}").status_code == 200
    assert client.get(f"/api/sheets/{sheet_id}").status_code == 404
    assert not (data_dir / "sheets" / f"{sheet_id}.json").exists()
    assert not (data_dir / "sheets" / f"{sheet_id}.csv").exists()


def test_allocate_skips_note_and_sheet(client, app, monkeypatch):
    setup_owner(client)
    note_id = client.post("/api/notes").json()["note"]["id"]
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    runtime = app.state.runtime

    def use(*ids):
        values = iter(ids)
        monkeypatch.setattr("jotpy.notes.create_short_id", lambda length=5: next(values))

    use(note_id, sheet_id, "zzzzz")
    assert allocate_note_id(runtime) == "zzzzz"
    del runtime.sheets[sheet_id]
    use(sheet_id, "yyyyy")
    assert allocate_note_id(runtime) == "yyyyy"
    del runtime.notes[note_id]
    use(note_id, "xxxxx")
    assert allocate_note_id(runtime) == "xxxxx"


def test_batch_set_and_unknown_name(client, data_dir):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    saved = _ops(client, sheet_id, 0, [
        {"op": "insert_column", "name": "sku"},
        {"op": "insert_column", "name": "stav"},
        {"op": "insert_row", "values": {"sku": "ABC", "stav": "open"}},
        {"op": "insert_row", "values": {"sku": "ZZZ", "stav": "open"}},
        {"op": "set", "row": {"column": "sku", "value": "ABC"}, "column": "stav", "value": "closed"},
        {"op": "set", "row": {"column": "sku", "value": "ZZZ"}, "column": "sku", "value": "001"},
    ])
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 1
    assert body["columns"] == ["sku", "stav"]
    assert body["truncated"] is False
    rows = {row["sku"]: row for row in body["rows"]}
    assert rows["ABC"]["stav"] == "closed"
    assert rows["001"] == {**rows["001"], "sku": "001", "stav": "open"}
    assert ID_RE.fullmatch(rows["ABC"]["id"])
    meta = _meta(data_dir, sheet_id)
    local_ids = [column["id"] for column in meta["columns"]] + [row["id"] for row in meta["rows"]]
    assert all(ID_RE.fullmatch(item) for item in local_ids)
    assert len(local_ids) == len(set(local_ids))
    assert all(cell["version"] == 1 for row in meta["rows"] for cell in row["cells"].values())

    csv_path = data_dir / "sheets" / f"{sheet_id}.csv"
    before_csv = csv_path.read_text(encoding="utf-8")
    assert before_csv.splitlines()[0].split(",")[0] == "_id"
    failed = _ops(client, sheet_id, 1, [
        {"op": "insert_column", "name": "extra"},
        {"op": "set", "row": rows["ABC"]["id"], "column": "missing", "value": "x"},
    ])
    assert failed.status_code == 400
    assert failed.json()["op"] == 1
    assert csv_path.read_text(encoding="utf-8") == before_csv
    after = client.get(f"/api/sheets/{sheet_id}/data").json()
    assert after["version"] == 1
    assert after["columns"] == ["sku", "stav"]
    assert {row["sku"]: row["stav"] for row in after["rows"]} == {"ABC": "closed", "001": "open"}

    ambiguous = _ops(client, sheet_id, 1, [
        {"op": "insert_row", "values": {"sku": "ABC", "stav": "later"}},
        {"op": "set", "row": {"column": "sku", "value": "ABC"}, "column": "stav", "value": "nope"},
    ])
    assert ambiguous.status_code == 400
    assert client.get(f"/api/sheets/{sheet_id}/data").json()["version"] == 1

    sku_id = meta["columns"][0]["id"]
    placed = _ops(client, sheet_id, 1, [{"op": "insert_column", "name": "first", "before": sku_id}])
    assert placed.status_code == 200
    assert placed.json()["columns"] == ["first", "sku", "stav"]
    row_id = placed.json()["rows"][0]["id"]
    inserted = _ops(client, sheet_id, 2, [{"op": "insert_row", "before": row_id, "values": {"sku": "TOP"}}])
    assert inserted.status_code == 200
    assert inserted.json()["rows"][0]["sku"] == "TOP"
    missing_before = _ops(client, sheet_id, 3, [{"op": "insert_row", "before": "-----", "values": {"sku": "no"}}])
    assert missing_before.status_code == 400
    assert _ops(client, sheet_id, 3, [{"op": "insert_column", "name": "_id"}]).status_code == 400
    assert _ops(client, sheet_id, 3, [{"op": "insert_column", "name": "sku"}]).status_code == 400
    assert "no" not in {row["sku"] for row in client.get(f"/api/sheets/{sheet_id}/data").json()["rows"]}

    both = _ops(client, sheet_id, 3, [
        {"op": "insert_column", "name": "Cena"},
        {"op": "insert_column", "name": "cena"},
    ])
    assert both.status_code == 200
    assert both.json()["columns"][-2:] == ["Cena", "cena"]


def test_cell_version_conflict(client, data_dir):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    created = _ops(client, sheet_id, 0, [
        {"op": "insert_column", "name": "a"},
        {"op": "insert_column", "name": "b"},
        {"op": "insert_row", "values": {"a": "A", "b": "B"}},
    ])
    row_id = created.json()["rows"][0]["id"]
    other = _ops(client, sheet_id, 1, [{"op": "set", "row": row_id, "column": "a", "value": "A2"}])
    assert other.status_code == 200
    assert other.json()["version"] == 2
    meta = _meta(data_dir, sheet_id)
    columns = {column["name"]: column["id"] for column in meta["columns"]}
    cells = meta["rows"][0]["cells"]
    assert cells[columns["a"]]["version"] == 2
    assert cells[columns["b"]]["version"] == 1

    conflict = _ops(client, sheet_id, 1, [
        {"op": "set", "row": row_id, "column": "b", "value": "B2"},
        {"op": "set", "row": row_id, "column": "a", "value": "NO"},
    ])
    assert conflict.status_code == 409
    assert conflict.json() == {"ok": False, "error": "conflict", "version": 2, "op": 1}
    current = client.get(f"/api/sheets/{sheet_id}/data").json()
    assert current["version"] == 2
    assert current["rows"][0]["a"] == "A2"
    assert current["rows"][0]["b"] == "B"

    edited = _ops(client, sheet_id, 1, [{"op": "set", "row": row_id, "column": "b", "value": "B2"}])
    assert edited.status_code == 200
    assert edited.json()["version"] == 3
    assert edited.json()["rows"][0]["b"] == "B2"
    assert edited.json()["rows"][0]["a"] == "A2"

    same = _ops(client, sheet_id, 1, [
        {"op": "set", "row": row_id, "column": "a", "value": "NO"},
        {"op": "insert_column", "name": "extra"},
    ])
    assert same.status_code == 409
    assert same.json()["op"] == 0
    stayed = client.get(f"/api/sheets/{sheet_id}/data").json()
    assert stayed["version"] == 3
    assert stayed["rows"][0]["a"] == "A2"
    assert "extra" not in stayed["columns"]
    assert _meta(data_dir, sheet_id)["version"] == 3

    assert _ops(client, sheet_id, 1, [{"op": "rename_column", "name": "a", "newName": "aa"}]).status_code == 409
    renamed = _ops(client, sheet_id, 3, [{"op": "rename_column", "name": "a", "newName": "aa"}])
    assert renamed.status_code == 200
    assert renamed.json()["columns"] == ["aa", "b"]
    assert _ops(client, sheet_id, 1, [{"op": "delete_row", "row": row_id}]).status_code == 409
    assert client.get(f"/api/sheets/{sheet_id}/data").json()["rows"]
    removed = _ops(client, sheet_id, 4, [{"op": "delete_row", "row": row_id}])
    assert removed.status_code == 200
    assert removed.json()["rows"] == []


def test_sql_grammar(client):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    saved = _ops(client, sheet_id, 0, [
        {"op": "insert_column", "name": "name"},
        {"op": "insert_column", "name": "city"},
        {"op": "insert_column", "name": "n"},
        {"op": "insert_column", "name": "Cena"},
        {"op": "insert_column", "name": "unit price"},
        {"op": "insert_row", "values": {"name": "Ada", "city": "Praha", "n": "10", "Cena": "5", "unit price": "A;da"}},
        {"op": "insert_row", "values": {"name": "Bob", "city": "Brno", "n": "9", "Cena": "1", "unit price": "x"}},
        {"op": "insert_row", "values": {"name": "Cid", "city": "Brno", "n": "100", "Cena": "1", "unit price": "y"}},
    ])
    assert saved.status_code == 200, saved.text
    ada_id = next(row["id"] for row in saved.json()["rows"] if row["name"] == "Ada")

    def query(text, **params):
        payload = {"q": text}
        payload.update(params)
        return client.get(f"/api/sheets/{sheet_id}/data", params=payload)

    both = query("SELECT name, city WHERE name = 'Ada' AND city = 'Praha'")
    assert both.status_code == 200
    assert [row["name"] for row in both.json()["rows"]] == ["Ada"]
    assert both.json()["columns"] == ["name", "city"]
    assert both.json()["truncated"] is False

    either = query("SELECT name WHERE city = 'Brno' OR name = 'Ada'")
    assert [row["name"] for row in either.json()["rows"]] == ["Ada", "Bob", "Cid"]

    tighter_and = query("SELECT name WHERE name = 'Bob' OR name = 'Cid' AND city = 'Praha'")
    assert [row["name"] for row in tighter_and.json()["rows"]] == ["Bob"]

    grouped = query("SELECT name WHERE (name = 'Bob' OR name = 'Cid') AND city = 'Brno'")
    assert [row["name"] for row in grouped.json()["rows"]] == ["Bob", "Cid"]

    like = query("SELECT name WHERE city LIKE 'Br%'")
    assert [row["name"] for row in like.json()["rows"]] == ["Bob", "Cid"]
    assert query("SELECT name WHERE city LIKE 'br%'").json()["rows"] == []
    assert [row["name"] for row in query("SELECT name WHERE name LIKE 'A_a'").json()["rows"]] == ["Ada"]

    ordered = query("SELECT n ORDER BY n ASC")
    assert [row["n"] for row in ordered.json()["rows"]] == ["10", "100", "9"]
    by_city = query("SELECT name ORDER BY city ASC, name DESC")
    assert [row["name"] for row in by_city.json()["rows"]] == ["Cid", "Bob", "Ada"]
    limited = query("SELECT name, n ORDER BY n DESC LIMIT 1")
    assert limited.json()["rows"] == [{"n": "9", "name": "Bob", "id": limited.json()["rows"][0]["id"]}]
    assert limited.json()["truncated"] is True

    assert query("SELECT name FROM other_name WHERE name = 'Ada'").json()["rows"][0]["id"] == ada_id
    assert query(f"SELECT name WHERE _id = '{ada_id}'").json()["rows"][0]["name"] == "Ada"
    assert query('SELECT "unit price" WHERE name = \'Ada\'').json()["rows"][0]["unit price"] == "A;da"
    assert query("SELECT Cena").status_code == 200
    assert query("SELECT cena").status_code == 400
    assert query("SELECT cena").json()["error"] == "Unknown column."
    assert query("SELECT CENA").status_code == 400

    for text in (
        "SELECT name;",
        "SELECT name; DROP TABLE sheets",
        "SELECT name -- hi",
        "SELECT name /* hidden */",
        "SELECT name WHERE name IS NULL",
        "SELECT name GROUP BY name",
        "SELECT",
        "SELECT * FROM",
    ):
        rejected = query(text)
        assert rejected.status_code == 400, text
        assert "rows" not in rejected.json()

    quoted_semi = query("SELECT name WHERE name = 'A;da'")
    assert quoted_semi.status_code == 200
    assert quoted_semi.json()["rows"] == []

    csv_body = query("SELECT name WHERE name = 'Ada'", format="csv")
    assert csv_body.status_code == 200
    assert csv_body.headers["X-Jot-Version"] == "1"
    lines = csv_body.text.splitlines()
    assert lines[0] == "_id,name"
    assert lines[1].endswith(",Ada")
    assert lines[1].startswith(ada_id)


def test_result_over_2000_is_refused(client):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    ops = [{"op": "insert_column", "name": "n"}]
    ops.extend({"op": "insert_row", "values": {"n": str(index)}} for index in range(2001))
    saved = _ops(client, sheet_id, 0, ops)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 1
    assert "rows" not in body
    assert "columns" not in body
    assert len(body["inserted"]) == 2001
    assert all(ID_RE.fullmatch(item) for item in body["inserted"])

    refused = client.get(f"/api/sheets/{sheet_id}/data")
    assert refused.status_code == 400
    assert refused.json()["error"] == "Result exceeds 2000 rows. Narrow the condition."
    assert "rows" not in refused.json()
    wide = client.get(f"/api/sheets/{sheet_id}/data", params={"q": "SELECT n WHERE n LIKE '%'"})
    assert wide.status_code == 400
    assert "rows" not in wide.json()
    too_high = client.get(f"/api/sheets/{sheet_id}/data", params={"q": "SELECT n LIMIT 2001"})
    assert too_high.status_code == 400

    one = client.get(f"/api/sheets/{sheet_id}/data", params={"q": "SELECT n WHERE n = '2000'"})
    assert one.status_code == 200
    assert one.json()["rows"] == [{"n": "2000", "id": one.json()["rows"][0]["id"]}]
    assert one.json()["truncated"] is False
    assert one.json()["rows"][0]["id"] in body["inserted"]

    limited = client.get(f"/api/sheets/{sheet_id}/data", params={"q": "SELECT n ORDER BY n ASC LIMIT 2"})
    assert [row["n"] for row in limited.json()["rows"]] == ["0", "1"]
    assert limited.json()["truncated"] is True
    capped = client.get(f"/api/sheets/{sheet_id}/data", params={"q": "SELECT n LIMIT 2000"})
    assert capped.status_code == 200
    assert len(capped.json()["rows"]) == 2000
    assert capped.json()["truncated"] is True

    page = client.get(f"/sheets/{sheet_id}")
    assert page.status_code == 200
    assert 'data-too-large="1"' in page.text
    assert 'data-page="sheet"' in page.text


def test_csv_is_not_loaded(client, data_dir):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    saved = _ops(client, sheet_id, 0, [
        {"op": "insert_column", "name": "sku"},
        {"op": "insert_row", "values": {"sku": "ABC"}},
    ])
    assert saved.status_code == 200
    csv_path = data_dir / "sheets" / f"{sheet_id}.csv"
    assert csv_path.read_text(encoding="utf-8").splitlines()[0].split(",")[0] == "_id"
    csv_path.write_text("_id,sku\nhacked,hacked\n", encoding="utf-8")

    reloaded = create_app(data_dir)
    with TestClient(reloaded, follow_redirects=False) as second:
        second.cookies.update(client.cookies)
        body = second.get(f"/api/sheets/{sheet_id}/data").json()
        assert len(body["rows"]) == 1
        assert body["rows"][0]["sku"] == "ABC"
        assert body["rows"][0]["id"] != "hacked"
        changed = _ops(second, sheet_id, body["version"], [
            {"op": "set", "row": body["rows"][0]["id"], "column": "sku", "value": "kept"},
        ])
        assert changed.status_code == 200
        text = csv_path.read_text(encoding="utf-8")
        assert "hacked" not in text
        assert text.splitlines()[0].split(",")[0] == "_id"
        assert "kept" in text


def test_share_ticket_view_edit_and_rotate(client, app, data_dir):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    created = _ops(client, sheet_id, 0, [
        {"op": "insert_column", "name": "sku"},
        {"op": "insert_row", "values": {"sku": "ABC"}},
    ])
    assert created.status_code == 200
    note_id = client.post("/api/notes").json()["note"]["id"]
    note_ticket = client.put(f"/api/notes/{note_id}", json={"shareAccess": "view"}).json()["shareId"]
    anon = TestClient(app, follow_redirects=False)

    ignored = client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "comment"}).json()
    assert ignored["shareAccess"] == "edit"
    assert ignored["shareId"]

    view = client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "view"}).json()
    view_ticket = view["shareId"]
    assert view["shareUrl"].endswith(f"/s/{view_ticket}")
    assert anon.get(f"/api/share/{view_ticket}/data").status_code == 200
    denied = anon.post(
        f"/api/share/{view_ticket}/ops",
        json={"baseVersion": 1, "ops": [{"op": "set", "row": "zzzzz", "column": "sku", "value": "no"}]},
    )
    assert denied.status_code == 404
    assert denied.json()["error"] == "Shared sheet not found."
    note_path = anon.get(f"/api/share/{view_ticket}")
    assert note_path.status_code == 404
    assert note_path.json()["error"] == "Shared note not found."
    assert anon.post(
        f"/api/share/{view_ticket}/edit",
        json={"edits": [{"oldText": "x", "newText": "y"}]},
    ).json()["error"] == "Shared note not found."
    page = anon.get(f"/s/{view_ticket}")
    assert page.status_code == 200
    assert 'data-page="sheet"' in page.text
    assert f'data-sheet-id="{sheet_id}"' in page.text
    assert 'data-share-access="view"' in page.text

    reverse = anon.get(f"/api/share/{note_ticket}/data")
    assert reverse.status_code == 404
    assert reverse.json()["error"] == "Shared sheet not found."
    assert anon.post(f"/api/share/{note_ticket}/ops", json={"baseVersion": 0, "ops": [{"op": "insert_row"}]}).status_code == 404

    edit = client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "edit"}).json()
    assert edit["shareId"] != view_ticket
    assert anon.get(f"/api/share/{view_ticket}/data").status_code == 404
    row_id = anon.get(f"/api/share/{edit['shareId']}/data").json()["rows"][0]["id"]
    wrote = anon.post(
        f"/api/share/{edit['shareId']}/ops",
        json={"baseVersion": 1, "ops": [{"op": "set", "row": row_id, "column": "sku", "value": "edited"}]},
    )
    assert wrote.status_code == 200
    assert wrote.json()["rows"][0]["sku"] == "edited"

    rotated = client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "edit", "rotateShare": True}).json()
    assert rotated["shareId"] != edit["shareId"]
    assert anon.get(f"/api/share/{edit['shareId']}/data").status_code == 404
    assert anon.get(f"/s/{edit['shareId']}").status_code == 404
    assert anon.get(f"/api/share/{rotated['shareId']}/data").status_code == 200
    meta = _meta(data_dir, sheet_id)
    assert "shareId" not in meta
    assert meta["shareAccess"] == "edit"
    assert isinstance(meta["shareExpiresDay"], int)

    off = client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "none"}).json()
    assert off["shareId"] is None
    assert off["shareUrl"] == ""
    assert anon.get(f"/api/share/{rotated['shareId']}/data").status_code == 404
    assert anon.get(f"/s/{rotated['shareId']}").status_code == 404
    stored = _meta(data_dir, sheet_id)
    assert stored["shareAccess"] == "none"
    assert stored["shareExpiresDay"] is None


def test_import_csv_fills_empty_table_only(client, app):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    csv_text = 'sku,cena\n001,"1,5"\nABC,10\n'
    imported = client.post(
        f"/api/sheets/{sheet_id}/import-csv",
        content=csv_text.encode(),
        headers={"Content-Type": "text/csv; charset=utf-8", "X-Jot-Base-Version": "0"},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["version"] == 1
    assert body["columns"] == ["sku", "cena"]
    assert body["rows"][0]["sku"] == "001"
    assert body["rows"][0]["cena"] == "1,5"
    assert ID_RE.fullmatch(body["rows"][0]["id"])
    again = client.post(
        f"/api/sheets/{sheet_id}/import-csv",
        content=csv_text.encode(),
        headers={"Content-Type": "text/csv", "X-Jot-Base-Version": "1"},
    )
    assert again.status_code == 409
    assert again.json()["error"] == "Import only replaces an empty table."
    stale = client.post("/api/sheets").json()["sheet"]["id"]
    wrong = client.post(
        f"/api/sheets/{stale}/import-csv",
        content=b"sku\nA\n",
        headers={"Content-Type": "text/csv", "X-Jot-Base-Version": "4"},
    )
    assert wrong.status_code == 409
    assert wrong.json()["version"] == 0

    fresh = client.post("/api/sheets").json()["sheet"]["id"]
    exported = "_id,sku\nold,001\n"
    kept = client.post(
        f"/api/sheets/{fresh}/import-csv",
        content=exported.encode(),
        headers={"Content-Type": "text/csv", "X-Jot-Base-Version": "0"},
    )
    assert kept.status_code == 200
    assert kept.json()["columns"] == ["sku"]
    assert kept.json()["rows"][0]["sku"] == "001"
    assert kept.json()["rows"][0]["id"] != "old"

    wide = client.post(
        f"/api/sheets/{fresh}/import-csv",
        content=b"a\n1,2\n",
        headers={"Content-Type": "text/csv", "X-Jot-Base-Version": "0"},
    )
    assert wide.status_code == 409

    empty = client.post("/api/sheets").json()["sheet"]["id"]
    ragged = client.post(
        f"/api/sheets/{empty}/import-csv",
        content=b"a\n1,2\n",
        headers={"Content-Type": "text/csv", "X-Jot-Base-Version": "0"},
    )
    assert ragged.status_code == 400
    assert ragged.json()["error"] == "Row has more columns than the header."

    shared = client.post("/api/sheets").json()["sheet"]["id"]
    view = client.put(f"/api/sheets/{shared}", json={"shareAccess": "view"}).json()["shareId"]
    edit = client.put(f"/api/sheets/{shared}", json={"shareAccess": "edit"}).json()["shareId"]
    anon = TestClient(app, follow_redirects=False)
    denied = anon.post(
        f"/api/share/{view}/import-csv",
        content=b"sku\nA\n",
        headers={"Content-Type": "text/csv", "X-Jot-Base-Version": "0"},
    )
    assert denied.status_code == 404
    allowed = anon.post(
        f"/api/share/{edit}/import-csv",
        content="jméno\n001\n".encode(),
        headers={"Content-Type": "text/csv; charset=utf-8", "X-Jot-Base-Version": "0"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["rows"][0]["jméno"] == "001"


def test_websocket_grid_and_view_ticket(client, app):
    setup_owner(client)
    sheet_id = client.post("/api/sheets").json()["sheet"]["id"]
    with client.websocket_connect(f"/?sheetId={sheet_id}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "sheet"
        assert hello["version"] == 0
        assert hello["columns"] == []
        assert hello["rows"] == []
        socket.send_json({
            "type": "ops",
            "clientId": hello["clientId"],
            "baseVersion": 0,
            "ops": [
                {"op": "insert_column", "name": "sku"},
                {"op": "insert_row", "values": {"sku": "001"}},
            ],
        })
        result = socket.receive_json()
        assert result["type"] == "ops-result"
        assert result["ok"] is True
        assert result["version"] == 1
        column_id = result["columns"][0]["id"]
        assert result["columns"][0]["name"] == "sku"
        assert result["rows"][0]["cells"][column_id] == "001"
    stored = client.get(f"/api/sheets/{sheet_id}/data").json()
    assert stored["rows"][0]["sku"] == "001"
    assert app.state.runtime.clients == []

    view_ticket = client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "view"}).json()["shareId"]
    with client.websocket_connect(f"/?shareId={view_ticket}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "sheet"
        assert hello["shareAccess"] == "view"
        socket.send_json({
            "type": "ops",
            "clientId": hello["clientId"],
            "baseVersion": hello["version"],
            "ops": [{"op": "insert_column", "name": "nope"}],
        })
        rejected = socket.receive_json()
        assert rejected["ok"] is False
        assert rejected["error"] == "Read only."
    assert "nope" not in client.get(f"/api/sheets/{sheet_id}/data").json()["columns"]

    edit_ticket = client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "edit"}).json()["shareId"]
    with client.websocket_connect(f"/?shareId={edit_ticket}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "sheet"
        client.put(f"/api/sheets/{sheet_id}", json={"shareAccess": "none"})
        socket.send_json({
            "type": "presence",
            "clientId": hello["clientId"],
            "selection": {"rowId": "aaaaa", "columnId": "bbbbb"},
        })
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
