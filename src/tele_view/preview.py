import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

# Facebook, Instagram and Reddit serve link-preview crawlers the post's text and photo as Open Graph
# tags without a login. That covers text and photo posts, which yt-dlp rejects for having no video
CRAWLER_UA = "TelegramBot (like TwitterBot)"
INSTAGRAM_TITLE_RE = re.compile(r'^(.*?) on Instagram: "(.*)"$', re.S)
INSTAGRAM_HANDLE_RE = re.compile(r" - ([\w.]+) on [^:]+:")
SITE_SUFFIX_RE = re.compile(r"\s*\|\s*(Facebook|Instagram|Reddit)\s*$")


@dataclass
class Post:
    author: str
    handle: str
    text: str
    photos: list[str]


class _OpenGraph(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        a = dict(attrs)
        key = a.get("property") or a.get("name") or ""
        # keep the first value: later og:image tags are usually smaller variants
        if key.startswith("og:") and a.get("content") and key not in self.tags:
            self.tags[key] = a["content"]


async def fetch(url: str) -> Post | None:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": CRAWLER_UA}) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    parser = _OpenGraph()
    parser.feed(resp.text)
    og = parser.tags

    title = SITE_SUFFIX_RE.sub("", og.get("og:title", "")).strip()
    description = og.get("og:description", "").strip()
    photos = [og["og:image"]] if "og:image" in og else []
    # missing, private and login-walled posts carry no tags, or just the site name
    if not (description or photos) or title.lower() in ("", "facebook", "instagram", "reddit"):
        return None

    host = (resp.url.host or "").lower()
    if host.endswith("instagram.com") and (m := INSTAGRAM_TITLE_RE.match(title)):
        handle = m.group(1) if (h := INSTAGRAM_HANDLE_RE.search(description)) is None else h.group(1)
        return Post(author=m.group(1), handle=handle, text=m.group(2), photos=photos)
    if host.endswith("facebook.com"):
        # titles read "Author | text" (or just the author); the description holds the full text
        return Post(author=title.split(" | ")[0], handle="", text=description, photos=photos)
    # Reddit and anything else: the title is the post itself
    text = f"{title}\n\n{description}" if description and description != title else title
    return Post(author="", handle="", text=text, photos=photos)
