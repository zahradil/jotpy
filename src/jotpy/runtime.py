from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from jotpy.notes import NoteRecord, load_notes_into_memory
from jotpy.tickets import load_link_key


@dataclass
class Runtime:
    data_dir: Path
    notes_dir: Path
    auth_path: Path
    sheets_dir: Path
    notes: dict[str, NoteRecord] = field(default_factory=dict)
    sheets: dict = field(default_factory=dict)
    clients: list = field(default_factory=list)
    client_id_counter: int = 0
    next_color_index: int = 0
    link_key: bytes = b""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def open(cls, data_dir: Path) -> Runtime:
        resolved = data_dir.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        notes_dir = resolved / "notes"
        sheets_dir = resolved / "sheets"
        notes_dir.mkdir(parents=True, exist_ok=True)
        sheets_dir.mkdir(parents=True, exist_ok=True)
        runtime = cls(
            data_dir=resolved,
            notes_dir=notes_dir,
            auth_path=resolved / "auth.json",
            sheets_dir=sheets_dir,
        )
        runtime.link_key = load_link_key(resolved)
        load_notes_into_memory(runtime)
        from jotpy.sheets import load_sheets_into_memory

        load_sheets_into_memory(runtime)
        return runtime

    def next_client_id(self) -> str:
        self.client_id_counter += 1
        return f"c{self.client_id_counter}"

    def next_color(self, colors: list[str]) -> str:
        color = colors[self.next_color_index % len(colors)]
        self.next_color_index += 1
        return color
