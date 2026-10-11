import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from freddy.bridge.prompt import build_bridge_prompt


def load_bridge() -> ModuleType:
    path = Path(__file__).parents[2] / "bridge" / "freddy_bridge.py"
    spec = importlib.util.spec_from_file_location("freddy_bridge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_without_history() -> None:
    assert build_bridge_prompt([], "[now: x]\nhi") == "[now: x]\nhi"


def test_prompt_with_history_skips_tool_results() -> None:
    history = [
        {"role": "user", "content": "[now: x]\nadd goal: gym"},
        {"role": "assistant", "content": [{"type": "text", "text": "✅ Goal added"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]},
    ]
    prompt = build_bridge_prompt(history, "[now: y]\nanything else?")
    assert "Me: [now: x]\nadd goal: gym" in prompt
    assert "Freddy: ✅ Goal added" in prompt
    assert "tool_result" not in prompt
    assert prompt.endswith("New message:\n[now: y]\nanything else?")


def test_bridge_command_and_mcp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = load_bridge()
    monkeypatch.setenv("FREDDY_URL", "https://freddy.example.com/")
    monkeypatch.setenv("FREDDY_BRIDGE_TOKEN", "b")
    monkeypatch.setenv("FREDDY_MCP_TOKEN", "m")
    monkeypatch.setenv("BRIDGE_WORKDIR", str(tmp_path))
    cfg = bridge.Config()
    assert cfg.ws_url == "wss://freddy.example.com/bridge"

    mcp_path, system_path = bridge.prepare_workdir(cfg)
    mcp = json.loads(mcp_path.read_text())["mcpServers"]["freddy"]
    assert mcp == {
        "type": "http",
        "url": "https://freddy.example.com/mcp/",
        "headers": {"Authorization": "Bearer m"},
    }
    assert oct(mcp_path.stat().st_mode & 0o777) == "0o600"

    cmd = bridge.build_command(cfg, mcp_path, system_path)
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__freddy__*"
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"


def test_parse_output() -> None:
    bridge = load_bridge()
    assert bridge.parse_output(b'{"result": "hey", "is_error": false}') == ("hey", False)
    assert bridge.parse_output(b'{"result": "nope", "is_error": true}') == ("nope", True)
