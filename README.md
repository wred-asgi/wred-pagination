# wred-pagination

Bounded pagination parameters, response helpers, and optional SQLAlchemy adapters.
Python 3.11+, Wred 0.13–0.15.

```sh
pip install './extensions/wred-pagination[sqlalchemy]'
```

```python
from wred import App
from wred_pagination import paginate

app = App()


@app.route("/items")
@paginate(default=25, maximum=100)
async def items(request, pagination):
    values = list(range(200))
    return pagination.page(
        values[pagination.offset : pagination.offset + pagination.limit],
        total=len(values),
    )
```

`paginate(default=25, maximum=100, argument='pagination', mode='offset', maximum_offset=10000)` injects an
immutable `Pagination(limit, offset=0, cursor=None)`. Invalid, negative, zero, or
excessive limits return 422; values are rejected rather than silently clamped.
Offsets above 10,000 return 422 by default. Set `maximum_offset` to a workload-tested
cap, or explicitly use `None` for legacy unbounded offsets. Prefer indexed cursor
pagination for deep traversal. Offset mode accepts `limit` and `offset`. Cursor mode accepts `limit` and `cursor`.
Mixing modes is rejected. No database query or list slicing happens in the decorator.

`Pagination.page(items, total=None, next_cursor=None, previous_cursor=None)` builds
a response envelope. Total is optional to avoid an automatic expensive count.

`CursorCodec(secret, ttl=86400)` signs JSON cursor payloads with HMAC-SHA256. Use a
stable application secret of at least 32 bytes shared across workers. Tokens are
signed, not encrypted. Do not encode sensitive ordering values. `encode(values,
binding=..., direction='next')` and `decode(token, binding=...)` support generic
backends. Values must be a list; encoded tokens are capped at 8,192 characters.
Direct `Pagination` construction also rejects nonpositive limits and negative offsets.
Invalid, expired, or context-mismatched tokens raise `InvalidCursor`,
which the decorator maps to 422.

The `wred_pagination.sqlalchemy` optional module provides:

- `offset_page(session, statement, pagination, serialize=..., count=False)`.
- `cursor_page(session, statement, pagination, order=[(Model.column, descending), ...],
  codec=..., binding=..., serialize=..., count=False)`.

Supply a filtered single-entity `Select` with no existing limit/offset. Offset
queries must have deterministic ordering. Cursor ordering columns must be non-null,
ending in a unique tie-breaker; values can be strings, numbers, booleans, dates,
datetimes, UUIDs, or decimals. Column attributes must be accessible by column key.
Cursor binding must identify tenant, filters, and ordering. Authorization filtering
remains the caller's responsibility. Forward/backward traversal uses lexicographic
keyset predicates, not an encoded offset. Counts are opt-in, and page/count queries
do not promise a snapshot across concurrent writes unless the caller's transaction
is configured for one.

Tests: `pip install -e '.[test,sqlalchemy]'`, then `pytest`. SQLite tests always run;
set `WRED_TEST_POSTGRES` to an isolated asyncpg database URL for PostgreSQL checks.

## Extension compatibility

Follows the [Wred extension contract v1.1](https://github.com/wred-asgi/.github/blob/main/EXTENSIONS.md).
Keep `@app.route` outermost for
route decorators; register routes on the inner Wred app when using ASGI wrappers.
