# Raqobat AI Assistant

Raqobat qo‘mitasi rahbariyati va xodimlari uchun sun’iy intellektga asoslangan agent-yordamchi: hujjatlarni tahlil qiladi, normativ-huquqiy hujjatlar (NHH) bazasidan huquqiy asosni **manbasi bilan** topadi, rasmiy xat va ma’lumotnoma loyihalarini tayyorlaydi va rahbar uchun boshqaruv tahlilini beradi. Interfeys tili — o‘zbek (lotin).

> **Asosiy tamoyil:** tizim huquqiy javobni shunchaki «to‘qib» bermaydi. Har bir huquqiy javob «qaysi hujjatga asoslanib berildi?» degan savolga hujjat nomi, modda raqami, rasmiy havola va foydalanilgan parcha bilan javob beradi. Dalil bilan tasdiqlanmagan matn foydalanuvchiga chiqarilmaydi.

**Stek:** Python 3.12 · FastAPI · PostgreSQL 16+ + pgvector · BAAI/bge-m3 embedding · OpenAI-mos LLM (o‘z serveringizdagi vLLM yoki Groq) · React 19 + TypeScript + Vite.

---

## Mundarija

1. [Tizim nima qiladi](#tizim-nima-qiladi)
2. [Arxitektura](#arxitektura)
3. [Tez boshlash](#tez-boshlash)
4. [Konfiguratsiya](#konfiguratsiya)
5. [Xavfsizlik](#xavfsizlik)
6. [Rollar va amaliy oqim](#rollar-va-amaliy-oqim)
7. [Loyiha tuzilmasi](#loyiha-tuzilmasi)
8. [Testlar](#testlar)
9. [Production deploy](#production-deploy)
10. [Rasmiy korpus va namuna ma’lumotlar](#rasmiy-korpus-va-namuna-malumotlar)

---

## Tizim nima qiladi

| Modul | Imkoniyat |
|---|---|
| **AI chat** | Huquqiy savolga NHH bazasidan javob: hujjat nomi, modda/band, lex.uz havolasi, foydalanilgan parcha. Umumiy ish savollariga esa manbasiz, aniq belgilangan «umumiy» rejimda javob beradi. |
| **Hujjatlar bilan ishlash** | PDF, DOCX, XLSX yuklash; qisqacha mazmun, asosiy bandlar, hujjat bo‘yicha savol-javob, ichki qarama-qarshiliklarni aniqlash. |
| **NHH bazasi** | Qonunlar, farmonlar, qarorlar, nizomlar, idoraviy hujjatlar; modda darajasida indekslanadi; administrator yuklaydi va boshqaradi. |
| **Hujjat loyihalari** | Murojaatga javob xati, hisobot, ma’lumotnoma, qisqa ma’lumot, tahliliy xulosa — tashkilot rekvizitlari bilan **DOCX** ko‘rinishida. |
| **Rahbar analitikasi** | «Bugungi kun uchun asosiy muammolarni ko‘rsat» — mavjud muammolar, kechikayotgan topshiriqlar, muhim murojaatlar, statistika, e’tibor talab qiladigan holatlar. Tizimning o‘z yozuvlaridan hisoblanadi, LLM ishlatilmaydi. |
| **Topshiriqlar** | Yaratish, tayinlash, holat va muddat nazorati, tarix. |
| **Administrator paneli** | Foydalanuvchilar va rollar, NHH bazasi, tashkilot profili (rasmiy xat rekvizitlari), diagnostika, audit jurnali. |

---

## Arxitektura

Texnik topshiriqdagi umumiy sxema:

```
Foydalanuvchi
      ↓
Web interfeys          React + TypeScript (frontend/)
      ↓
Backend API            FastAPI, JWT, RBAC (backend/app/api.py)
      ↓
AI Agent               backend/app/services/ai_agent.py
   /         \
 RAG          LLM
  ↓            ↓
NHH bazasi   AI model    PostgreSQL + pgvector      vLLM (gpt-oss-20b) yoki Groq
          ↓
      AI javob         faqat dalil bilan tasdiqlangan matn
```

### Bitta huquqiy savol qanday yo‘l bosib o‘tadi

```mermaid
flowchart TD
    Q[Savol] --> M{Rejim}
    M -- umumiy --> G[LLM: bitta javob, qidiruvsiz, manbasiz]
    M -- huquqiy --> D{Deterministik yo‘l?}
    D -- "ulush 45% bo‘lsa…", "nechta modda", "qaysi modda", rahbar analitikasi --> DA[Bazadan hisoblangan javob, LLMsiz]
    D -- yo‘q --> E[BGE-M3 embedding]
    E --> R[pgvector: semantik + leksik + huquqiy niyat reytingi]
    R --> F[Mavzu filtri, modda dedup, ruxsat filtri]
    F --> C[Cheklangan kontekst, backend bergan citation raqamlari]
    C --> L[LLM: tuzilmali javob bloklari]
    L --> V{Grounding tekshiruvi}
    V -- o‘tdi --> A[Javob + manba kartochkalari]
    V -- shakl xatosi --> L
    V -- fakt xatosi --> X[Faqat tekshirilgan asl parchalar]
```

### Komponentlar

| Qatlam | Fayl(lar) | Vazifa |
|---|---|---|
| Web interfeys | `frontend/src/App.tsx`, `api.ts` | Sahifalar, rollarga mos menyu, xavfsiz Markdown render, manba kartochkalari |
| Backend API | `backend/app/api.py`, `security.py`, `main.py` | REST endpointlar, JWT, rollar tekshiruvi, kirish cheklovi, audit jurnali |
| AI Agent | `services/ai_agent.py` | Savolni yo‘naltiradi: deterministik javob → RAG → LLM → grounding → fallback |
| RAG | `services/rag.py` | Modda darajasida chunking, BGE-M3 embedding, pgvector gibrid qidiruv, lotin/kirill moslashtirish |
| Grounding | `services/grounding.py` | Modda, raqam, sana, iqtibos, hujjat nomi va citation dalil bilan solishtiriladi |
| Deterministik javoblar | `services/legal_facts.py`, `analytics.py` | Sonli mezonlar, moddalar ro‘yxati, modda soni, rahbar dashboard |
| LLM qatlami | `services/llm.py` | Bitta OpenAI-mos klient: vLLM yoki Groq, model puli, 429 failover, sxema degradatsiyasi |
| Hujjatlar | `services/documents.py`, `document_qa.py`, `document_analysis.py` | Parsing, turini tekshirish, hujjat bo‘yicha savol, qarama-qarshilik tahlili |
| Loyihalar | `services/drafting.py`, `export.py` | Javob xati tuzilmasi, validatsiya, rasmiy DOCX (rekvizitlar, QORALAMA belgisi) |
| Ma’lumotlar | `models.py`, `alembic/` | Foydalanuvchilar, hujjatlar, NHH, chunklar (vektorlar), tarix, topshiriqlar, audit |

### Nima uchun javoblar ishonchli

- **Citation raqamlarini model emas, backend beradi.** Manbalar oldindan deduplikatsiya qilinib raqamlanadi; model faqat shu raqamlarni ishlatishi mumkin. Mavjud bo‘lmagan raqam javobni yiqitadi.
- **Grounding tekshiruvi.** Javobdagi har bir modda raqami, son (shu jumladan «qirq foiz», «30 000» kabi yozuvlar), sana, iqtibos va hujjat nomi manba matnida borligi tekshiriladi. Shakl xatosi bo‘lsa bir marta tuzatish so‘raladi, fakt xatosi bo‘lsa darhol tekshirilgan asl parchalarga o‘tiladi.
- **Rasmiy ro‘yxatlar to‘liq beriladi.** Manbada raqamlangan ro‘yxat bo‘lsa (ustun mavqe mezonlari, jarima stavkalari), model javobi har bir bandni qamrab olgani tekshiriladi; tushib qolgan band manbadan so‘zma-so‘z qo‘shiladi.
- **Modda sarlavhalari qat’iy aniqlanadi.** Qonunning o‘zgartirish kirituvchi moddalari ichida iqtibos keltirilgan boshqa kodeks moddalari shu qonunning moddasi sifatida indekslanmaydi.
- **Bazada bo‘lmagan hujjat haqida javob berilmaydi.** «Konstitutsiyaning 1-moddasi» kabi savolga boshqa qonunning 1-moddasi ko‘rsatilmaydi — «yetarli huquqiy asos topilmadi» qaytadi.
- **Rejim — shartnoma.** «Umumiy savol» rejimida savol hech qachon huquqiy RAG ga o‘tkazilmaydi, «Huquqiy qidiruv» rejimida faqat tasdiqlangan manba asosida javob beriladi.

---

## Tez boshlash

### Talablar

- Python 3.11 yoki 3.12, Node.js 20+
- PostgreSQL 16+ va `pgvector` kengaytmasi (Docker yoki lokal)
- LLM: Groq API kaliti ([console.groq.com](https://console.groq.com), bepul reja yetarli) **yoki** o‘z serveringizdagi vLLM
- Birinchi ishga tushirishda BGE-M3 modelini (~2 GB) yuklab olish uchun internet

### 1. Repozitoriy va sozlamalar

```bash
git clone https://github.com/VohidovTohirjon/Antimonopoliya.git
cd Antimonopoliya
cp .env.example .env
```

`.env` da kamida quyidagilarni to‘ldiring:

```env
SECRET_KEY=<openssl rand -hex 32 natijasi>
LLM_PROVIDER=groq
GROQ_API_KEY=<groq kalitingiz>
```

### 2. PostgreSQL + pgvector

**A varianti — Docker:**

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d db
```

**B varianti — lokal PostgreSQL (macOS, Homebrew):**

```bash
brew install postgresql@18 pgvector && brew services start postgresql@18
psql postgres -c "CREATE USER raqobat WITH PASSWORD 'raqobat' CREATEDB;" -c "CREATE DATABASE raqobat OWNER raqobat;"
```

`.env` dagi `DATABASE_URL` standart holda `postgresql+psycopg://raqobat:raqobat@localhost:5432/raqobat` ga ishora qiladi.

### 3. Backend

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
alembic upgrade head
python -m app.cli --username admin --password 'mustahkam-parol' --full-name 'Tizim administratori'
python scripts/import_nhh.py --admin-username admin      # rasmiy qonunni bazaga yuklaydi
uvicorn app.main:app --reload --port 8000
```

API hujjatlari: `http://localhost:8000/api/docs`. Server ochilishi embedding modelini kutmaydi: login darhol ishlaydi, model fon rejimida tayyorlanadi (`/api/health` → `embedding: ready`).

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Interfeys: `http://localhost:5173`.

### Bitta buyruq bilan (macOS)

```bash
./local-demo.sh          # bazani, backendni va frontendni ko‘taradi
./local-demo.sh status   # holat
./local-demo.sh stop     # to‘xtatish
```

Skript lokal PostgreSQL bo‘lsa uni, bo‘lmasa Docker'ni ishlatadi. Loglar `tmp/local-demo/` ichida.

---

## Konfiguratsiya

Barcha sozlamalar `.env` orqali beriladi (`backend/app/config.py`). Eng muhimlari:

| O‘zgaruvchi | Vazifasi | Standart |
|---|---|---|
| `SECRET_KEY` | JWT imzolash kaliti, kamida 32 belgi | — (majburiy) |
| `DATABASE_URL` | PostgreSQL ulanish satri | `postgresql+psycopg://raqobat:raqobat@localhost:5432/raqobat` |
| `LLM_PROVIDER` | `local` (o‘z vLLM serveringiz) yoki `groq` | `groq` |
| `LLM_FALLBACK_ENABLED` | Ikkinchi provayderga avtomatik o‘tish | `false` |
| `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL` | vLLM endpointi (`/v1` bilan) va model | `openai/gpt-oss-20b` |
| `GROQ_API_KEY` / `GROQ_MODELS` | Groq kaliti va priority ro‘yxati (429 bo‘lsa keyingisiga o‘tadi) | `openai/gpt-oss-120b,…` |
| `GROQ_REASONING_EFFORT` | gpt-oss fikrlash chuqurligi | `low` |
| `CONTEXT_MAX_CHARS` | LLM ga beriladigan manba matni chegarasi | `18000` (Groq bepul reja uchun `9000` tavsiya) |
| `LLM_MAX_TOKENS_GENERAL/LEGAL/DOCUMENT/DRAFTING` | So‘rov turiga qarab completion byudjeti | `512/1024/1024/3200` (umumiy rejim uchun `1800` tavsiya) |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | Embedding modeli va o‘lchami | `BAAI/bge-m3` / `1024` |
| `EMBEDDING_HALF_PRECISION` | MPS/CUDA da float16 (xotira ikki baravar kam) | `true` |
| `RETRIEVAL_MIN_SCORE` | Yetarli dalil bo‘lmagan qidiruvni rad etish chegarasi | `0.48` |
| `ALLOW_EXTERNAL_CONFIDENTIAL_AI` | Maxfiy/idoraviy matnni tashqi LLM ga yuborishga ruxsat | `false` |
| `DATA_DIR` | Yuklangan fayllar katalogi | `data` |
| `CORS_ORIGINS` | Ruxsat etilgan frontend manzillari | localhost:5173 |
| `VITE_API_URL` | Frontend chaqiradigan backend manzili | `http://localhost:8000` (Nginx orqali bo‘sh) |
| `LOCAL_SEED_PASSWORD` | **Faqat development.** Lokal seed yaratadigan hisoblar paroli | `12345678` |

**Groq bepul rejasi haqida.** Har bir model uchun daqiqasiga ~8000 token limiti bor. `CONTEXT_MAX_CHARS=9000` va `GROQ_REASONING_EFFORT=low` bilan bitta huquqiy so‘rov 0,7–3 ming token sarflaydi. Limit tugasa backend avtomatik keyingi modelga o‘tadi, barcha modellar band bo‘lsa 30 soniyagacha kutib qayta urinadi. Tasdiqlangan javoblar 24 soat keshlanadi (`data/answer_cache.json`), NHH qayta indekslanganda kesh tozalanadi.

---

## Xavfsizlik

Texnik topshiriqning 7-bo‘limi talablari va ularning bajarilishi:

| Talab | Amalga oshirilishi |
|---|---|
| Login va parol orqali autentifikatsiya | bcrypt xesh, JWT (HS256), `token_version` orqali sessiyani bekor qilish (parol/rol o‘zgarganda, logout) |
| Rollarga asoslangan kirish nazorati | Har bir endpoint backend darajasida `require_roles` bilan tekshiriladi; frontend faqat ko‘rinishni cheklaydi |
| Harakatlarni jurnalga yozish | Har bir API so‘rovi `audit_logs` jadvaliga foydalanuvchi, metod, yo‘l va holat kodi bilan yoziladi |
| Hujjatlarga kirishni cheklash | «Maxfiy hujjat» faqat egasi va administratorga ko‘rinadi; RAG qidiruvi ham shu filtrni SQL darajasida qo‘llaydi |
| API kalitlari frontendda saqlanmaydi | Barcha kalitlar faqat serverdagi `.env` da; frontend faqat JWT bilan ishlaydi |
| Maxfiy hujjatlar tashqi AI ga yuborilmaydi | Maxfiy va «Idoraviy (ichki)» hujjat matni tashqi provayderga ketmaydi, lokal ekstraktiv oqimda ishlanadi |
| Parol tanlashdan himoya | Bitta hisob uchun 8 ta xato urinishdan so‘ng 5 daqiqa blok, IP uchun 30 ta; mavjud bo‘lmagan login uchun ham parol tekshiruvi bajariladi (vaqt orqali hisobni aniqlab bo‘lmaydi) |
| Fayl xavfsizligi | Kengaytmaga ishonilmaydi: PDF sarlavhasi va Office ZIP tuzilmasi tekshiriladi; hajm chegarasi; matnsiz (skanerlangan) fayl rad etiladi |

**Repozitoriy gigiyenasi.** `.env`, `.env.*` (namunalardan tashqari), `data/`, `tmp/`, `.claude/` va shaxsiy fayllar `.gitignore` da. Haqiqiy kalitlar hech qachon commit qilinmaydi — faqat `.env.example` va `.env.production.example` (placeholder qiymatlar bilan). Kalit tasodifan oshkor bo‘lsa, uni provayder panelida darhol bekor qiling.

`SECRET_KEY` yaratish:

```bash
openssl rand -hex 32
```

Productionda `LOCAL_SEED_PASSWORD` ni o‘rnatmang va seed hisoblaridan foydalanmang; foydalanuvchilarni Administrator paneli orqali yarating.

---

## Rollar va amaliy oqim

| Rol | Huquqlari |
|---|---|
| **Administrator** | Barcha funksiyalar, foydalanuvchilar va rollar, NHH bazasi, tashkilot profili, audit |
| **Rahbar** | Boshqaruv paneli, AI chat, hujjat tahlili, hisobot va ma’lumotnomalar, topshiriq yaratish va nazorat |
| **Xodim** | AI chat, hujjat yuklash va tahlil, hujjat loyihalari, o‘z topshiriqlari va o‘z AI tarixi |

Xodim yoki rahbar bitta oynada:

1. savol beradi — javob manbasi (hujjat, modda, havola, parcha) bilan keladi;
2. PDF/DOCX/XLSX yuklaydi va qisqacha mazmun, asosiy bandlar, savol-javob yoki qarama-qarshilik tahlilini oladi;
3. «Ushbu murojaatga javob xatini tayyorla» deb loyiha tayyorlaydi — tizim murojaatni tahlil qiladi, tegishli NHHni topadi va huquqiy asoslarni ko‘rsatadi;
4. natijani DOCX sifatida yuklab oladi: **Qoralama DOCX** (rekvizitlar to‘ldirilmagan bo‘lsa, `QORALAMA` belgisi bilan), **Rasmiy DOCX** (tashkilot profili va rekvizitlar to‘liq bo‘lganda) va **Ichki dalillar** (citation va NHH mappingi alohida xizmat hujjatida).

Rahbar qo‘shimcha ravishda «Bugungi kun uchun asosiy muammolarni ko‘rsat» deb so‘rasa, chatning o‘zida besh bo‘limli boshqaruv tahlilini oladi; xodim faqat o‘ziga biriktirilgan topshiriqlar bo‘yicha ko‘radi.

---

## Loyiha tuzilmasi

```
backend/
  app/
    api.py               REST endpointlar
    security.py          JWT, parol xeshi, kirish cheklovi
    main.py              FastAPI ilova, CORS, audit middleware
    config.py            .env sozlamalari
    models.py, schemas.py
    services/
      ai_agent.py        AI Agent: yo‘naltirish, grounding, fallback
      rag.py             chunking, embedding, pgvector gibrid qidiruv
      grounding.py       dalil tekshiruvi, ekstraktiv fallback, transliteratsiya
      legal_facts.py     deterministik huquqiy javoblar
      legal_intent.py    huquqiy tushunchalar (ustun mavqe, kelishuv, savdolar…)
      analytics.py       rahbar analitikasi
      llm.py             OpenAI-mos provayder qatlami
      documents.py, document_qa.py, document_analysis.py
      drafting.py, export.py
      answer_cache.py
  alembic/               migratsiyalar
  scripts/               import_nhh.py, seed_*.py, check_*.py
  tests/                 172 ta test (SQLite + hash embedding bilan izolyatsiyalangan)
frontend/                React + TypeScript + Vite
legal-corpus/            rasmiy NHH fayllari va manifest.json
sample-data/             namuna hujjatlar
deploy/                  bitta VM uchun Nginx + Docker deploy
docker-compose.yml       production compose
docker-compose.local.yml lokal ishlab chiqish uchun qo‘shimcha
local-demo.sh            bitta buyruqli lokal ishga tushirish
```

---

## Testlar

Testlar tashqi LLM chaqiruvini deterministik adapter bilan almashtiradi va SQLite + hash embeddingdan faqat izolyatsiyalangan muhit sifatida foydalanadi.

```bash
source .venv/bin/activate
cd backend && pip install -r requirements-dev.txt && pytest -q
```

```bash
cd frontend && npm test && npm run build
```

Ishlayotgan serverda rol chegaralarini va huquqiy qidiruv marshrutlarini tekshirish:

```bash
cd backend
RBAC_CHECK_XODIM_PASSWORD=... RBAC_CHECK_RAHBAR_PASSWORD=... python scripts/check_rbac_api.py --base-url http://127.0.0.1:8000
python scripts/check_retrieval.py --username admin
```

---

## Production deploy

Bitta VM, Nginx yagona ochiq port, PostgreSQL va backend ichki tarmoqda, vLLM host'da. To‘liq yo‘riqnoma: **[deploy/README.md](deploy/README.md)**.

```bash
cp .env.production.example .env   # qiymatlarni to‘ldiring
./deploy/deploy.sh
```

`APP_ENV=production` bo‘lganda backend ishga tushishdan oldin konfiguratsiyani tekshiradi: `LLM_PROVIDER` aniq ko‘rsatilmagan yoki lokal provayder uchun `LOCAL_LLM_BASE_URL`/`LOCAL_LLM_MODEL` bo‘sh bo‘lsa, **ishga tushmaydi**. Bu tasodifan tashqi provayderga o‘tib ketishning oldini oladi.

---

## Rasmiy korpus va namuna ma’lumotlar

`legal-corpus/` katalogida rasmiy NHH fayllari va `manifest.json` (nom, turi, rasmiy raqam, lex.uz havolasi) saqlanadi. Import admin UI bilan bir xil parsing va indekslash yo‘lidan o‘tadi va idempotent:

```bash
cd backend && python scripts/import_nhh.py --admin-username admin        # import
cd backend && python scripts/import_nhh.py --status                      # holat
```

Lex.uz sahifasi skanerlangan PDF bersa, saqlangan HTML sahifani matnli DOCXga aylantirish mumkin (tarmoqqa chiqmaydi):

```bash
python backend/scripts/lexuz_html_to_docx.py lexuz.html qonun.docx --source-url https://lex.uz/docs/6518381
```

Operatsion namuna ma’lumotlari (hujjatlar, topshiriqlar, uchta rol bo‘yicha hisoblar) **faqat development** uchun:

```bash
cd backend && python scripts/seed_operational_data.py --confirm-development --admin-username admin
```

Seed faqat o‘zi yaratadigan yangi hisoblarga parol beradi (`--seed-password`, `LOCAL_SEED_PASSWORD` yoki standart `12345678`) va mavjud hisob paroliga hech qachon tegmaydi. Tozalash: `--reset --reset-only`.
