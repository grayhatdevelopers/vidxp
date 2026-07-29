from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Mapping


MAX_CURSOR_OFFSET = (1 << 63) - 1


class CursorError(ValueError):
    """Raised when an opaque VidXP cursor cannot be validated."""


def encode_cursor(scope: str, position: Mapping[str, Any]) -> str:
    payload = {
        "version": 1,
        "scope": scope,
        **position,
    }
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(encoded).decode()


def decode_cursor(cursor: str, scope: str) -> dict[str, Any]:
    try:
        if not isinstance(cursor, str):
            raise TypeError
        encoded = cursor.encode("ascii")
        decoded = base64.b64decode(
            encoded,
            altchars=b"-_",
            validate=True,
        )
        if base64.urlsafe_b64encode(decoded) != encoded:
            raise CursorError("The cursor is invalid.")
        payload = json.loads(decoded.decode())
    except (
        CursorError,
        TypeError,
        UnicodeEncodeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise CursorError("The cursor is invalid.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("scope") != scope
    ):
        raise CursorError("The cursor is invalid.")
    return payload


def encode_offset_cursor(
    offset: int,
    *,
    scope: str,
    has_more: bool = True,
) -> str | None:
    if not has_more:
        return None
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or offset > MAX_CURSOR_OFFSET
    ):
        raise CursorError("The cursor offset is invalid.")
    return encode_cursor(scope, {"offset": offset})


def decode_offset_cursor(cursor: str | None, *, scope: str) -> int:
    if cursor is None:
        return 0
    payload = decode_cursor(cursor, scope)
    offset = payload.get("offset")
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or offset > MAX_CURSOR_OFFSET
    ):
        raise CursorError("The cursor offset is invalid.")
    return offset
