# Cloudflare deployment (dalilai.pages.dev)

The public face of the project has two parts.

| Part | Where it lives | Always online? |
|---|---|---|
| Showcase page (`/`) and the React app files (`/app/`) | Cloudflare Pages | yes |
| Backend, database, embedding model (`/api/*`) | the author's laptop, reached through a Cloudflare quick tunnel | only while `go-live.sh` is running |

The gateway in `site/_worker.js` sits in between. It is the only thing the internet can reach:

- only the endpoints listed in `ALLOWED` pass; uploads, user management, the statute base, the audit log and the organisation profile are refused;
- login is accepted for the `demo` user only (role: manager, read and ask);
- AI calls are capped at 30 per visitor per hour, request bodies at 20 KB;
- it adds a shared secret (`X-Gateway-Key`), and the backend (`gateway_guard` in `backend/app/main.py`) refuses any request that comes through the tunnel without it, so the random tunnel address alone is useless;
- when the laptop is off, the page stays up and the app answers with a clear "demo is offline" message.

## Files

| File | Purpose |
|---|---|
| `site/index.html` | showcase page, Uzbek and English |
| `site/_worker.js` | gateway (Pages advanced mode) |
| `site/_headers` | security headers |
| `build.sh` | builds the React app for `/app/` and zips everything into `dalilai-site.zip` |
| `go-live.sh` | starts database, backend and tunnel, then tells the gateway the new tunnel address |
| `.gateway.env` | the shared secret (git-ignored, never commit it) |

## One-time setup in Cloudflare

1. Workers & Pages > Create > Pages > Upload assets. Project name `dalilai`. Upload `dalilai-site.zip`.
2. Settings > Bindings > add a **KV namespace** binding named `DALIL`.
3. Settings > Variables and Secrets > add the **secret** `GATEWAY_KEY` with the value from `.gateway.env`.
4. Redeploy once so the bindings take effect.

## Every time

```bash
./deploy/cloudflare/go-live.sh          # turn the live demo on
./deploy/cloudflare/go-live.sh status
./deploy/cloudflare/go-live.sh stop
```

The tunnel address changes on every start. The script registers the new one, nothing else to do.

## After changing the frontend or the page

```bash
./deploy/cloudflare/build.sh
```

Then upload the new `dalilai-site.zip` as a new deployment.
