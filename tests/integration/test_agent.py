from typing import Any

from anthropic.types.beta import BetaMessage
from sqlalchemy.ext.asyncio import AsyncSession

from freddy.assistant.agent import Agent
from freddy.assistant.history import ConversationStore
from freddy.assistant.tools import ToolContext
from freddy.db.models import User
from freddy.domain.goals import GoalService


def _msg(content: list[dict[str, Any]], stop_reason: str) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )


class FakeMessages:
    def __init__(self, responses: list[BetaMessage]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> BetaMessage:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[BetaMessage]) -> None:
        self.beta = type("Beta", (), {"messages": FakeMessages(responses)})()


async def test_agent_runs_tools_and_persists(session: AsyncSession, user: User) -> None:
    client = FakeClient(
        [
            _msg(
                [
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "add_goal",
                        "input": {"title": "Read 20 pages"},
                    },
                ],
                "tool_use",
            ),
            _msg([{"type": "text", "text": "✅ Goal added: Read 20 pages"}], "end_turn"),
        ]
    )
    agent = Agent(client, model="claude-sonnet-5-5")  # type: ignore[arg-type]
    store = ConversationStore(session, user)

    result = await agent.run(
        await store.load(), "[now: x]\nadd reading", ToolContext(session, user)
    )
    await store.append(result.messages)

    assert result.reply == "✅ Goal added: Read 20 pages"
    assert result.tool_names == ["add_goal"]
    goals = await GoalService(session, user).for_day()
    assert [g.title for g in goals] == ["Read 20 pages"]

    history = await ConversationStore(session, user).load()
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[1]["content"][0]["type"] == "thinking"
    assert history[2]["content"][0]["type"] == "tool_result"

    second_call = client.beta.messages.calls[1]
    assert second_call["fallbacks"] == "default"
    assert second_call["messages"][1]["content"][1]["name"] == "add_goal"


async def test_agent_reports_tool_errors(session: AsyncSession, user: User) -> None:
    client = FakeClient(
        [
            _msg(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "complete_goal",
                        "input": {"goal_id": 99999},
                    }
                ],
                "tool_use",
            ),
            _msg([{"type": "text", "text": "I can't find that goal."}], "end_turn"),
        ]
    )
    agent = Agent(client, model="claude-sonnet-5-5")  # type: ignore[arg-type]
    result = await agent.run([], "[now: x]\ndone 99999", ToolContext(session, user))
    tool_result = result.messages[2].content[0]
    assert tool_result["is_error"] is True
    assert "no goal" in tool_result["content"]
