# 3 daqiqalik demo videosi uchun skript

Bu skript tizimning har bir kuchli tomonini 180 soniyaga sig‘dirish uchun tuzilgan.
Har bir bo‘limda: ekranda nima ko‘rinadi, siz nima bosasiz/yozasiz va nima deysiz.

---

## 1. Yozishdan oldin (10 daqiqa tayyorgarlik)

1. **Tizimni ishga tushiring va tayyor bo‘lishini kuting:**

   ```bash
   cd /Users/tokhirjon/Documents/Antimonopoliya && ./local-demo.sh
   ```

   `curl -s http://127.0.0.1:8000/api/health` javobida `"embedding":"ready"` chiqsin.

2. **Tashkilot profilini to‘ldiring** (Administrator paneli → Tashkilot profili).
   Rasmiy DOCX tugmasi ochilishi uchun 8 ta maydon kerak: tashkilot nomi, pochta manzili,
   telefon, e-pochta, veb-sayt, chiqish raqami prefiksi, imzolovchi F.I.Sh., imzolovchi
   lavozimi. To‘ldirmasangiz videoda faqat «Qoralama DOCX» ko‘rinadi.

3. **Savollarni bir marta oldindan bering.** Tasdiqlangan javoblar 24 soatga keshlanadi,
   shuning uchun videoda ular bir zumda chiqadi va Groq limitiga urilmaysiz. Quyidagi
   uchta savolni oldindan yuboring:
   - Raqobat to‘g‘risidagi qonunda ustun mavqeni aniqlash mezonlarini top
   - Bozorda 45% ulushga ega korxona ustun mavqega egami? (Umumiy savol rejimida)
   - Bugungi kun uchun asosiy muammolarni ko‘rsat

4. **Brauzerni tayyorlang:** faqat bitta tab, masshtab 100%, yashirin rejim emas,
   bildirishnomalarni o‘chiring. Ekran o‘lchami 1440×900 yoki kattaroq.

5. **Kirib turing** (`admin`), lekin yozishni **login sahifasidan** boshlang: chiqib
   qayta kiring, birinchi kadr login ekrani bo‘lsin.

6. **Muhim:** ketma-ket ikkita yangi (keshda yo‘q) savol bermang — orasida kamida
   10 soniya bo‘lsin. Skriptdagi tartib buni hisobga olgan.

---

## 2. Skript (180 soniya)

### 0:00–0:15 — Ochilish

**Ekranda:** login sahifasi → `admin` bilan kirasiz → Administrator paneli ochiladi.
Chap menyuni bir marta ko‘rsating.

> «Bu — Raqobat qo‘mitasi xodimlari uchun ichki sun’iy intellekt yordamchisi.
> U hujjatlarni tahlil qiladi, qonundan huquqiy asos topadi va rasmiy xat loyihasini
> tayyorlaydi. Bitta tamoyil ustiga qurilgan: har bir huquqiy javob o‘z manbasini
> ko‘rsatadi.»

---

### 0:15–0:45 — Huquqiy savol va manba

**Ekranda:** «AI yordamchi» → rejim «Huquqiy qidiruv» → «Ustun mavqe mezonlari»
namuna tugmasini bosing → «Savol yuborish».

**Yozasiz:** `Raqobat to‘g‘risidagi qonunda ustun mavqeni aniqlash mezonlarini top`

Javob chiqqach **pastga suring** va manba kartochkasini ko‘rsating, so‘ng
«To‘liq matnni ko‘rsatish» ni bosing.

> «Savol beramiz. Javob bir necha soniyada keladi va qonunning 13-moddasidagi
> to‘rtta mezonni to‘liq beradi — birortasi tushib qolmaydi.
> Pastda esa eng muhimi: qaysi hujjat, qaysi modda, rasmiy lex.uz havolasi va
> qonundan olingan asl matn parchasi. Ya’ni javobni tekshirish mumkin.»

---

### 0:45–1:05 — Ishonch nazorati (eng kuchli nuqta)

**Ekranda:** rejimni «Umumiy savol» ga o‘tkazing va savolni yuboring.

**Yozasiz:** `Bozorda 45% ulushga ega korxona ustun mavqega egami?`

Sariq ogohlantirishni va javob matnini ko‘rsating. Keyin rejimni «Huquqiy qidiruv» ga
qaytarib **xuddi shu savolni** qayta yuboring.

> «Endi ataylab xato qilamiz: xuddi shu huquqiy savolni umumiy rejimda beramiz.
> Tizim javob to‘qib bermaydi. U ochiq aytadi: bu rejimda normativ baza ishlatilmaydi,
> shuning uchun modda va foiz ko‘rsatilmaydi — va huquqiy rejimga o‘tishni taklif qiladi.
> Huquqiy rejimda esa aniq javob: 45 foiz qonundagi 40 foiz mezonni qanoatlantiradi,
> manbasi 13-modda.»

---

### 1:05–1:35 — Hujjat tahlili

**Ekranda:** «Hujjatlar va tahlil» → `murojaat_raqobat.docx` ni tanlang →
«Qisqacha mazmun». Javob chiqqach `qarama_qarshilik.docx` ni tanlab
«Qarama-qarshiliklar» ni bosing (bu deyarli bir zumda ishlaydi).

> «Hujjatlar bilan ishlash. Murojaatni tanlab, qisqacha mazmunini olamiz —
> har bir fikr hujjatning o‘z parchasiga bog‘langan.
> Ikkinchi hujjatda esa tizim ichki ziddiyatni topadi: bayonnomada bir joyda
> 15-avgust, boshqa joyda 20-avgust muddat ko‘rsatilgan. Ikkala jumla ham asl
> matndan so‘zma-so‘z olingan.»

---

### 1:35–2:10 — Javob xati va DOCX

**Ekranda:** «Hujjat loyihalari» → Natija turi: «Javob xati» → Asosiy hujjat:
`murojaat_raqobat.docx` → Qabul qiluvchi va chiqish raqamini to‘ldiring →
Topshiriq maydoniga yozing → «Loyiha tayyorlash».

**Yozasiz:** `Ushbu murojaatga javob xatini tayyorla`

Javob chiqqach «Huquqiy asos» qismini ko‘rsating, so‘ng **«Rasmiy DOCX»** tugmasini
bosing va yuklab olingan faylni oching (Word oynasini 3–4 soniya ko‘rsating).

> «Endi eng amaliy qismi — javob xati. Tizim murojaatni o‘qiydi, qonundan tegishli
> normani topadi va rasmiy xat loyihasini tuzadi.
> E’tibor bering: xulosa qat’iy emas — «tasdiqlangan taqdirda» deb yozilgan, chunki
> qo‘mita hali tekshiruv o‘tkazmagan.
> Natija haqiqiy Word hujjati: tashkilot rekvizitlari, chiqish raqami, sana va imzo
> joyi bilan. Rekvizitlar to‘liq bo‘lmasa, tizim rasmiy eksportni ochmaydi —
> faqat qoralama beradi.»

---

### 2:10–2:30 — Rahbar uchun tahlil

**Ekranda:** «AI yordamchi» → savolni yuboring (javob bir zumda chiqadi).

**Yozasiz:** `Bugungi kun uchun asosiy muammolarni ko‘rsat`

> «Rahbar uchun alohida so‘rov. Bu javob sun’iy intellekt tomonidan yozilmaydi —
> u tizimning o‘z yozuvlaridan hisoblanadi: ochiq muammolar, kechikkan topshiriqlar,
> muhim murojaatlar, statistika va e’tibor talab qiladigan holatlar.
> Shuning uchun bu yerda xato bo‘lishi mumkin emas va javob bir zumda keladi.»

---

### 2:30–2:50 — Administrator paneli

**Ekranda:** «Administrator paneli» → «NHH bazasi» tab (qonun ro‘yxati) →
«Rollar» tab → «Audit jurnali» tab. Har birida 4–5 soniya turing.

> «Administrator tomonida: normativ-huquqiy hujjatlar bazasi — qonun yuklanadi va
> modda darajasida indekslanadi. Uchta rol: administrator, rahbar, xodim —
> ruxsatlar server tomonida tekshiriladi. Va audit jurnali: har bir amal yozib boriladi.
> Maxfiy hujjat matni esa hech qachon tashqi AI xizmatiga yuborilmaydi.»

---

### 2:50–3:00 — Yakun

**Ekranda:** «AI yordamchi» sahifasiga qayting, javob va manbalar ko‘rinib tursin.

> «Xodim bitta oynada savol beradi, hujjat yuklaydi, tahlil qiladi, huquqiy asos oladi
> va tayyor Word hujjatini yuklab oladi. Har bir huquqiy javob esa doim bitta savolga
> javob bera oladi: qaysi hujjatga asoslanib berildi.»

---

## 3. Yozish paytida e’tibor bering

- **Sekundomer:** har bo‘lim oxirida vaqtni tekshiring. Eng ko‘p vaqt ketadigan joy —
  javob xati (35 soniya). Kerak bo‘lsa administrator panelini 10 soniyaga qisqartiring.
- **Kutish paytida gapiring.** Javob 2–4 soniyada keladi; shu paytda keyingi jumlani
  ayting, jimlik qolmasin.
- **Sichqonchani sekin harakatlantiring** va bosishdan oldin bir lahza to‘xtang —
  montajda kesish oson bo‘ladi.
- **Agar javob kutilganidan boshqacha chiqsa:** dublni qaytadan oling. Savolni
  o‘zgartirmang — skriptdagi savollar tekshirilgan.
- **Agar «AI xizmati band» chiqsa:** 30 soniya kuting va qayta yuboring. Bu Groq bepul
  rejasining daqiqalik limiti, tizim avtomatik keyingi modelga o‘tadi.
- **Ovoz:** tashqi mikrofon bo‘lmasa, tinch xonada yozing va videoni tugatgach ovozni
  alohida yozib ustiga qo‘ying — sifat sezilarli yaxshi bo‘ladi.

---

## 4. Agar 5 daqiqalik versiya kerak bo‘lsa

Quyidagilarni qo‘shing:
- **Kirill savol:** `Устун мавқени аниқлаш мезонлари қандай?` — lotin savol kirill
  qonun matni bilan ishlaydi, javob esa lotin yozuvida qaytadi.
- **Bazada yo‘q hujjat:** `Konstitutsiyaning 1-moddasi nima deydi?` — tizim boshqa
  qonunning 1-moddasini ko‘rsatmaydi, «yetarli huquqiy asos topilmadi» deydi.
- **Jarima savoli:** `Raqobat qonunchiligini buzganlik uchun jarima miqdori qancha?` —
  42-moddadagi barcha stavkalar to‘liq chiqadi.
- **Ichki dalillar hujjati:** javob xati yonidagi «Ichki dalillar» tugmasi — citation,
  modda va rasmiy havola mappingi alohida xizmat hujjatida.
