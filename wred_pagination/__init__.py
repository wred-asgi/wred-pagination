"""Bounded offset and signed cursor pagination, independent of database choice."""

import base64
import hmac
import json
import math
import time
from dataclasses import dataclass
from functools import wraps

__version__ = "0.2.0"


class InvalidCursor(ValueError):
    pass


class CursorCodec:
    def __init__(self, secret, *, ttl=86400):
        self.secret = secret.encode() if isinstance(secret, str) else secret
        if (
            not isinstance(self.secret, bytes)
            or len(self.secret) < 32
            or isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or not math.isfinite(ttl)
            or ttl <= 0
        ):
            raise ValueError("Use a secret of at least 32 bytes and a positive TTL")
        self.ttl = ttl

    def encode(self, values, *, binding, direction="next"):
        if not isinstance(values, list) or direction not in ("next", "previous"):
            raise ValueError("Cursor values must be a list with a valid direction")
        data = json.dumps(
            {
                "v": 1,
                "values": values,
                "binding": binding,
                "direction": direction,
                "exp": time.time() + self.ttl,
            },
            separators=(",", ":"),
        ).encode()
        signature = hmac.digest(self.secret, data, "sha256")
        token = base64.urlsafe_b64encode(signature + data).rstrip(b"=").decode()
        if len(token) > 8192:
            raise ValueError("Cursor ordering values exceed the token size limit")
        return token

    def decode(self, token, *, binding):
        try:
            if len(token) > 8192:
                raise ValueError()
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            signature, data = raw[:32], raw[32:]
            if not hmac.compare_digest(
                signature, hmac.digest(self.secret, data, "sha256")
            ):
                raise ValueError()
            payload = json.loads(data)
            if (
                payload["v"] != 1
                or payload["exp"] <= time.time()
                or payload["binding"] != binding
                or payload["direction"] not in ("next", "previous")
                or not isinstance(payload["values"], list)
            ):
                raise ValueError()
            return payload
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise InvalidCursor("Invalid or expired cursor") from exc


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int = 0
    cursor: str | None = None

    def __post_init__(self):
        if (
            type(self.limit) is not int
            or self.limit < 1
            or type(self.offset) is not int
            or self.offset < 0
        ):
            raise ValueError(
                "Pagination requires a positive integer limit and nonnegative integer offset"
            )
        if self.cursor is not None and (
            not isinstance(self.cursor, str)
            or not self.cursor
            or len(self.cursor) > 8192
        ):
            raise ValueError("Invalid pagination cursor")

    def page(self, items, *, total=None, next_cursor=None, previous_cursor=None):
        result = {"items": list(items), "limit": self.limit}
        if self.cursor is None:
            result["offset"] = self.offset
        if total is not None:
            result["total"] = total
        result.update(next_cursor=next_cursor, previous_cursor=previous_cursor)
        return result


def paginate(
    default=25,
    maximum=100,
    *,
    argument="pagination",
    mode="offset",
    maximum_offset=10000,
):
    if (
        type(default) is not int
        or type(maximum) is not int
        or not 1 <= default <= maximum
        or mode not in ("offset", "cursor")
    ):
        raise ValueError("Invalid pagination configuration")
    if maximum_offset is not None and (
        type(maximum_offset) is not int or maximum_offset < 0
    ):
        raise ValueError("maximum_offset must be a nonnegative integer or None")
    if (
        not isinstance(argument, str)
        or not argument.isidentifier()
        or argument == "request"
    ):
        raise ValueError("Use a named argument other than request")

    def decorate(handler):
        @wraps(handler)
        async def wrapped(request, *args, **kwargs):
            try:
                query = request.query
                limit = int(query.get("limit", default))
                offset = int(query.get("offset", "0"))
                if (
                    not 1 <= limit <= maximum
                    or offset < 0
                    or (maximum_offset is not None and offset > maximum_offset)
                ):
                    raise ValueError()
                if (
                    mode == "cursor"
                    and "offset" in query
                    or mode == "offset"
                    and "cursor" in query
                ):
                    raise ValueError()
                cursor = query.get("cursor")
                if cursor is not None and (not cursor or len(cursor) > 8192):
                    raise ValueError()
            except (ValueError, UnicodeError):
                return (
                    {"detail": "Invalid pagination parameters"},
                    [("content-type", "application/json")],
                    422,
                )
            if argument in kwargs:
                raise TypeError(
                    "Pagination argument collides with an existing argument"
                )
            owns_context = "wred.extensions.route_kwargs" not in request.scope
            if owns_context:
                request.scope["wred.extensions.route_kwargs"] = dict(kwargs)
            try:
                return await handler(
                    request,
                    *args,
                    **kwargs,
                    **{argument: Pagination(limit, offset, cursor)},
                )
            except InvalidCursor:
                return (
                    {"detail": "Invalid or expired cursor"},
                    [("content-type", "application/json")],
                    422,
                )
            finally:
                if owns_context:
                    del request.scope["wred.extensions.route_kwargs"]

        metadata = dict(getattr(handler, "__wred_openapi__", {}))
        parameters = list(metadata.get("parameters", []))
        parameters += [
            {
                "name": "limit",
                "in": "query",
                "schema": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": maximum,
                    "default": default,
                },
            },
            {
                "name": "cursor" if mode == "cursor" else "offset",
                "in": "query",
                "schema": {"type": "string"}
                if mode == "cursor"
                else {"type": "integer", "minimum": 0, "default": 0},
            },
        ]
        if mode == "offset" and maximum_offset is not None:
            parameters[-1]["schema"]["maximum"] = maximum_offset
        metadata["parameters"] = parameters
        wrapped.__wred_openapi__ = metadata
        return wrapped

    return decorate
