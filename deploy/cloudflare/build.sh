#!/usr/bin/env bash
# Builds the Cloudflare Pages bundle: showcase page + the real React app under /app/.
# Output: dalilai-site.zip (upload it in Cloudflare: Workers & Pages > dalilai > Create deployment).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

echo "frontend build (/app/, same-origin /api)..."
( cd "$ROOT/frontend" && VITE_API_URL= npx vite build --base=/app/ --outDir "$HERE/site/app" --emptyOutDir >/dev/null )

echo "zip..."
rm -f "$HERE/dalilai-site.zip"
( cd "$HERE/site" && zip -qr "$HERE/dalilai-site.zip" . -x ".DS_Store" )
echo "tayyor: $HERE/dalilai-site.zip ($(du -h "$HERE/dalilai-site.zip" | cut -f1))"
