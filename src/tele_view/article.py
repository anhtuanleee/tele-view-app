import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
import trafilatura

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Telegraph rejects content over 64KB
MAX_CONTENT_BYTES = 60_000

# tag mapping into the subset Telegraph accepts; None = drop the tag but keep its children
TAG_MAP = {
    "h1": "h3", "h2": "h3", "h3": "h4", "h4": "h4", "h5": "h4", "h6": "h4",
    "p": "p", "blockquote": "blockquote", "ul": "ul", "ol": "ol", "li": "li",
    "pre": "pre", "code": "code", "b": "b", "strong": "strong", "i": "i", "em": "em",
    "u": "u", "s": "s", "del": "s", "a": "a", "br": "br", "hr": "hr", "img": "img",
    "figure": "figure", "figcaption": "figcaption", "aside": "aside",
}
VOID_TAGS = {"br", "hr", "img"}
# whole subtrees to discard
SKIP_TAGS = {"script", "style", "noscript", "head", "title"}

# cheap check before handing the page to yt-dlp's generic extractor
VIDEO_HINT_RE = re.compile(r"<video|\.m3u8|og:video|youtube(?:-nocookie)?\.com/embed/|player\.vimeo\.com/video/", re.I)


class ArticleError(Exception):
    pass


@dataclass
class Article:
    url: str
    title: str
    author: str
    site: str
    nodes: list
    has_video: bool


class _TelegraphNodeBuilder(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.root: list = []
        # stack of (source tag, children list it appends into)
        self.stack: list[tuple[str, list]] = [("", self.root)]
        self.skip_depth = 0

    def _children(self) -> list:
        return self.stack[-1][1]

    def handle_starttag(self, tag, attrs):
        if self.skip_depth or tag in SKIP_TAGS:
            if tag not in VOID_TAGS:
                self.skip_depth += 1
            return
        mapped = TAG_MAP.get(tag)
        if mapped is None:
            if tag not in VOID_TAGS:
                self.stack.append((tag, self._children()))
            return

        attrs = dict(attrs)
        node: dict = {"tag": mapped}
        if mapped == "a" and attrs.get("href"):
            node["attrs"] = {"href": urljoin(self.base_url, attrs["href"])}
        elif mapped == "img":
            src = attrs.get("src") or attrs.get("data-src")
            if not src:
                return
            node["attrs"] = {"src": urljoin(self.base_url, src)}
        self._children().append(node)
        if mapped not in VOID_TAGS:
            node["children"] = []
            self.stack.append((tag, node["children"]))

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag not in VOID_TAGS:
                self.skip_depth -= 1
            return
        # pop back to the matching open tag, tolerating sloppy HTML
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip_depth or not data.strip():
            return
        self._children().append(data)


def html_to_nodes(html: str, base_url: str) -> list:
    builder = _TelegraphNodeBuilder(base_url)
    builder.feed(html)
    builder.close()
    return builder.root


def _fit(nodes: list, source_url: str) -> list:
    footer = [
        {"tag": "hr"},
        {"tag": "p", "children": ["Nguồn: ", {"tag": "a", "attrs": {"href": source_url}, "children": [source_url]}]},
    ]
    budget = MAX_CONTENT_BYTES - len(json.dumps(footer, ensure_ascii=False).encode())
    out, used = [], 2
    for node in nodes:
        size = len(json.dumps(node, ensure_ascii=False).encode()) + 1
        if used + size > budget:
            out.append({"tag": "p", "children": [{"tag": "i", "children": ["… Bài dài, đọc tiếp ở link gốc."]}]})
            break
        out.append(node)
        used += size
    return out + footer


async def fetch(url: str) -> Article:
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=20, headers={"User-Agent": USER_AGENT, "Accept-Language": "vi,en;q=0.8"}
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ArticleError(f"Không mở được trang: {e}") from e
    if "html" not in resp.headers.get("content-type", "html"):
        raise ArticleError("Link không phải trang web")

    final_url = str(resp.url)
    html = resp.text
    body = trafilatura.extract(
        html, url=final_url, output_format="html", include_images=True,
        include_links=True, include_formatting=True, favor_recall=True,
    )
    if not body:
        raise ArticleError("Không trích được nội dung bài (có thể trang cần JS hoặc đăng nhập)")
    meta = trafilatura.extract_metadata(html, default_url=final_url)

    nodes = html_to_nodes(body, final_url)
    has_image = '"img"' in json.dumps(nodes)
    if meta and meta.image and not has_image:
        nodes.insert(0, {"tag": "figure", "children": [{"tag": "img", "attrs": {"src": urljoin(final_url, meta.image)}}]})

    title = (meta.title if meta else None) or final_url
    # trafilatura often repeats the title as the first heading
    if nodes and isinstance(nodes[0], dict) and nodes[0]["tag"] == "h3" and nodes[0].get("children") == [title]:
        nodes.pop(0)

    return Article(
        url=final_url,
        title=title,
        author=(meta.author if meta else None) or "",
        site=(meta.sitename if meta else None) or (meta.hostname if meta else "") or "",
        nodes=_fit(nodes, final_url),
        has_video=bool(VIDEO_HINT_RE.search(html)),
    )
