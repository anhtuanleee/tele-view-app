# TeleView

Bot Telegram giúp xem nội dung link ngay trong chat:

| Link | Bot làm gì |
|---|---|
| TikTok, Facebook, X, Douyin | Tải video về rồi gửi lại, xem luôn trong chat. Tweet không có video thì gửi chữ và ảnh |
| Trang báo / bài viết | Trích nội dung thành trang Telegraph, có nút **⚡ Instant View** |
| Bài báo có video | Gửi thêm video (video nhúng YouTube/Vimeo thì gửi link để Telegram tự hiện player) |
| YouTube, Vimeo | Bỏ qua, vì Telegram đã tự hiện player |

## Chạy

1. Tạo bot với [@BotFather](https://t.me/BotFather), lấy token.
2. **Dùng trong group**: tắt privacy mode (`/setprivacy` → *Disable*) hoặc cho bot làm admin, nếu không bot sẽ không thấy tin nhắn.
3. Cấu hình rồi chạy:

```bash
cp .env.example .env   # điền BOT_TOKEN
uv run tele-view
```

Máy cần có `ffmpeg`. Hoặc chạy bằng Docker: `docker compose up -d --build`.

**Đưa lên VPS** (Ubuntu, có SSH): `./deploy.sh root@IP_VPS`. Script tự cài Docker, copy code và `.env` lên, rồi build và chạy. Sửa code xong thì chạy lại đúng lệnh này. Nhớ tắt bot trên máy local trước, vì hai nơi dùng chung token sẽ báo lỗi `Conflict`.

## Giới hạn và cách nâng

- **Video tối đa 50MB** khi dùng Bot API thường. Video lớn hơn sẽ tự nén xuống 720p. Video quá dài vẫn không vừa thì bot báo lỗi.
  Muốn gửi tới **2GB**: lấy `api_id`/`api_hash` ở <https://my.telegram.org>, thêm `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `BOT_API_URL=http://telegram-bot-api:8081` vào `.env`, rồi chạy `docker compose --profile local up -d --build`.
- **Douyin** bắt buộc có cookie: đặt `COOKIES_FROM_BROWSER=chrome` (khi chạy trên máy có Chrome) hoặc xuất `cookies.txt` (định dạng Netscape) rồi đặt `COOKIES_FILE`. Facebook/TikTok riêng tư cũng cần cookie.
- Các nền tảng hay đổi cơ chế chống tải. Khi bị lỗi thì cập nhật yt-dlp trước: `uv lock --upgrade-package yt-dlp && uv sync`.
- Trang cần JavaScript mới hiện nội dung, hoặc có paywall, thì không trích được bài.
- Ảnh trong trang Telegraph được lấy trực tiếp từ link gốc. Trang gốc chặn hotlink thì ảnh có thể không hiện.
- Kết quả được cache 30 ngày theo URL (`data/cache.sqlite3`), nên gửi lại cùng link sẽ có kết quả ngay.

## Cấu trúc

```
src/tele_view/
  __init__.py   bot: nhận link, điều phối, gửi kết quả
  links.py      tách link khỏi tin nhắn, phân loại nền tảng
  video.py      yt-dlp tải video + ffmpeg chuyển/nén cho Telegram
  article.py    trích bài (trafilatura) → node Telegraph
  telegraph.py  API Telegraph
  tweet.py      tweet không có video (qua FxTwitter)
  cache.py      cache SQLite
```
