// Public gateway for the Raqobat AI Assistant demo (Cloudflare Pages, advanced mode).
//
//   /            static showcase page          (always online)
//   /app/*       the real React application    (static files)
//   /api/*       proxied to the author's laptop through a Cloudflare tunnel,
//                but only the endpoints in ALLOWED, and only as the "demo" user
//   /_status     is the live backend reachable right now?
//   /_register   go-live.sh tells the gateway the current tunnel address
//
// Everything that writes or administers (users, statute base, uploads, audit log,
// organisation profile) is refused here, so the public demo is read-and-ask only.

const DEMO_USER = "demo";

const ALLOWED = [
  ["POST", /^\/api\/auth\/login$/],
  ["GET", /^\/api\/auth\/me$/],
  ["POST", /^\/api\/auth\/logout$/],
  ["GET", /^\/api\/ai\/readiness$/],
  ["GET", /^\/api\/health$/],
  ["GET", /^\/api\/documents$/],
  ["GET", /^\/api\/documents\/[\w-]+$/],
  ["GET", /^\/api\/documents\/[\w-]+\/download$/],
  ["GET", /^\/api\/nhh\/[\w-]+\/download$/],
  ["POST", /^\/api\/documents\/[\w-]+\/analyze$/],
  ["POST", /^\/api\/chat$/],
  ["POST", /^\/api\/drafts$/],
  ["GET", /^\/api\/history$/],
  ["GET", /^\/api\/history\/[\w-]+\/(?:export|evidence-export)$/],
  ["GET", /^\/api\/tasks$/],
  ["GET", /^\/api\/tasks\/[\w-]+\/events$/],
  ["GET", /^\/api\/users\/assignees$/],
  ["GET", /^\/api\/dashboard$/],
  ["GET", /^\/api\/nhh$/],
];

// Calls that spend the model quota: capped per visitor.
const AI_PATHS = [/^\/api\/chat$/, /^\/api\/drafts$/, /^\/api\/documents\/[\w-]+\/analyze$/];
const AI_LIMIT_PER_HOUR = 30;
const LOGIN_LIMIT_PER_HOUR = 20;
const MAX_BODY_BYTES = 20_000;
const SAMPLE = 5;

const json = (status, detail, extra = {}) =>
  new Response(JSON.stringify({ detail }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra },
  });

const OFFLINE = "Jonli demo hozir o‘chiq: muallifning kompyuteri yoqilganda ishlaydi. "
  + "Bosh sahifadagi skrinshotlar va tavsifni ko‘rishingiz mumkin.";

async function backendOrigin(env) {
  return env.DALIL ? await env.DALIL.get("backend") : null;
}

async function overLimit(env, bucket, ip, max) {
  if (!env.DALIL) return false;
  const slot = Math.floor(Date.now() / 3_600_000);
  const key = `rl:${bucket}:${ip}:${slot}`;
  try {
    const used = parseInt((await env.DALIL.get(key)) || "0", 10);
    if (used >= max) return true;
    // Sampled: write +SAMPLE once per ~SAMPLE calls. Unbiased on average, and it keeps
    // the number of KV writes (1000/day on the free plan) at a fifth.
    if (Math.random() < 1 / SAMPLE) await env.DALIL.put(key, String(used + SAMPLE), { expirationTtl: 3700 });
  } catch {
    // The counter must never take the demo down.
  }
  return false;
}

async function ping(origin, env) {
  try {
    const response = await fetch(`${origin}/api/health`, {
      headers: { "x-gateway-key": env.GATEWAY_KEY || "" },
      signal: AbortSignal.timeout(4000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

// Answered from memory for 30 s: open tabs poll every 20 s and must not each wake the laptop.
// (A KV write per refresh would exceed the free plan's 1000 writes a day.)
let statusMemo = { at: 0, online: false, origin: "" };

async function handleStatus(env) {
  const origin = await backendOrigin(env);
  if (origin !== statusMemo.origin || Date.now() - statusMemo.at > 30_000) {
    statusMemo = { at: Date.now(), online: origin ? await ping(origin, env) : false, origin: origin || "" };
  }
  return new Response(JSON.stringify({ online: statusMemo.online }), {
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

async function handleRegister(request, env) {
  if (request.method !== "PUT") return json(405, "PUT kerak");
  const given = request.headers.get("x-gateway-key") || "";
  if (!env.GATEWAY_KEY || given !== env.GATEWAY_KEY) return json(403, "Ruxsat yo‘q");
  let url = "";
  try {
    url = String((await request.json()).url || "").replace(/\/+$/, "");
  } catch {
    return json(400, "JSON kerak");
  }
  if (!/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(url)) return json(400, "Noto‘g‘ri tunnel manzili");
  await env.DALIL.put("backend", url);
  return json(200, "ok");
}

async function handleApi(request, env, url) {
  const path = url.pathname;
  const rule = ALLOWED.find(([method, pattern]) => method === request.method && pattern.test(path));
  if (!rule) return json(403, "Namoyish rejimida bu amal o‘chirilgan.");

  const origin = await backendOrigin(env);
  if (!origin) return json(503, OFFLINE);

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  let body;
  if (request.method === "POST") {
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) return json(413, "So‘rov juda katta.");
  }

  if (path === "/api/auth/login") {
    const form = new URLSearchParams(new TextDecoder().decode(body));
    if ((form.get("username") || "").trim().toLowerCase() !== DEMO_USER) {
      return json(403, "Namoyish rejimida faqat demo hisob bilan kirish mumkin.");
    }
    if (await overLimit(env, "login", ip, LOGIN_LIMIT_PER_HOUR)) {
      return json(429, "Urinishlar ko‘p. Bir soatdan keyin qayta urinib ko‘ring.");
    }
  }
  if (AI_PATHS.some((pattern) => pattern.test(path)) && (await overLimit(env, "ai", ip, AI_LIMIT_PER_HOUR))) {
    return json(429, `Namoyish rejimida soatiga ${AI_LIMIT_PER_HOUR} ta savol mumkin. Keyinroq urinib ko‘ring.`);
  }

  const headers = new Headers({ "x-gateway-key": env.GATEWAY_KEY || "" });
  for (const name of ["authorization", "content-type", "accept"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const upstream = await fetch(`${origin}${path}${url.search}`, {
      method: request.method,
      headers,
      body,
      signal: AbortSignal.timeout(110_000),
    });
    const out = new Headers(upstream.headers);
    out.set("cache-control", "no-store");
    out.delete("set-cookie");
    return new Response(upstream.body, { status: upstream.status, headers: out });
  } catch {
    return json(503, OFFLINE);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) return handleApi(request, env, url);
    if (url.pathname === "/_status") return handleStatus(env);
    if (url.pathname === "/_register") return handleRegister(request, env);
    return env.ASSETS.fetch(request);
  },
};
