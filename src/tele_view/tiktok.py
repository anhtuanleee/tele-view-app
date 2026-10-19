from dataclasses import dataclass

import httpx

# tikwm's public API reads posts yt-dlp can't without a login: sensitive or age-restricted videos,
# some geo-blocked ones, and photo slideshows. Free tier allows about one request per second
TIKWM_API = "https://www.tikwm.com/api/"


@dataclass
class Post:
    author: str
    handle: str
    text: str
    video: str | None
    photos: list[str]


async def fetch(url: str) -> Post | None:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            resp = await client.post(TIKWM_API, data={"url": url, "hd": "1"})
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
    data = body.get("data") if body.get("code") == 0 else None
    if not data:
        return None
    photos = data.get("images") or []
    author = data.get("author") or {}
    return Post(
        author=author.get("nickname", ""),
        handle=author.get("unique_id", ""),
        text=data.get("title", ""),
        video=None if photos else (data.get("hdplay") or data.get("play")),
        photos=photos,
    )
