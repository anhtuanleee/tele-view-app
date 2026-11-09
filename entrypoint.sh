#!/bin/sh
# YouTube blocks datacenter IPs, so route its requests through a free Cloudflare WARP tunnel (wireproxy exposes
# it as a local SOCKS5 proxy) unless YOUTUBE_PROXY names another proxy. Other platforms keep going direct.
set -e

if [ -z "$YOUTUBE_PROXY" ] || [ "$YOUTUBE_PROXY" = "warp" ]; then
  dir="${DATA_DIR:-data}/warp"
  mkdir -p "$dir"
  dir="$(cd "$dir" && pwd)"
  # the account is kept in the data dir so restarts reuse it instead of registering a new one
  if (cd "$dir" && { [ -f wgcf-account.toml ] || wgcf register --accept-tos; } \
      && { [ -f wgcf-profile.conf ] || wgcf generate; }) >/dev/null 2>&1; then
    printf 'WGConfig = %s/wgcf-profile.conf\n\n[Socks5]\nBindAddress = 127.0.0.1:40000\n' "$dir" > "$dir/wireproxy.conf"
    # restart the tunnel if it ever exits
    (while true; do wireproxy -s -c "$dir/wireproxy.conf" >/dev/null 2>&1; echo "wireproxy exited, restarting" >&2; sleep 5; done) &
    # verify that wireproxy is actually listening before routing YouTube through it
    started=0
    for i in 1 2 3 4 5; do
      if python3 -c "import socket; s = socket.socket(); s.connect(('127.0.0.1', 40000)); s.close()" >/dev/null 2>&1; then
        started=1
        break
      fi
      sleep 1
    done
    if [ "$started" -eq 1 ]; then
      export YOUTUBE_PROXY=socks5h://127.0.0.1:40000
      echo "WARP: YouTube đi qua Cloudflare WARP" >&2
    else
      echo "WARP: wireproxy không mở được port 40000, YouTube sẽ đi thẳng" >&2
      export YOUTUBE_PROXY=
    fi
  else
    echo "WARP: không đăng ký được tài khoản WARP, YouTube sẽ đi thẳng" >&2
    export YOUTUBE_PROXY=
  fi
fi

exec "$@"
