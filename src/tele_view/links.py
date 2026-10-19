from urllib.parse import urlparse

from aiogram.types import Message

VIDEO_PLATFORMS = {
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com", "fb.watch", "fb.com"),
    "x": ("x.com", "twitter.com"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "instagram": ("instagram.com", "instagr.am"),
    "threads": ("threads.com", "threads.net"),
    "reddit": ("reddit.com", "redd.it"),
}

PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "x": "X",
    "douyin": "Douyin",
    "instagram": "Instagram",
    "threads": "Threads",
    "reddit": "Reddit",
    "youtube": "YouTube Shorts",
}

# Telegram already renders these natively (embedded player / Instant View)
SKIP_DOMAINS = ("youtube.com", "youtu.be", "vimeo.com", "t.me", "telegram.me", "telegra.ph")

SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".zip", ".mp4", ".mp3")

MAX_LINKS_PER_MESSAGE = 3


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def classify(url: str) -> str | None:
    """Return a platform key from VIDEO_PLATFORMS, "article", or None to ignore the link."""
    host = _host(url)
    if not host:
        return None
    # Telegram shows a player for regular YouTube links, but not for Shorts
    if _matches(host, "youtube.com") and urlparse(url).path.startswith("/shorts/"):
        return "youtube"
    if any(_matches(host, d) for d in SKIP_DOMAINS):
        return None
    for platform, domains in VIDEO_PLATFORMS.items():
        if any(_matches(host, d) for d in domains):
            return platform
    if urlparse(url).path.lower().endswith(SKIP_EXTENSIONS):
        return None
    return "article"


def extract_urls(message: Message) -> list[str]:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    urls: list[str] = []
    for e in entities:
        if e.type == "url":
            url = e.extract_from(text)
        elif e.type == "text_link" and e.url:
            url = e.url
        else:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if url not in urls:
            urls.append(url)
    return urls[:MAX_LINKS_PER_MESSAGE]
