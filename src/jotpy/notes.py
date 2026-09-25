from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from jotpy.auth import get_commenter_identity, is_owner_authenticated, request_host, request_protocol
from jotpy.collab import (
    ApplyResult,
    CollabState,
    apply_client_mutations,
    collab_from_markdown,
    collab_to_markdown,
    id_at_index,
    id_before_index,
    load_collab_state,
    new_collab_state,
    save_collab_state,
)
from jotpy.js_text import count_occurrences, utf16_index_of, utf16_len, utf16_slice
from jotpy.markdown_html import render_markdown
from jotpy.util import create_short_id, now_iso, read_json, write_json

SHARE_LEVELS = {"none": 0, "view": 1, "comment": 2, "edit": 3}


@dataclass
class NoteRecord:
    id: str
    title: str
    share_id: str
    share_access: str
    created_at: str
    updated_at: str
    threads: list
    markdown: str
    collab: CollabState
    client_acks: dict = field(default_factory=dict)


def normalize_title(value: str) -> str:
    return utf16_slice(value.strip(), 0, 160) or "untitled"


def normalize_comment_body(value: str) -> str:
    return utf16_slice(value.strip(), 0, 4000)


def normalize_commenter_name(value: str) -> str:
    return utf16_slice(value.strip(), 0, 80)


def sanitize_anchor(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    quote = utf16_slice(str(value.get("quote") or ""), 0, 1000)
    prefix = utf16_slice(str(value.get("prefix") or ""), 0, 200)
    suffix = utf16_slice(str(value.get("suffix") or ""), 0, 200)
    try:
        start = float(value.get("start"))
        end = float(value.get("end"))
    except (TypeError, ValueError):
        return None
    if not quote or start != start or end != end or start < 0 or end < start:
        return None
    start_num: int | float = int(start) if start.is_integer() else start
    end_num: int | float = int(end) if end.is_integer() else end
    return {"quote": quote, "prefix": prefix, "suffix": suffix, "start": start_num, "end": end_num}


def _normalize_threads(raw) -> list:
    if not isinstance(raw, list):
        return []
    threads = []
    for thread in raw:
        if not isinstance(thread, dict):
            continue
        messages = []
        raw_messages = thread.get("messages")
        if isinstance(raw_messages, list):
            for message in raw_messages:
                if not isinstance(message, dict):
                    continue
                item = dict(message)
                parent = item.get("parentId")
                item["parentId"] = parent if isinstance(parent, str) else None
                messages.append(item)
        copied = dict(thread)
        copied["messages"] = messages
        threads.append(copied)
    return threads


def load_notes_into_memory(runtime) -> None:
    runtime.notes.clear()
    if not runtime.notes_dir.exists():
        return
    for path in runtime.notes_dir.iterdir():
        if not path.name.endswith(".md"):
            continue
        file_id = path.name[: -len(".md")]
        meta_path = runtime.notes_dir / f"{file_id}.json"
        if not meta_path.exists():
            continue
        markdown = path.read_text(encoding="utf-8")
        meta = read_json(meta_path)
        if not isinstance(meta, dict):
            continue
        if isinstance(meta.get("collab"), dict):
            collab = load_collab_state(meta["collab"])
        elif isinstance(meta.get("collabState"), dict):
            collab = load_collab_state(meta["collabState"])
        else:
            collab = collab_from_markdown(markdown)
        runtime.notes[file_id] = NoteRecord(
            id=str(meta.get("id") or file_id),
            title=str(meta.get("title") or "untitled"),
            share_id=str(meta.get("shareId") or ""),
            share_access=meta.get("shareAccess") or "none",
            created_at=str(meta.get("createdAt") or ""),
            updated_at=str(meta.get("updatedAt") or ""),
            threads=_normalize_threads(meta.get("threads")),
            markdown=collab_to_markdown(collab),
            collab=collab,
            client_acks={},
        )


def persist_note(runtime, note: NoteRecord) -> None:
    note.markdown = collab_to_markdown(note.collab)
    meta = {
        "id": note.id,
        "title": note.title,
        "shareId": note.share_id,
        "shareAccess": note.share_access,
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
        "threads": note.threads,
        "collab": save_collab_state(note.collab),
    }
    (runtime.notes_dir / f"{note.id}.md").write_text(note.markdown, encoding="utf-8")
    write_json(runtime.notes_dir / f"{note.id}.json", meta)


def create_note(runtime) -> NoteRecord:
    timestamp = now_iso()
    note = NoteRecord(
        id=create_short_id(),
        title="untitled",
        share_id=create_short_id(14),
        share_access="none",
        created_at=timestamp,
        updated_at=timestamp,
        threads=[],
        markdown="",
        collab=new_collab_state(),
    )
    runtime.notes[note.id] = note
    persist_note(runtime, note)
    return note


def delete_note_files(runtime, note_id: str) -> None:
    for suffix in (".md", ".json"):
        path = runtime.notes_dir / f"{note_id}{suffix}"
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def find_note_by_share_id(runtime, share_id: str) -> NoteRecord | None:
    for note in runtime.notes.values():
        if note.share_id == share_id:
            return note
    return None


def locate_message(note: NoteRecord, message_id: str):
    for thread in note.threads:
        for message in thread.get("messages") or []:
            if message.get("id") == message_id:
                return thread, message
    return None


def build_snippet(markdown: str, needle: str) -> str:
    source = re.sub(r"\s+", " ", markdown).strip()
    if not source:
        return ""
    if not needle:
        return source[:140]
    index = source.lower().find(needle)
    if index == -1:
        return source[:140]
    start = max(0, index - 40)
    end = min(len(source), index + len(needle) + 80)
    return source[start:end]


def summarize_note(note: NoteRecord, needle: str) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "updatedAt": note.updated_at,
        "shareId": note.share_id,
        "snippet": build_snippet(note.markdown, needle),
    }


def search_notes(runtime, query: str) -> list[dict]:
    needle = query.strip().lower()
    found = [summarize_note(note, needle) for note in runtime.notes.values()]
    if needle:
        found = [
            item
            for item in found
            if needle in item["title"].lower() or needle in item["snippet"].lower()
        ]
    found.sort(key=lambda item: item["updatedAt"], reverse=True)
    return found


def share_url(request, share_id: str) -> str:
    return f"{request_protocol(request)}://{request_host(request)}/s/{share_id}"


def build_viewer_info(runtime, request, name_override=None, has_identity_override=None) -> dict:
    commenter = get_commenter_identity(request.headers)
    if name_override is None:
        name = commenter["name"]
    else:
        name = name_override
    if has_identity_override is None:
        has_identity = bool(commenter["id"])
    else:
        has_identity = has_identity_override
    return {
        "isOwner": is_owner_authenticated(runtime, request.headers),
        "commenterName": name,
        "hasCommenterIdentity": has_identity,
    }


def serialize_threads(runtime, note: NoteRecord, request) -> list[dict]:
    viewer = build_viewer_info(runtime, request)
    commenter = get_commenter_identity(request.headers)
    ordered = sorted(note.threads, key=lambda thread: (thread["anchor"]["start"], thread["createdAt"]))
    result = []
    for thread in ordered:
        messages = sorted(thread.get("messages") or [], key=lambda message: message["createdAt"])
        result.append(
            {
                "id": thread["id"],
                "resolved": thread["resolved"],
                "createdAt": thread["createdAt"],
                "updatedAt": thread["updatedAt"],
                "anchor": thread["anchor"],
                "canReply": viewer["isOwner"] or viewer["hasCommenterIdentity"],
                "canResolve": viewer["isOwner"] or viewer["hasCommenterIdentity"],
                "canDeleteThread": viewer["isOwner"],
                "messages": [
                    {
                        "id": message["id"],
                        "parentId": message.get("parentId"),
                        "authorName": message["authorName"],
                        "body": message["body"],
                        "createdAt": message["createdAt"],
                        "updatedAt": message["updatedAt"],
                        "canEdit": viewer["isOwner"] or bool(commenter["id"] and commenter["id"] == message.get("authorId")),
                        "canDelete": viewer["isOwner"]
                        or bool(commenter["id"] and commenter["id"] == message.get("authorId")),
                    }
                    for message in messages
                ],
            }
        )
    return result


def serialize_note_for_client(runtime, note: NoteRecord, request) -> dict:
    return {
        "note": {
            "id": note.id,
            "title": note.title,
            "markdown": note.markdown,
            "renderedHtml": render_markdown(note.markdown),
            "shareId": note.share_id,
            "shareAccess": note.share_access,
            "shareUrl": share_url(request, note.share_id),
            "updatedAt": note.updated_at,
            "createdAt": note.created_at,
        },
        "viewer": build_viewer_info(runtime, request),
        "threads": serialize_threads(runtime, note, request),
    }


def plan_text_edits(note: NoteRecord, edits: list) -> tuple[list[str], list[dict], int, CollabState, str]:
    working = note.collab
    markdown = note.markdown
    sender_counter = 0
    errors: list[str] = []
    updates: list[dict] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            old_text = ""
            new_text = ""
        else:
            old_raw = edit.get("oldText")
            new_raw = edit.get("newText")
            old_text = "" if not old_raw else str(old_raw)
            new_text = "" if not new_raw else str(new_raw)
        if not old_text:
            errors.append(f"Edit {index}: oldText is empty.")
            continue
        first = utf16_index_of(markdown, old_text)
        if first == -1:
            errors.append(f"Edit {index}: oldText not found.")
            continue
        second = utf16_index_of(markdown, old_text, first + 1)
        if second != -1:
            errors.append(
                f"Edit {index}: oldText is ambiguous (found {count_occurrences(markdown, old_text)} times)."
            )
            continue
        next_counter = sender_counter + 1
        mutations: list[dict] = []
        if utf16_len(old_text) > 0:
            mutations.append(
                {
                    "name": "delete",
                    "clientCounter": next_counter,
                    "args": {
                        "startId": _id_json(id_at_index(working, first)),
                        "endId": _id_json(id_at_index(working, first + utf16_len(old_text) - 1)),
                        "contentLength": utf16_len(old_text),
                    },
                }
            )
            next_counter += 1
        if new_text:
            before = id_before_index(working, first) if first > 0 else None
            mutations.append(
                {
                    "name": "insert",
                    "clientCounter": next_counter,
                    "args": {
                        "before": _id_json(before) if before is not None else None,
                        "id": {"bunchId": str(uuid.uuid4()), "counter": 0},
                        "content": new_text,
                        "isInWord": False,
                    },
                }
            )
            next_counter += 1
        result: ApplyResult = apply_client_mutations(working, mutations)
        working = result.state
        markdown = result.markdown
        updates.extend(result.id_list_updates)
        last = mutations[-1]["clientCounter"] if mutations else 0
        sender_counter = last or sender_counter
    return errors, updates, sender_counter, working, markdown


def _id_json(element_id) -> dict:
    return {"bunchId": element_id.bunch_id, "counter": element_id.counter}


def can_manage_message(runtime, request, message: dict) -> bool:
    if is_owner_authenticated(runtime, request.headers):
        return True
    commenter = get_commenter_identity(request.headers)
    return bool(commenter["id"] and commenter["id"] == message.get("authorId"))


def can_manage_thread(runtime, request, thread: dict) -> bool:
    if is_owner_authenticated(runtime, request.headers):
        return True
    commenter = get_commenter_identity(request.headers)
    if not commenter["id"]:
        return False
    return any(message.get("authorId") == commenter["id"] for message in thread.get("messages") or [])


def require_share_note(runtime, request, share_id: str, minimum: str) -> NoteRecord | None:
    note = find_note_by_share_id(runtime, share_id)
    if note is None:
        return None
    if is_owner_authenticated(runtime, request.headers):
        return note
    if SHARE_LEVELS.get(note.share_access, 0) < SHARE_LEVELS[minimum]:
        return None
    return note
