import asyncio
import contextlib
import hmac
import itertools
import time
from typing import Any

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect

log = structlog.get_logger()


class BridgeOffline(Exception):
    pass


class BridgeTimeout(Exception):
    pass


class BridgeFailed(Exception):
    pass


class BridgeHub:
    def __init__(
        self,
        token: str,
        timeout: float = 150,
        ack_timeout: float = 8,
        stale_after: float = 45,
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.ack_timeout = ack_timeout
        self.stale_after = stale_after
        self._ws: WebSocket | None = None
        self._last_seen = 0.0
        self._ids = itertools.count(1)
        self._acks: dict[str, asyncio.Future[None]] = {}
        self._results: dict[str, asyncio.Future[str]] = {}

    @property
    def online(self) -> bool:
        return self._ws is not None and time.monotonic() - self._last_seen < self.stale_after

    def authorized(self, header: str | None) -> bool:
        expected = f"Bearer {self.token}"
        return header is not None and hmac.compare_digest(header, expected)

    async def handle(self, ws: WebSocket) -> None:
        if not self.authorized(ws.headers.get("authorization")):
            log.warning("bridge.rejected", client=str(ws.client))
            await ws.close(code=1008)
            return
        await ws.accept()
        if self._ws is not None:
            log.info("bridge.replaced")
            await self._drop(self._ws)
        self._ws = ws
        self._last_seen = time.monotonic()
        log.info("bridge.connected", client=str(ws.client))
        try:
            while True:
                msg = await ws.receive_json()
                self._last_seen = time.monotonic()
                self._dispatch(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if self._ws is ws:
                self._ws = None
                self._fail_pending()
                log.info("bridge.disconnected")

    def _dispatch(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        job_id = str(msg.get("id", ""))
        if kind == "ack" and job_id in self._acks:
            self._resolve(self._acks.pop(job_id), None)
        elif kind == "result" and job_id in self._results:
            self._resolve(self._results.pop(job_id), str(msg.get("reply", "")))
        elif kind == "error" and job_id in self._results:
            fut = self._results.pop(job_id)
            if not fut.done():
                fut.set_exception(BridgeFailed(str(msg.get("error", "unknown error"))))

    @staticmethod
    def _resolve(fut: asyncio.Future[Any], value: Any) -> None:
        if not fut.done():
            fut.set_result(value)

    def _fail_pending(self) -> None:
        pending: list[asyncio.Future[Any]] = [*self._acks.values(), *self._results.values()]
        for fut in pending:
            if not fut.done():
                fut.set_exception(BridgeOffline())
        self._acks.clear()
        self._results.clear()

    async def _drop(self, ws: WebSocket) -> None:
        self._ws = None
        self._fail_pending()
        with contextlib.suppress(RuntimeError):
            await ws.close(code=1012)

    async def ask(self, prompt: str, system: str) -> str:
        ws = self._ws
        if ws is None or not self.online:
            raise BridgeOffline()
        job_id = str(next(self._ids))
        loop = asyncio.get_running_loop()
        ack: asyncio.Future[None] = loop.create_future()
        result: asyncio.Future[str] = loop.create_future()
        self._acks[job_id] = ack
        self._results[job_id] = result
        try:
            await ws.send_json({"type": "job", "id": job_id, "prompt": prompt, "system": system})
            try:
                await asyncio.wait_for(asyncio.shield(ack), self.ack_timeout)
            except TimeoutError as e:
                raise BridgeOffline() from e
            try:
                return await asyncio.wait_for(asyncio.shield(result), self.timeout)
            except TimeoutError as e:
                raise BridgeTimeout() from e
        except RuntimeError as e:
            raise BridgeOffline() from e
        finally:
            self._acks.pop(job_id, None)
            self._results.pop(job_id, None)
