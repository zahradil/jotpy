from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from jotpy.auth import is_owner_authenticated
from jotpy.notes import allocate_note_id, share_url, update_share
from jotpy.sheet_query import ROW_CAP, QueryError, TooManyRows, execute
from jotpy.tickets import NOTE_ID_RE, open_ticket, ticket_for, today_utc
from jotpy.util import create_short_id, now_iso, read_json, write_json

__all__ = [
    "ROW_CAP",
    "QueryError",
    "SheetOpError",
    "TooManyRows",
    "commit_ops",
    "create_sheet",
    "import_csv",
    "delete_sheet_files",
    "load_sheets_into_memory",
    "ops_http_body",
    "persist_sheet",
    "require_sheet",
    "resolve_sheet_share",
    "run_sheet_query",
    "search_sheets",
    "sheet_for_client",
    "sheet_ops_result",
    "sheet_ticket",
    "sheet_ws_payload",
    "summarize_sheet",
]

_SHEET_LEVELS = {"view": 1, "edit": 2}
_COLUMN_OPS = frozenset({"insert_column", "rename_column", "delete_column"})


class SheetOpError(Exception):
    def __init__(self, status: int, error: str, op: int | None = None, version: int | None = None) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.op = op
        self.version = version


@dataclass
class Cell:
    value: str
    version: int


@dataclass
class SheetColumn:
    id: str
    name: str


@dataclass
class SheetRow:
    id: str
    cells: dict[str, Cell] = field(default_factory=dict)


@dataclass
class SheetRecord:
    id: str
    title: str
    version: int
    share_generation: int
    share_access: str
    share_expires_day: int | None
    created_at: str
    updated_at: str
    columns: list[SheetColumn] = field(default_factory=list)
    rows: list[SheetRow] = field(default_factory=list)


def _share_from_meta(meta: dict) -> tuple[int, str, int | None]:
    access = meta.get("shareAccess") or "none"
    if access not in ("none", "view", "edit"):
        access = "none"
    generation = meta.get("shareGeneration", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or not 0 <= generation <= 255:
        generation = 0
    day = meta.get("shareExpiresDay")
    if access == "none" or isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 4095:
        day = None
    return generation, access, day


def _local_id(used: set[str]) -> str:
    while True:
        candidate = create_short_id(5)
        if NOTE_ID_RE.fullmatch(candidate) and candidate not in used:
            used.add(candidate)
            return candidate


def _used_ids(sheet: SheetRecord) -> set[str]:
    used = {column.id for column in sheet.columns}
    used.update(row.id for row in sheet.rows)
    return used


def cell_value(row: SheetRow, column_id: str) -> str:
    cell = row.cells.get(column_id)
    return "" if cell is None else cell.value


def _cell_version(row: SheetRow, column_id: str) -> int:
    cell = row.cells.get(column_id)
    return 0 if cell is None else cell.version


def clone_sheet(sheet: SheetRecord) -> SheetRecord:
    return SheetRecord(
        id=sheet.id,
        title=sheet.title,
        version=sheet.version,
        share_generation=sheet.share_generation,
        share_access=sheet.share_access,
        share_expires_day=sheet.share_expires_day,
        created_at=sheet.created_at,
        updated_at=sheet.updated_at,
        columns=[SheetColumn(column.id, column.name) for column in sheet.columns],
        rows=[
            SheetRow(row.id, {col_id: Cell(cell.value, cell.version) for col_id, cell in row.cells.items()})
            for row in sheet.rows
        ],
    )


def _sheet_to_json(sheet: SheetRecord) -> dict:
    rows = []
    for row in sheet.rows:
        cells = {}
        for column in sheet.columns:
            cell = row.cells.get(column.id)
            if cell is None:
                continue
            cells[column.id] = {"value": cell.value, "version": cell.version}
        rows.append({"id": row.id, "cells": cells})
    return {
        "id": sheet.id,
        "title": sheet.title,
        "version": sheet.version,
        "shareGeneration": sheet.share_generation,
        "shareAccess": sheet.share_access,
        "shareExpiresDay": sheet.share_expires_day,
        "createdAt": sheet.created_at,
        "updatedAt": sheet.updated_at,
        "columns": [{"id": column.id, "name": column.name} for column in sheet.columns],
        "rows": rows,
    }


def render_csv(column_names: list[str], records: list[tuple[str, dict[str, str]]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["_id", *column_names])
    for row_id, values in records:
        writer.writerow([row_id, *[values.get(name, "") for name in column_names]])
    return buffer.getvalue()


def sheet_csv(sheet: SheetRecord) -> str:
    names = [column.name for column in sheet.columns]
    records = []
    for row in sheet.rows:
        values = {column.name: cell_value(row, column.id) for column in sheet.columns}
        records.append((row.id, values))
    return render_csv(names, records)


def persist_sheet(runtime, sheet: SheetRecord) -> None:
    write_json(runtime.sheets_dir / f"{sheet.id}.json", _sheet_to_json(sheet))
    (runtime.sheets_dir / f"{sheet.id}.csv").write_text(sheet_csv(sheet), encoding="utf-8")


def delete_sheet_files(runtime, sheet_id: str) -> None:
    for suffix in (".json", ".csv"):
        path = runtime.sheets_dir / f"{sheet_id}{suffix}"
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _load_sheet(runtime, path) -> SheetRecord | None:
    file_id = path.stem
    if NOTE_ID_RE.fullmatch(file_id) is None or file_id in runtime.notes:
        return None
    meta = read_json(path)
    if not isinstance(meta, dict):
        return None
    raw_id = meta.get("id")
    if raw_id not in (None, file_id):
        return None
    generation, access, expires = _share_from_meta(meta)
    version = meta.get("version", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        version = 0
    columns: list[SheetColumn] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    raw_columns = meta.get("columns")
    if isinstance(raw_columns, list):
        for item in raw_columns:
            if not isinstance(item, dict):
                continue
            column_id = item.get("id")
            name = item.get("name")
            if not isinstance(column_id, str) or NOTE_ID_RE.fullmatch(column_id) is None:
                continue
            if not isinstance(name, str) or name == "" or name == "_id":
                continue
            if column_id in seen_ids or name in seen_names:
                continue
            seen_ids.add(column_id)
            seen_names.add(name)
            columns.append(SheetColumn(column_id, name))
    column_ids = {column.id for column in columns}
    rows: list[SheetRow] = []
    raw_rows = meta.get("rows")
    if isinstance(raw_rows, list):
        for item in raw_rows:
            if not isinstance(item, dict):
                continue
            row_id = item.get("id")
            if not isinstance(row_id, str) or NOTE_ID_RE.fullmatch(row_id) is None or row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            cells: dict[str, Cell] = {}
            raw_cells = item.get("cells")
            if isinstance(raw_cells, dict):
                for col_id, raw_cell in raw_cells.items():
                    if col_id not in column_ids or not isinstance(raw_cell, dict):
                        continue
                    value = raw_cell.get("value")
                    cell_version = raw_cell.get("version", 0)
                    if not isinstance(value, str):
                        continue
                    if isinstance(cell_version, bool) or not isinstance(cell_version, int) or cell_version < 0:
                        cell_version = 0
                    if value == "" and cell_version == 0:
                        continue
                    cells[col_id] = Cell(value, cell_version)
            rows.append(SheetRow(row_id, cells))
    title = meta.get("title")
    return SheetRecord(
        id=file_id,
        title=title if isinstance(title, str) and title else "untitled",
        version=version,
        share_generation=generation,
        share_access=access,
        share_expires_day=expires,
        created_at=str(meta.get("createdAt") or ""),
        updated_at=str(meta.get("updatedAt") or ""),
        columns=columns,
        rows=rows,
    )


def load_sheets_into_memory(runtime) -> None:
    runtime.sheets.clear()
    if not runtime.sheets_dir.exists():
        return
    for path in runtime.sheets_dir.iterdir():
        if path.suffix != ".json":
            continue
        sheet = _load_sheet(runtime, path)
        if sheet is not None and sheet.id not in runtime.sheets:
            runtime.sheets[sheet.id] = sheet


def create_sheet(runtime) -> SheetRecord:
    timestamp = now_iso()
    sheet = SheetRecord(
        id=allocate_note_id(runtime),
        title="untitled",
        version=0,
        share_generation=0,
        share_access="none",
        share_expires_day=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    update_share(sheet, "edit", False)
    runtime.sheets[sheet.id] = sheet
    persist_sheet(runtime, sheet)
    return sheet


def sheet_ticket(runtime, sheet: SheetRecord) -> str | None:
    if sheet.share_access not in ("view", "edit") or sheet.share_expires_day is None:
        return None
    return ticket_for(
        runtime.link_key,
        sheet.id,
        sheet.share_access,
        sheet.share_generation,
        sheet.share_expires_day,
    )


def summarize_sheet(runtime, sheet: SheetRecord) -> dict:
    return {
        "id": sheet.id,
        "title": sheet.title,
        "updatedAt": sheet.updated_at,
        "shareId": sheet_ticket(runtime, sheet),
        "columnCount": len(sheet.columns),
        "rowCount": len(sheet.rows),
    }


def search_sheets(runtime, query: str) -> list[dict]:
    needle = query.strip().lower()
    found = [summarize_sheet(runtime, sheet) for sheet in runtime.sheets.values()]
    if needle:
        found = [item for item in found if needle in item["title"].lower()]
    found.sort(key=lambda item: item["updatedAt"], reverse=True)
    return found


def sheet_for_client(runtime, request, sheet: SheetRecord) -> dict:
    ticket = sheet_ticket(runtime, sheet)
    return {
        "id": sheet.id,
        "title": sheet.title,
        "version": sheet.version,
        "updatedAt": sheet.updated_at,
        "createdAt": sheet.created_at,
        "columnCount": len(sheet.columns),
        "rowCount": len(sheet.rows),
        "shareAccess": sheet.share_access,
        "shareId": ticket,
        "shareUrl": share_url(request, ticket) if ticket else "",
    }


def resolve_sheet_share(runtime, ticket: str) -> tuple[SheetRecord, str] | None:
    opened = open_ticket(runtime.link_key, ticket, today_utc())
    if opened is None or opened.access not in ("view", "edit"):
        return None
    sheet = runtime.sheets.get(opened.note_id)
    if sheet is None or sheet.share_access == "none":
        return None
    if sheet.share_generation != opened.generation or sheet.share_access != opened.access:
        return None
    return sheet, opened.access


def require_sheet(runtime, request, ticket: str, minimum: str) -> SheetRecord | None:
    resolved = resolve_sheet_share(runtime, ticket)
    if resolved is None:
        return None
    sheet, access = resolved
    if is_owner_authenticated(runtime, request.headers):
        return sheet
    if _SHEET_LEVELS.get(access, 0) < _SHEET_LEVELS[minimum]:
        return None
    return sheet


def _column_by_name(sheet: SheetRecord, name) -> SheetColumn:
    if not isinstance(name, str) or name == "":
        raise SheetOpError(400, "Unknown column.")
    for column in sheet.columns:
        if column.name == name:
            return column
    raise SheetOpError(400, "Unknown column.")


def _require_new_name(sheet: SheetRecord, name) -> str:
    if not isinstance(name, str) or name == "":
        raise SheetOpError(400, "Column name is required.")
    if name == "_id":
        raise SheetOpError(400, "Column name _id is reserved.")
    if any(column.name == name for column in sheet.columns):
        raise SheetOpError(400, "Column already exists.")
    return name


def _resolve_row(sheet: SheetRecord, spec) -> SheetRow:
    if isinstance(spec, str):
        for row in sheet.rows:
            if row.id == spec:
                return row
        raise SheetOpError(400, "Row not found.")
    if isinstance(spec, dict) and "column" in spec and "value" in spec:
        value = spec.get("value")
        if not isinstance(value, str):
            raise SheetOpError(400, "value must be a string.")
        column = _column_by_name(sheet, spec.get("column"))
        matches = [row for row in sheet.rows if cell_value(row, column.id) == value]
        if len(matches) != 1:
            raise SheetOpError(400, "Condition must match exactly one row.")
        return matches[0]
    raise SheetOpError(400, "Row not found.")


def _place(items: list, before, new_item) -> None:
    if before is None:
        items.append(new_item)
        return
    if not isinstance(before, str):
        raise SheetOpError(400, "before not found.")
    index = next((pos for pos, item in enumerate(items) if item.id == before), -1)
    if index < 0:
        raise SheetOpError(400, "before not found.")
    items.insert(index, new_item)


def apply_ops(sheet: SheetRecord, base_version, ops) -> tuple[SheetRecord, list[str]]:
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise SheetOpError(400, "baseVersion must be an integer.")
    if not isinstance(ops, list) or len(ops) == 0:
        raise SheetOpError(400, "ops must be a non-empty array.")
    original_version = sheet.version
    working = clone_sheet(sheet)
    used = _used_ids(working)
    changed: set[tuple[str, str]] = set()
    inserted: list[str] = []
    for index, op in enumerate(ops):
        try:
            if not isinstance(op, dict) or not isinstance(op.get("op"), str):
                raise SheetOpError(400, "Invalid operation.")
            kind = op["op"]
            if kind in _COLUMN_OPS and base_version != original_version:
                raise SheetOpError(409, "conflict", version=original_version)
            if kind == "insert_column":
                _insert_column(working, op, used)
            elif kind == "rename_column":
                _rename_column(working, op)
            elif kind == "delete_column":
                _delete_column(working, op, changed)
            elif kind == "insert_row":
                inserted.append(_insert_row(working, op, used, changed))
            elif kind == "delete_row":
                _delete_row(working, op, base_version, original_version, changed, inserted)
            elif kind == "set":
                _set_cell(working, op, base_version, original_version, changed)
            else:
                raise SheetOpError(400, "Invalid operation.")
        except SheetOpError as exc:
            if exc.op is None:
                exc.op = index
            if exc.status == 409 and exc.version is None:
                exc.version = original_version
            raise
    new_version = original_version + 1
    column_ids = {column.id for column in working.columns}
    rows_by_id = {row.id: row for row in working.rows}
    for row_id, column_id in changed:
        row = rows_by_id.get(row_id)
        if row is None or column_id not in column_ids:
            continue
        cell = row.cells.get(column_id)
        if cell is not None:
            cell.version = new_version
    working.version = new_version
    return working, inserted


def _insert_column(sheet: SheetRecord, op: dict, used: set[str]) -> None:
    name = _require_new_name(sheet, op.get("name"))
    column = SheetColumn(_local_id(used), name)
    _place(sheet.columns, op.get("before"), column)


def _rename_column(sheet: SheetRecord, op: dict) -> None:
    column = _column_by_name(sheet, op.get("name"))
    new_name = op.get("newName")
    if not isinstance(new_name, str) or new_name == "":
        raise SheetOpError(400, "Column name is required.")
    if new_name == "_id":
        raise SheetOpError(400, "Column name _id is reserved.")
    if new_name != column.name and any(item.name == new_name for item in sheet.columns):
        raise SheetOpError(400, "Column already exists.")
    column.name = new_name


def _delete_column(sheet: SheetRecord, op: dict, changed: set[tuple[str, str]]) -> None:
    column = _column_by_name(sheet, op.get("name"))
    sheet.columns = [item for item in sheet.columns if item.id != column.id]
    for row in sheet.rows:
        row.cells.pop(column.id, None)
    changed.difference_update({item for item in changed if item[1] == column.id})


def _insert_row(sheet: SheetRecord, op: dict, used: set[str], changed: set[tuple[str, str]]) -> str:
    values = op.get("values", None)
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise SheetOpError(400, "Invalid operation.")
    row = SheetRow(_local_id(used), {})
    for key, value in values.items():
        column = _column_by_name(sheet, key)
        if not isinstance(value, str):
            raise SheetOpError(400, "value must be a string.")
        row.cells[column.id] = Cell(value, 0)
        changed.add((row.id, column.id))
    _place(sheet.rows, op.get("before"), row)
    return row.id


def _delete_row(sheet, op, base_version, original_version, changed, inserted) -> None:
    row = _resolve_row(sheet, op.get("row"))
    for column in sheet.columns:
        if _cell_version(row, column.id) > base_version:
            raise SheetOpError(409, "conflict", version=original_version)
    sheet.rows = [item for item in sheet.rows if item.id != row.id]
    changed.difference_update({item for item in changed if item[0] == row.id})
    if row.id in inserted:
        inserted.remove(row.id)


def _set_cell(sheet, op, base_version, original_version, changed) -> None:
    column = _column_by_name(sheet, op.get("column"))
    row = _resolve_row(sheet, op.get("row"))
    if _cell_version(row, column.id) > base_version:
        raise SheetOpError(409, "conflict", version=original_version)
    value = op.get("value")
    if not isinstance(value, str):
        raise SheetOpError(400, "value must be a string.")
    row.cells[column.id] = Cell(value, 0)
    changed.add((row.id, column.id))


def commit_ops(runtime, sheet: SheetRecord, base_version, ops) -> tuple[SheetRecord, list[str]]:
    updated, inserted = apply_ops(sheet, base_version, ops)
    updated.updated_at = now_iso()
    runtime.sheets[sheet.id] = updated
    persist_sheet(runtime, updated)
    return updated, inserted


def import_csv(runtime, sheet: SheetRecord, base_version, text: str) -> SheetRecord:
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise SheetOpError(400, "baseVersion must be an integer.")
    if sheet.columns or sheet.rows:
        raise SheetOpError(409, "Import only replaces an empty table.", version=sheet.version)
    if base_version != sheet.version:
        raise SheetOpError(409, "conflict", version=sheet.version)
    names, data = _parse_import_csv(text)
    working = clone_sheet(sheet)
    used: set[str] = set()
    new_version = sheet.version + 1
    columns = [SheetColumn(_local_id(used), name) for name in names]
    rows = []
    for values in data:
        row = SheetRow(_local_id(used), {})
        for column, value in zip(columns, values, strict=True):
            row.cells[column.id] = Cell(value, new_version)
        rows.append(row)
    working.columns = columns
    working.rows = rows
    working.version = new_version
    working.updated_at = now_iso()
    runtime.sheets[sheet.id] = working
    persist_sheet(runtime, working)
    return working


def _parse_import_csv(text: str) -> tuple[list[str], list[list[str]]]:
    try:
        parsed = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error:
        raise SheetOpError(400, "Invalid CSV.") from None
    if not parsed or not any(name != "" for name in parsed[0]):
        raise SheetOpError(400, "CSV header is required.")
    header = parsed[0]
    names: list[str] = []
    keep: list[int] = []
    for index, name in enumerate(header):
        if name == "_id":
            continue
        if name == "":
            raise SheetOpError(400, "Column name is required.")
        if name in names:
            raise SheetOpError(400, "Column already exists.")
        names.append(name)
        keep.append(index)
    if not names:
        raise SheetOpError(400, "CSV header is required.")
    data: list[list[str]] = []
    for row in parsed[1:]:
        if len(row) > len(header):
            raise SheetOpError(400, "Row has more columns than the header.")
        data.append([row[index] if index < len(row) else "" for index in keep])
    if len(data) > ROW_CAP:
        raise SheetOpError(400, "Import exceeds 2000 rows.")
    return names, data


def _row_records(sheet: SheetRecord) -> tuple[list[str], list[str], list[dict[str, str]]]:
    names = [column.name for column in sheet.columns]
    row_ids = [row.id for row in sheet.rows]
    row_values = []
    for row in sheet.rows:
        row_values.append({column.name: cell_value(row, column.id) for column in sheet.columns})
    return names, row_ids, row_values


def run_sheet_query(sheet: SheetRecord, query: str | None) -> tuple[dict, str]:
    names, row_ids, row_values = _row_records(sheet)
    selected, indexes, truncated = execute(names, row_ids, row_values, query)
    rows_json = []
    records = []
    for index in indexes:
        row_id = row_ids[index]
        values = {name: row_values[index].get(name, "") for name in selected}
        item = {name: values[name] for name in selected}
        item["id"] = row_id
        rows_json.append(item)
        records.append((row_id, values))
    payload = {
        "version": sheet.version,
        "columns": selected,
        "rows": rows_json,
        "truncated": truncated,
    }
    return payload, render_csv(selected, records)


def ops_http_body(sheet: SheetRecord, inserted: list[str]) -> dict:
    if len(sheet.rows) > ROW_CAP:
        return {"version": sheet.version, "inserted": inserted}
    payload, _csv = run_sheet_query(sheet, None)
    return payload


def sheet_grid(sheet: SheetRecord) -> dict:
    return {
        "version": sheet.version,
        "columns": [{"id": column.id, "name": column.name} for column in sheet.columns],
        "rows": [
            {"id": row.id, "cells": {column.id: cell_value(row, column.id) for column in sheet.columns}}
            for row in sheet.rows
        ],
    }


def sheet_ws_payload(runtime, sheet: SheetRecord) -> dict:
    payload = {
        "sheetId": sheet.id,
        "title": sheet.title,
        "version": sheet.version,
        "shareAccess": sheet.share_access,
        "shareId": sheet_ticket(runtime, sheet),
    }
    if len(sheet.rows) > ROW_CAP:
        payload["tooLarge"] = True
        payload["rowCount"] = len(sheet.rows)
        return payload
    grid = sheet_grid(sheet)
    payload["columns"] = grid["columns"]
    payload["rows"] = grid["rows"]
    return payload


def sheet_ops_result(runtime, sheet: SheetRecord, inserted: list[str]) -> dict:
    if len(sheet.rows) > ROW_CAP:
        return {
            "type": "ops-result",
            "ok": True,
            "version": sheet.version,
            "inserted": inserted,
            "tooLarge": True,
            "rowCount": len(sheet.rows),
        }
    payload = sheet_ws_payload(runtime, sheet)
    payload["type"] = "ops-result"
    payload["ok"] = True
    return payload



