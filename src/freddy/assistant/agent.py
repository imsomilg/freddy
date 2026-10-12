import json
from dataclasses import dataclass, field
from typing import Any

import anthropic
import structlog

from freddy.assistant.history import NewMessage
from freddy.assistant.prompts import SYSTEM_PROMPT
from freddy.assistant.tools import TOOLS, Tool, ToolContext, ToolError
from freddy.db.models import MessageSource

log = structlog.get_logger()

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AgentError(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


@dataclass
class TurnResult:
    reply: str
    messages: list[NewMessage]
    tool_names: list[str] = field(default_factory=list)


def _dump(block: Any) -> dict[str, Any]:
    data: dict[str, Any] = block.model_dump(mode="json", exclude_none=True)
    return data


class Agent:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str,
        effort: str = "low",
        max_tokens: int = 4096,
        max_tool_rounds: int = 8,
        tools: list[Tool] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.max_tool_rounds = max_tool_rounds
        self.tools = {t.name: t for t in tools or TOOLS}
        self.tool_defs = [t.definition() for t in self.tools.values()]
        self.system = [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ]

    def _params(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "tools": self.tool_defs,
            "messages": messages,
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }

    async def _create(self, messages: list[dict[str, Any]]) -> Any:
        try:
            return await self.client.beta.messages.create(**self._params(messages))
        except anthropic.RateLimitError as e:
            log.warning("claude.rate_limited", error=str(e))
            raise AgentError(
                "I'm being rate limited, sir. Give me a minute to compose myself."
            ) from e
        except anthropic.APIStatusError as e:
            log.error("claude.api_error", status=e.status_code, error=str(e))
            raise AgentError("Claude had trouble with that one, sir. Try again in a moment?") from e
        except anthropic.APIConnectionError as e:
            log.error("claude.connection_error", error=str(e))
            raise AgentError("I can't reach Claude right now, sir. Try again in a moment?") from e

    async def run(
        self, history: list[dict[str, Any]], user_content: str, ctx: ToolContext
    ) -> TurnResult:
        new: list[NewMessage] = [NewMessage("user", user_content, MessageSource.USER)]
        messages = [*history, {"role": "user", "content": user_content}]
        tool_names: list[str] = []

        for _ in range(self.max_tool_rounds):
            resp = await self._create(messages)
            usage = resp.usage
            log.info(
                "claude.response",
                stop_reason=resp.stop_reason,
                model=resp.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read=getattr(usage, "cache_read_input_tokens", None),
                cache_write=getattr(usage, "cache_creation_input_tokens", None),
            )

            if resp.stop_reason == "refusal":
                raise AgentError("I'm afraid I can't help with that one, sir.")

            content = [_dump(b) for b in resp.content]
            messages.append({"role": "assistant", "content": content})
            new.append(
                NewMessage(
                    "assistant",
                    content,
                    MessageSource.CLAUDE,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            )

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not tool_uses:
                text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
                if resp.stop_reason == "max_tokens" and not text:
                    raise AgentError("That reply got rather long, sir. Perhaps a smaller piece?")
                new[0].tool_names = tool_names or None
                return TurnResult(reply=text or "👍", messages=new, tool_names=tool_names)

            results = []
            for block in tool_uses:
                tool_names.append(block.name)
                results.append(await self._run_tool(block, ctx))
            messages.append({"role": "user", "content": results})
            new.append(NewMessage("user", results, MessageSource.CLAUDE))

        raise AgentError("That took too many steps, sir. Perhaps break it into smaller asks?")

    async def _run_tool(self, block: Any, ctx: ToolContext) -> dict[str, Any]:
        tool = self.tools.get(block.name)
        raw = block.input if isinstance(block.input, dict) else json.loads(block.input or "{}")
        try:
            if tool is None:
                raise ToolError(f"unknown tool {block.name}")
            output = await tool.call(raw, ctx)
            is_error = False
        except ToolError as e:
            output, is_error = f"error: {e}", True
        log.info("tool.call", tool=block.name, is_error=is_error)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
            "is_error": is_error,
        }
