# Deploying without Docker

Knowledge Studio runs as three native processes plus managed services:

| Process | Start command | Notes |
|---|---|---|
| **web** | `npm ci && npm run build && npm run start -- --port 3000` (in `frontend/`) | stateless; set `API_INTERNAL_URL` |
| **api** | `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips 127.0.0.1` (in `backend/`) | stateless; scale with `--workers` or more hosts |
| **worker** | `python -m workers.main --concurrency 4` (in `backend/`) | run 1+ instances; `--types ingest.file,graph.extract` to specialise |

Managed services: **MongoDB Atlas** (required), **Gemini API**, **Paddle**, an **SMTP** provider, and optionally **Sentry**. No object store is needed: uploads are held on the server's disk or in MongoDB (Admin > Settings), and by default only until they are indexed. S3-compatible storage remains an optional third location.

## Recommended topology

Put one reverse proxy in front and route by path, so the browser sees a single origin and large uploads never pass through Node:

```
https://app.example.com
  ├─ /api/*     ──▶ uvicorn  127.0.0.1:8000   (FastAPI)
  ├─ /health/*  ──▶ uvicorn  127.0.0.1:8000
  └─ /*         ──▶ next     127.0.0.1:3000   (Next.js)
worker(s) ── no inbound traffic; connect out to Atlas, Gemini
```

The Next.js `/api/*` rewrite still works if the proxy sends everything to Next. It's convenient for development and small deployments, but uploads are then buffered by Node up to `UPLOAD_PROXY_MAX_BODY`.

### Caddy (Windows, macOS or Linux; automatic HTTPS)

```caddyfile
app.example.com {
  encode zstd gzip
  request_body /api/v1/files/upload {
    max_size 210MB
  }
  @api path /api/* /health/*
  handle @api {
    reverse_proxy 127.0.0.1:8000 {
      flush_interval -1          # stream SSE answers and research progress immediately
      transport http { read_timeout 15m }
    }
  }
  handle {
    reverse_proxy 127.0.0.1:3000
  }
}
```

### nginx

```nginx
server {
  listen 443 ssl http2;
  server_name app.example.com;
  client_max_body_size 210m;

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;              # SSE
    proxy_request_buffering off;      # stream uploads
    proxy_read_timeout 900s;
  }
  location /health/ { proxy_pass http://127.0.0.1:8000; }
  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

Uvicorn's `--proxy-headers --forwarded-allow-ips <proxy ip>` makes the API see real client IPs, which rate limiting and the session list depend on. Only trust your own proxy's address.

## Windows Server

1. Install Python 3.13, Node.js 22 LTS and Caddy (or IIS with URL Rewrite and ARR).
2. Clone the repository and run `./scripts/setup.ps1 -Production`. This installs dependencies and builds the frontend.
3. Create `.env` with production values (see the checklist below) and `frontend/.env.local` with `API_INTERNAL_URL=http://127.0.0.1:8000`.
4. Run `./scripts/setup-db.ps1` once per release.
5. Register the three processes as Windows services, for example with [NSSM](https://nssm.cc/), which sends Ctrl+C on stop so the worker shuts down gracefully:

```powershell
$root = "C:\apps\knowledge-studio"
nssm install ks-api    "$root\backend\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips 127.0.0.1"
nssm set     ks-api    AppDirectory "$root\backend"
nssm install ks-worker "$root\backend\.venv\Scripts\python.exe" "-m workers.main --concurrency 4"
nssm set     ks-worker AppDirectory "$root\backend"
nssm set     ks-worker AppStopMethodConsole 60000      # allow a minute to reach a checkpoint
nssm install ks-web    "C:\Program Files\nodejs\npm.cmd" "run start -- --port 3000"
nssm set     ks-web    AppDirectory "$root\frontend"
nssm start ks-api; nssm start ks-worker; nssm start ks-web
```

## Linux (systemd)

```ini
# /etc/systemd/system/ks-api.service
[Unit]
Description=Knowledge Studio API
After=network-online.target
[Service]
WorkingDirectory=/opt/knowledge-studio/backend
EnvironmentFile=/opt/knowledge-studio/.env
ExecStart=/opt/knowledge-studio/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
User=knowledge
[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/ks-worker@.service   (start ks-worker@1, ks-worker@2, …)
[Service]
WorkingDirectory=/opt/knowledge-studio/backend
EnvironmentFile=/opt/knowledge-studio/.env
ExecStart=/opt/knowledge-studio/backend/.venv/bin/python -m workers.main --concurrency 4
KillSignal=SIGTERM
TimeoutStopSec=90
Restart=always
User=knowledge
```

```ini
# /etc/systemd/system/ks-web.service
[Service]
WorkingDirectory=/opt/knowledge-studio/frontend
Environment=API_INTERNAL_URL=http://127.0.0.1:8000
ExecStart=/usr/bin/npm run start -- --port 3000
Restart=always
User=knowledge
```

## One free service (no separate worker)

For small or personal deployments, everything can run in a single service, for example one
free Render web service:

* `RUN_WORKER_IN_API=1` runs the background worker inside the API process, so no worker
  service is needed. Use one uvicorn worker.
* `scripts/start-single.sh` starts the API on `127.0.0.1:8000` and the web app on `$PORT`.
  The web app proxies `/api/*` to the API, which is its default.
* `render.yaml` at the repository root describes this service as a Render Blueprint. Set
  `MONGODB_URI` and `GEMINI_API_KEY` when Render asks for them. `SESSION_SECRET` is
  generated, and the public URL is taken from Render.
* Use MongoDB file storage (`STORAGE_BACKEND=mongodb`, the Blueprint's default): the
  platform's disk is wiped on every restart.

What to expect on a free instance: it sleeps after 15 minutes without traffic and takes about
a minute to wake; jobs pause while it sleeps and continue afterwards; and with 512 MB of
memory, large PDFs can exhaust it during indexing. Move the worker to its own service
(below) when that becomes a problem.

## Platform hosting (no containers)

The processes also run on buildpack-style platforms (Render, Railway, Heroku-style Procfiles, Azure App Service for Python and Node):

```Procfile
api: cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'
worker: cd backend && python -m workers.main
```

Use `--forwarded-allow-ips '*'` only where the platform guarantees that its router is the sole ingress. Host the web app as a Node service with `API_INTERNAL_URL` pointing at the API's private URL. If the platform caps request bodies on the web service, route `/api/*` to the API at the edge instead.

## Production checklist

* `APP_ENV=production`. Startup refuses insecure combinations: missing `SESSION_SECRET`, `VECTOR_SEARCH_BACKEND=exact`, and Paddle enabled without API key or webhook secret.
* `APP_PUBLIC_URL=https://app.example.com`, `CORS_ALLOWED_ORIGINS=https://app.example.com`.
* `SESSION_SECRET`: 48+ random characters, unique per environment, kept in your secret manager.
* `MONGODB_URI` for a dedicated Atlas cluster with backups enabled; IP access list limited to your hosts.
* File storage (Admin > Settings, default from `STORAGE_BACKEND`):
  * **Server disk** (`local`): the API and every worker must see the same `LOCAL_STORAGE_DIR`, so run them on one server (or mount one shared volume), on a disk that survives restarts and redeploys.
  * **MongoDB** (`mongodb`): use this when the API and workers run on different servers or the platform's disk is temporary. A file takes database space while it is held, so with originals not kept that is only until it is indexed.
  * If you turn on *Keep original files*, back that location up; the files then stay for as long as the library entry does.
  * Optional `s3`: a **private** bucket, versioning on, no public ACLs, and server-side encryption.
* `GEMINI_API_KEY`: a key from a Google Cloud project with billing and quotas you control. Review Google's data-use terms for that tier and reflect them in the privacy policy.
* Atlas Search indexes `READY` (`python -m app.cli setup-search-indexes --wait`).
* `EMAIL_PROVIDER=smtp` with a transactional email provider; SPF, DKIM and DMARC set for `EMAIL_FROM`.
* Paddle: `PADDLE_ENVIRONMENT=production`, live API key, client token, and webhook secret; webhook destination `https://app.example.com/api/v1/billing/webhooks/paddle`. Map and **verify** prices in Admin > Paddle prices.
* `GOOGLE_AUTH_ENABLED=1` only with a production OAuth client whose redirect URI is `https://app.example.com/api/v1/auth/google/callback`.
* `SENTRY_DSN` (and `pip install sentry-sdk`) if you use Sentry.
* Create the first super admin: register, then `python -m app.cli create-admin --email … --role SUPER_ADMIN`.
* Fill in and legally review the privacy policy template at `/privacy`.
* Schedule backups and test a restore ([runbook.md](runbook.md)).

## Releases

1. Deploy code to all hosts.
2. Run `python -m app.cli setup-db` (idempotent: new indexes and validators).
3. Restart **workers** (they finish or checkpoint running jobs; leases let other workers resume them), then the **API**, then **web**.
4. If `CHUNKING_VERSION` or the embedding configuration changed, follow the runbook's migration steps.

## Scaling notes

* API and web are stateless; add instances behind the proxy.
* Workers scale horizontally; claiming is atomic. Dedicated pools (`--types research.run`) keep long research runs from delaying ingestion.
* MongoDB is the queue: watch `jobs` backlog in Admin > Jobs. If queue load becomes significant, the queue interface (`app/jobs/queue.py`) is the seam for a Redis-backed implementation.
* Atlas Search nodes can be scaled separately from the database tier on dedicated clusters.
