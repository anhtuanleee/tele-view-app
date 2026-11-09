# wgcf registers a free Cloudflare WARP account, wireproxy serves that tunnel as a SOCKS5 proxy (see entrypoint.sh)
FROM alpine:3.22 AS warp
ARG TARGETARCH=amd64
RUN wget -qO /wgcf https://github.com/ViRb3/wgcf/releases/download/v2.3.0/wgcf_2.3.0_linux_${TARGETARCH} \
 && wget -qO- https://github.com/windtf/wireproxy/releases/download/v1.1.3/wireproxy_linux_${TARGETARCH}.tar.gz | tar xz -C / wireproxy \
 && chmod +x /wgcf /wireproxy

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /bin/uv
# yt-dlp needs a JS runtime to solve YouTube's player challenges
COPY --from=denoland/deno:bin-2.9.7 /deno /usr/local/bin/deno
COPY --from=warp /wgcf /wireproxy /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["uv", "run", "--no-sync", "tele-view"]
