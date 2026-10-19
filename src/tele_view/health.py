import asyncio
import logging
import time
from collections.abc import Callable
from importlib.metadata import version

from aiogram import Bot
from aiohttp import web

log = logging.getLogger(__name__)

CHECK_EVERY = 60
# report unhealthy once Telegram has been unreachable this long, so the host restarts us
STALE_AFTER = 5 * 60


class Health:
    """/health endpoint for uptime pingers and the host's health check (200 ok, 503 unhealthy)."""

    def __init__(self, bot: Bot, username: str, stats: Callable[[], dict]):
        self.bot = bot
        self.username = username
        self.stats = stats
        self.started = time.time()
        # get_me just succeeded at startup
        self.telegram_ok_at = self.started
        self.telegram_error: str | None = None
        self._runner: web.AppRunner | None = None
        self._watcher: asyncio.Task | None = None

    async def _watch_telegram(self) -> None:
        while True:
            await asyncio.sleep(CHECK_EVERY)
            try:
                await self.bot.get_me()
            except Exception as e:
                # the endpoint is public: expose only the error type, log the details
                self.telegram_error = type(e).__name__
                log.warning("telegram check failed: %s", e)
            else:
                self.telegram_ok_at = time.time()
                self.telegram_error = None

    async def _handle(self, _: web.Request) -> web.Response:
        now = time.time()
        healthy = now - self.telegram_ok_at < STALE_AFTER
        body = {
            "status": "ok" if healthy else "unhealthy",
            "bot": f"@{self.username}",
            "version": version("tele-view"),
            "uptime_s": int(now - self.started),
            "telegram": {
                "ok": self.telegram_error is None,
                "last_ok_s_ago": int(now - self.telegram_ok_at),
                "error": self.telegram_error,
            },
            "jobs": self.stats(),
        }
        return web.json_response(body, status=200 if healthy else 503, headers={"Cache-Control": "no-store"})

    async def start(self, port: int) -> None:
        server = web.Application()
        server.router.add_get("/", self._handle)
        server.router.add_get("/health", self._handle)
        self._runner = web.AppRunner(server, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", port).start()
        self._watcher = asyncio.create_task(self._watch_telegram())
        log.info("health check on :%d/health", port)

    async def stop(self) -> None:
        if self._watcher:
            self._watcher.cancel()
        if self._runner:
            await self._runner.cleanup()
