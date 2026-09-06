"""Resource-bound opaque cursors; authorization is always applied separately."""

import base64
import binascii
import json

from fastapi import HTTPException, Response

from ragkb.domain.pagination import PageKey


def read_cursor(cursor: str | None, scope: str, offset: int) -> PageKey | None:
    if cursor is None:
        return None
    try:
        if offset or len(cursor) > 2048:
            raise ValueError
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        saved_scope, ordinal, identity = payload
        if (
            saved_scope != scope
            or type(ordinal) is not int
            or ordinal < 0
            or not isinstance(identity, str)
            or not identity
            or len(identity) > 255
        ):
            raise ValueError
        return ordinal, identity
    except (ValueError, TypeError, binascii.Error):
        raise HTTPException(status_code=400, detail="INVALID_PAGE_CURSOR") from None


def write_cursor(response: Response, scope: str, key: PageKey | None) -> None:
    response.headers["X-Next-Cursor"] = (
        base64.urlsafe_b64encode(json.dumps([scope, *key]).encode()).decode() if key else ""
    )
