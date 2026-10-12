import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx2
import pytest
import websockets
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from freddy.bridge.hub import BridgeFailed, BridgeHub, BridgeOffline, BridgeTimeout
from freddy.db.engine import make_sessionmaker
from freddy.db.models import Goal, User
from freddy.mcp_server import build_mcp_app
from freddy.web import build_app, make_server
from tests.fakes import FakeCalendar

BRIDGE_TOKEN = "bridge-secret"
MCP_TOKEN = "mcp-secret"


class FakeChannel:
    ready = True

    async def notify(self, text: str) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


@contextlib.asynccontextmanager
async def serve(
    engine: AsyncEngine, calendar: FakeCalendar | None = None
) -> AsyncIterator[tuple[str, BridgeHub]]:
    hub = BridgeHub(BRIDGE_TOKEN, timeout=3, ack_timeout=0.5)
    mcp = build_mcp_app(make_sessionmaker(engine), 9001, MCP_TOKEN, calendar)  # type: ignore[arg-type]
    srv = make_server(build_app(FakeChannel(), hub, mcp), 0)
    task = asyncio.create_task(srv.serve())
    while not srv.started:  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"127.0.0.1:{port}", hub
    srv.should_exit = True
    await task


@pytest.fixture
async def server(engine: AsyncEngine) -> AsyncIterator[tuple[str, BridgeHub]]:
    async with serve(engine) as running:
        yield running


Handler = Callable[[Any, dict[str, Any]], Awaitable[None]]


@contextlib.asynccontextmanager
async def fake_bridge(addr: str, on_job: Handler) -> AsyncIterator[Any]:
    headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}"}
    async with websockets.connect(f"ws://{addr}/bridge", additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "ping"}))

        async def loop() -> None:
            with contextlib.suppress(websockets.ConnectionClosed):
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["type"] == "job":
                        await on_job(ws, msg)

        task = asyncio.create_task(loop())
        await asyncio.sleep(0.1)
        try:
            yield ws
        finally:
            task.cancel()


async def test_healthz(server: tuple[str, BridgeHub]) -> None:
    addr, _ = server
    async with httpx2.AsyncClient() as client:
        r = await client.get(f"http://{addr}/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "bridge": "offline"}


async def test_bridge_rejects_bad_token(server: tuple[str, BridgeHub]) -> None:
    addr, hub = server
    with pytest.raises(websockets.InvalidStatus):
        async with websockets.connect(
            f"ws://{addr}/bridge", additional_headers={"Authorization": "Bearer nope"}
        ):
            pass
    assert not hub.online


async def test_bridge_round_trip(server: tuple[str, BridgeHub]) -> None:
    addr, hub = server

    async def reply(ws: Any, msg: dict[str, Any]) -> None:
        await ws.send(json.dumps({"type": "ack", "id": msg["id"]}))
        await ws.send(
            json.dumps({"type": "result", "id": msg["id"], "reply": "hi " + msg["prompt"]})
        )

    async with fake_bridge(addr, reply):
        assert hub.online
        assert await hub.ask("there", "system") == "hi there"
    await asyncio.sleep(0.1)
    assert not hub.online
    with pytest.raises(BridgeOffline):
        await hub.ask("x", "system")


async def test_bridge_no_ack_is_offline(server: tuple[str, BridgeHub]) -> None:
    addr, hub = server

    async def ignore(ws: Any, msg: dict[str, Any]) -> None:
        pass

    async with fake_bridge(addr, ignore):
        with pytest.raises(BridgeOffline):
            await hub.ask("x", "system")


async def test_bridge_timeout_and_error(server: tuple[str, BridgeHub]) -> None:
    addr, hub = server

    async def ack_only(ws: Any, msg: dict[str, Any]) -> None:
        await ws.send(json.dumps({"type": "ack", "id": msg["id"]}))
        if msg["prompt"] == "fail":
            await ws.send(json.dumps({"type": "error", "id": msg["id"], "error": "boom"}))

    async with fake_bridge(addr, ack_only):
        with pytest.raises(BridgeFailed):
            await hub.ask("fail", "system")
        with pytest.raises(BridgeTimeout):
            await hub.ask("slow", "system")


async def test_bridge_drop_mid_job(server: tuple[str, BridgeHub]) -> None:
    addr, hub = server

    async def ack_then_close(ws: Any, msg: dict[str, Any]) -> None:
        await ws.send(json.dumps({"type": "ack", "id": msg["id"]}))
        await ws.close()

    async with fake_bridge(addr, ack_then_close):
        with pytest.raises(BridgeOffline):
            await hub.ask("x", "system")


async def test_mcp_requires_token(server: tuple[str, BridgeHub]) -> None:
    addr, _ = server
    async with httpx2.AsyncClient() as client:
        r = await client.post(f"http://{addr}/mcp/", json={})
    assert r.status_code == 401


async def test_mcp_tools(
    server: tuple[str, BridgeHub], committed_owner: User, engine: AsyncEngine
) -> None:
    addr, _ = server
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    async with (
        http,
        streamable_http_client(f"http://{addr}/mcp/", http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert {"add_goal", "set_reminder", "complete_goal"} <= names
        assert "list_events" not in names

        result = await session.call_tool("add_goal", {"title": "Bridge goal"})
        assert not result.is_error
        bad = await session.call_tool("complete_goal", {"goal_id": 999999})
        assert bad.is_error

    async with make_sessionmaker(engine)() as s:
        titles = list(await s.scalars(select(Goal.title).where(Goal.user_id == committed_owner.id)))
        owner = await s.scalar(select(User).where(User.id == committed_owner.id))
    assert owner is not None
    assert titles == ["Bridge goal"]


async def test_mcp_calendar_tools(engine: AsyncEngine, committed_owner: User) -> None:
    cal = FakeCalendar()
    async with serve(engine, cal) as (addr, _):
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {MCP_TOKEN}"})
        async with (
            http,
            streamable_http_client(f"http://{addr}/mcp/", http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"list_events", "create_event", "find_free_time"} <= names
            result = await session.call_tool("list_events", {"day": "2030-01-07"})
            assert not result.is_error
    assert cal.calls[0][0] == "list"
