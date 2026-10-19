# TeleView

[English](README.md) | **Tiếng Việt**

Bot Telegram giúp xem nội dung link ngay trong chat:

| Link | Bot làm gì |
|---|---|
| TikTok, Facebook, Instagram, Threads, X, Reddit, Douyin, YouTube Shorts | Tải video về rồi gửi lại, xem luôn trong chat. Bài không có video (chỉ có chữ hoặc ảnh) thì gửi chữ và ảnh |
| Trang báo / bài viết | Trích nội dung thành trang Telegraph, có nút **⚡ Instant View** |
| Bài báo có video | Gửi thêm video (video nhúng YouTube/Vimeo thì gửi link để Telegram tự hiện player) |
| Video YouTube thường, Vimeo | Bỏ qua, vì Telegram đã tự hiện player |

## Chạy

1. Tạo bot với [@BotFather](https://t.me/BotFather), lấy token.
2. **Dùng trong group**: tắt privacy mode (`/setprivacy` → *Disable*) hoặc cho bot làm admin, nếu không bot sẽ không thấy tin nhắn.
3. Cấu hình rồi chạy:

```bash
cp .env.example .env   # điền BOT_TOKEN
uv run tele-view
```

Máy cần có `ffmpeg`. Hoặc chạy bằng Docker: `docker compose up -d --build`.

## Deploy

### VPS

Cần VPS Ubuntu có SSH. Chạy `./deploy.sh root@IP_VPS`. Script tự cài Docker, copy code và `.env` lên, rồi build và chạy. Sửa code xong thì chạy lại đúng lệnh này.

Nhớ tắt bot trên máy local trước, vì hai nơi dùng chung token sẽ báo lỗi `Conflict`.

### Render (free)

1. Tạo *Web Service* từ repo GitHub: Language chọn **Docker**, Instance Type chọn **Free**, Health Check Path đặt **`/health`**.
2. Thêm biến môi trường `BOT_TOKEN`. Nên đặt thêm `MAX_JOBS=1` vì bản free ít RAM, và `TELEGRAPH_TOKEN` để bot không tạo tài khoản Telegraph mới mỗi lần khởi động. Token này nằm trong file `data/telegraph_token` sau khi bot chạy local lần đầu.
3. Không bắt buộc, dùng cho nội dung cần đăng nhập (riêng tư, giới hạn độ tuổi, nhạy cảm): đăng nhập trang đó trên trình duyệt, xuất `cookies.txt` (định dạng Netscape), thêm vào *Environment → Secret Files* với tên `cookies.txt`, rồi đặt `COOKIES_FILE=/etc/secrets/cookies.txt`. Cookie của nhiều trang có thể gộp chung một file. Nên dùng tài khoản phụ, vì nền tảng có thể đánh dấu tài khoản.
4. Bản free sẽ ngủ sau 15 phút không có request. Dùng [cron-job.org](https://cron-job.org) hoặc [UptimeRobot](https://uptimerobot.com) ping `https://<tên-service>.onrender.com/health` mỗi 5–10 phút.

Bản free không có ổ đĩa lưu trữ, nên cache mất mỗi lần restart. Video đã gửi vẫn nằm trên Telegram, gửi lại link cũ thì bot chỉ tải lại từ đầu.

### Health check

Khi có biến `PORT` (Render tự đặt), bot mở endpoint `/health`:

```json
{
  "status": "ok",
  "bot": "@ten_bot",
  "version": "0.1.0",
  "uptime_s": 3600,
  "telegram": { "ok": true, "last_ok_s_ago": 12, "error": null },
  "jobs": { "processing": 0, "max_concurrent": 2, "succeeded": 42, "failed": 1 }
}
```

Cứ 60 giây bot gọi thử Telegram một lần. Nếu mất kết nối quá 5 phút, hoặc token bị revoke, endpoint trả `503` để Render tự restart service.

## Giới hạn và cách nâng

- **Video tối đa 50MB** khi dùng Bot API thường. Video lớn hơn sẽ tự nén xuống 720p. Video quá dài (khoảng trên 25 phút) vẫn không vừa thì bot báo lỗi.
  Muốn gửi tới **2GB**: lấy `api_id`/`api_hash` ở <https://my.telegram.org>, thêm `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `BOT_API_URL=http://telegram-bot-api:8081` vào `.env`, rồi chạy `docker compose --profile local up -d --build`.
- **Douyin** bắt buộc có cookie: đặt `COOKIES_FROM_BROWSER=chrome` (khi chạy trên máy có Chrome) hoặc xuất `cookies.txt` (định dạng Netscape) rồi đặt `COOKIES_FILE`. Facebook/TikTok riêng tư cũng cần cookie.
- **Instagram, Reddit, YouTube** hay chặn IP máy chủ (Render, VPS). Nếu trên server bị lỗi đòi đăng nhập hoặc "not a bot", thêm cookie theo cách trên.
- **Threads** không có extractor trong yt-dlp: bot đọc bài từ trang mà Threads trả cho bot tìm kiếm. Nếu Threads đổi trang đó, link Threads sẽ ngừng chạy cho tới khi sửa code.
- Bài có nhiều video (carousel Instagram/Threads) chỉ gửi video đầu tiên.
- **TikTok**: khi yt-dlp lỗi (bài nhạy cảm, giới hạn độ tuổi, một số video bị chặn khu vực), bot chuyển sang API công khai [tikwm](https://www.tikwm.com). Cách này không cần đăng nhập và lấy được cả bài ảnh slideshow, nhưng link sẽ được gửi qua dịch vụ bên thứ ba đó. Một số video vẫn không xem được từ nước đặt máy chủ.
- **Dịch**: caption, nội dung bài và tiêu đề bài báo không phải tiếng Việt sẽ được dịch, bản gốc giữ trong khung thu gọn. Bot dùng endpoint miễn phí, không chính thức của Google Translate (không cần key), nên có thể ngừng chạy bất cứ lúc nào; khi đó bot chỉ hiện bản gốc. Đặt `TRANSLATE_TO` thành mã ngôn ngữ khác, hoặc để trống để tắt dịch. Nội dung đầy đủ trong trang bài báo không được dịch.
- Khi link lỗi, bot trả lời kèm lý do ngắn (giới hạn khu vực, riêng tư, đã xoá, không có video…). Lỗi chi tiết của yt-dlp chỉ ghi vào log.
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
  threads.py    bài Threads (video, ảnh, chữ)
  tiktok.py     TikTok dự phòng qua tikwm (bài bị giới hạn, slideshow)
  translate.py  dịch qua endpoint miễn phí của Google Translate
  preview.py    bài chữ/ảnh Facebook, Instagram, Reddit (thẻ Open Graph)
  cache.py      cache SQLite
  health.py     endpoint /health (bật khi có biến PORT)
```
