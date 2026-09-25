from __future__ import annotations

import json
import math
import traceback
import urllib.parse

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from jotpy.auth import (
    OWNER_TOKEN_KEY,
    auth_configured,
    bearer_token,
    clear_owner_session_cookie,
    create_api_key,
    delete_api_key,
    get_api_key_label,
    get_commenter_identity,
    get_or_create_commenter_id,
    initialize_owner_auth,
    is_owner_authenticated,
    issue_owner_token,
    list_api_keys,
    password_matches,
    revoke_owner_token,
    set_commenter_name_cookie,
    set_owner_session_cookie,
    verify_owner_token,
)
from jotpy.collab import collab_from_markdown, save_collab_state
from jotpy.errors import ApiError
from jotpy.js_text import utf16_index_of, utf16_len, utf16_slice
from jotpy.markdown_html import render_markdown
from jotpy.notes import (
    can_manage_message,
    can_manage_thread,
    create_note,
    delete_note_files,
    find_note_by_share_id,
    locate_message,
    normalize_comment_body,
    normalize_commenter_name,
    normalize_title,
    persist_note,
    plan_text_edits,
    require_share_note,
    sanitize_anchor,
    search_notes,
    serialize_note_for_client,
    serialize_threads,
    share_url,
    summarize_note,
    build_viewer_info,
)
from jotpy.pages import render_app_shell, render_auth_page, render_simple_page
from jotpy.util import create_id, now_iso
from jotpy.ws import (
    broadcast_editor_hello,
    broadcast_editor_mutation,
    broadcast_note_update,
    broadcast_threads_updated,
    enforce_share_access,
    websocket_endpoint,
)


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error}, status_code=status)


def _ok(payload: dict | None = None, status: int = 200) -> JSONResponse:
    body = {"ok": True}
    if payload:
        body.update(payload)
    return JSONResponse(body, status_code=status)


def _page_missing(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(render_simple_page(title, body), status_code=404)


async def read_json_object(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    ctype = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in ctype:
        parsed = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(500, "Internal server error.") from exc
    if isinstance(data, dict):
        return data
    return {}


def _owner(runtime, request: Request) -> JSONResponse | None:
    if not is_owner_authenticated(runtime, request.headers):
        return _error(401, "Unauthorized.")
    return None


def _note_or_error(runtime, note_id: str):
    note = runtime.notes.get(note_id)
    if note is None:
        return None, _error(404, "Note not found.")
    return note, None


def _share_or_error(runtime, request: Request, share_id: str, minimum: str):
    if find_note_by_share_id(runtime, share_id) is None:
        return None, _error(404, "Shared note not found.")
    note = require_share_note(runtime, request, share_id, minimum)
    if note is None:
        return None, _error(404, "Shared note not found.")
    return note, None


async def _save_threads(runtime, note) -> None:
    persist_note(runtime, note)
    await broadcast_note_update(runtime, note)
    await broadcast_threads_updated(runtime, note)


def _owner_author(runtime, request: Request, body: dict) -> str:
    token = bearer_token(request.headers)
    label = get_api_key_label(runtime, token) if token else None
    return label or "Owner"


def _ensure_author(runtime, request: Request, response: JSONResponse, body: dict):
    if is_owner_authenticated(runtime, request.headers):
        return {"authorId": "__owner__", "authorName": "Owner"}
    commenter = get_commenter_identity(request.headers)
    name = commenter["name"] or normalize_commenter_name(str(body.get("name") or ""))
    if not name:
        return None
    commenter_id = commenter["id"] or get_or_create_commenter_id(request, response)
    return {"authorId": commenter_id, "authorName": name}


def _query_number(raw: str | None):
    if raw is None or raw == "":
        return None
    try:
        number = float(raw)
    except ValueError:
        return math.nan
    if math.isnan(number):
        return math.nan
    if number.is_integer():
        return int(number)
    return number


def _js_or(value, fallback):
    if value is None or value == 0 or (isinstance(value, float) and math.isnan(value)):
        return fallback
    return value


def register_routes(app: FastAPI) -> None:
    @app.get("/health")
    async def health():
        return PlainTextResponse("ok")

    @app.get("/login")
    async def login_page(request: Request):
        runtime = request.app.state.runtime
        async with runtime.lock:
            if is_owner_authenticated(runtime, request.headers):
                return RedirectResponse("/", status_code=302)
            mode = "login" if auth_configured(runtime) else "setup"
            return HTMLResponse(render_auth_page(mode))

    @app.get("/")
    async def home(request: Request):
        runtime = request.app.state.runtime
        async with runtime.lock:
            if not is_owner_authenticated(runtime, request.headers):
                return RedirectResponse("/login", status_code=302)
            return HTMLResponse(render_app_shell("list", "Notes"))

    @app.get("/notes/{note_id}")
    async def note_page(request: Request, note_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            if not is_owner_authenticated(runtime, request.headers):
                return RedirectResponse("/login", status_code=302)
            note = runtime.notes.get(note_id)
            if note is None:
                return _page_missing("Not found", "<p>Note not found.</p><p><a href=\"/\">Back</a></p>")
            return HTMLResponse(render_app_shell("editor", note.title, {"noteId": note.id}))

    @app.get("/s/{share_id}")
    async def share_page(request: Request, share_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            note = find_note_by_share_id(runtime, share_id)
            if note is None or note.share_access == "none":
                return _page_missing("Not found", "<p>Shared note not found.</p>")
            return HTMLResponse(
                render_app_shell(
                    "public",
                    note.title,
                    {"shareId": note.share_id, "shareAccess": note.share_access},
                )
            )

    @app.get("/api/viewer")
    async def viewer(request: Request):
        runtime = request.app.state.runtime
        async with runtime.lock:
            return _ok(
                {
                    "authConfigured": auth_configured(runtime),
                    "ownerAuthenticated": is_owner_authenticated(runtime, request.headers),
                    "ownerLocalStorageTokenKey": OWNER_TOKEN_KEY,
                    "viewer": build_viewer_info(runtime, request),
                }
            )

    @app.post("/api/auth/setup")
    async def auth_setup(request: Request):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            if auth_configured(runtime):
                return _error(400, "Password already configured.")
            password = str(body.get("password") or "")
            confirm = str(body.get("confirmPassword") or "")
            if utf16_len(password) < 8:
                return _error(400, "Use at least 8 characters.")
            if password != confirm:
                return _error(400, "Passwords do not match.")
            token = initialize_owner_auth(runtime, password)
            return _ok({"token": token, "ownerLocalStorageTokenKey": OWNER_TOKEN_KEY})

    @app.post("/api/auth/login")
    async def auth_login(request: Request):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            if not auth_configured(runtime):
                return _error(400, "Password is not configured yet.")
            password = str(body.get("password") or "")
            if not password_matches(runtime, password):
                return _error(401, "Wrong password.")
            token = issue_owner_token(runtime)
            return _ok({"token": token, "ownerLocalStorageTokenKey": OWNER_TOKEN_KEY})

    @app.post("/api/auth/token")
    async def auth_token(request: Request):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            token = str(body.get("token") or "")
            if not token or not verify_owner_token(runtime, token):
                response = JSONResponse({"ok": False}, status_code=401)
                clear_owner_session_cookie(response, request)
                return response
            response = _ok()
            set_owner_session_cookie(response, request, token)
            return response

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request):
        runtime = request.app.state.runtime
        async with runtime.lock:
            cookies = request.headers.get("cookie")
            from jotpy.auth import OWNER_SESSION_COOKIE, parse_cookies

            token = parse_cookies(cookies).get(OWNER_SESSION_COOKIE)
            if token:
                revoke_owner_token(runtime, token)
            response = _ok()
            clear_owner_session_cookie(response, request)
            return response

    @app.get("/api/keys")
    async def keys_list(request: Request):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            return _ok({"keys": list_api_keys(runtime)})

    @app.post("/api/keys")
    async def keys_create(request: Request):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            created = create_api_key(runtime, str(body.get("label") or "unnamed"))
            return _ok(created)

    @app.delete("/api/keys/{key_id}")
    async def keys_delete(request: Request, key_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            if not delete_api_key(runtime, key_id):
                return _error(404, "API key not found.")
            return _ok()

    @app.get("/api/notes")
    async def notes_list(request: Request):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            query = str(request.query_params.get("q") or "")
            return _ok({"notes": search_notes(runtime, query)})

    @app.post("/api/notes")
    async def notes_create(request: Request):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note = create_note(runtime)
            await broadcast_note_update(runtime, note)
            return _ok({"note": summarize_note(note, "")})

    @app.get("/api/notes/{note_id}")
    async def notes_get(request: Request, note_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            offset_num = _query_number(request.query_params.get("offset"))
            limit_num = _query_number(request.query_params.get("limit"))
            if offset_num is not None or limit_num is not None:
                lines = note.markdown.split("\n")
                start = max(0, _js_or(offset_num, 1) - 1)
                if _js_or(limit_num, None) is None:
                    end = len(lines)
                else:
                    end = min(len(lines), start + limit_num)
                start_i = int(start)
                end_i = int(end)
                chosen = lines[start_i:end_i]
                return _ok(
                    {
                        "note": {
                            "id": note.id,
                            "title": note.title,
                            "totalLines": len(lines),
                            "offset": start + 1,
                            "limit": len(chosen),
                            "remaining": len(lines) - end_i,
                            "content": "\n".join(f"{start_i + index + 1}: {line}" for index, line in enumerate(chosen)),
                        }
                    }
                )
            return _ok(serialize_note_for_client(runtime, note, request))

    @app.put("/api/notes/{note_id}")
    async def notes_put(request: Request, note_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            if "title" in body:
                raw_title = body.get("title")
                next_title = normalize_title(note.title if not raw_title else str(raw_title))
            else:
                next_title = note.title
            if "markdown" in body:
                raw_markdown = body.get("markdown")
                next_markdown = "" if not raw_markdown else str(raw_markdown)
            else:
                next_markdown = note.markdown
            if "shareAccess" in body and body.get("shareAccess") in ("none", "view", "comment", "edit"):
                next_access = body["shareAccess"]
            else:
                next_access = note.share_access
            title_changed = next_title != note.title
            markdown_changed = next_markdown != note.markdown
            access_changed = next_access != note.share_access
            note.title = next_title
            note.share_access = next_access
            if markdown_changed:
                note.collab = collab_from_markdown(next_markdown, note.collab.server_counter + 1)
                note.markdown = next_markdown
            note.updated_at = now_iso()
            persist_note(runtime, note)
            if access_changed:
                await enforce_share_access(runtime, note)
            if title_changed or markdown_changed or access_changed:
                await broadcast_editor_hello(runtime, note)
                await broadcast_note_update(runtime, note)
            return _ok({"savedAt": note.updated_at, "shareAccess": note.share_access})

    @app.delete("/api/notes/{note_id}")
    async def notes_delete(request: Request, note_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            del runtime.notes[note_id]
            delete_note_files(runtime, note_id)
            return _ok()

    @app.get("/api/notes/{note_id}/collab")
    async def notes_collab(request: Request, note_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            return _collab_payload(request, note)

    @app.post("/api/render")
    async def render_route(request: Request):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            markdown = str(body.get("markdown") or "")
            return _ok({"html": render_markdown(markdown)})

    @app.post("/api/notes/{note_id}/edit")
    async def notes_edit(request: Request, note_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            return await _apply_edits(runtime, request, note, body, allow_title=True)

    @app.post("/api/notes/{note_id}/threads")
    async def notes_thread_create(request: Request, note_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            quote = str(body.get("quote") or "")
            text = normalize_comment_body(str(body.get("body") or ""))
            if not quote or not text:
                return _error(400, "quote and body are required.")
            start = utf16_index_of(note.markdown, quote)
            if start == -1:
                return _error(400, "Quoted text not found in note.")
            end = start + utf16_len(quote)
            anchor = {
                "quote": quote,
                "prefix": utf16_slice(note.markdown, max(0, start - 32), start),
                "suffix": utf16_slice(note.markdown, end, end + 32),
                "start": start,
                "end": end,
            }
            timestamp = now_iso()
            thread = {
                "id": create_id(10),
                "resolved": False,
                "createdAt": timestamp,
                "updatedAt": timestamp,
                "anchor": anchor,
                "messages": [
                    {
                        "id": create_id(10),
                        "parentId": None,
                        "authorId": "__owner__",
                        "authorName": _owner_author(runtime, request, body),
                        "body": text,
                        "createdAt": timestamp,
                        "updatedAt": timestamp,
                    }
                ],
            }
            note.threads.append(thread)
            note.updated_at = timestamp
            await _save_threads(runtime, note)
            return _ok({"thread": {"id": thread["id"]}})

    @app.post("/api/notes/{note_id}/threads/{thread_id}/replies")
    async def notes_thread_reply(request: Request, note_id: str, thread_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            thread = next((item for item in note.threads if item.get("id") == thread_id), None)
            if thread is None:
                return _error(404, "Thread not found.")
            text = normalize_comment_body(str(body.get("body") or ""))
            parent = str(body.get("parentMessageId") or (thread["messages"][0]["id"] if thread["messages"] else ""))
            if not text:
                return _error(400, "body is required.")
            if not any(message.get("id") == parent for message in thread["messages"]):
                return _error(400, "Parent message not found.")
            timestamp = now_iso()
            thread["messages"].append(
                {
                    "id": create_id(10),
                    "parentId": parent,
                    "authorId": "__owner__",
                    "authorName": _owner_author(runtime, request, body),
                    "body": text,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                }
            )
            thread["updatedAt"] = timestamp
            note.updated_at = timestamp
            await _save_threads(runtime, note)
            return _ok()

    @app.patch("/api/notes/{note_id}/threads/{thread_id}")
    async def notes_thread_patch(request: Request, note_id: str, thread_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            thread = next((item for item in note.threads if item.get("id") == thread_id), None)
            if thread is None:
                return _error(404, "Thread not found.")
            thread["resolved"] = bool(body.get("resolved"))
            thread["updatedAt"] = now_iso()
            note.updated_at = thread["updatedAt"]
            await _save_threads(runtime, note)
            return _ok()

    @app.delete("/api/notes/{note_id}/threads/{thread_id}")
    async def notes_thread_delete(request: Request, note_id: str, thread_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            note.threads = [item for item in note.threads if item.get("id") != thread_id]
            note.updated_at = now_iso()
            await _save_threads(runtime, note)
            return _ok()

    @app.patch("/api/notes/{note_id}/messages/{message_id}")
    async def notes_message_patch(request: Request, note_id: str, message_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            located = locate_message(note, message_id)
            if located is None:
                return _error(404, "Message not found.")
            text = normalize_comment_body(str(body.get("body") or ""))
            if not text:
                return _error(400, "Body is required.")
            thread, message = located
            message["body"] = text
            message["updatedAt"] = now_iso()
            thread["updatedAt"] = message["updatedAt"]
            note.updated_at = message["updatedAt"]
            await _save_threads(runtime, note)
            return _ok()

    @app.delete("/api/notes/{note_id}/messages/{message_id}")
    async def notes_message_delete(request: Request, note_id: str, message_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            denied = _owner(runtime, request)
            if denied:
                return denied
            note, missing = _note_or_error(runtime, note_id)
            if missing:
                return missing
            located = locate_message(note, message_id)
            if located is None:
                return _error(404, "Message not found.")
            thread, message = located
            thread["messages"] = [item for item in thread["messages"] if item.get("id") != message["id"]]
            if not thread["messages"]:
                note.threads = [item for item in note.threads if item.get("id") != thread.get("id")]
            else:
                thread["updatedAt"] = now_iso()
            note.updated_at = now_iso()
            await _save_threads(runtime, note)
            return _ok()

    @app.get("/api/share/{share_id}")
    async def share_get(request: Request, share_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "view")
            if missing:
                return missing
            return _ok(serialize_note_for_client(runtime, note, request))

    @app.get("/api/share/{share_id}/note")
    async def share_note(request: Request, share_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "view")
            if missing:
                return missing
            return _ok(
                {
                    "note": {
                        "id": note.id,
                        "title": note.title,
                        "markdown": note.markdown,
                        "shareAccess": note.share_access,
                        "updatedAt": note.updated_at,
                    },
                    "threads": serialize_threads(runtime, note, request),
                }
            )

    @app.get("/api/share/{share_id}/collab")
    async def share_collab(request: Request, share_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "edit")
            if missing:
                return missing
            return _collab_payload(request, note)

    @app.post("/api/share/{share_id}/render")
    async def share_render(request: Request, share_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "view")
            if missing:
                return missing
            return _ok({"html": render_markdown(str(body.get("markdown") or ""))})

    @app.post("/api/share/{share_id}/edit")
    async def share_edit(request: Request, share_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "edit")
            if missing:
                return missing
            return await _apply_edits(runtime, request, note, body, allow_title=False)

    @app.post("/api/share/{share_id}/identity")
    async def share_identity(request: Request, share_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "comment")
            if missing:
                return missing
            name = normalize_commenter_name(str(body.get("name") or ""))
            if not name:
                return _error(400, "Name is required.")
            response = JSONResponse({"ok": True})
            commenter_id = get_or_create_commenter_id(request, response)
            set_commenter_name_cookie(request, response, name)
            return _replace_json(
                response,
                200,
                {
                    "ok": True,
                    "commenterIdSet": bool(commenter_id),
                    "viewer": build_viewer_info(
                        runtime,
                        request,
                        name_override=name,
                        has_identity_override=True,
                    ),
                },
            )

    @app.post("/api/share/{share_id}/threads")
    async def share_thread_create(request: Request, share_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "comment")
            if missing:
                return missing
            response = JSONResponse({"ok": True})
            identity = _ensure_author(runtime, request, response, body)
            if identity is None:
                return _error(400, "Set your name first.")
            anchor = sanitize_anchor(body.get("anchor"))
            text = normalize_comment_body(str(body.get("body") or ""))
            if anchor is None or not text:
                return _replace_json(response, 400, {"ok": False, "error": "Anchor and comment body are required."})
            timestamp = now_iso()
            thread = {
                "id": create_id(10),
                "resolved": False,
                "createdAt": timestamp,
                "updatedAt": timestamp,
                "anchor": anchor,
                "messages": [
                    {
                        "id": create_id(10),
                        "parentId": None,
                        "authorId": identity["authorId"],
                        "authorName": identity["authorName"],
                        "body": text,
                        "createdAt": timestamp,
                        "updatedAt": timestamp,
                    }
                ],
            }
            note.threads.append(thread)
            note.updated_at = timestamp
            await _save_threads(runtime, note)
            return _replace_json(response, 200, {"ok": True, "threads": serialize_threads(runtime, note, request)})

    @app.post("/api/share/{share_id}/threads/{thread_id}/replies")
    async def share_thread_reply(request: Request, share_id: str, thread_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "comment")
            if missing:
                return missing
            thread = next((item for item in note.threads if item.get("id") == thread_id), None)
            if thread is None:
                return _error(404, "Thread not found.")
            response = JSONResponse({"ok": True})
            identity = _ensure_author(runtime, request, response, body)
            if identity is None:
                return _error(400, "Set your name first.")
            text = normalize_comment_body(str(body.get("body") or ""))
            if not text:
                return _replace_json(response, 400, {"ok": False, "error": "Reply body is required."})
            raw_parent = body.get("parentMessageId")
            requested = raw_parent if isinstance(raw_parent, str) else ""
            parent = requested or (thread["messages"][0]["id"] if thread["messages"] else "")
            if not parent or not any(message.get("id") == parent for message in thread["messages"]):
                return _replace_json(response, 400, {"ok": False, "error": "Parent message not found."})
            timestamp = now_iso()
            thread["messages"].append(
                {
                    "id": create_id(10),
                    "parentId": parent,
                    "authorId": identity["authorId"],
                    "authorName": identity["authorName"],
                    "body": text,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                }
            )
            thread["updatedAt"] = timestamp
            note.updated_at = timestamp
            await _save_threads(runtime, note)
            return _replace_json(response, 200, {"ok": True, "threads": serialize_threads(runtime, note, request)})

    @app.patch("/api/share/{share_id}/threads/{thread_id}")
    async def share_thread_patch(request: Request, share_id: str, thread_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "comment")
            if missing:
                return missing
            thread = next((item for item in note.threads if item.get("id") == thread_id), None)
            if thread is None:
                return _error(404, "Thread not found.")
            if not can_manage_thread(runtime, request, thread):
                return _error(403, "Not allowed.")
            thread["resolved"] = bool(body.get("resolved"))
            thread["updatedAt"] = now_iso()
            note.updated_at = thread["updatedAt"]
            await _save_threads(runtime, note)
            return _ok({"threads": serialize_threads(runtime, note, request)})

    @app.delete("/api/share/{share_id}/threads/{thread_id}")
    async def share_thread_delete(request: Request, share_id: str, thread_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "comment")
            if missing:
                return missing
            thread = next((item for item in note.threads if item.get("id") == thread_id), None)
            if thread is None:
                return _error(404, "Thread not found.")
            if not is_owner_authenticated(runtime, request.headers):
                return _error(403, "Only the owner can delete a whole thread.")
            note.threads = [item for item in note.threads if item.get("id") != thread.get("id")]
            note.updated_at = now_iso()
            await _save_threads(runtime, note)
            return _ok({"threads": serialize_threads(runtime, note, request)})

    @app.patch("/api/share/{share_id}/messages/{message_id}")
    async def share_message_patch(request: Request, share_id: str, message_id: str):
        runtime = request.app.state.runtime
        body = await read_json_object(request)
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "comment")
            if missing:
                return missing
            located = locate_message(note, message_id)
            if located is None:
                return _error(404, "Message not found.")
            thread, message = located
            if not can_manage_message(runtime, request, message):
                return _error(403, "Not allowed.")
            text = normalize_comment_body(str(body.get("body") or ""))
            if not text:
                return _error(400, "Body is required.")
            message["body"] = text
            message["updatedAt"] = now_iso()
            thread["updatedAt"] = message["updatedAt"]
            note.updated_at = message["updatedAt"]
            await _save_threads(runtime, note)
            return _ok({"threads": serialize_threads(runtime, note, request)})

    @app.delete("/api/share/{share_id}/messages/{message_id}")
    async def share_message_delete(request: Request, share_id: str, message_id: str):
        runtime = request.app.state.runtime
        async with runtime.lock:
            note, missing = _share_or_error(runtime, request, share_id, "comment")
            if missing:
                return missing
            located = locate_message(note, message_id)
            if located is None:
                return _error(404, "Message not found.")
            thread, message = located
            if not can_manage_message(runtime, request, message):
                return _error(403, "Not allowed.")
            thread["messages"] = [item for item in thread["messages"] if item.get("id") != message.get("id")]
            if not thread["messages"]:
                note.threads = [item for item in note.threads if item.get("id") != thread.get("id")]
            else:
                thread["updatedAt"] = now_iso()
            note.updated_at = now_iso()
            await _save_threads(runtime, note)
            return _ok({"threads": serialize_threads(runtime, note, request)})

    @app.websocket("/")
    async def ws_route(websocket: WebSocket):
        await websocket_endpoint(websocket)

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def missing(full_path: str):
        return _page_missing("Not found", "<p>Page not found.</p>")


def _replace_json(response: JSONResponse, status: int, payload: dict) -> JSONResponse:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response.status_code = status
    response.body = raw
    response.headers["content-length"] = str(len(raw))
    return response


def _collab_payload(request: Request, note) -> JSONResponse:
    return _ok(
        {
            "noteId": note.id,
            "title": note.title,
            "shareId": note.share_id,
            "shareUrl": share_url(request, note.share_id),
            "serverCounter": note.collab.server_counter,
            "collabState": save_collab_state(note.collab),
        }
    )


async def _apply_edits(runtime, request: Request, note, body: dict, allow_title: bool):
    edits = body.get("edits")
    if not isinstance(edits, list) or len(edits) == 0:
        return _error(400, "edits must be a non-empty array of {oldText, newText}.")
    errors, updates, sender_counter, working, markdown = plan_text_edits(note, edits)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    note.collab = working
    note.markdown = markdown
    note.updated_at = now_iso()
    title_changed = False
    if allow_title and "title" in body:
        next_title = normalize_title(note.title if not body.get("title") else str(body.get("title")))
        title_changed = next_title != note.title
        note.title = next_title
    persist_note(runtime, note)
    if allow_title and title_changed:
        await broadcast_editor_hello(runtime, note)
    elif updates:
        await broadcast_editor_mutation(
            runtime,
            note,
            {
                "type": "mutation",
                "senderId": "__api__",
                "senderCounter": sender_counter,
                "serverCounter": note.collab.server_counter,
                "markdown": note.markdown,
                "idListUpdates": updates,
            },
        )
    await broadcast_note_update(runtime, note)
    return _ok({"savedAt": note.updated_at})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        if exc.status >= 500:
            traceback.print_exc()
        if exc.errors is not None:
            return JSONResponse({"ok": False, "errors": exc.errors}, status_code=exc.status)
        if exc.error is None:
            return JSONResponse({"ok": False}, status_code=exc.status)
        return JSONResponse({"ok": False, "error": exc.error}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        from starlette.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return HTMLResponse(
                render_simple_page("Not found", "<p>Page not found.</p>"),
                status_code=exc.status_code,
            )
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": "Internal server error."}, status_code=500)
