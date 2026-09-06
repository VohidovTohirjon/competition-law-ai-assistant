#!/usr/bin/env bash
# Lokal demo: PostgreSQL + backend (venv/uvicorn) + frontend (Vite).
#   ./local-demo.sh          - hammasini ishga tushiradi
#   ./local-demo.sh stop     - backend/frontend jarayonlarini to'xtatadi
#   ./local-demo.sh status   - holatni ko'rsatadi
#
# Baza uchun ikki yo'l bor:
#   1) Native PostgreSQL (Homebrew, tavsiya etiladi) - ~35 MB xotira;
#   2) Docker Compose (production bilan bir xil) - Docker VM 2-4 GB xotira talab qiladi.
# Skript avval native serverni qidiradi, topilmasa Docker'ga o'tadi.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG="$ROOT/tmp/local-demo"; mkdir -p "$LOG"
cd "$ROOT"

PGBIN=""
for candidate in /opt/homebrew/opt/postgresql@18/bin /opt/homebrew/opt/postgresql@17/bin /opt/homebrew/opt/postgresql@16/bin; do
  [ -x "$candidate/pg_isready" ] && PGBIN="$candidate" && break
done

# /usr/local/bin/docker eski Docker.app nusxasiga ishora qilishi mumkin (API 1.43),
# shuning uchun Docker Desktop'ning o'z CLI'si ustuvor.
DOCKER=docker
[ -x /Applications/Docker.app/Contents/Resources/bin/docker ] && DOCKER=/Applications/Docker.app/Contents/Resources/bin/docker
COMPOSE=("$DOCKER" compose -f docker-compose.yml -f docker-compose.local.yml)

db_up() { [ -n "$PGBIN" ] && "$PGBIN/pg_isready" -h 127.0.0.1 -p 5432 -q 2>/dev/null; }

status() {
  echo "--- PostgreSQL :5432:"
  if db_up; then
    local mb; mb=$(ps -axo rss=,comm= | grep -i postgres | awk '{s+=$1} END {printf "%d", s/1024}')
    echo "  native (Homebrew), ${mb} MB"
  elif "$DOCKER" info >/dev/null 2>&1 && "${COMPOSE[@]}" ps db 2>/dev/null | grep -q Up; then
    echo "  docker konteyner"
  else
    echo "  ishlamayapti"
  fi
  echo "--- Backend :8000:"; curl -s -m 5 http://127.0.0.1:8000/api/health || echo "  javob yo'q"; echo
  echo "--- Frontend :5173:"; curl -s -m 5 -o /dev/null -w "  HTTP %{http_code}\n" http://127.0.0.1:5173 || echo "  javob yo'q"
}

stop_all() {
  pkill -f "uvicorn app.main:app" 2>/dev/null && echo "backend to'xtatildi" || true
  pkill -f "vite --host" 2>/dev/null && echo "frontend to'xtatildi" || true
  echo "PostgreSQL ataylab to'xtatilmaydi (brew services stop postgresql@18)."
}

case "${1:-start}" in
  status) status; exit 0 ;;
  stop) stop_all; exit 0 ;;
esac

# 1) Baza
if db_up; then
  echo "PostgreSQL (native) ishlayapti."
elif [ -n "$PGBIN" ] && brew services list 2>/dev/null | grep -q "postgresql@"; then
  echo "PostgreSQL ishga tushirilmoqda..."
  brew services start "$(brew services list | awk '/postgresql@/{print $1; exit}')" >/dev/null
  for _ in $(seq 1 30); do db_up && break; sleep 2; done
fi
if ! db_up; then
  echo "Native PostgreSQL topilmadi; Docker'ga o'tilmoqda (VM 2-4 GB xotira oladi)."
  "$DOCKER" info >/dev/null 2>&1 || { open -a Docker; for _ in $(seq 1 60); do "$DOCKER" info >/dev/null 2>&1 && break; sleep 2; done; }
  "$DOCKER" info >/dev/null 2>&1 || { echo "Baza ishga tushmadi."; exit 1; }
  "${COMPOSE[@]}" up -d db
  for _ in $(seq 1 30); do "$DOCKER" exec antimonopoliya-db-1 pg_isready -U raqobat -d raqobat >/dev/null 2>&1 && break; sleep 2; done
fi

# 2) Migratsiya
( cd backend && ../.venv/bin/alembic upgrade head >/dev/null && echo "migratsiya: head" )

# 3) Backend
pkill -f "uvicorn app.main:app" 2>/dev/null || true
( cd backend && nohup ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$LOG/backend.log" 2>&1 & )
for _ in $(seq 1 30); do curl -s -m 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 2; done

# 4) Frontend
pkill -f "vite --host" 2>/dev/null || true
( cd frontend && nohup npm run dev > "$LOG/frontend.log" 2>&1 & )
for _ in $(seq 1 30); do curl -s -m 2 -o /dev/null http://127.0.0.1:5173 2>/dev/null && break; sleep 1; done

status
echo
echo "Embedding modeli (BGE-M3) fon rejimida ~40 soniyada tayyor: /api/health -> embedding: ready"
echo "Interfeys: http://localhost:5173   API hujjatlari: http://localhost:8000/api/docs"
echo "Loglar: $LOG/backend.log, $LOG/frontend.log"
