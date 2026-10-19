import json
import re
from dataclasses import dataclass

import httpx

# yt-dlp has no Threads extractor. Logged-out browsers get a login wall, but the page served to
# search crawlers embeds the post as JSON, including its video and photo URLs
CRAWLER_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
POST_RE = re.compile(r"/post/([\w-]+)")
JSON_RE = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)


@dataclass
class Post:
    author: str
    handle: str
    text: str
    videos: list[str]
    photos: list[str]


def _find(node, code: str) -> dict | None:
    if isinstance(node, dict):
        if node.get("code") == code and ("video_versions" in node or "image_versions2" in node):
            return node
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        if found := _find(child, code):
            return found
    return None


def _media(item: dict) -> tuple[str | None, str | None]:
    videos = item.get("video_versions") or []
    images = (item.get("image_versions2") or {}).get("candidates") or []
    return (videos[0]["url"] if videos else None), (images[0]["url"] if images else None)


async def fetch(url: str) -> Post | None:
    m = POST_RE.search(url)
    if not m:
        return None
    code = m.group(1)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": CRAWLER_UA}) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None

    post = None
    for blob in JSON_RE.findall(resp.text):
        if code in blob:
            try:
                post = _find(json.loads(blob), code)
            except json.JSONDecodeError:
                continue
            if post:
                break
    if post is None:
        return None

    videos, photos = [], []
    for item in post.get("carousel_media") or [post]:
        video, image = _media(item)
        if video:
            videos.append(video)
        elif image:
            photos.append(image)
    user = post.get("user") or {}
    return Post(
        author=user.get("full_name") or user.get("username", ""),
        handle=user.get("username", ""),
        text=(post.get("caption") or {}).get("text", ""),
        videos=videos,
        photos=photos,
    )
