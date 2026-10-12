import hmac
from typing import Any

import structlog
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import Receive, Scope, Send

from freddy.assistant.tools import ToolContext, ToolError, tools_for
from freddy.db.models import User
from freddy.integrations.google.calendar import CalendarService

log = structlog.get_logger()


def build_mcp_server(
    sessionmaker: async_sessionmaker[AsyncSession],
    owner_discord_id: int,
    calendar: CalendarService | None = None,
) -> Server[Any]:
    tools = {t.name: t for t in tools_for(calendar is not None)}
    tool_list = [
        types.Tool(
            name=t.name, description=t.description, input_schema=t.definition()["input_schema"]
        )
        for t in tools.values()
    ]

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_list)

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = tools.get(params.name)
        try:
            if tool is None:
                raise ToolError(f"unknown tool {params.name}")
            async with sessionmaker() as session, session.begin():
                user = await session.scalar(select(User).where(User.discord_id == owner_discord_id))
                if user is None:
                    raise ToolError("owner not set up")
                text = await tool.call(
                    dict(params.arguments or {}), ToolContext(session, user, calendar)
                )
            is_error = False
        except ToolError as e:
            text, is_error = f"error: {e}", True
        log.info("mcp.tool_call", tool=params.name, is_error=is_error)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], is_error=is_error
        )

    return Server("freddy", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool)


class AuthedMCP:
    def __init__(self, manager: StreamableHTTPSessionManager, token: str) -> None:
        self.manager = manager
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers") or [])
        if not hmac.compare_digest(headers.get(b"authorization", b""), self.expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await self.manager.handle_request(scope, receive, send)


def build_mcp_app(
    sessionmaker: async_sessionmaker[AsyncSession],
    owner_discord_id: int,
    token: str,
    calendar: CalendarService | None = None,
) -> tuple[AuthedMCP, StreamableHTTPSessionManager]:
    manager = StreamableHTTPSessionManager(
        app=build_mcp_server(sessionmaker, owner_discord_id, calendar),
        stateless=True,
        json_response=True,
    )
    return AuthedMCP(manager, token), manager
