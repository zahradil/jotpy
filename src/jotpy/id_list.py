"""Array IdList with the same save() as articulated 1.3.1.

Mutations return a new list. Collab keeps the previous list when a later
mutation throws, matching the immutable JavaScript IdList.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple


class ElementId(NamedTuple):
    bunch_id: str
    counter: int


class _Element(NamedTuple):
    id: ElementId
    is_deleted: bool


def _check_count(count: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"Invalid count: {count}")


def _check_counter(counter: int) -> None:
    if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
        raise ValueError(f"Invalid startCounter: {counter}")


class IdList:
    def __init__(self, state: tuple[_Element, ...], length: int) -> None:
        self._state = state
        self._length = length

    @staticmethod
    def new() -> IdList:
        return IdList((), 0)

    @classmethod
    def load(cls, saved_state: list[dict] | None) -> IdList:
        elements: list[_Element] = []
        length = 0
        for item in saved_state or []:
            count = item["count"]
            start = item["startCounter"]
            _check_count(count)
            _check_counter(start)
            if count == 0:
                continue
            deleted = bool(item["isDeleted"])
            bunch_id = item["bunchId"]
            for offset in range(count):
                elements.append(_Element(ElementId(bunch_id, start + offset), deleted))
            if not deleted:
                length += count
        return cls(tuple(elements), length)

    @property
    def length(self) -> int:
        return self._length

    def insert_after(self, before: ElementId | None, new_id: ElementId, count: int = 1) -> IdList:
        _check_count(count)
        if before is None:
            index = -1
        else:
            index = self._find(before)
            if index == -1:
                raise ValueError("before is not known")
        if count == 0:
            return self
        if self._any_known(new_id, count):
            raise ValueError("An inserted id is already known")
        inserted = tuple(
            _Element(ElementId(new_id.bunch_id, new_id.counter + offset), False) for offset in range(count)
        )
        state = self._state[: index + 1] + inserted + self._state[index + 1 :]
        return IdList(state, self._length + count)

    def delete(self, element_id: ElementId) -> IdList:
        index = self._find(element_id)
        if index == -1:
            return self
        current = self._state[index]
        if current.is_deleted:
            return self
        state = list(self._state)
        state[index] = _Element(current.id, True)
        return IdList(tuple(state), self._length - 1)

    def delete_range(self, start: int, end: int) -> IdList:
        # Half-open [start, end), same as IdList.deleteRange in articulated.
        ids = [self.at(index) for index in range(start, end)]
        result: IdList = self
        for element_id in ids:
            result = result.delete(element_id)
        return result

    def has(self, element_id: ElementId) -> bool:
        found = self._get(element_id)
        return found is not None and not found.is_deleted

    def is_known(self, element_id: ElementId) -> bool:
        return self._get(element_id) is not None

    def at(self, index: int) -> ElementId:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= self._length:
            raise IndexError(f"Index out of bounds: {index} (length: {self._length})")
        remaining = index
        for element in self._state:
            if element.is_deleted:
                continue
            if remaining == 0:
                return element.id
            remaining -= 1
        raise IndexError("Internal error")

    def index_of(self, element_id: ElementId, bias: str = "none") -> int:
        index = 0
        for element in self._state:
            if element.id == element_id:
                if not element.is_deleted:
                    return index
                if bias == "none":
                    return -1
                if bias == "left":
                    return index - 1
                if bias == "right":
                    return index
                raise ValueError(f"Unknown bias: {bias}")
            if not element.is_deleted:
                index += 1
        raise ValueError("id is not known")

    def values(self) -> Iterator[ElementId]:
        for element in self._state:
            if not element.is_deleted:
                yield element.id

    def save(self) -> list[dict]:
        saved: list[dict] = []
        for element in self._state:
            if saved:
                current = saved[-1]
                if (
                    element.id.bunch_id == current["bunchId"]
                    and element.id.counter == current["startCounter"] + current["count"]
                    and element.is_deleted == current["isDeleted"]
                ):
                    current["count"] += 1
                    continue
            saved.append(
                {
                    "bunchId": element.id.bunch_id,
                    "startCounter": element.id.counter,
                    "count": 1,
                    "isDeleted": element.is_deleted,
                }
            )
        return saved

    def _find(self, element_id: ElementId) -> int:
        for index, element in enumerate(self._state):
            if element.id == element_id:
                return index
        return -1

    def _get(self, element_id: ElementId) -> _Element | None:
        index = self._find(element_id)
        if index == -1:
            return None
        return self._state[index]

    def _any_known(self, element_id: ElementId, count: int) -> bool:
        end = element_id.counter + count
        for element in self._state:
            if element.id.bunch_id != element_id.bunch_id:
                continue
            if element_id.counter <= element.id.counter < end:
                return True
        return False
