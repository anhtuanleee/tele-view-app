import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MB = 1024 * 1024


@dataclass(frozen=True)
class Config:
    bot_token: str
    # URL of a self-hosted Local Bot API Server (raises the upload limit from 50MB to 2000MB)
    bot_api_url: str | None
    allowed_chat_ids: frozenset[int]
    cookies_file: str | None
    cookies_from_browser: str | None
    telegraph_token: str | None
    data_dir: Path
    max_jobs: int
    # set by hosts like Render: serve /health there so an external pinger keeps the service awake
    port: int | None
    # language captions and titles are translated into; None turns translation off
    translate_to: str | None

    @property
    def max_upload_bytes(self) -> int:
        # keep a little headroom under Telegram's hard limits
        return (1950 if self.bot_api_url else 49) * MB


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Thiếu BOT_TOKEN trong .env (lấy từ @BotFather)")

    allowed = os.getenv("ALLOWED_CHAT_IDS", "")
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    cookies_file = os.getenv("COOKIES_FILE") or None
    if cookies_file:
        if not Path(cookies_file).is_file():
            raise SystemExit(f"Không thấy file cookie: {cookies_file}")
        # yt-dlp writes cookies back after every download, and hosts like Render mount secret files
        # read-only, so work on a copy inside the data dir
        writable = data_dir / "cookies.txt"
        if Path(cookies_file).resolve() != writable.resolve():
            shutil.copyfile(cookies_file, writable)
        cookies_file = str(writable)

    return Config(
        bot_token=token,
        bot_api_url=os.getenv("BOT_API_URL") or None,
        allowed_chat_ids=frozenset(int(x) for x in allowed.replace(" ", "").split(",") if x),
        cookies_file=cookies_file,
        cookies_from_browser=os.getenv("COOKIES_FROM_BROWSER") or None,
        telegraph_token=os.getenv("TELEGRAPH_TOKEN") or None,
        data_dir=data_dir,
        max_jobs=int(os.getenv("MAX_JOBS", "2")),
        port=int(port) if (port := os.getenv("PORT")) else None,
        translate_to=os.getenv("TRANSLATE_TO", "vi") or None,
    )
