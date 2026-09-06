# Vast.ai deploy — Ubuntu 22.04 + RTX 4090

```
Internet
   |
   v
Nginx :80  (yagona ochiq port)
   |
   +-- /       -> React static (frontend konteyner)
   +-- /api/*  -> FastAPI :8000  (ichki, compose tarmog'i)
                     |
                     +-- PostgreSQL + pgvector :5432 (ichki)
                     +-- BGE-M3 (backend jarayonida)
                     +-- vLLM :8001 (HOST, systemd)
                              +-- openai/gpt-oss-20b -> RTX 4090
```

| Qatlam | Port | Ko'rinish |
|---|---|---|
| Nginx | 80 | **Ochiq** |
| SSH | 22 | **Ochiq** |
| FastAPI | 8000 | Faqat compose tarmog'i |
| PostgreSQL | 5432 | Faqat compose tarmog'i |
| vLLM | 8001 | Faqat host + Docker bridge (ufw bloklaydi) |

## vLLM tarmoq sozlamasi — muhim

Backend Docker ichida, vLLM esa host'da ishlaydi. **Linux'da konteyner host'ning
`127.0.0.1` iga ULANA OLMAYDI.** Shuning uchun:

* vLLM `--host 0.0.0.0` bilan tinglaydi (docker bridge undan foydalanadi);
* `ufw` 8001 ni tashqi interfeysda bloklaydi, faqat `docker0` dan ruxsat beradi;
* compose'da `extra_hosts: host.docker.internal:host-gateway` bor, shuning uchun
  backend `http://host.docker.internal:8001/v1` manzilidan foydalanadi.

vLLM hech qachon internetga ochilmaydi.

---

## 1. OS paketlari

```bash
ssh root@<VAST_IP>
```

```bash
apt-get update && apt-get install -y git curl ufw python3-venv python3-pip && nvidia-smi && docker --version && docker compose version
```

## 2. Repozitoriyni klonlash

```bash
mkdir -p /opt && cd /opt && git clone https://github.com/VohidovTohirjon/competition-law-ai-assistant.git raqobat && cd /opt/raqobat
```

## 3. Production `.env`

```bash
cp .env.production.example .env && sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env && sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env && grep -E '^(APP_ENV|LLM_PROVIDER|LOCAL_LLM_BASE_URL|LOCAL_LLM_MODEL|CORS_ORIGINS)=' .env
```

## 4–6. vLLM muhiti va model keshi

Model keshi barqaror host katalogida — konteyner qayta ishga tushsa ham 13+ GB
qayta yuklanmaydi:

```bash
mkdir -p /opt/raqobat-ai/models && python3 -m venv /opt/vllm && /opt/vllm/bin/pip install --upgrade pip && /opt/vllm/bin/pip install vllm
```

Modelni oldindan yuklab olish (birinchi start'ni tezlashtiradi):

```bash
HF_HOME=/opt/raqobat-ai/models /opt/vllm/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('openai/gpt-oss-20b')"
```

## 7. vLLM systemd

```bash
cat >/etc/systemd/system/vllm.service <<'EOF'
[Unit]
Description=vLLM gpt-oss-20b (OpenAI-compatible)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HF_HOME=/opt/raqobat-ai/models
# 0.0.0.0: Docker bridge orqali backend ulanadi. Tashqi kirish ufw bilan yopiladi.
ExecStart=/opt/vllm/bin/vllm serve openai/gpt-oss-20b \
  --host 0.0.0.0 --port 8001 \
  --served-model-name openai/gpt-oss-20b \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85
Restart=always
RestartSec=15
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now vllm
```

Model yuklanishini kuting (bir necha daqiqa), so'ng:

```bash
curl -s http://127.0.0.1:8001/v1/models | python3 -m json.tool
```

`data[].id` aynan `openai/gpt-oss-20b` bo'lishi shart.

## 8. Firewall

```bash
ufw default deny incoming && ufw default allow outgoing && ufw allow 22/tcp && ufw allow 80/tcp && ufw allow in on docker0 to any port 8001 proto tcp && ufw --force enable && ufw status verbose
```

## 9–13. Ilovani ishga tushirish

```bash
cd /opt/raqobat && ./deploy/deploy.sh
```

Skript: `.env` ni tekshiradi, vLLM'ni probe qiladi, образlarni quradi,
PostgreSQL'ni ko'taradi, migratsiyalarni (`0001 -> head`) bajaradi, backend/frontend/Nginx
ni ishga tushiradi va sog'liqni kutadi.

### Administrator (parol faqat env orqali)

```bash
cd /opt/raqobat && read -rsp "Admin paroli: " ADMIN_PASSWORD && echo && docker compose exec -e ADMIN_PASSWORD="$ADMIN_PASSWORD" backend sh -c 'python -m app.cli --username admin --password "$ADMIN_PASSWORD" --full-name "Tizim administratori"' && unset ADMIN_PASSWORD
```

Takroran ishga tushirilsa `Bu login allaqachon mavjud` deb xavfsiz to'xtaydi.

### Huquqiy korpus

```bash
cd /opt/raqobat && docker compose exec backend python scripts/import_nhh.py --admin-username admin
```

Tekshirish (hujjat / parcha / faol-indekslangan sonlari):

```bash
cd /opt/raqobat && docker compose exec backend python scripts/import_nhh.py --status
```

Kutilgan: `NHH hujjatlar: 1`, `Faol + indekslangan: 1`, `NHH parchalar: 118`.
Takroriy ishga tushirish dublikat yaratmaydi (`[skip]`).

## 14. Autostart tekshiruvi

```bash
systemctl is-enabled vllm && systemctl status vllm --no-pager | head -5 && docker compose -f /opt/raqobat/docker-compose.yml ps
```

Barcha compose xizmatlari `restart: unless-stopped`, vLLM `enable`langan —
SSH yopilsa, MacBook o'chsa ham tizim ishlashda davom etadi.

## 15. Sog'liq tekshiruvlari

```bash
curl -s http://127.0.0.1/api/health; echo
```

```bash
curl -s http://127.0.0.1:8001/v1/models | python3 -m json.tool | head -12
```

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1/api/auth/login -d "username=admin&password=<parol>" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])") && curl -s http://127.0.0.1/api/system/status -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -30
```

Kutilgan: `llm_provider: "local"`, `llm_providers[0].state: "ready"`, `model_loaded: true`.

## 16. Funksional smoke testlar

GENERAL — ikkalasi ham `general`, `sources: []`:

```bash
TOKEN=<token>; for q in "Sun’iy intellekt nima?" "O‘zbekiston Konstitutsiyasida nechta modda bor?"; do curl -s -X POST http://127.0.0.1/api/chat -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"$q\",\"mode\":\"general\"}" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['effective_mode'],'| sources:',len(d['sources']),'|',d['answer'][:100])"; done
```

LEGAL — ikkalasi ham `legal` + citation:

```bash
TOKEN=<token>; for q in "Ustun mavqe qanday aniqlanadi?" "Raqobatga qarshi kelishuvlar qaysi moddada tartibga solingan?"; do curl -s -X POST http://127.0.0.1/api/chat -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"$q\",\"mode\":\"legal\"}" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['effective_mode'],'|',[s['display_label'] for s in d['sources']],'|',d['answer'][:100])"; done
```

Kutilgan: `13-modda` va `19-modda`.

Brauzerda: **http://<PUBLIC_IP>**

## Persistensiya

| Ma'lumot | Joy | Saqlanadi |
|---|---|---|
| PostgreSQL | `raqobat_pgdata` docker volume | ✅ |
| Yuklangan hujjatlar + NHH fayllari | `raqobat_data` docker volume (`/app/data`) | ✅ |
| BGE-M3 keshi | `raqobat_hf` docker volume | ✅ |
| GPT-OSS-20B og'irliklari | `/opt/raqobat-ai/models` (host) | ✅ |

`docker compose down` volume'larni o'chirmaydi. `down -v` — o'chiradi, ehtiyot bo'ling.

## Muammolarni bartaraf etish

```bash
docker compose logs --tail=80 backend
```

```bash
journalctl -u vllm -n 80 --no-pager
```

Backend konteyneridan host vLLM ga ulanishni tekshirish:

```bash
docker compose exec backend python -c "import urllib.request,os;print(urllib.request.urlopen(os.environ['LOCAL_LLM_BASE_URL']+'/models',timeout=10).status)"
```
