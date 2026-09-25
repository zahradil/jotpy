from __future__ import annotations

import uuid
from dataclasses import dataclass

from jotpy.id_list import ElementId, IdList
from jotpy.js_text import text_to_units, units_to_text


def char_key(bunch_id: str, counter: int) -> str:
    return f"{bunch_id}:{counter}"


def element_id_to_json(element_id: ElementId | None) -> dict | None:
    if element_id is None:
        return None
    return {"bunchId": element_id.bunch_id, "counter": element_id.counter}


def parse_element_id(value) -> ElementId:
    if not isinstance(value, dict) or "bunchId" not in value or "counter" not in value:
        raise ValueError("bad element id")
    counter = value["counter"]
    if isinstance(counter, bool) or not isinstance(counter, int):
        raise ValueError("bad element id")
    return ElementId(str(value["bunchId"]), counter)


@dataclass
class CollabState:
    id_list: IdList
    chars: dict[str, str]
    server_counter: int


@dataclass
class ApplyResult:
    state: CollabState
    markdown: str
    id_list_updates: list[dict]
    changed: bool


class TrackedIdList:
    def __init__(self, id_list: IdList, track_changes: bool) -> None:
        self._id_list = id_list
        self.track_changes = track_changes
        self.updates: list[dict] = []

    @property
    def id_list(self) -> IdList:
        return self._id_list

    def get_and_reset_updates(self) -> list[dict]:
        if not self.track_changes:
            raise RuntimeError("trackChanges not enabled")
        updates = self.updates
        self.updates = []
        return updates

    def insert_after(self, before: ElementId | None, new_id: ElementId, count: int = 1) -> None:
        self._id_list = self._id_list.insert_after(before, new_id, count)
        if self.track_changes:
            self.updates.append(
                {
                    "type": "insertAfter",
                    "before": element_id_to_json(before),
                    "id": element_id_to_json(new_id),
                    "count": count,
                }
            )

    def delete_range(self, start_index: int, end_index: int) -> None:
        ids: list[ElementId] = []
        for index in range(start_index, end_index + 1):
            ids.append(self._id_list.at(index))
        for element_id in ids:
            self._id_list = self._id_list.delete(element_id)
        if self.track_changes:
            self.updates.append({"type": "deleteRange", "startIndex": start_index, "endIndex": end_index})

    def apply(self, update: dict) -> None:
        kind = update["type"]
        if kind == "insertAfter":
            before = None if update["before"] is None else parse_element_id(update["before"])
            self._id_list = self._id_list.insert_after(before, parse_element_id(update["id"]), update["count"])
            return
        if kind == "deleteRange":
            self.delete_range(update["startIndex"], update["endIndex"])
            if not self.track_changes:
                return
            self.updates.pop()
            return
        raise ValueError(f"Unknown update: {kind}")


def new_collab_state() -> CollabState:
    return CollabState(IdList.new(), {}, 0)


def collab_from_markdown(markdown: str, server_counter: int = 0) -> CollabState:
    if not markdown:
        return CollabState(IdList.new(), {}, server_counter)
    units = text_to_units(markdown)
    bunch_id = str(uuid.uuid4())
    id_list = IdList.new().insert_after(None, ElementId(bunch_id, 0), len(units))
    chars = {char_key(bunch_id, index): unit for index, unit in enumerate(units)}
    return CollabState(id_list, chars, server_counter)


def collab_to_markdown(state: CollabState) -> str:
    units: list[str] = []
    for element_id in state.id_list.values():
        char = state.chars.get(char_key(element_id.bunch_id, element_id.counter))
        if char is not None:
            units.append(char)
    return units_to_text(units)


def save_collab_state(state: CollabState) -> dict:
    id_list_state = state.id_list.save()
    chars: list[dict] = []
    for item in id_list_state:
        units: list[str] = []
        for offset in range(item["count"]):
            key = char_key(item["bunchId"], item["startCounter"] + offset)
            units.append(state.chars.get(key, "\0"))
        chars.append(
            {
                "bunchId": item["bunchId"],
                "startCounter": item["startCounter"],
                "chars": units_to_text(units),
            }
        )
    return {"idListState": id_list_state, "chars": chars, "serverCounter": state.server_counter}


def load_collab_state(saved: dict | None) -> CollabState:
    saved = saved or {}
    id_list = IdList.load(saved.get("idListState") or [])
    chars: dict[str, str] = {}
    for bunch in saved.get("chars") or []:
        units = text_to_units(bunch.get("chars") or "")
        bunch_id = bunch["bunchId"]
        start = bunch.get("startCounter") or 0
        for offset, unit in enumerate(units):
            chars[char_key(bunch_id, start + offset)] = unit
    return CollabState(id_list, chars, saved.get("serverCounter") or 0)


def id_at_index(state: CollabState, index: int) -> ElementId:
    return state.id_list.at(index)


def id_before_index(state: CollabState, index: int) -> ElementId | None:
    if index <= 0:
        return None
    return state.id_list.at(index - 1)


def _apply_insert(tracked: TrackedIdList, chars: dict[str, str], mutation: dict) -> None:
    args = mutation["args"]
    content = args.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    if not content:
        return
    before = None if args.get("before") is None else parse_element_id(args["before"])
    new_id = parse_element_id(args["id"])
    if before is not None and not tracked.id_list.is_known(before):
        return
    if tracked.id_list.is_known(new_id):
        return
    if args.get("isInWord") and before is not None and not tracked.id_list.has(before):
        return
    units = text_to_units(content)
    tracked.insert_after(before, new_id, len(units))
    for offset, unit in enumerate(units):
        chars[char_key(new_id.bunch_id, new_id.counter + offset)] = unit


def _apply_delete(tracked: TrackedIdList, mutation: dict) -> None:
    args = mutation["args"]
    start_id = parse_element_id(args["startId"])
    if not tracked.id_list.is_known(start_id):
        return
    start_index = tracked.id_list.index_of(start_id, "right")
    if "endId" not in args or args.get("endId") is None:
        end_index = start_index
    elif tracked.id_list.is_known(parse_element_id(args["endId"])):
        end_index = tracked.id_list.index_of(parse_element_id(args["endId"]), "left")
    else:
        end_index = start_index - 1
    if end_index < start_index:
        return
    current_length = end_index - start_index + 1
    if "contentLength" in args and args["contentLength"] is not None:
        if current_length > args["contentLength"] + 10:
            return
    tracked.delete_range(start_index, end_index)


def apply_client_mutations(state: CollabState, mutations: list[dict]) -> ApplyResult:
    tracked = TrackedIdList(state.id_list, True)
    chars = dict(state.chars)
    for mutation in mutations:
        name = mutation.get("name")
        if name == "insert":
            _apply_insert(tracked, chars, mutation)
        elif name == "delete":
            _apply_delete(tracked, mutation)
    updates = tracked.get_and_reset_updates()
    next_state = CollabState(
        tracked.id_list,
        chars,
        state.server_counter + 1 if updates else state.server_counter,
    )
    return ApplyResult(next_state, collab_to_markdown(next_state), updates, bool(updates))
