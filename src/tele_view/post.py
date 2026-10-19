from dataclasses import dataclass, field


@dataclass
class Media:
    # "photo": a direct image URL Telegram fetches itself; "video": any URL yt-dlp can download
    kind: str
    url: str


@dataclass
class Post:
    """What a social post holds, in the order the platform shows it. Any part may be empty."""

    author: str = ""
    handle: str = ""
    text: str = ""
    media: list[Media] = field(default_factory=list)
    # shown under the text, e.g. why the post's video couldn't be fetched
    note: str = ""

    @property
    def videos(self) -> list[Media]:
        return [m for m in self.media if m.kind == "video"]
