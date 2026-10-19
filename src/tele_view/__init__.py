import asyncio
import contextlib
import dataclasses
import json
import logging
import re
import tempfile
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InputMediaPhoto, LinkPreviewOptions, Message, ReplyParameters

from . import article, threads, tweet, video
from .cache import Cache
from .config import Config, load_config
from .health import Health
from .links import PLATFORM_LABELS, classify, extract_urls
from .telegraph import Telegraph, TelegraphError

log = logging.getLogger("tele_view")

HELP = (
    "👋 Thêm tao vào group hoặc gửi link trực tiếp.\n\n"
    "• <b>TikTok, Facebook, Instagram, Threads, X, Reddit, Douyin, YouTube Shorts</b>: "
    "tao tải video về và gửi lại để xem ngay trong chat.\n"
    "• <b>Trang báo / bài viết</b>: tao chuyển thành trang có nút ⚡ Instant View.\n"
    "• Video YouTube thường, Vimeo: Telegram đã tự hiện player nên tao bỏ qua."
)

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
# longer bodies go in a collapsed quote so a long caption doesn't flood the chat
COLLAPSE_CHARS = 200
COLLAPSE_LINES = 4

YOUTUBE_EMBED_RE = re.compile(r"youtube(?:-nocookie)?\.com/embed/([\w-]{11})")


def _trim(text: str, limit: int) -> str:
    # Telegram measures length in UTF-16 code units, so most emoji count as two
    units = text.encode("utf-16-le")
    if len(units) // 2 <= limit:
        return text
    return units[: (limit - 1) * 2].decode("utf-16-le", "ignore").rstrip() + "…"


def _body(text: str, limit: int) -> str:
    """Escaped text, collapsed into an expandable quote when long."""
    text = _trim(text.strip(), limit)
    if len(text) > COLLAPSE_CHARS or text.count("\n") >= COLLAPSE_LINES:
        return f"<blockquote expandable>{escape(text)}</blockquote>"
    return escape(text)


def _video_caption(v: video.Video, url: str, label: str) -> str:
    source = f'🔗 <a href="{escape(url)}">{escape(label)}</a>'
    if v.uploader:
        source += f" · {escape(v.uploader)}"
    body = v.description or v.title
    # TikTok/Douyin titles are usually the description cut short
    if v.title and v.description and not v.description.startswith(v.title[:30]):
        body = f"{v.title}\n{v.description}"
    # Telegram counts the caption limit after parsing, so tags don't use up room
    room = CAPTION_LIMIT - len(f"{label} · {v.uploader}") - 8
    return f"{_body(body, room)}\n\n{source}" if body.strip() else source


def _error_text(kind: str, error: Exception) -> str:
    if kind == "article":
        title = "Không lấy được bài viết"
    elif kind == "threads":
        title = "Không tải được bài Threads"
    else:
        title = f"Không tải được video {PLATFORM_LABELS[kind]}"
    # only our own errors carry a message meant for users; anything else gets a generic reason
    if isinstance(error, (video.VideoError, article.ArticleError)):
        reason = escape(str(error))
    elif isinstance(error, TelegramAPIError):
        reason = "Telegram không nhận nội dung này."
    else:
        reason = "Có lỗi khi xử lý link. Thử lại sau."
    return f"⚠️ <b>{title}</b>\n{reason}"


class TeleView:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = Cache(cfg.data_dir / "cache.sqlite3")
        self.telegraph = Telegraph(cfg.telegraph_token, cfg.data_dir / "telegraph_token")
        self.jobs = asyncio.Semaphore(cfg.max_jobs)
        # link counters since startup, reported by /health
        self.processing = 0
        self.succeeded = 0
        self.failed = 0

    def stats(self) -> dict:
        return {
            "processing": self.processing,
            "max_concurrent": self.cfg.max_jobs,
            "succeeded": self.succeeded,
            "failed": self.failed,
        }

    async def handle(self, message: Message) -> None:
        if self.cfg.allowed_chat_ids and message.chat.id not in self.cfg.allowed_chat_ids:
            return
        for url in extract_urls(message):
            kind = classify(url)
            if kind is None:
                continue
            # cache hits answer instantly; anything slower gets a status reply, removed once done or failed
            status = None
            if self.cache.get(url) is None:
                if kind == "article":
                    text = "⏳ Đang lấy bài viết…"
                elif kind == "threads":
                    text = "⏳ Đang tải bài Threads…"
                else:
                    text = f"⏳ Đang tải video {PLATFORM_LABELS[kind]}…"
                status = await message.reply(text, disable_notification=True)
            self.processing += 1
            try:
                if kind == "article":
                    await self.send_article(message, url)
                elif kind == "threads":
                    await self.send_threads(message, url)
                else:
                    await self.send_video(message, url, PLATFORM_LABELS[kind], is_x=kind == "x")
                self.succeeded += 1
            except Exception as e:
                self.failed += 1
                if isinstance(e, (video.VideoError, article.ArticleError)):
                    # expected (private, deleted, geo-blocked…): the cause is already logged, no traceback
                    log.info("gave up on %s: %s", url, e)
                else:
                    log.exception("failed on %s", url)
                await message.reply(_error_text(kind, e), disable_notification=True)
            finally:
                self.processing -= 1
                if status:
                    with contextlib.suppress(TelegramAPIError):
                        await status.delete()

    async def send_video(
        self,
        message: Message,
        url: str,
        label: str,
        is_x: bool = False,
        cache_key: str | None = None,
        source: str | None = None,
        meta: dict | None = None,
    ) -> None:
        """Download `source` (default: `url`) and send it; `meta` overrides the Video fields used in the caption."""
        reply = ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True)
        cache_key = cache_key or url
        cached = self.cache.get(cache_key)
        if cached and cached[0] == "video":
            await message.answer_video(cached[1], caption=cached[2], reply_parameters=reply, supports_streaming=True)
            return

        async with self.jobs:
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO)
            with tempfile.TemporaryDirectory(dir=self.cfg.data_dir) as tmp:
                try:
                    v = await asyncio.to_thread(video.download, source or url, self.cfg, Path(tmp))
                except video.VideoError:
                    # X posts without video: show the text and photos instead
                    if is_x and await self.send_tweet(message, url):
                        return
                    raise
                if meta:
                    v = dataclasses.replace(v, **meta)
                caption = _video_caption(v, url, label)
                sent = await message.answer_video(
                    FSInputFile(v.path),
                    caption=caption,
                    duration=v.duration or None,
                    width=v.width,
                    height=v.height,
                    supports_streaming=True,
                    reply_parameters=reply,
                )
        if sent.video:
            self.cache.put(cache_key, "video", sent.video.file_id, caption)

    async def send_tweet(self, message: Message, url: str) -> bool:
        t = await tweet.fetch(url)
        if t is None:
            return False
        await self.send_post(message, url, "X", t.author, t.handle, t.text, t.photos)
        return True

    async def send_threads(self, message: Message, url: str) -> None:
        post = await threads.fetch(url)
        if post is None:
            raise video.VideoError("Bài riêng tư, đã bị xoá, hoặc Threads đang chặn bot.")
        if post.videos:
            # a carousel can hold several videos; send the first like the other platforms do
            meta = {"title": "", "description": post.text, "uploader": f"@{post.handle}"}
            await self.send_video(message, url, "Threads", source=post.videos[0], meta=meta)
        else:
            await self.send_post(message, url, "Threads", post.author, post.handle, post.text, post.photos)

    async def send_post(
        self, message: Message, url: str, label: str, author: str, handle: str, body: str, photos: list[str]
    ) -> None:
        """A text post with optional photos (tweets and Threads posts without video)."""
        reply = ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True)
        header = f"<b>{escape(author)}</b> @{escape(handle)}"
        source = f'🔗 <a href="{escape(url)}">{escape(label)}</a>'
        room = (CAPTION_LIMIT if photos else MESSAGE_LIMIT) - len(f"{author} @{handle} {label}") - 8
        text = f"{header}\n{_body(body, room)}\n\n{source}" if body.strip() else f"{header}\n\n{source}"
        if not photos:
            await message.answer(text, reply_parameters=reply, link_preview_options=LinkPreviewOptions(is_disabled=True))
        elif len(text) <= CAPTION_LIMIT:
            media = [InputMediaPhoto(media=p) for p in photos[:10]]
            media[0].caption = text
            await message.answer_media_group(media, reply_parameters=reply)
        else:
            await message.answer_media_group([InputMediaPhoto(media=p) for p in photos[:10]], reply_parameters=reply)
            await message.answer(text, link_preview_options=LinkPreviewOptions(is_disabled=True))

    async def send_article(self, message: Message, url: str) -> None:
        cached = self.cache.get(url)
        if cached and cached[0] == "article":
            page_url, meta = cached[1], json.loads(cached[2])
        else:
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            a = await article.fetch(url)
            try:
                page_url = await self.telegraph.create_page(a.title, a.nodes, a.author or a.site, a.url)
            except TelegraphError as e:
                log.warning("telegraph rejected %s: %s", url, e)
                raise article.ArticleError("Telegraph không nhận bài này.") from e
            meta = {"title": a.title, "site": a.site, "has_video": a.has_video}
            self.cache.put(url, "article", page_url, json.dumps(meta, ensure_ascii=False))

        text = f"📰 <b>{escape(meta['title'])}</b>"
        if meta["site"]:
            text += f"\n{escape(meta['site'])}"
        text += f'\n\n<a href="{page_url}">⚡ Đọc ngay trong Telegram</a>'
        await message.reply(
            text,
            link_preview_options=LinkPreviewOptions(url=page_url, prefer_large_media=True),
            disable_notification=True,
        )

        if meta.get("has_video"):
            try:
                await self.send_article_video(message, url, meta["site"])
            except Exception as e:
                # the article itself was delivered; a missing video isn't worth an error message
                log.info("no video sent for article %s: %s", url, e)

    async def send_article_video(self, message: Message, url: str, site: str) -> None:
        embed = await asyncio.to_thread(video.find_embed, url, self.cfg)
        if embed:
            if m := YOUTUBE_EMBED_RE.search(embed):
                embed = f"https://www.youtube.com/watch?v={m.group(1)}"
            kind = classify(embed)
            if kind is None:
                # YouTube/Vimeo: Telegram's own link preview plays it inline
                await message.reply(
                    f'🎬 <a href="{escape(embed)}">Video trong bài</a>',
                    link_preview_options=LinkPreviewOptions(url=embed, prefer_large_media=True),
                    disable_notification=True,
                )
                return
            if kind != "article":
                await self.send_video(message, embed, PLATFORM_LABELS[kind], is_x=kind == "x")
                return
        await self.send_video(message, url, f"Video trong bài · {site or 'nguồn'}", cache_key=f"video:{url}")


def _make_bot(cfg: Config) -> Bot:
    # long timeout: uploading a large video can take minutes
    session = AiohttpSession(timeout=600)
    if cfg.bot_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(cfg.bot_api_url, is_local=True), timeout=600)
    return Bot(cfg.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def run() -> None:
    cfg = load_config()
    app = TeleView(cfg)
    bot = _make_bot(cfg)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(HELP)

    @dp.message(F.text | F.caption)
    async def on_message(message: Message) -> None:
        await app.handle(message)

    me = await bot.get_me()
    log.info("running as @%s (upload limit %dMB)", me.username, cfg.max_upload_bytes // 1024 // 1024)
    health = Health(bot, me.username, app.stats) if cfg.port else None
    if health:
        await health.start(cfg.port)
    try:
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        await app.telegraph.close()
        if health:
            await health.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
