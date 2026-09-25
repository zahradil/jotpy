"""Indexing that matches JavaScript strings.

The browser and the TypeScript server count UTF-16 code units. Collab ids are
one per code unit, so Python must use the same indexing.
"""

from __future__ import annotations


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-le", "surrogatepass")


def utf16_len(text: str) -> int:
    return len(_utf16(text)) // 2


def text_to_units(text: str) -> list[str]:
    raw = _utf16(text)
    return [raw[index : index + 2].decode("utf-16-le", "surrogatepass") for index in range(0, len(raw), 2)]


def units_to_text(units: list[str]) -> str:
    raw = b"".join(unit.encode("utf-16-le", "surrogatepass") for unit in units)
    return raw.decode("utf-16-le", "surrogatepass")


def utf16_slice(text: str, start: int, end: int | None = None) -> str:
    raw = _utf16(text)
    total = len(raw) // 2
    if start < 0:
        start = 0
    if end is None or end > total:
        end = total
    if end < start:
        end = start
    return raw[start * 2 : end * 2].decode("utf-16-le", "surrogatepass")


def utf16_index_of(haystack: str, needle: str, start: int = 0) -> int:
    hay = _utf16(haystack)
    ndl = _utf16(needle)
    total = len(hay) // 2
    if start < 0:
        start = 0
    if start > total:
        return total if ndl == b"" else -1
    if ndl == b"":
        return start
    pos = start * 2
    limit = len(hay) - len(ndl)
    while pos <= limit:
        if hay[pos : pos + len(ndl)] == ndl:
            return pos // 2
        pos += 2
    return -1


def count_occurrences(haystack: str, needle: str) -> int:
    count = 0
    index = utf16_index_of(haystack, needle)
    while index != -1:
        count += 1
        index = utf16_index_of(haystack, needle, index + 1)
    return count
