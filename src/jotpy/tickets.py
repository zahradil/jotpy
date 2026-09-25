from __future__ import annotations

import base64
import hmac
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 26-bit id, 2-bit access, 8-bit generation, 12-bit day, high bit first.
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
NOTE_ID_RE = re.compile(r"^[0-9a-z]{5}$")
ACCESS_TO_CODE = {"view": 0, "comment": 1, "edit": 2}
CODE_TO_ACCESS = {0: "view", 1: "comment", 2: "edit"}
_B64_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
_NOTE_SPACE = 36**5
# The highest day number marks a link that never expires.
PERMANENT_DAY = 4095
# Generations only climb, so a revoked link cannot come back after a wrap.
MAX_GENERATION = 255


@dataclass(frozen=True)
class OpenedTicket:
    note_id: str
    access: str
    generation: int
    end_day: int


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def day_number(moment: datetime) -> int:
    utc = _as_utc(moment)
    midnight = datetime(utc.year, utc.month, utc.day, tzinfo=timezone.utc)
    return (midnight - EPOCH).days


def today_utc(now: datetime | None = None) -> int:
    return day_number(now or datetime.now(timezone.utc))


def expiry_day(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    return min(day_number(_as_utc(current) + timedelta(hours=24)), PERMANENT_DAY - 1)


def end_day_iso(end_day: int) -> str | None:
    """Last valid UTC day of a link, or None for a permanent one."""
    if end_day == PERMANENT_DAY:
        return None
    return (EPOCH + timedelta(days=end_day)).date().isoformat()


def encode_note_id(note_id: str) -> int:
    if NOTE_ID_RE.fullmatch(note_id) is None:
        raise ValueError(note_id)
    value = 0
    for char in note_id:
        value = value * 36 + ALPHABET.index(char)
    return value


def decode_note_id(value: int) -> str:
    chars: list[str] = []
    for _ in range(5):
        value, index = divmod(value, 36)
        chars.append(ALPHABET[index])
    if value:
        raise ValueError(value)
    return "".join(reversed(chars))


def pack_ticket_fields(note_id: str, access: str, generation: int, end_day: int) -> bytes:
    if access not in ACCESS_TO_CODE:
        raise ValueError(access)
    if isinstance(generation, bool) or not isinstance(generation, int) or not 0 <= generation <= 255:
        raise ValueError(generation)
    if isinstance(end_day, bool) or not isinstance(end_day, int) or not 0 <= end_day <= 4095:
        raise ValueError(end_day)
    note_bits = encode_note_id(note_id)
    value = (note_bits << 22) | (ACCESS_TO_CODE[access] << 20) | (generation << 12) | end_day
    return value.to_bytes(6, "big")


def sign_ticket_fields(key: bytes, fields: bytes) -> str:
    if len(fields) != 6:
        raise ValueError(len(fields))
    mac = hmac.new(key, fields, hashlib.sha256).digest()[:4]
    return base64.urlsafe_b64encode(fields + mac).decode("ascii").rstrip("=")


def ticket_for(key: bytes, note_id: str, access: str, generation: int, end_day: int) -> str | None:
    try:
        fields = pack_ticket_fields(note_id, access, generation, end_day)
    except ValueError:
        return None
    return sign_ticket_fields(key, fields)


def _decode_ticket(text: str) -> bytes | None:
    if not isinstance(text, str) or not text or any(char not in _B64_ALPHABET for char in text):
        return None
    pad = "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception:
        return None


def open_ticket(key: bytes, ticket: str, today: int) -> OpenedTicket | None:
    raw = _decode_ticket(ticket)
    if raw is None or len(raw) != 10:
        return None
    fields, mac = raw[:6], raw[6:]
    expected = hmac.new(key, fields, hashlib.sha256).digest()[:4]
    if not hmac.compare_digest(mac, expected):
        return None
    value = int.from_bytes(fields, "big")
    end_day = value & 0xFFF
    generation = (value >> 12) & 0xFF
    access_code = (value >> 20) & 0x3
    note_bits = value >> 22
    if access_code == 3 or note_bits >= _NOTE_SPACE or (end_day != PERMANENT_DAY and end_day < today):
        return None
    access = CODE_TO_ACCESS.get(access_code)
    if access is None:
        return None
    return OpenedTicket(decode_note_id(note_bits), access, generation, end_day)


def load_link_key(data_dir: Path) -> bytes:
    path = data_dir / "link.key"
    if path.is_file():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    try:
        os.write(fd, key)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return key
