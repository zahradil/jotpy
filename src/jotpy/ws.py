from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from jotpy.auth import get_commenter_identity, is_owner_authenticated
from jotpy.collab import apply_client_mutations, save_collab_state
from jotpy.notes import find_note_by_share_id, persist_note
from jotpy.util import now_iso

CURSOR_COLORS = ["#4285f4", "#ea4335", "#34a853", "#fbbc04", "#9c27b0", "#ff6d00", "#00bcd4", "#e91e63"]


@dataclass
class ClientConn:
    ws: WebSocket
    kind: str
    note_id: str
    share_id: str
    client_id: str
    name: str
    color: str
    selection: dict | None = None


def _collaborative(conn: ClientConn, note_id: str) -> bool:
    return conn.kind in ("editor", "public-editor") and conn.note_id == note_id


async def send_message(ws: WebSocket, message: dict) -> None:
    if ws.client_state != WebSocketState.CONNECTED:
        return
    try:
        await ws.send_json(message)
    except Exception:
        return


def build_hello(note) -> dict:
    return {
        "type": "hello",
        "noteId": note.id,
        "title": note.title,
        "shareId": note.share_id,
        "markdown": note.markdown,
        "idListState": save_collab_state(note.collab)["idListState"],
        "serverCounter": note.collab.server_counter,
    }


async def broadcast_editor_hello(runtime, note) -> None:
    message = build_hello(note)
    for conn in runtime.clients:
        if _collaborative(conn, note.id):
            outgoing = dict(message)
            if conn.client_id:
                outgoing["clientId"] = conn.client_id
            await send_message(conn.ws, outgoing)


async def broadcast_editor_mutation(runtime, note, message: dict) -> None:
    for conn in runtime.clients:
        if _collaborative(conn, note.id):
            await send_message(conn.ws, message)


async def broadcast_note_update(runtime, note) -> None:
    message = {"type": "updated", "noteId": note.id, "shareId": note.share_id, "updatedAt": note.updated_at}
    for conn in runtime.clients:
        if conn.kind == "public-viewer" and conn.share_id == note.share_id:
            await send_message(conn.ws, message)


async def broadcast_threads_updated(runtime, note) -> None:
    message = {"type": "threads-updated", "noteId": note.id, "shareId": note.share_id}
    for conn in runtime.clients:
        if conn.note_id == note.id:
            await send_message(conn.ws, message)


async def enforce_share_access(runtime, note) -> None:
    for conn in list(runtime.clients):
        if conn.share_id != note.share_id:
            continue
        if conn.kind == "public-editor" and note.share_access != "edit":
            try:
                await conn.ws.close()
            except Exception:
                pass
            continue
        if conn.kind == "public-viewer" and note.share_access == "none":
            try:
                await conn.ws.close()
            except Exception:
                pass


async def _send_existing_presence(runtime, target: ClientConn) -> None:
    for conn in runtime.clients:
        if conn is target or not _collaborative(conn, target.note_id) or not conn.selection:
            continue
        await send_message(
            target.ws,
            {
                "type": "presence",
                "clientId": conn.client_id,
                "name": conn.name,
                "color": conn.color,
                "selection": conn.selection,
            },
        )


async def _broadcast_presence(runtime, sender: ClientConn, selection) -> None:
    outgoing = {
        "type": "presence",
        "clientId": sender.client_id,
        "name": sender.name,
        "color": sender.color,
        "selection": selection,
    }
    for conn in runtime.clients:
        if conn is sender:
            continue
        if _collaborative(conn, sender.note_id):
            await send_message(conn.ws, outgoing)


async def _broadcast_presence_leave(runtime, sender: ClientConn) -> None:
    outgoing = {"type": "presence-leave", "clientId": sender.client_id}
    for conn in runtime.clients:
        if conn is sender:
            continue
        if _collaborative(conn, sender.note_id):
            await send_message(conn.ws, outgoing)


def _counter(mutation: dict) -> int | float:
    value = mutation.get("clientCounter") or 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


async def handle_editor_message(runtime, conn: ClientConn, data: str) -> None:
    import json

    try:
        message = json.loads(data)
    except json.JSONDecodeError:
        return
    if not isinstance(message, dict):
        return
    if message.get("type") == "presence":
        if message.get("clientId") != conn.client_id:
            return
        conn.selection = message.get("selection")
        await _broadcast_presence(runtime, conn, message.get("selection"))
        return
    mutations = message.get("mutations")
    if (
        message.get("type") != "mutation"
        or not message.get("clientId")
        or not isinstance(mutations, list)
        or len(mutations) == 0
    ):
        return
    if message.get("clientId") != conn.client_id:
        return
    note = runtime.notes.get(conn.note_id)
    if note is None:
        return
    sender_counter = _counter(mutations[-1])
    last_ack = note.client_acks.get(message["clientId"]) or 0
    fresh = [item for item in mutations if isinstance(item, dict) and _counter(item) > last_ack]
    if not fresh:
        await send_message(
            conn.ws,
            {
                "type": "mutation",
                "senderId": message["clientId"],
                "senderCounter": sender_counter,
                "serverCounter": note.collab.server_counter,
                "markdown": note.markdown,
                "idListUpdates": [],
            },
        )
        return
    try:
        result = apply_client_mutations(note.collab, fresh)
    except Exception:
        import traceback

        traceback.print_exc()
        hello = build_hello(note)
        hello["clientId"] = conn.client_id
        await send_message(conn.ws, hello)
        return
    note.client_acks[message["clientId"]] = sender_counter
    if not result.changed:
        await send_message(
            conn.ws,
            {
                "type": "mutation",
                "senderId": message["clientId"],
                "senderCounter": sender_counter,
                "serverCounter": note.collab.server_counter,
                "markdown": note.markdown,
                "idListUpdates": [],
            },
        )
        return
    note.collab = result.state
    note.markdown = result.markdown
    note.updated_at = now_iso()
    persist_note(runtime, note)
    await broadcast_editor_mutation(
        runtime,
        note,
        {
            "type": "mutation",
            "senderId": message["clientId"],
            "senderCounter": sender_counter,
            "serverCounter": note.collab.server_counter,
            "markdown": note.markdown,
            "idListUpdates": result.id_list_updates,
        },
    )
    await broadcast_note_update(runtime, note)


async def _attach(runtime, ws: WebSocket, conn: ClientConn, hello: bool) -> None:
    runtime.clients.append(conn)
    if hello:
        message = build_hello(runtime.notes[conn.note_id])
        message["clientId"] = conn.client_id
        await send_message(ws, message)
        await _send_existing_presence(runtime, conn)


async def websocket_endpoint(websocket: WebSocket) -> None:
    runtime = websocket.app.state.runtime
    await websocket.accept()
    note_id = websocket.query_params.get("noteId") or ""
    share_id = websocket.query_params.get("shareId") or ""
    conn: ClientConn | None = None
    async with runtime.lock:
        if note_id:
            if not is_owner_authenticated(runtime, websocket.headers):
                await websocket.close()
            else:
                note = runtime.notes.get(note_id)
                if note is None:
                    await websocket.close()
                else:
                    conn = ClientConn(
                        ws=websocket,
                        kind="editor",
                        note_id=note.id,
                        share_id=note.share_id,
                        client_id=runtime.next_client_id(),
                        name="Owner",
                        color=runtime.next_color(CURSOR_COLORS),
                    )
                    await _attach(runtime, websocket, conn, True)
        elif share_id:
            note = find_note_by_share_id(runtime, share_id)
            if note is None or note.share_access == "none":
                await websocket.close()
            elif note.share_access == "edit":
                commenter_name = get_commenter_identity(websocket.headers)["name"]
                conn = ClientConn(
                    ws=websocket,
                    kind="public-editor",
                    note_id=note.id,
                    share_id=note.share_id,
                    client_id=runtime.next_client_id(),
                    name=commenter_name or "Anonymous",
                    color=runtime.next_color(CURSOR_COLORS),
                )
                await _attach(runtime, websocket, conn, True)
            else:
                conn = ClientConn(
                    ws=websocket,
                    kind="public-viewer",
                    note_id=note.id,
                    share_id=note.share_id,
                    client_id=runtime.next_client_id(),
                    name="",
                    color="",
                )
                await _attach(runtime, websocket, conn, False)
        else:
            await websocket.close()
    if conn is None:
        return
    try:
        while True:
            incoming = await websocket.receive()
            if incoming["type"] == "websocket.disconnect":
                break
            if conn.kind == "public-viewer":
                continue
            text = incoming.get("text")
            if text is None and incoming.get("bytes") is not None:
                text = incoming["bytes"].decode("utf-8", "replace")
            if text is None:
                continue
            async with runtime.lock:
                await handle_editor_message(runtime, conn, text)
    except WebSocketDisconnect:
        pass
    finally:
        async with runtime.lock:
            try:
                runtime.clients.remove(conn)
            except ValueError:
                return
            if conn.kind in ("editor", "public-editor"):
                await _broadcast_presence_leave(runtime, conn)
