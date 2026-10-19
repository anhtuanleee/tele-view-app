import re
from dataclasses import dataclass

import httpx

# FxTwitter's public API returns tweet text/photos without needing an X login
FX_API = "https://api.fxtwitter.com/status/{id}"
STATUS_RE = re.compile(r"/status(?:es)?/(\d+)")


@dataclass
class Tweet:
    author: str
    handle: str
    text: str
    photos: list[str]


async def fetch(url: str) -> Tweet | None:
    m = STATUS_RE.search(url)
    if not m:
        return None
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(FX_API.format(id=m.group(1)))
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    tweet = resp.json().get("tweet")
    if not tweet:
        return None
    media = tweet.get("media") or {}
    return Tweet(
        author=tweet.get("author", {}).get("name", ""),
        handle=tweet.get("author", {}).get("screen_name", ""),
        text=tweet.get("text", ""),
        photos=[p["url"] for p in media.get("photos", []) if p.get("url")],
    )
