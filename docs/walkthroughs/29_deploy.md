# 29 — `docker-compose.demo.yml` + `DEPLOY.md` (the deploy bundle)

## What this file is for

Up to now everything has run on a developer's laptop: you start Kestrel in one
terminal, TypeWright in another, and poke at `localhost`. **These two files are the
recipe for putting the whole thing on a real server so a stranger can use it** — the
recruiter who clicks a link, pastes a function, and sees bugs.

`docker-compose.demo.yml` is the *what*: a single file that says "run these
containers, wire them together like so." `DEPLOY.md` is the *how*: the step-by-step
a human follows on the server — build the images, make a couple of folders, bring it
up, and open a temporary public URL.

A useful analogy: the compose file is the **stage plan** for a play (who stands
where, who talks to whom); DEPLOY.md is the **director's notes** for setting the
stage up on the night. Two documents, one performance.

## A mental model

Five ideas make the whole bundle obvious:

1. **This can't go on a one-click host (Render, Fly, Heroku…).** TypeWright's whole
   trick is running *untrusted* test code safely, and it does that by asking
   **Kestrel** to launch a throwaway, locked-down container per run. Kestrel launches
   those by talking to the **host's Docker daemon** through `/var/run/docker.sock`.
   A managed PaaS won't hand you that socket. So the deploy target is **one machine
   you control** — a small VPS, or your own box.

2. **"docker-out-of-docker" is the awkward bit.** Kestrel itself runs in a container,
   but the sandboxes it spawns run on the *host's* daemon, as siblings — not inside
   Kestrel. That's fine, except for one snag: when Kestrel writes the code-to-run to a
   temp file and tells the host "mount this file into the sandbox," the host has to be
   able to *find* that file at the same path. So the temp folder
   (`/var/kestrel/spool`) is shared between Kestrel's container and the host at the
   **exact same path on both sides**. Get that path wrong and sandboxes fail with
   "no such file."

3. **The three guards are what make a public URL sane.** A web page that runs code
   and calls a paid AI, open to the internet, is normally a terrible idea. It's
   defensible here only because three limits already exist in the app: a per-IP rate
   limit (D53), a per-analysis cost cap (D52), and a **global monthly spend cap**
   (D58). The compose turns all three on. The monthly cap is the real backstop — it
   means the worst a flood of visitors can do is burn your set monthly budget, then
   the site goes read-only with a `503` until next month.

4. **The public URL is a tunnel you open on demand, not a door left open.** Instead
   of exposing a port to the whole internet 24/7, the app is published on
   `127.0.0.1` (the server's own loopback only), and you run a **Cloudflare quick
   tunnel** for the length of a demo, then close it. There's no standing
   code-execution endpoint for a bot to find at 3 a.m.

5. **One small permissions gotcha drives one design choice.** TypeWright's container
   runs as a non-root user (uid 10001). The file it needs to write — `runs.db`, which
   holds shareable links *and* the monthly-spend counter — has to live somewhere that
   user can write. A fresh Docker *named volume* mounts owned by root, which uid 10001
   can't write to. So the data folder is a **host directory we `chown` to 10001** and
   bind-mount in. (Kestrel's spool folder is mounted the same way, for the path
   reason above — so the pattern is consistent.)

## The whole file — `docker-compose.demo.yml`

```yaml
services:
  # ---- Kestrel sandbox: auth OFF, no Postgres/Redis (mirrors the verified local smoke) ----
  kestrel:
    image: kestrel-api:0.8.0                 # built separately from the Kestrel repo (prereq 2)
    environment:
      KESTREL_DEV_API_KEY: ""                # empty key + null backends => auth disabled
      KESTREL_API_KEY_BACKEND: "null"
      KESTREL_AUDIT_BACKEND: "null"
      KESTREL_SESSION_BACKEND: "memory"
      KESTREL_EXECUTOR_DOCKER_IMAGE: "typewright-test-runtime:0.1"  # pytest+hypothesis runtime
      KESTREL_EXECUTE_TIMEOUT_SECONDS: "60"  # Kestrel's own default is 5s -> spurious 504s
      KESTREL_EXECUTE_OUTPUT_CAP_BYTES: "262144"
      KESTREL_EXEC_SPOOL_DIR: "/var/kestrel/spool"  # must equal the host bind-mount path
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock   # drive the HOST daemon
      - /var/kestrel/spool:/var/kestrel/spool       # IDENTICAL host:container path (docker-out-of-docker)
    restart: unless-stopped

  # ---- TypeWright web demo (the public face) ----
  typewright:
    build: .
    image: typewright:demo
    environment:
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY in a .env beside this file}"
      TYPEWRIGHT_KESTREL_BASE_URL: "http://kestrel:8000"
      TYPEWRIGHT_KESTREL_TIMEOUT_SECONDS: "45"   # <= Kestrel's 60s ceiling
      TYPEWRIGHT_RUNS_DB_PATH: "/data/runs.db"   # bind volume -> links + monthly cap survive redeploys
      TYPEWRIGHT_MAX_MONTHLY_COST_USD: "10.00"   # global LLM-spend backstop (D58); lower to taste
      TYPEWRIGHT_MAX_COST_USD: "0.50"            # per-analysis cap (D52)
      TYPEWRIGHT_TRUST_FORWARDED_FOR: "true"     # the Cloudflare tunnel is a trusted proxy -> real client IP
      TYPEWRIGHT_LOG_FORMAT: "json"              # structured logs for an aggregator (D54)
      TYPEWRIGHT_REDIS_URL: "redis://redis:6379"
      TYPEWRIGHT_GITHUB_WEBHOOK_SECRET: "${TYPEWRIGHT_GITHUB_WEBHOOK_SECRET:-}"
    volumes:
      - /var/typewright/data:/data
    ports:
      - "127.0.0.1:8001:8000"          # localhost-only; public access is via the tunnel
    depends_on:
      - kestrel
    restart: unless-stopped

  # ---- OPTIONAL GitHub PR bot (webhook queue + worker). Enable with --profile github ----
  redis:
    image: redis:7-alpine
    profiles: ["github"]
    restart: unless-stopped

  worker:
    image: typewright:demo             # same image; runs the arq worker instead of uvicorn
    profiles: ["github"]
    command: ["arq", "typewright.worker.WorkerSettings"]
    environment:
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY:?}"
      TYPEWRIGHT_KESTREL_BASE_URL: "http://kestrel:8000"
      TYPEWRIGHT_KESTREL_TIMEOUT_SECONDS: "45"
      TYPEWRIGHT_RUNS_DB_PATH: "/data/runs.db"   # SAME volume as the web app -> one shared monthly counter (D58)
      TYPEWRIGHT_MAX_MONTHLY_COST_USD: "10.00"
      TYPEWRIGHT_MAX_COST_USD: "0.50"
      TYPEWRIGHT_REDIS_URL: "redis://redis:6379"
      TYPEWRIGHT_GITHUB_APP_ID: "${TYPEWRIGHT_GITHUB_APP_ID:-}"
      TYPEWRIGHT_GITHUB_APP_PRIVATE_KEY_PATH: "/secrets/app.pem"
    volumes:
      - /var/typewright/data:/data
      - ./app.pem:/secrets/app.pem:ro  # copy/rename your GitHub App private key here (gitignored *.pem)
    depends_on:
      - redis
      - kestrel
    restart: unless-stopped
```

(`DEPLOY.md` is prose, not code — its sections are: *why a trusted host*,
*prerequisites*, *1. build the images*, *2. make the host folders*, *3. bring it up*,
*4. verify*, *5. open the tunnel*, *operations*, *the optional GitHub bot*, and
*troubleshooting*. The steps below mirror it.)

## Step-by-step

**The `kestrel` service** runs Kestrel with **auth turned off and no database** —
exactly the trust model the live smoke used, just in a container instead of as a host
process. The four `KESTREL_*_BACKEND` / `DEV_API_KEY` lines are the off switch:
Kestrel disables auth when the dev key is empty *and* there's no key store (`null`
backends), and `SESSION_BACKEND=memory` means it needs neither Postgres nor Redis.
`KESTREL_EXECUTOR_DOCKER_IMAGE` points it at *our* runtime image (walkthrough 16) so
sandboxes have pytest + Hypothesis. The `60`-second timeout override matters: Kestrel's
own default is 5 seconds, which would cut real test runs short and surface as spurious
`504`s.

**The two `kestrel` volumes** are the docker-out-of-docker plumbing. `docker.sock`
lets Kestrel drive the host daemon. The spool mount uses the **same path on both
sides** (`/var/kestrel/spool:/var/kestrel/spool`) so that when Kestrel writes a code
file there and asks the host to mount it into a sandbox, the host finds it at the path
Kestrel named. Notice Kestrel has **no `ports:`** — nothing outside the compose
network should reach the code-runner directly; only TypeWright talks to it, at
`http://kestrel:8000` over the private network Compose creates.

**The `typewright` service** is the only thing the public touches. `build: .` builds
the app image from this repo's `Dockerfile` (walkthrough 08). Its environment turns on
the guards and points it at the sandbox:
- `TYPEWRIGHT_KESTREL_BASE_URL: http://kestrel:8000` — reach Kestrel by its service
  name, not `localhost` (inside a container `localhost` is the container itself).
- `TYPEWRIGHT_RUNS_DB_PATH: /data/runs.db` — write the database onto the mounted
  volume, so shareable links and the monthly counter **survive a redeploy**.
- `TYPEWRIGHT_MAX_MONTHLY_COST_USD`, `TYPEWRIGHT_MAX_COST_USD` — the spend caps,
  on with sane defaults you can lower.
- `TYPEWRIGHT_TRUST_FORWARDED_FOR: "true"` — see "What could go wrong"; this is the
  line that makes per-IP rate limiting work behind the tunnel.
- `TYPEWRIGHT_LOG_FORMAT: "json"` — structured logs, one summary line per analysis,
  ready for a log aggregator.

**`ports: "127.0.0.1:8001:8000"`** publishes the app on the server's loopback only —
*not* `0.0.0.0`. So even though a port is "published," it isn't reachable from the
outside; the only way in is the tunnel you start by hand.

**`volumes: /var/typewright/data:/data`** is the host bind mount (a real folder on the
server you `chown` to uid 10001 first), chosen over a named volume so the non-root app
user can actually write `runs.db`.

**The `redis` + `worker` services** are wrapped in `profiles: ["github"]`, so a plain
`docker compose up` ignores them — the core demo is just TypeWright + Kestrel. Add
`--profile github` and you also get Redis and the **arq worker** (same image, but its
`command:` runs the worker instead of uvicorn) that powers the PR-comment bot. The
worker mounts the **same** `/data` volume as the web app on purpose: that's how the
monthly spend cap stays *one global number* across both the website and the bot.

**`DEPLOY.md`'s steps** then string this together on the server: build the three
images (the runtime image and Kestrel's image by hand, the app image via Compose),
`sudo mkdir` the two host folders and `chown` the data one to `10001`, make sure
`ANTHROPIC_API_KEY` is in a `.env` next to the compose file, `docker compose -f
docker-compose.demo.yml up -d --build`, curl `/health` and run one analyze to prove
the whole chain, then `cloudflared tunnel --url http://localhost:8001` to get a
shareable `https://…trycloudflare.com` URL for the demo.

## What could go wrong

- **Sandboxes fail with "no such file … main.py".** The spool dir isn't mounted at an
  identical host:container path. `KESTREL_EXEC_SPOOL_DIR` must equal both sides of the
  `/var/kestrel/spool:/var/kestrel/spool` mount. This is the single most likely
  docker-out-of-docker mistake.

- **TypeWright can't write `runs.db` ("permission denied").** The data folder is owned
  by root, not by the app's uid 10001. The fix is in DEPLOY.md step 2: `sudo chown
  10001:10001 /var/typewright/data` before bringing the stack up. This is exactly why
  a host bind mount is used instead of a named volume (a named volume would reintroduce
  the root-owned problem).

- **Rate limiting lumps everyone together behind the tunnel.** Without
  `TYPEWRIGHT_TRUST_FORWARDED_FOR=true`, every request appears to come from the
  tunnel's address, so the per-IP limit treats all visitors as one client. Turning it
  on makes the app read the real visitor IP from `X-Forwarded-For`. It's **off by
  default for a reason** — a direct client could spoof that header — so only enable it
  when something you trust (the tunnel) sits in front. Don't set it on a stack whose
  port is exposed directly.

- **Every analysis returns `503`.** That's the monthly cap doing its job —
  `spent ≥ limit` for the current month. Raise `TYPEWRIGHT_MAX_MONTHLY_COST_USD`, or
  clear the counter (DEPLOY.md "Operations" shows the one-line SQL: `DELETE FROM
  monthly_cost;`). It is *not* an outage.

- **Kestrel can't reach the Docker socket.** On a typical host the Kestrel container
  runs as root and the `docker.sock` mount just works. If you've hardened the socket's
  permissions, the container's user needs to be in the right group, or sandboxes won't
  launch.

- **The containerized Kestrel misbehaves and you're stuck.** This is the one piece not
  yet exercised live (the smoke ran Kestrel as a *host process*). DEPLOY.md's
  troubleshooting has the fallback: run Kestrel directly with `uvicorn` on the host
  (the exact verified command) and point TypeWright at it with
  `TYPEWRIGHT_KESTREL_BASE_URL=http://host.docker.internal:8000` plus an `extra_hosts`
  entry. Same app, sandbox path proven, just not co-located in the compose.

- **Leaving the tunnel up forever.** The on-demand tunnel is a deliberate safety
  choice. Running it 24/7 turns the demo back into a permanent open code-exec endpoint
  — the very thing the `127.0.0.1` publish was meant to avoid. Open it for the demo,
  `Ctrl-C` it after.

## Change history

- **2026-06-30 — created (Phase 10, Stage 2, D59).** Added repo-root
  `docker-compose.demo.yml` + `DEPLOY.md`: the full demo stack (TypeWright built from
  the repo + Kestrel containerized auth-off, docker-out-of-docker via `docker.sock` +
  an identical-path spool mount) on one trusted host, `runs.db` on a host bind mount
  `chown`ed to uid 10001, all three spend/rate guards on, published on `127.0.0.1` and
  exposed via an on-demand Cloudflare tunnel; an optional `--profile github` adds Redis
  + the arq worker sharing one `runs.db` (so the D58 monthly counter is global). Both
  profiles pass `docker compose config`. The remaining verification is a live bring-up
  on a real host (the containerized auth-off Kestrel is the one untested piece; a
  verified host-process fallback is documented in DEPLOY.md).
- **2026-08-16** — Phase 10 (D62): the compose file and `DEPLOY.md` now set **four** guards, not three —
  `TYPEWRIGHT_MAX_DAILY_COST_USD=2.00` joins the monthly cap, per-analysis cap, and per-IP rate limit — and
  pass `TYPEWRIGHT_DEMO_ACCESS_CODE` through (empty by default; set it to gate the demo and share links as
  `https://<host>/?code=<value>`). Operations gained "change a cap", "reset a counter" (both tables), and
  "gate the demo"; troubleshooting gained the 403 case and a 503 that names which period is exhausted.
