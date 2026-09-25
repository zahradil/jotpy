from __future__ import annotations

import base64
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    current = datetime.now(timezone.utc)
    millis = current.microsecond // 1000
    return current.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"


def create_short_id(length: int = 8) -> str:
    # Same alphabet reduction as crypto.randomBytes(length).toString("base64url").
    text = base64.urlsafe_b64encode(secrets.token_bytes(length)).decode("ascii")
    text = re.sub(r"[^a-z0-9]", "", text.rstrip("=").lower())
    return text[:length]


def create_id(length: int = 12) -> str:
    return create_short_id(length)


def random_base64url(nbytes: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def secrets_hex(nbytes: int) -> str:
    return secrets.token_bytes(nbytes).hex()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, value) -> None:
    try:
        text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")
    except UnicodeEncodeError:
        text = json.dumps(value, indent=2, ensure_ascii=True) + "\n"
        path.write_text(text, encoding="utf-8")
