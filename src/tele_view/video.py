import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from .config import Config


class VideoError(Exception):
    pass


@dataclass
class Video:
    path: Path
    title: str
    description: str
    uploader: str
    duration: int
    width: int | None
    height: int | None


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
        raise VideoError(f"ffmpeg lỗi: {proc.stderr.strip()[-300:]}")


def _make_telegram_friendly(src: Path, max_bytes: int) -> Path:
    """Ensure H.264/HEVC mp4 with faststart (streamable in Telegram) and under the upload limit."""
    info = _probe(src)
    vstream = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if vstream is None:
        raise VideoError("Link không có video")
    duration = float(info["format"].get("duration") or 0)
    size = src.stat().st_size
    dst = src.with_name(src.stem + ".tg.mp4")

    if vstream.get("codec_name") in ("h264", "hevc") and size <= max_bytes:
        _ffmpeg(["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)])
        return dst

    if duration <= 0:
        raise VideoError("Không xác định được thời lượng video để nén")
    audio_kbps = 96
    video_kbps = int(max_bytes * 8 * 0.9 / duration / 1000) - audio_kbps
    if video_kbps < 150:
        raise VideoError(f"Video quá dài ({int(duration // 60)} phút) để nén dưới giới hạn upload")
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
        raise VideoError("Video vẫn vượt giới hạn upload sau khi nén")
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
        raise VideoError(str(e).removeprefix("ERROR: ")) from e

    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]
    downloads = info.get("requested_downloads") or []
    if not downloads:
        raise VideoError("Không tìm thấy video trong link")

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
