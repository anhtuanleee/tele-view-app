import re

import httpx

from .post import Media, Post

# FxTwitter's public API returns tweet text, photos and videos without needing an X login
FX_API = "https://api.fxtwitter.com/status/{id}"
STATUS_RE = re.compile(r"/status(?:es)?/(\d+)")
# its Cloudflare front rejects the default python-httpx agent
USER_AGENT = "TeleView/0.1 (+https://github.com/anhtuanleee/tele-view-app)"
# variant URLs carry their size, e.g. /vid/1280x720/
SIZE_RE = re.compile(r"/(\d+)x(\d+)/")
# same cap as yt-dlp downloads: the default 1080p is ~5x heavier, slow to upload and often over 50MB
MAX_SIDE = 720


def _video_url(item: dict) -> str:
    """The best mp4 whose shorter side is at most 720px, else the default (highest) one."""
    best, best_rate = item["url"], -1
    for v in item.get("variants") or []:
        m = SIZE_RE.search(v.get("url", ""))
        if v.get("content_type") != "video/mp4" or not m or min(int(m[1]), int(m[2])) > MAX_SIDE:
            continue
        if (v.get("bitrate") or 0) > best_rate:
            best, best_rate = v["url"], v.get("bitrate") or 0
    return best


async def fetch(url: str) -> Post | None:
    m = STATUS_RE.search(url)
    if not m:
        return None
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        try:
            resp = await client.get(FX_API.format(id=m.group(1)))
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    tweet = resp.json().get("tweet")
    if not tweet:
        return None
    # "all" keeps the order the tweet shows; GIFs come as videos
    media = [
        Media("photo", item["url"]) if item.get("type") == "photo" else Media("video", _video_url(item))
        for item in (tweet.get("media") or {}).get("all", [])
        if item.get("url")
    ]
    return Post(
        author=tweet.get("author", {}).get("name", ""),
        handle=tweet.get("author", {}).get("screen_name", ""),
        text=tweet.get("text", ""),
        media=media,
    )
