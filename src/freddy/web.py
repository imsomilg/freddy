import contextlib
from collections.abc import AsyncIterator, Iterator

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocket

from freddy.bridge.hub import BridgeHub
from freddy.channels.base import Channel
from freddy.mcp_server import AuthedMCP


def build_app(
    channel: Channel,
    hub: BridgeHub | None = None,
    mcp: tuple[AuthedMCP, StreamableHTTPSessionManager] | None = None,
) -> Starlette:
    async def healthz(_: Request) -> JSONResponse:
        if channel.ready:
            return JSONResponse(
                {"status": "ok", "bridge": "online" if hub and hub.online else "offline"}
            )
        return JSONResponse({"status": "starting"}, status_code=503)

    async def bridge(ws: WebSocket) -> None:
        assert hub is not None
        await hub.handle(ws)

    routes: list[Route | WebSocketRoute | Mount] = [Route("/healthz", healthz)]
    if hub is not None:
        routes.append(WebSocketRoute("/bridge", bridge))
    if mcp is not None:
        routes.append(Mount("/mcp", app=mcp[0]))

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        if mcp is None:
            yield
            return
        async with mcp[1].run():
            yield

    return Starlette(routes=routes, lifespan=lifespan)


class Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def make_server(app: Starlette, port: int) -> Server:
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return Server(config)
