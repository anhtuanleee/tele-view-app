import json
import re

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from .post import Media, Post

# Facebook answers logged-out requests that look like a real Chrome with the full post page, which
# embeds the post as Relay JSON: the whole text (Open Graph tags cut it to ~200 characters), photos,
# and video file URLs. Anything less browser-like gets 400/404 or a login wall
JSON_RE = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)


def _first(node, match) -> dict | None:
    if isinstance(node, dict):
        if match(node):
            return node
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        if (found := _first(child, match)) is not None:
            return found
    return None


def _media(node, out: list[Media], seen: set[str]) -> None:
    """Photos and videos under a story's attachments, in order. A video's thumbnail is not a photo."""
    if isinstance(node, list):
        for child in node:
            _media(child, out, seen)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("__typename")
    if kind in ("Video", "Photo") and node.get("id") not in seen:
        if kind == "Video":
            fields = node.get("videoDeliveryLegacyFields") or node
            url = fields.get("browser_native_hd_url") or fields.get("browser_native_sd_url")
        else:
            url = ((node.get("photo_image") or node.get("viewer_image") or node.get("image")) or {}).get("uri")
        if url:
            seen.add(node.get("id"))
            out.append(Media(kind.lower(), url))
        return
    for child in node.values():
        _media(child, out, seen)


async def fetch(url: str) -> Post | None:
    try:
        async with requests.AsyncSession(impersonate="chrome", timeout=30) as session:
            resp = await session.get(url, headers={"Accept-Language": "vi,en;q=0.8"})
    except RequestException:
        return None
    if resp.status_code != 200:
        return None

    story = None
    for blob in JSON_RE.findall(resp.text):
        if "RelayPrefetchedStreamCache" not in blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        # the post itself is the first story with its own layout sections; comments come later
        story = _first(data, lambda o: o.get("__typename") == "Story" and "comet_sections" in o)
        if story:
            break
    if story is None:
        return None

    content = ((story.get("comet_sections") or {}).get("content") or {}).get("story") or {}
    text = ((content.get("message") or story.get("message")) or {}).get("text", "")
    media: list[Media] = []
    _media(story.get("attachments") or [], media, set())
    if not (text or media):
        return None
    actors = story.get("actors") or content.get("actors") or [{}]
    return Post(author=actors[0].get("name", ""), text=text, media=media)
