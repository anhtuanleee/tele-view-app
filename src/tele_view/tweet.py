import re

import httpx

from .post import Media, Post

# FxTwitter's public API returns tweet text, photos and videos without needing an X login
FX_API = "https://api.fxtwitter.com/status/{id}"
STATUS_RE = re.compile(r"/status(?:es)?/(\d+)")
# its Cloudflare front rejects the default python-httpx agent
USER_AGENT = "TeleView/0.1 (+https://github.com/anhtuanleee/tele-view-app)"


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
        Media("photo" if item.get("type") == "photo" else "video", item["url"])
        for item in (tweet.get("media") or {}).get("all", [])
        if item.get("url")
    ]
    return Post(
        author=tweet.get("author", {}).get("name", ""),
        handle=tweet.get("author", {}).get("screen_name", ""),
        text=tweet.get("text", ""),
        media=media,
    )
