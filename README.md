# TeleView

**English** | [Tiếng Việt](README.vi.md)

A Telegram bot that lets you view linked content right inside the chat:

| Link | What the bot does |
|---|---|
| TikTok, Facebook, Instagram, Threads, X, Reddit, Douyin, YouTube Shorts | Downloads the video and sends it back so it plays in the chat. X and Threads posts without video are sent as text and photos |
| News sites / articles | Extracts the article into a Telegraph page with an **⚡ Instant View** button |
| Articles with a video | Also sends the video (YouTube/Vimeo embeds are sent as a link so Telegram shows its own player) |
| Regular YouTube videos, Vimeo | Skipped, since Telegram already shows a player |

## Running

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. **For groups**: turn off privacy mode (`/setprivacy` → *Disable*) or make the bot an admin, otherwise it can't see messages.
3. Configure and run:

```bash
cp .env.example .env   # fill in BOT_TOKEN
uv run tele-view
```

Requires `ffmpeg`. Or run with Docker: `docker compose up -d --build`.

## Deploying

### VPS

You need an Ubuntu VPS with SSH access. Run `./deploy.sh root@VPS_IP`. The script installs Docker if needed, uploads the code and `.env`, then builds and starts the bot. Run the same command again after changing the code.

Stop the bot on your local machine first: two instances using the same token fail with `Conflict`.

### Render (free)

1. Create a *Web Service* from the GitHub repo: Language **Docker**, Instance Type **Free**, Health Check Path **`/health`**.
2. Add the `BOT_TOKEN` environment variable. Also recommended: `MAX_JOBS=1`, since the free tier has little RAM, and `TELEGRAPH_TOKEN`, so the bot doesn't create a new Telegraph account on every start. You'll find that token in `data/telegraph_token` after running the bot locally once.
3. Optional, for content that needs a login (private, age-restricted, sensitive): export a `cookies.txt` (Netscape format) while logged in to the site, add it under *Environment → Secret Files* as `cookies.txt`, and set `COOKIES_FILE=/etc/secrets/cookies.txt`. Cookies for several sites can go in one file. Use a throwaway account, since the platform may flag it.
4. Free services sleep after 15 minutes without requests. Use [cron-job.org](https://cron-job.org) or [UptimeRobot](https://uptimerobot.com) to ping `https://<service-name>.onrender.com/health` every 5–10 minutes.

The free tier has no persistent disk, so the cache is lost on every restart. Videos already sent stay on Telegram; resending an old link just makes the bot download it again.

### Health check

When `PORT` is set (Render sets it automatically), the bot serves `/health`:

```json
{
  "status": "ok",
  "bot": "@your_bot",
  "version": "0.1.0",
  "uptime_s": 3600,
  "telegram": { "ok": true, "last_ok_s_ago": 12, "error": null },
  "jobs": { "processing": 0, "max_concurrent": 2, "succeeded": 42, "failed": 1 }
}
```

The bot checks its connection to Telegram every 60 seconds. If Telegram has been unreachable for more than 5 minutes, or the token was revoked, the endpoint returns `503` so Render restarts the service.

## Limits and how to raise them

- **Videos up to 50MB** with the regular Bot API. Larger videos are compressed to 720p automatically. Videos that are too long to fit even after compressing (roughly over 25 minutes) fail with an error.
  To send up to **2GB**: get an `api_id`/`api_hash` at <https://my.telegram.org>, add `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and `BOT_API_URL=http://telegram-bot-api:8081` to `.env`, then run `docker compose --profile local up -d --build`.
- **Douyin** requires cookies: set `COOKIES_FROM_BROWSER=chrome` (on a machine with Chrome) or export a `cookies.txt` (Netscape format) and set `COOKIES_FILE`. Private Facebook/TikTok videos need cookies too.
- **Instagram, Reddit, YouTube** often block server IPs (Render, VPS). If downloads fail there with a login or "not a bot" error, add cookies the same way.
- **Threads** has no yt-dlp extractor: the bot reads the post from the page Threads serves to search crawlers. If Threads changes that page, Threads links stop working until the code is updated.
- A post with several videos (Instagram/Threads carousel) only sends the first one.
- **TikTok**: when yt-dlp fails (sensitive or age-restricted posts, some geo-blocked videos) the bot falls back to the public [tikwm](https://www.tikwm.com) API, which needs no login and also handles photo slideshows. Links are then sent to that third-party service. Some videos are still unavailable from the server's country.
- **Translation**: captions, post text and article titles that aren't in Vietnamese are translated, with the original kept in a collapsed quote. It uses Google Translate's free, unofficial endpoint (no key), so it may stop working without notice; the bot then just shows the original. Set `TRANSLATE_TO` to another language code, or leave it empty to turn translation off. Full article pages are not translated.
- When a link fails, the bot replies with a short reason (geo-restricted, private, deleted, no video…). The full yt-dlp error only goes to the log.
- Platforms change their anti-download measures often. When downloads break, update yt-dlp first: `uv lock --upgrade-package yt-dlp && uv sync`.
- Pages that need JavaScript to show their content, or are behind a paywall, can't be extracted.
- Images on Telegraph pages are loaded straight from the original site. If that site blocks hotlinking, images may not show.
- Results are cached for 30 days per URL (`data/cache.sqlite3`), so sending the same link again is instant.

## Structure

```
src/tele_view/
  __init__.py   bot: receives links, dispatches, sends results
  links.py      extracts links from messages, classifies the platform
  video.py      yt-dlp download + ffmpeg conversion/compression for Telegram
  article.py    article extraction (trafilatura) → Telegraph nodes
  telegraph.py  Telegraph API
  tweet.py      tweets without video (via FxTwitter)
  threads.py    Threads posts (video, photos, text)
  tiktok.py     TikTok fallback via tikwm (restricted posts, slideshows)
  translate.py  translation via Google Translate's free endpoint
  cache.py      SQLite cache
  health.py     /health endpoint (enabled when PORT is set)
```
