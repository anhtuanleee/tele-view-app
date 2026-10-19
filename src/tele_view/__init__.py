import asyncio
import contextlib
import json
import logging
import re
import tempfile
from html import escape, unescape
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import (
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    ReplyParameters,
)

from . import article, preview, threads, tiktok, tweet, video
from .cache import Cache
from .config import Config, load_config
from .health import Health
from .links import PLATFORM_LABELS, classify, expected_media, extract_urls
from .post import Post
from .telegraph import Telegraph, TelegraphError
from .translate import Translation, translate

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

# platforms whose text and photo posts are shown from their link preview tags
PREVIEW_PLATFORMS = ("facebook", "instagram", "reddit")

YOUTUBE_EMBED_RE = re.compile(r"youtube(?:-nocookie)?\.com/embed/([\w-]{11})")


def _trim(text: str, limit: int) -> str:
    # Telegram measures length in UTF-16 code units, so most emoji count as two
    units = text.encode("utf-16-le")
    if len(units) // 2 <= limit:
        return text
    return units[: (limit - 1) * 2].decode("utf-16-le", "ignore").rstrip() + "…"


def _visible_len(html: str) -> int:
    """Length as Telegram counts it: after parsing tags and entities, in UTF-16 code units."""
    return len(unescape(re.sub(r"<[^>]+>", "", html)).encode("utf-16-le")) // 2


def _text_block(text: str, limit: int) -> str:
    """Escaped text, collapsed into an expandable quote when long."""
    text = _trim(text.strip(), limit)
    if len(text) > COLLAPSE_CHARS or text.count("\n") >= COLLAPSE_LINES:
        return f"<blockquote expandable>{escape(text)}</blockquote>"
    return escape(text)


def _body(text: str, limit: int, translation: Translation | None = None) -> str:
    """Post text; when translated, the translation comes first and the original sits collapsed below."""
    if translation is None:
        return _text_block(text, limit)
    note = f"🌐 Bản gốc ({translation.source.upper()}):"
    half = (limit - len(note) - 2) // 2
    original = escape(_trim(text.strip(), half))
    return f"{_text_block(translation.text, half)}\n<blockquote expandable>{escape(note)}\n{original}</blockquote>"


def _video_text(v: video.Video) -> str:
    # placeholder titles such as Instagram's "Video by <user>" say nothing
    title = "" if v.title.startswith("Video by ") else v.title
    # TikTok/Douyin titles are usually the description cut short
    if title and v.description and not v.description.startswith(title[:30]):
        return f"{title}\n{v.description}"
    return v.description or title


def _video_caption(v: video.Video, url: str, label: str, translation: Translation | None = None) -> str:
    source = f'🔗 <a href="{escape(url)}">{escape(label)}</a>'
    if v.uploader:
        source += f" · {escape(v.uploader)}"
    body = _video_text(v)
    # Telegram counts the caption limit after parsing, so tags don't use up room
    room = CAPTION_LIMIT - len(f"{label} · {v.uploader}") - 8
    return f"{_body(body, room, translation)}\n\n{source}" if body.strip() else source


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

    async def translate(self, text: str) -> Translation | None:
        return await translate(text, self.cfg.translate_to) if self.cfg.translate_to else None

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
                elif expected_media(kind, url) == "video":
                    text = f"⏳ Đang tải video {PLATFORM_LABELS[kind]}…"
                else:
                    text = f"⏳ Đang tải bài {PLATFORM_LABELS[kind]}…"
                status = await message.reply(text, disable_notification=True)
            self.processing += 1
            try:
                if kind == "article":
                    await self.send_article(message, url)
                else:
                    await self.send_social(message, url, kind)
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

    async def send_social(self, message: Message, url: str, kind: str) -> None:
        """A social post: video, photos, text or a mix, whichever it holds."""
        label = PLATFORM_LABELS[kind]
        cached = self.cache.get(url)
        if cached and cached[0] == "video":
            await self.send_video(message, url, label)
            return

        # X and Threads have an API listing everything a post holds, so ask it first
        if kind in ("x", "threads"):
            post = await (tweet.fetch(url) if kind == "x" else threads.fetch(url))
            if post is not None:
                await self.send_post(message, url, label, post)
            elif kind == "x":
                # FxTwitter down: yt-dlp can still fetch the video
                await self.send_video(message, url, label)
            else:
                raise video.VideoError("Bài riêng tư, đã bị xoá, hoặc Threads đang chặn bot.")
            return

        expect = expected_media(kind, url)
        if expect == "photo":
            post = await self.fetch_post(kind, url)
            if post is None:
                raise video.VideoError("Không đọc được bài này (riêng tư hoặc đã bị xoá).")
            await self.send_post(message, url, label, post)
            return

        try:
            await self.send_video(message, url, label)
        except video.VideoError as e:
            # TikTok: tikwm reads sensitive, age-restricted and some geo-blocked posts without a login.
            # Facebook, Instagram, Reddit: the post is text or photos, or its video needs a login (Facebook
            # answers logged-out requests with 404). The preview still has the text when the post exists
            post = await self.fetch_post(kind, url)
            if post is None:
                raise
            log.info("no video from yt-dlp for %s (%s), sending the post instead", url, e)
            if not post.videos and not e.no_video:
                post.note = f"🎬 Bài có video nhưng không tải được: {e}"
            await self.send_post(message, url, label, post)

    async def fetch_post(self, kind: str, url: str) -> Post | None:
        if kind == "tiktok":
            return await tiktok.fetch(url)
        if kind in PREVIEW_PLATFORMS:
            return await preview.fetch(url)
        return None

    async def send_video(
        self,
        message: Message,
        url: str,
        label: str,
        cache_key: str | None = None,
        source: str | None = None,
        post: Post | None = None,
    ) -> None:
        """Download `source` (default: `url`) and send it, captioned from `post` or else the video's own metadata."""
        reply = ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True)
        cache_key = cache_key or url
        cached = self.cache.get(cache_key)
        if cached and cached[0] == "video":
            await message.answer_video(cached[1], caption=cached[2], reply_parameters=reply, supports_streaming=True)
            return

        async with self.jobs:
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO)
            with tempfile.TemporaryDirectory(dir=self.cfg.data_dir) as tmp:
                v = await asyncio.to_thread(video.download, source or url, self.cfg, Path(tmp))
                if post is not None:
                    caption = await self.post_caption(post, url, label, CAPTION_LIMIT)
                else:
                    caption = _video_caption(v, url, label, await self.translate(_video_text(v)))
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

    async def post_caption(self, post: Post, url: str, label: str, limit: int) -> str:
        names = [f"<b>{escape(post.author)}</b>" if post.author else "", f"@{escape(post.handle)}" if post.handle else ""]
        parts = [" ".join(filter(None, names))]
        if post.text.strip():
            room = limit - len(f"{post.author} @{post.handle} {label} {post.note}") - 8
            parts.append(_body(post.text, room, await self.translate(post.text)))
        if post.note:
            parts.append(f"<i>{escape(post.note)}</i>")
        source = f'🔗 <a href="{escape(url)}">{escape(label)}</a>'
        head = "\n".join(filter(None, parts))
        return f"{head}\n\n{source}" if head else source

    async def send_post(self, message: Message, url: str, label: str, post: Post) -> None:
        reply = ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True)
        if not post.media:
            text = await self.post_caption(post, url, label, MESSAGE_LIMIT)
            await message.answer(text, reply_parameters=reply, link_preview_options=LinkPreviewOptions(is_disabled=True))
            return
        if len(post.media) == 1 and post.videos:
            # a lone video goes the usual way, so it streams and is cached
            await self.send_video(message, url, label, source=post.videos[0].url, post=post)
            return

        caption = await self.post_caption(post, url, label, CAPTION_LIMIT)
        async with self.jobs:
            if post.videos:
                await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO)
            with tempfile.TemporaryDirectory(dir=self.cfg.data_dir) as tmp:
                items: list[InputMediaPhoto | InputMediaVideo] = []
                for i, m in enumerate(post.media[:10]):
                    if m.kind == "photo":
                        items.append(InputMediaPhoto(media=m.url))
                        continue
                    try:
                        v = await asyncio.to_thread(video.download, m.url, self.cfg, Path(tmp) / str(i))
                    except video.VideoError as e:
                        # one bad item shouldn't sink the rest of the album
                        log.info("skipped a video in %s: %s", url, e)
                        continue
                    items.append(InputMediaVideo(
                        media=FSInputFile(v.path), duration=v.duration or None, width=v.width, height=v.height,
                        supports_streaming=True,
                    ))
                if not items:
                    raise video.VideoError("Không tải được ảnh hay video nào trong bài này.")
                fits = _visible_len(caption) <= CAPTION_LIMIT
                # aiogram types are frozen, so the caption goes in with a copy of the first item
                items[0] = items[0].model_copy(update={"caption": caption if fits else None})
                if len(items) == 1:
                    first = items[0]
                    send = message.answer_photo if isinstance(first, InputMediaPhoto) else message.answer_video
                    await send(first.media, caption=first.caption, reply_parameters=reply)
                else:
                    await message.answer_media_group(items, reply_parameters=reply)
        if not fits:
            await message.answer(caption, link_preview_options=LinkPreviewOptions(is_disabled=True))

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
            if tr := await self.translate(a.title):
                meta["translated_title"] = tr.text
            self.cache.put(url, "article", page_url, json.dumps(meta, ensure_ascii=False))

        if translated := meta.get("translated_title"):
            text = f"📰 <b>{escape(translated)}</b>\n<i>{escape(meta['title'])}</i>"
        else:
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
                await self.send_social(message, embed, kind)
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
