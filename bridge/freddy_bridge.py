import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

log = logging.getLogger("freddy-bridge")

HEARTBEAT_SECONDS = 15
JOB_TIMEOUT_SECONDS = 140


class Config:
    def __init__(self) -> None:
        base = os.environ["FREDDY_URL"].rstrip("/")
        self.ws_url = (
            base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/bridge"
        )
        self.mcp_url = base + "/mcp/"
        self.bridge_token = os.environ["FREDDY_BRIDGE_TOKEN"]
        self.mcp_token = os.environ["FREDDY_MCP_TOKEN"]
        self.claude_bin = os.environ.get("CLAUDE_BIN", "claude")
        self.model = os.environ.get("CLAUDE_MODEL", "sonnet")
        self.workdir = Path(
            os.environ.get("BRIDGE_WORKDIR", Path.home() / ".local/share/freddy-bridge")
        )


def write_private(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o600)


def prepare_workdir(cfg: Config) -> tuple[Path, Path]:
    cfg.workdir.mkdir(parents=True, exist_ok=True)
    mcp_path = cfg.workdir / "mcp.json"
    mcp = {
        "mcpServers": {
            "freddy": {
                "type": "http",
                "url": cfg.mcp_url,
                "headers": {"Authorization": f"Bearer {cfg.mcp_token}"},
            }
        }
    }
    write_private(mcp_path, json.dumps(mcp))
    return mcp_path, cfg.workdir / "system.md"


def build_command(cfg: Config, mcp_path: Path, system_path: Path) -> list[str]:
    return [
        cfg.claude_bin,
        "-p",
        "--output-format", "json",
        "--model", cfg.model,
        "--tools", "",
        "--strict-mcp-config",
        "--mcp-config", str(mcp_path),
        "--allowedTools", "mcp__freddy__*",
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--system-prompt-file", str(system_path),
        "--no-session-persistence",
        "--disable-slash-commands",
        "--max-turns", "10",
    ]  # fmt: skip


def parse_output(stdout: bytes) -> tuple[str, bool]:
    data = json.loads(stdout.decode())
    return str(data.get("result", "")), bool(data.get("is_error"))


async def run_claude(cfg: Config, prompt: str, system: str) -> str:
    mcp_path, system_path = prepare_workdir(cfg)
    write_private(system_path, system)
    proc = await asyncio.create_subprocess_exec(
        *build_command(cfg, mcp_path, system_path),
        cwd=cfg.workdir,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()), JOB_TIMEOUT_SECONDS
        )
    except TimeoutError:
        proc.send_signal(signal.SIGINT)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), 5)
        if proc.returncode is None:
            proc.kill()
        raise RuntimeError("claude timed out") from None
    try:
        result, is_error = parse_output(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr.decode()[-300:]}") from None
    if is_error:
        raise RuntimeError(result or "claude reported an error")
    return result


class Bridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.job_lock = asyncio.Lock()

    async def handle_job(self, ws: ClientConnection, msg: dict[str, Any]) -> None:
        job_id = msg["id"]
        await ws.send(json.dumps({"type": "ack", "id": job_id}))
        async with self.job_lock:
            log.info("job %s started", job_id)
            try:
                reply = await run_claude(self.cfg, msg["prompt"], msg["system"])
                out = {"type": "result", "id": job_id, "reply": reply}
            except Exception as e:
                log.warning("job %s failed: %s", job_id, e)
                out = {"type": "error", "id": job_id, "error": str(e)[:500]}
        with contextlib.suppress(websockets.ConnectionClosed):
            await ws.send(json.dumps(out))
        log.info("job %s done", job_id)

    async def heartbeat(self, ws: ClientConnection) -> None:
        while True:
            await ws.send(json.dumps({"type": "ping"}))
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def session(self) -> None:
        headers = {"Authorization": f"Bearer {self.cfg.bridge_token}"}
        async with websockets.connect(
            self.cfg.ws_url, additional_headers=headers, ping_interval=20, max_size=2**22
        ) as ws:
            log.info("connected to %s", self.cfg.ws_url)
            beat = asyncio.create_task(self.heartbeat(ws))
            jobs: set[asyncio.Task[None]] = set()
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "job":
                        task = asyncio.create_task(self.handle_job(ws, msg))
                        jobs.add(task)
                        task.add_done_callback(jobs.discard)
            finally:
                beat.cancel()

    async def run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                await self.session()
                delay = 1.0
            except (OSError, websockets.WebSocketException) as e:
                log.warning("connection lost: %s, retrying in %.0fs", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )
    asyncio.run(Bridge(Config()).run_forever())


if __name__ == "__main__":
    main()
