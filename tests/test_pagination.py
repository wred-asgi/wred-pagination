import os
import httpx
import pytest
from sqlalchemy import Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from wred import App
from wred_pagination import CursorCodec, InvalidCursor, Pagination, paginate
from wred_pagination.sqlalchemy import cursor_page, offset_page


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "pagination_test_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group: Mapped[str] = mapped_column(String)


@pytest.fixture(params=["sqlite", "postgres"])
async def database(request, tmp_path):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "pages.db")
    if request.param == "postgres":
        url = os.getenv("WRED_TEST_POSTGRES")
        if not url:
            pytest.skip("Set WRED_TEST_POSTGRES")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine)() as session:
        session.add_all([Item(id=i, group="same") for i in range(1, 7)])
        await session.commit()
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_cursor_ties_reverse_and_binding(database):
    codec = CursorCodec(b"x" * 32)
    options = dict(
        order=[(Item.group, False), (Item.id, False)],
        codec=codec,
        binding="tenant:1:group:same:id:asc",
        serialize=lambda i: i.id,
    )
    statement = select(Item).where(Item.group == "same")
    first = await cursor_page(database, statement, Pagination(2), **options, count=True)
    second = await cursor_page(
        database, statement, Pagination(2, cursor=first["next_cursor"]), **options
    )
    back = await cursor_page(
        database, statement, Pagination(2, cursor=second["previous_cursor"]), **options
    )
    assert first["items"] == back["items"] == [1, 2]
    assert second["items"] == [3, 4] and first["total"] == 6
    last = await cursor_page(
        database, statement, Pagination(2, cursor=second["next_cursor"]), **options
    )
    assert last["items"] == [5, 6] and last["next_cursor"] is None
    with pytest.raises(InvalidCursor):
        codec.decode(first["next_cursor"], binding="tenant:2")
    with pytest.raises(InvalidCursor):
        codec.decode(first["next_cursor"] + "x", binding=options["binding"])
    page = await offset_page(
        database,
        statement.order_by(Item.id),
        Pagination(2, 2),
        serialize=lambda i: i.id,
        count=True,
    )
    assert page["items"] == [3, 4] and page["total"] == 6
    empty = await offset_page(
        database,
        statement.order_by(Item.id),
        Pagination(2, 99),
        serialize=lambda i: i.id,
    )
    assert empty["items"] == []


async def test_parameter_bounds():
    app = App()

    @app.route("/")
    @paginate(maximum=30)
    async def handler(request, pagination):
        return {"limit": pagination.limit}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/")).json() == {"limit": 25}
        for query in (
            "limit=0",
            "limit=31",
            "offset=-1",
            "offset=10001",
            "limit=no",
            "cursor=abc",
        ):
            assert (await client.get("/?" + query)).status_code == 422
