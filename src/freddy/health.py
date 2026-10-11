from aiohttp import web

from freddy.channels.base import Channel


async def start_health_server(channel: Channel, port: int) -> web.AppRunner:
    async def healthz(_: web.Request) -> web.Response:
        if channel.ready:
            return web.json_response({"status": "ok"})
        return web.json_response({"status": "starting"}, status=503)

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner
