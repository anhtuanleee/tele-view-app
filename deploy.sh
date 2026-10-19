#!/usr/bin/env bash
# Deploy TeleView lên VPS: ./deploy.sh root@1.2.3.4 [thư_mục_trên_vps]
set -euo pipefail

HOST="${1:?Dùng: ./deploy.sh user@ip [thư_mục]}"
DIR="${2:-/opt/tele-view}"
cd "$(dirname "$0")"

[ -f .env ] || { echo "Chưa có .env — chạy: cp .env.example .env rồi điền BOT_TOKEN"; exit 1; }
grep -q '^BOT_TOKEN=.\+' .env || { echo "BOT_TOKEN trong .env đang trống"; exit 1; }

echo "→ Cài Docker trên $HOST nếu chưa có"
ssh "$HOST" 'command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh'

echo "→ Copy code lên $HOST:$DIR"
ssh "$HOST" "mkdir -p '$DIR/data'"
rsync -az --delete --exclude .venv --exclude data --exclude .git --exclude __pycache__ ./ "$HOST:$DIR/"
# cookies.txt nằm ngoài data/ thì vẫn được copy theo code; trong data/ thì copy riêng
[ -f data/cookies.txt ] && rsync -az data/cookies.txt "$HOST:$DIR/data/"

echo "→ Build và chạy"
PROFILE=""
grep -q '^BOT_API_URL=.\+' .env && PROFILE="--profile local"
ssh "$HOST" "cd '$DIR' && docker compose $PROFILE up -d --build && docker compose ps"

echo "✓ Xong. Xem log: ssh $HOST 'cd $DIR && docker compose logs -f bot'"
