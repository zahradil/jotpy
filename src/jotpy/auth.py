from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone

from jotpy.util import create_id, now_iso, random_base64url, read_json, secrets_hex, write_json

OWNER_SESSION_COOKIE = "md_owner_session"
OWNER_TOKEN_KEY = "md_owner_token"
COMMENTER_ID_COOKIE = "md_commenter_id"
COMMENTER_NAME_COOKIE = "md_commenter_name"
OWNER_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
COMMENTER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
_LAST_USED_REFRESH = 1000 * 60 * 60 * 12


def hash_secret(value: str, salt: str) -> str:
    # Node crypto.scryptSync(value, salt, 64) encodes a string salt as UTF-8.
    # The salt stored in auth.json is hex text; it is not decoded before hashing.
    # Defaults match Node: N=16384, r=8, p=1.
    digest = hashlib.scrypt(
        value.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384,
        r=8,
        p=1,
        dklen=64,
    )
    return digest.hex()


def secure_equals_hex(left: str, right: str) -> bool:
    try:
        a = bytes.fromhex(left)
        b = bytes.fromhex(right)
    except ValueError:
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def encode_uri_component(value: str) -> str:
    return urllib.parse.quote(value, safe="-_.!~*'()")


def parse_cookies(header: str | None) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not header:
        return cookies
    for item in header.split(";"):
        index = item.find("=")
        if index == -1:
            continue
        key = item[:index].strip()
        value = item[index + 1 :].strip()
        cookies[key] = urllib.parse.unquote(value)
    return cookies


def cookies_from_headers(headers) -> dict[str, str]:
    return parse_cookies(headers.get("cookie"))


def is_secure_request(request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def request_protocol(request) -> str:
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.url.scheme


def request_host(request) -> str:
    return request.headers.get("host") or request.url.netloc


def set_cookie(response, request, name: str, value: str, max_age: int, http_only: bool = True) -> None:
    secure = "; Secure" if is_secure_request(request) else ""
    http_only_part = "; HttpOnly" if http_only else ""
    response.headers.append(
        "set-cookie",
        f"{name}={encode_uri_component(value)}; Path=/; SameSite=Lax; Max-Age={max_age}{http_only_part}{secure}",
    )


def clear_cookie(response, request, name: str, http_only: bool = True) -> None:
    secure = "; Secure" if is_secure_request(request) else ""
    http_only_part = "; HttpOnly" if http_only else ""
    response.headers.append("set-cookie", f"{name}=; Path=/; SameSite=Lax; Max-Age=0{http_only_part}{secure}")


def bearer_token(headers) -> str | None:
    header = headers.get("authorization")
    if not header or not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    return token or None


def load_auth(runtime) -> dict | None:
    data = read_json(runtime.auth_path)
    if not isinstance(data, dict):
        return None
    return data


def save_auth(runtime, auth: dict) -> None:
    write_json(runtime.auth_path, auth)


def auth_configured(runtime) -> bool:
    auth = load_auth(runtime)
    return bool(auth and auth.get("passwordSalt") and auth.get("passwordHash"))


def password_matches(runtime, password: str) -> bool:
    auth = load_auth(runtime)
    if not auth:
        return False
    return secure_equals_hex(hash_secret(password, auth["passwordSalt"]), auth["passwordHash"])


def initialize_owner_auth(runtime, password: str) -> str:
    salt = secrets_hex(16)
    auth = {
        "passwordSalt": salt,
        "passwordHash": hash_secret(password, salt),
        "tokens": [],
    }
    save_auth(runtime, auth)
    return issue_owner_token(runtime)


def issue_owner_token(runtime) -> str:
    auth = load_auth(runtime)
    if not auth:
        raise RuntimeError("Password not configured.")
    token = random_base64url(32)
    salt = secrets_hex(16)
    timestamp = now_iso()
    auth.setdefault("tokens", []).append(
        {
            "id": create_id(10),
            "salt": salt,
            "hash": hash_secret(token, salt),
            "createdAt": timestamp,
            "lastUsedAt": timestamp,
        }
    )
    save_auth(runtime, auth)
    return token


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def verify_owner_token(runtime, token: str) -> bool:
    auth = load_auth(runtime)
    if not auth:
        return False
    changed = False
    for stored in auth.get("tokens") or []:
        if not secure_equals_hex(hash_secret(token, stored["salt"]), stored["hash"]):
            continue
        last_seen = _parse_time(stored.get("lastUsedAt") or "")
        if last_seen is None or (datetime.now(timezone.utc) - last_seen).total_seconds() * 1000 > _LAST_USED_REFRESH:
            stored["lastUsedAt"] = now_iso()
            changed = True
        if changed:
            save_auth(runtime, auth)
        return True
    return False


def revoke_owner_token(runtime, token: str) -> None:
    auth = load_auth(runtime)
    if not auth:
        return
    tokens = auth.get("tokens") or []
    kept = [stored for stored in tokens if not secure_equals_hex(hash_secret(token, stored["salt"]), stored["hash"])]
    if len(kept) != len(tokens):
        auth["tokens"] = kept
        save_auth(runtime, auth)


def verify_api_key(runtime, key: str) -> bool:
    auth = load_auth(runtime)
    if not auth or not auth.get("apiKeys"):
        return False
    for stored in auth["apiKeys"]:
        if secure_equals_hex(hash_secret(key, stored["keySalt"]), stored["keyHash"]):
            return True
    return False


def get_api_key_label(runtime, key: str) -> str | None:
    auth = load_auth(runtime)
    if not auth or not auth.get("apiKeys"):
        return None
    for stored in auth["apiKeys"]:
        if secure_equals_hex(hash_secret(key, stored["keySalt"]), stored["keyHash"]):
            return stored["label"]
    return None


def create_api_key(runtime, label: str) -> dict:
    auth = load_auth(runtime)
    if not auth:
        raise RuntimeError("Password not configured.")
    auth.setdefault("apiKeys", [])
    raw_key = random_base64url(32)
    salt = secrets_hex(16)
    api_key = {
        "id": create_id(10),
        "label": (label or "").strip()[:80] or "unnamed",
        "keySalt": salt,
        "keyHash": hash_secret(raw_key, salt),
        "createdAt": now_iso(),
    }
    auth["apiKeys"].append(api_key)
    save_auth(runtime, auth)
    return {"id": api_key["id"], "label": api_key["label"], "key": raw_key, "createdAt": api_key["createdAt"]}


def delete_api_key(runtime, key_id: str) -> bool:
    auth = load_auth(runtime)
    if not auth or not auth.get("apiKeys"):
        return False
    before = len(auth["apiKeys"])
    auth["apiKeys"] = [item for item in auth["apiKeys"] if item.get("id") != key_id]
    if len(auth["apiKeys"]) != before:
        save_auth(runtime, auth)
        return True
    return False


def list_api_keys(runtime) -> list[dict]:
    auth = load_auth(runtime)
    if not auth or not auth.get("apiKeys"):
        return []
    return [{"id": item["id"], "label": item["label"], "createdAt": item["createdAt"]} for item in auth["apiKeys"]]


def is_owner_authenticated(runtime, headers) -> bool:
    token = bearer_token(headers)
    if token and verify_api_key(runtime, token):
        return True
    session = cookies_from_headers(headers).get(OWNER_SESSION_COOKIE)
    return bool(session and verify_owner_token(runtime, session))


def get_commenter_identity(headers) -> dict:
    cookies = cookies_from_headers(headers)
    return {
        "id": cookies.get(COMMENTER_ID_COOKIE) or None,
        "name": cookies.get(COMMENTER_NAME_COOKIE) or None,
    }


def set_owner_session_cookie(response, request, token: str) -> None:
    set_cookie(response, request, OWNER_SESSION_COOKIE, token, OWNER_COOKIE_MAX_AGE)


def clear_owner_session_cookie(response, request) -> None:
    clear_cookie(response, request, OWNER_SESSION_COOKIE)


def get_or_create_commenter_id(request, response) -> str:
    existing = get_commenter_identity(request.headers)["id"]
    if existing:
        return existing
    created = random_base64url(24)
    set_cookie(response, request, COMMENTER_ID_COOKIE, created, COMMENTER_COOKIE_MAX_AGE)
    return created


def set_commenter_name_cookie(request, response, name: str) -> None:
    set_cookie(response, request, COMMENTER_NAME_COOKIE, name, COMMENTER_COOKIE_MAX_AGE)
