#!/usr/bin/env bash
# Jonli demoni yoqadi: baza + backend + Cloudflare tunnel, keyin tunnel manzilini
# https://dalilai.pages.dev darvozasiga bildiradi.
#
#   ./deploy/cloudflare/go-live.sh          yoqish
#   ./deploy/cloudflare/go-live.sh stop     o'chirish (tunnel va backend)
#   ./deploy/cloudflare/go-live.sh status   holat
#
# Tunnel manzili har yoqilganda yangi bo'ladi, shuning uchun skript uni o'zi yangilaydi.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SITE="${DALIL_SITE:-https://dalilai.pages.dev}"
PORT=8001
LOG="$ROOT/tmp/local-demo"; mkdir -p "$LOG"

[ -f "$HERE/.gateway.env" ] || { echo "Topilmadi: $HERE/.gateway.env (darvoza kaliti)"; exit 1; }
KEY="$(grep '^GATEWAY_KEY=' "$HERE/.gateway.env" | cut -d= -f2-)"
PGREADY=/opt/homebrew/opt/postgresql@18/bin/pg_isready

backend_up() { curl -s -m 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q '"database":"ready"'; }

status() {
  echo "--- Baza:     $($PGREADY -h 127.0.0.1 -q && echo ishlayapti || echo o\'chiq)"
  echo "--- Backend:  $(backend_up && echo ishlayapti || echo o\'chiq)"
  echo "--- Tunnel:   $(pgrep -f 'cloudflared tunnel' >/dev/null && echo ishlayapti || echo o\'chiq)"
  echo "--- Sayt:     $(curl -s -m 10 "$SITE/_status" || echo javob yo\'q)"
}

case "${1:-start}" in
  status) status; exit 0 ;;
  stop)
    pkill -f "cloudflared tunnel" 2>/dev/null && echo "tunnel to'xtatildi" || true
    pkill -f "caffeinate -i -w" 2>/dev/null || true
    pkill -f "uvicorn app.main:app" 2>/dev/null && echo "backend to'xtatildi" || true
    echo "Baza ataylab to'xtatilmaydi (brew services stop postgresql@18)."
    exit 0 ;;
esac

# 1) Baza
if ! $PGREADY -h 127.0.0.1 -q; then
  echo "PostgreSQL yoqilmoqda..."
  brew services start postgresql@18 >/dev/null
  for _ in $(seq 1 30); do $PGREADY -h 127.0.0.1 -q && break; sleep 1; done
fi

# 2) Backend
if ! backend_up; then
  echo "Backend yoqilmoqda..."
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  ( cd "$ROOT/backend" && nohup ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT > "$LOG/backend.log" 2>&1 & )
fi
for _ in $(seq 1 60); do curl -s -m 3 "http://127.0.0.1:$PORT/api/health" | grep -q '"embedding":"ready"' && break; sleep 3; done

# 3) Tunnel
pkill -f "cloudflared tunnel" 2>/dev/null || true
: > "$LOG/tunnel.log"
( nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > "$LOG/tunnel.log" 2>&1 & )
URL=""
for _ in $(seq 1 40); do
  URL="$(grep -ao 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG/tunnel.log" | grep -v '^https://api\.' | head -1 || true)"
  [ -n "$URL" ] && grep -aq "Registered tunnel connection" "$LOG/tunnel.log" && break
  sleep 1
done
[ -n "$URL" ] || { echo "Tunnel manzili chiqmadi. Log: $LOG/tunnel.log"; exit 1; }

# tunnel haqiqatan javob berguncha kutamiz (kalit bilan)
for _ in $(seq 1 30); do
  [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "X-Gateway-Key: $KEY" "$URL/api/health")" = "200" ] && break
  sleep 2
done

# 4) Darvozaga bildiramiz
CODE="$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X PUT "$SITE/_register" \
        -H "X-Gateway-Key: $KEY" -d "{\"url\":\"$URL\"}")"
[ "$CODE" = "200" ] || { echo "Ro'yxatdan o'tkazib bo'lmadi (HTTP $CODE). Sayt yuklanganmi va GATEWAY_KEY qo'yilganmi?"; exit 1; }

# kompyuter uxlab qolmasin (tunnel ishlab turgan vaqtda)
( nohup caffeinate -i -w "$(pgrep -f 'cloudflared tunnel' | head -1)" >/dev/null 2>&1 & )

sleep 2
status
echo
echo "Tayyor: $SITE"
echo "Kirish: demo / Dalil-Demo-26"
echo "O'chirish: ./deploy/cloudflare/go-live.sh stop"
