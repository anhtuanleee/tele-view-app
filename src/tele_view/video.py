import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from .config import Config

log = logging.getLogger(__name__)


class VideoError(Exception):
    """A failure whose message is safe to show users as is."""

    def __init__(self, message: str, no_video: bool = False):
        super().__init__(message)
        # the post exists but holds no video (a text or photo post), so its text can be shown instead
        self.no_video = no_video


# yt-dlp error text → (reason shown to users, whether the post just has no video), first match wins
REASONS = [
    # TikTok answers removed and geo-blocked posts with the same status, which yt-dlp reports as an IP block
    (("ip address is blocked",),
     "Video đã bị xoá, bị giới hạn khu vực, hoặc nền tảng đang chặn máy chủ của bot.", False),
    (("available in your country", "geo restrict", "geo-restrict"),
     "Video bị giới hạn theo khu vực, không xem được từ máy chủ của bot.", False),
    (("private", "permission to view", "login", "log in", "sign in", "cookies", "not a bot",
      "comfortable for some audiences", "age-restricted", "age restricted", "inappropriate"),
     "Video riêng tư, giới hạn độ tuổi, hoặc nền tảng yêu cầu đăng nhập.", False),
    # "no media found" is Reddit's text posts, "cannot parse data" Facebook's text and photo posts
    (("no video formats", "no video could be found", "there's no video", "no video in this post",
      "no media found", "cannot parse data"),
     "Bài này không có video.", True),
    (("unsupported url",),
     "Link này không có video hoặc chưa được hỗ trợ.", True),
    (("http error 429", "too many requests", "rate-limit", "rate limit", "http error 403"),
     "Nền tảng đang tạm chặn bot. Thử lại sau ít phút.", False),
    (("removed", "deleted", "not available", "unavailable", "does not exist", "http error 404", "status code"),
     "Video đã bị xoá hoặc không còn tồn tại.", False),
    (("timed out", "timeout", "connection", "network", "temporary failure"),
     "Không kết nối được tới nền tảng. Thử lại sau.", False),
]


def _explain(error: str) -> VideoError:
    lowered = error.lower()
    for needles, reason, no_video in REASONS:
        if any(n in lowered for n in needles):
            return VideoError(reason, no_video)
    return VideoError("Không tải được video từ link này.")


@dataclass
class Video:
    path: Path
    title: str
    description: str
    uploader: str
    duration: int
    width: int | None
    height: int | None


class _YdlLogger:
    """yt-dlp prints errors to stderr on its own; keep its output at debug, download() logs failures once."""

    def debug(self, msg: str) -> None:
        log.debug(msg)

    info = warning = error = debug


def _ydl_opts(cfg: Config, workdir: Path) -> dict:
    opts = {
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "format": "bv*+ba/b",
        # prefer <=720p H.264 in mp4: plays everywhere in Telegram and stays small
        "format_sort": ["res:720", "vcodec:h264", "ext:mp4:m4a"],
        "merge_output_format": "mp4",
        "noplaylist": True,
        "playlist_items": "1",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YdlLogger(),
        "socket_timeout": 30,
        "retries": 3,
    }
    if cfg.cookies_file:
        opts["cookiefile"] = cfg.cookies_file
    if cfg.cookies_from_browser:
        opts["cookiesfrombrowser"] = (cfg.cookies_from_browser,)
    return opts


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        log.warning("ffmpeg failed: %s", proc.stderr.strip()[-500:])
        raise VideoError("Không xử lý được file video này.")


def _make_telegram_friendly(src: Path, max_bytes: int) -> Path:
    """Ensure H.264/HEVC mp4 with faststart (streamable in Telegram) and under the upload limit."""
    info = _probe(src)
    vstream = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if vstream is None:
        # e.g. TikTok photo slideshows, which yt-dlp returns as audio only
        raise VideoError("Bài này không có video (có thể là bài ảnh).", no_video=True)
    duration = float(info["format"].get("duration") or 0)
    size = src.stat().st_size
    dst = src.with_name(src.stem + ".tg.mp4")

    if vstream.get("codec_name") in ("h264", "hevc") and size <= max_bytes:
        _ffmpeg(["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)])
        return dst

    if duration <= 0:
        raise VideoError("Không xử lý được file video này.")
    audio_kbps = 96
    video_kbps = int(max_bytes * 8 * 0.9 / duration / 1000) - audio_kbps
    if video_kbps < 150:
        raise VideoError(f"Video quá dài ({int(duration // 60)} phút), Telegram chỉ nhận video bot gửi tới 50MB.")
    video_kbps = min(video_kbps, 2500)
    _ffmpeg([
        "-i", str(src),
        "-vf", "scale=-2:'min(720,ih)'",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart", str(dst),
    ])
    if dst.stat().st_size > max_bytes:
        raise VideoError("Video quá nặng, nén rồi vẫn vượt giới hạn 50MB của Telegram.")
    return dst


def find_embed(url: str, cfg: Config) -> str | None:
    """Blocking. If a page's video is an embed from another site (YouTube, TikTok...), return that URL."""
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(cfg, Path("."))) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except yt_dlp.utils.DownloadError:
        return None
    if info.get("_type") == "playlist":
        info = next(iter(info.get("entries") or []), None) or {}
    if info.get("_type") in ("url", "url_transparent"):
        return info.get("url")
    return None


def download(url: str, cfg: Config, workdir: Path) -> Video:
    """Blocking: run via asyncio.to_thread."""
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(cfg, workdir)) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        log.warning("yt-dlp failed on %s: %s", url, e)
        raise _explain(str(e)) from e

    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]
    downloads = info.get("requested_downloads") or []
    if not downloads:
        raise VideoError("Bài này không có video.", no_video=True)

    path = _make_telegram_friendly(Path(downloads[0]["filepath"]), cfg.max_upload_bytes)
    probe = _probe(path)
    vstream = next(s for s in probe["streams"] if s.get("codec_type") == "video")
    return Video(
        path=path,
        title=(info.get("title") or "").strip(),
        description=(info.get("description") or "").strip(),
        uploader=(info.get("uploader") or info.get("channel") or "").strip(),
        # direct files and some Instagram posts carry no duration in their metadata
        duration=int(info.get("duration") or float(probe["format"].get("duration") or 0)),
        width=vstream.get("width"),
        height=vstream.get("height"),
    )
