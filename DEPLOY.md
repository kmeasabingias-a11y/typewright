# Deploying the TypeWright demo

This brings up the full stack — the **TypeWright** web demo and the **Kestrel**
sandbox — on **one trusted Docker host**, and exposes it publicly through an
**on-demand Cloudflare quick tunnel**.

## Why a trusted host (and not Render/Fly/etc.)

Kestrel runs untrusted, AI-generated test code. It does this by driving the
**host's Docker daemon** (`/var/run/docker.sock`) to launch a locked-down sandbox
container per run (`--network none`, `--read-only`, `--user 65534`, 256 MB, 1 CPU,
no caps). That "docker-out-of-docker" design needs a host where you control the
daemon — a one-click PaaS won't give you the socket. Any Linux box with Docker
(a small VPS, or your own machine) works.

Three guards make a public URL safe to expose: per-IP rate limiting (10/min),
a per-analysis cost cap ($0.50 → 402), and a **global monthly LLM-spend cap**
($10 → 503). The monthly cap is the backstop against an unauthenticated demo
running up your bill.

## Prerequisites

- Docker + the Compose plugin on the host.
- This repo, and the **Kestrel** repo (sibling project) checked out.
- An `ANTHROPIC_API_KEY`. This repo's `.env` already holds it; Compose reads
  `.env` from the directory you run it in, so run the commands from the repo root.
- `cloudflared` for the public tunnel (`brew install cloudflared`, or
  https://github.com/cloudflare/cloudflared/releases).

## 1. Build the images

From **this** repo's root — the sandbox runtime (pytest + hypothesis baked in):

```bash
docker build -t typewright-test-runtime:0.1 -f docker/test-runtime.Dockerfile .
```

From the **Kestrel** repo's root — the Kestrel API image:

```bash
docker build -t kestrel-api:0.8.0 -f docker/api/Dockerfile .
```

(The `typewright:demo` app image is built by Compose with `--build`.)

## 2. Create the host directories

```bash
sudo mkdir -p /var/kestrel/spool /var/typewright/data
sudo chown 10001:10001 /var/typewright/data   # uid 10001 = appuser inside the TypeWright image
```

`/var/kestrel/spool` is where Kestrel stages code for the host daemon to mount.
`/var/typewright/data` holds `runs.db` — the shareable-link store **and** the
monthly-cap counter — so both survive a redeploy.

## 3. Bring up the web demo

```bash
docker compose -f docker-compose.demo.yml up -d --build
docker compose -f docker-compose.demo.yml ps        # both services Up
```

## 4. Verify locally

```bash
curl -s localhost:8001/health                       # {"status":"ok"}

curl -s -X POST localhost:8001/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"code":"def absolute(x):\n    \"\"\"Always >= 0.\"\"\"\n    return x\n",
       "function_name":"absolute","include_fix_suggestion":true}' | head -c 600
```

Expect a `200` with non-empty `bugs_found` and a `metadata.llm_cost_usd`. That one
call exercises the whole chain (LLM → Kestrel sandbox → results). To confirm the
**monthly cap**, lower it and restart, then call analyze twice — the second returns
`503` + `Retry-After`:

```bash
# temporarily, to demo the cap:
TYPEWRIGHT_MAX_MONTHLY_COST_USD=0.01 docker compose -f docker-compose.demo.yml up -d typewright
```

(Reset by raising it back and clearing the row: see "Operations" below.)

## 5. Go public with an on-demand tunnel

```bash
cloudflared tunnel --url http://localhost:8001
```

It prints a `https://<random>.trycloudflare.com` URL — share that for the demo,
then `Ctrl-C` to close it. The URL is ephemeral (new each run); that's deliberate
— there's no 24/7 open code-exec endpoint, only a tunnel you open for a demo.
Because TypeWright sees `X-Forwarded-For` from the tunnel and
`TYPEWRIGHT_TRUST_FORWARDED_FOR=true`, per-IP rate limiting keys on the real
visitor, not the tunnel.

## Operations

- **Lower the monthly cap:** edit `TYPEWRIGHT_MAX_MONTHLY_COST_USD` in the compose
  file and `docker compose -f docker-compose.demo.yml up -d typewright`.
- **Reset this month's counter:**
  ```bash
  docker run --rm -v /var/typewright/data:/data nouchka/sqlite3 \
    /data/runs.db "DELETE FROM monthly_cost;"
  ```
  (or `sudo sqlite3 /var/typewright/data/runs.db "DELETE FROM monthly_cost;"`).
- **Logs:** `docker compose -f docker-compose.demo.yml logs -f typewright`
  (JSON lines — one `event=analysis_trace …` summary per analysis).
- **Tear down:** `docker compose -f docker-compose.demo.yml down`
  (data + counter persist in `/var/typewright/data`).

## Optional: enable the GitHub PR bot

Adds Redis + an arq worker that comments property violations + a verified fix on
pull requests. They share `runs.db`, so the monthly cap is one global counter
across the web and bot paths.

1. Put your GitHub App private key at the repo root as `app.pem`, and set
   `TYPEWRIGHT_GITHUB_APP_ID` + `TYPEWRIGHT_GITHUB_WEBHOOK_SECRET` in `.env`.
2. `docker compose -f docker-compose.demo.yml --profile github up -d --build`
3. Point the App's webhook at `https://<your-tunnel>/webhook/github` (or use
   `smee` for local testing).

## Troubleshooting

- **Kestrel can't reach Docker** (`permission denied … docker.sock`): the daemon
  socket must be group-accessible to the Kestrel container. On most hosts it runs
  as root and this just works; if you've hardened the socket, grant access.
- **`volume … main.py: no such file`** from a sandbox run: the spool dir isn't at
  an identical host:container path — confirm `KESTREL_EXEC_SPOOL_DIR=/var/kestrel/spool`
  matches the bind mount.
- **All analyses 503:** the monthly cap is hit (`spent ≥ limit`). Raise it or clear
  the counter (Operations).
- **Fallback — Kestrel as a host process:** if the containerized Kestrel gives you
  trouble, run it on the host instead (the exact form verified in the live smoke)
  and point TypeWright at it with `TYPEWRIGHT_KESTREL_BASE_URL=http://host.docker.internal:8000`
  (add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `typewright`
  service):
  ```bash
  KESTREL_EXECUTOR_DOCKER_IMAGE=typewright-test-runtime:0.1 \
  KESTREL_EXECUTE_TIMEOUT_SECONDS=60 KESTREL_EXECUTE_OUTPUT_CAP_BYTES=262144 \
  uv run --directory /path/to/Kestrel uvicorn kestrel.app:create_app --factory --port 8000
  ```
