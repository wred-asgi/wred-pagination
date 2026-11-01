"""SQLAlchemy helpers. Caller supplies a filtered single-entity Select and session."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from sqlalchemy import and_, or_, func, select
from . import InvalidCursor


def _pack(value):
    if value is None:
        raise ValueError("Cursor ordering columns must be non-null")
    if isinstance(value, (datetime, date, Decimal, UUID)):
        return {"type": type(value).__name__, "value": str(value)}
    if isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unsupported cursor ordering value")


def _unpack(value):
    if isinstance(value, dict):
        constructors = {
            "datetime": datetime.fromisoformat,
            "date": date.fromisoformat,
            "Decimal": Decimal,
            "UUID": UUID,
        }
        try:
            return constructors[value["type"]](value["value"])
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidCursor("Unsupported cursor value") from exc
    return value


async def offset_page(session, statement, pagination, *, serialize, count=False):
    """The supplied statement must include deterministic ordering."""
    items = (
        await session.scalars(
            statement.limit(pagination.limit).offset(pagination.offset)
        )
    ).all()
    total = (
        await session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        if count
        else None
    )
    return pagination.page([serialize(item) for item in items], total=total)


async def cursor_page(
    session, statement, pagination, *, order, codec, binding, serialize, count=False
):
    """order is [(model.column, descending), ...], ending in a unique key.

    binding must identify route, tenant, filters, and ordering. Columns must be
    non-null; input Select must select one entity without limit or offset.
    """
    if not order:
        raise ValueError("Explicit unique ordering is required")
    backwards = False
    filtered = statement
    if pagination.cursor:
        payload = codec.decode(pagination.cursor, binding=binding)
        values = [_unpack(v) for v in payload["values"]]
        if len(values) != len(order):
            raise InvalidCursor("Cursor does not match ordering")
        backwards = payload["direction"] == "previous"
        conditions = []
        for index, (column, descending) in enumerate(order):
            compare = (
                column < values[index]
                if descending != backwards
                else column > values[index]
            )
            conditions.append(
                and_(*(order[i][0] == values[i] for i in range(index)), compare)
            )
        filtered = filtered.where(or_(*conditions))
    ordering = [
        col.desc() if descending != backwards else col.asc()
        for col, descending in order
    ]
    rows = list(
        (
            await session.scalars(
                filtered.order_by(None).order_by(*ordering).limit(pagination.limit + 1)
            )
        ).all()
    )
    more = len(rows) > pagination.limit
    rows = rows[: pagination.limit]
    if backwards:
        rows.reverse()

    def cursor(row, direction):
        return codec.encode(
            [_pack(getattr(row, col.key)) for col, _ in order],
            binding=binding,
            direction=direction,
        )

    next_cursor = previous_cursor = None
    if rows:
        if more if not backwards else bool(pagination.cursor):
            next_cursor = cursor(rows[-1], "next")
        if more if backwards else bool(pagination.cursor):
            previous_cursor = cursor(rows[0], "previous")
    total = (
        await session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        if count
        else None
    )
    return pagination.page(
        [serialize(row) for row in rows],
        total=total,
        next_cursor=next_cursor,
        previous_cursor=previous_cursor,
    )
