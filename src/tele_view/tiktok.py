import httpx

from .post import Media, Post

# tikwm's public API reads posts yt-dlp can't without a login: sensitive or age-restricted videos,
# some geo-blocked ones, and photo slideshows. Free and keyless, but unofficial: failures fall back to errors
TIKWM_API = "https://www.tikwm.com/api/"


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
    # slideshows list their images; anything else is a single video
    if photos := data.get("images"):
        media = [Media("photo", p) for p in photos]
    elif video := data.get("hdplay") or data.get("play"):
        media = [Media("video", video)]
    else:
        media = []
    author = data.get("author") or {}
    return Post(
        author=author.get("nickname", ""),
        handle=author.get("unique_id", ""),
        text=data.get("title", ""),
        media=media,
    )
