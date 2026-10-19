import os
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

    return Config(
        bot_token=token,
        bot_api_url=os.getenv("BOT_API_URL") or None,
        allowed_chat_ids=frozenset(int(x) for x in allowed.replace(" ", "").split(",") if x),
        cookies_file=os.getenv("COOKIES_FILE") or None,
        cookies_from_browser=os.getenv("COOKIES_FROM_BROWSER") or None,
        telegraph_token=os.getenv("TELEGRAPH_TOKEN") or None,
        data_dir=data_dir,
        max_jobs=int(os.getenv("MAX_JOBS", "2")),
    )
