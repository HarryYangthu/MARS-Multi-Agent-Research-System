# MARS Standard Deployment Runbook

This document is the production checklist for MARS. The recommended deployment
shape is:

```text
Browser
  -> Vercel Next.js frontend
  -> HTTPS/WSS backend API domain
  -> self-hosted FastAPI + Redis + Chroma + runs/knowledge/workspace volumes
  -> optional GPU/vLLM/external research-code executor
```

Vercel is a good fit for the frontend. The MARS backend should stay on a
long-running server because it owns Redis pub/sub, Socket.IO, persistent run
sedimentation, Chroma/local KB state, and execution backends.

## 1. Preflight

Run these before any deploy:

```bash
git status --short
bash scripts/acceptance.sh
```

Confirm these are production-correct:

- `projects/pimc/repo_link.yaml::repo_path` points to the server path, not a
  local `/Users/...` path.
- `configs/execution.yaml::execution.paper_static.*` points to server paths if
  `MARS_EXECUTION_BACKEND=paper_static`.
- `configs/agents.yaml` providers match the keys present in `.env.production`.
- `configs/gates.yaml` keeps `baseline_compatibility.enabled=true`.
- `MARS_RUNTIME_MODE=production`, `MARS_MOCK_MODE=never`.

## 2. Backend Server Setup

Install Docker, Docker Compose, Nginx, and Certbot on the server. Then:

```bash
git clone <repo-url> mars
cd mars
cp .env.production.example .env.production
mkdir -p knowledge workspace/uploads workspace/repos runs
```

Edit `.env.production`:

```env
BACKEND_URL=https://api.example.com
NEXT_PUBLIC_BACKEND_URL=https://api.example.com
NEXT_PUBLIC_WS_URL=wss://api.example.com
MARS_CORS_ORIGINS=https://mars.example.com,https://your-project.vercel.app

MARS_RUNTIME_MODE=production
MARS_MOCK_MODE=never
MARS_DEFAULT_PROJECT=pimc
MARS_EXECUTION_BACKEND=pim_cpu

DEEPSEEK_API_KEY=...
```

Start the backend stack:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build backend redis chromadb
docker compose -f docker-compose.prod.yml ps
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/api/readiness?project=pimc"
```

The readiness endpoint must be `ready: true` before production traffic.

## 3. Nginx and HTTPS

Backend-only proxy, for Vercel frontend:

```bash
sudo cp deploy/nginx/mars-api.conf /etc/nginx/conf.d/mars-api.conf
sudo sed -i 's/api.example.com/api.your-domain.com/g' /etc/nginx/conf.d/mars-api.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d api.your-domain.com
```

Self-hosted full stack alternative:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml --profile self-hosted-frontend up -d --build
sudo cp deploy/nginx/mars-fullstack.conf /etc/nginx/conf.d/mars-fullstack.conf
sudo sed -i 's/mars.example.com/mars.your-domain.com/g' /etc/nginx/conf.d/mars-fullstack.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d mars.your-domain.com
```

## 4. Vercel Frontend

The frontend is in `frontend/`. Configure these Vercel env vars for Preview and
Production:

```env
BACKEND_URL=https://api.your-domain.com
NEXT_PUBLIC_BACKEND_URL=https://api.your-domain.com
NEXT_PUBLIC_WS_URL=wss://api.your-domain.com
```

CLI flow:

```bash
cd frontend
vercel login
vercel env add BACKEND_URL production
vercel env add NEXT_PUBLIC_BACKEND_URL production
vercel env add NEXT_PUBLIC_WS_URL production
vercel deploy -y
vercel deploy --prod -y
```

Use Preview first. Promote to production only after the preview can create a
run, receive events, approve HITL steps, and fetch artifacts.

## 5. Standard Release Flow

1. Merge to a release branch or tag.
2. Run `bash scripts/acceptance.sh`.
3. Deploy backend staging with `MARS_RUNTIME_MODE=staging`.
4. Deploy Vercel Preview using the staging API URL.
5. Run a mock/demo task and one real-provider smoke task.
6. Deploy backend production.
7. Deploy or promote Vercel production.
8. Check `/health`, `/api/readiness`, WebSocket events, and one full run.

## 6. Rollback

Frontend rollback:

```bash
vercel rollback
# or promote a known-good preview deployment
vercel promote <deployment-url>
```

Backend rollback:

```bash
git checkout <known-good-tag-or-sha>
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build backend
curl http://127.0.0.1:8000/health
```

Data rollback is separate from code rollback. Back up these directories before
schema, storage, or executor changes:

- `runs/`
- `knowledge/`
- `workspace/`
- `projects/`
- `configs/`
- `.env.production`

## 7. Multi-User Access

MARS V0 is a research workbench, not a full multi-tenant SaaS yet. Treat
multi-user production as controlled-team access.

Recommended controls:

- Put the frontend behind Vercel password protection, SSO, Cloudflare Access, or
  Tailscale until app-level auth exists.
- Restrict backend CORS with `MARS_CORS_ORIGINS`.
- Do not expose Redis, Chroma, vLLM, or executor ports publicly.
- Use one project workspace per research project. Avoid multiple users writing
  to the same external research repo at the same time.
- Keep HITL approval on. Production blocks `auto_approve=true`.
- Track runs by `run_id`, owner in task text/metadata, and timestamp until
  first-class user ownership is added.

Concurrency guardrails:

- Tune `configs/execution.yaml::execution.max_concurrency`.
- Keep expensive execution backends at lower concurrency than mock.
- Use provider-side LLM rate limits and separate API keys for staging/prod.
- Monitor Redis and backend memory with `docker stats`.

## 8. Memory Management

MARS has three kinds of memory.

Application memory:

- Backend Python process memory, Chroma client memory, and queued events.
- Watch with `docker stats`, `docker compose logs backend`, and host metrics.
- If OOM happens, lower execution concurrency and reduce large artifact reads.

Agent/context memory:

- Controlled by `MARS_CONTEXT_MAX_TOKENS`, `MARS_CONTEXT_TARGET_TOKENS`,
  `MARS_CONTEXT_AUTO_COMPRESS`, and `MARS_CONTEXT_TOOL_RAW_EXTERNALIZE`.
- Keep raw tool outputs in files under `runs/<id>/context` instead of stuffing
  them into prompts.
- Let the context engine compress before adding more KB/tool output.
- Do not ingest every run artifact into long-term KB automatically. Promote only
  approved reports, stable code insights, and reusable methodology notes.

Persistent memory:

- `runs/` is the audit log and replay source.
- `knowledge/` is the long-term KB.
- `workspace/` is user uploads and connected repos.
- Back them up separately from containers. Containers are replaceable; these
  directories are not.

Retention policy:

- Keep recent runs hot on disk.
- Archive old runs to object storage.
- Keep only approved artifacts in KB.
- Rebuild Chroma indexes from source documents if index state becomes suspect.

## 9. Tool Safety

Tool calls must be boring and auditable in production.

Core rules:

- Agent tools are configured per agent in `configs/agents.yaml`.
- Tool availability and permissions live in `configs/tools.yaml`.
- Project repo access is constrained by `projects/<name>/repo_link.yaml`.
- Gate 5 protects baseline surfaces in the tool dispatch path.
- Network tools stay off unless `MARS_ENABLE_NETWORK_TOOLS=true` and allowlists
  are set.
- Local command tools must use configured argv allowlists, not free-form shell.
- Mount `configs/`, `projects/`, and `templates/` read-only in production.
- Review `runs/<id>/events/` and HITL audit logs when a tool behaves oddly.

If a tool starts doing the wrong thing:

1. Disable it in `configs/tools.yaml`.
2. Restart backend.
3. Inspect the run event log and tool audit record.
4. Tighten its schema/allowlist.
5. Add a regression test before re-enabling.

## 10. Common Problems and Answers

### The Vercel page opens, but API calls fail.

Check `BACKEND_URL`, `NEXT_PUBLIC_BACKEND_URL`, and `NEXT_PUBLIC_WS_URL` in
Vercel. Rebuild after changing them. Also check backend CORS:

```bash
curl https://api.your-domain.com/health
```

### WebSocket events do not arrive.

Use `wss://` for `NEXT_PUBLIC_WS_URL`. Confirm Nginx forwards `Upgrade` and
`Connection` headers. The included Nginx templates already do this.

### `/api/readiness` is not ready in production.

Typical causes:

- Missing DeepSeek or other configured LLM key.
- `MARS_EXECUTION_BACKEND=mock`.
- `projects/pimc/repo_link.yaml::repo_path` does not exist on the server.
- Gate 5 is disabled.
- Schema/template files are missing.

### A run is stuck at review.

That is expected HITL behavior. Approve or reject the current artifact. In
production, auto-approval is blocked by design.

### Memory keeps growing.

First reduce execution concurrency. Then check whether huge tool outputs are
being included in prompts instead of externalized under `runs/<id>/context`.
Finally inspect Chroma/KB ingestion volume and old runs.

### Disk fills up.

Archive `runs/` and uploaded files. Do not delete approved artifacts until they
are backed up. Chroma indexes can be rebuilt, but source papers and run outputs
should be preserved.

### Tools modified the wrong files.

Gate 5 should block protected baseline paths, but allowed paths can still be
too broad. Tighten `allowed_paths` and `protected_paths` in
`projects/<name>/repo_link.yaml`, then add a gate/tool regression test.

### Agents fall back to mock in production.

They should not. Confirm:

```env
MARS_RUNTIME_MODE=production
MARS_MOCK_MODE=never
```

Then check provider keys and `configs/agents.yaml`.

### Vercel build fails on peer dependencies.

Use the committed `frontend/vercel.json`; it sets:

```json
{
  "installCommand": "npm install --legacy-peer-deps"
}
```

### Backend returns 502/504 through Nginx.

Check:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=200 backend
sudo nginx -t
```

For long-running requests, keep `proxy_read_timeout` high or move the work to a
background run with event streaming.

### LLM provider rate limits runs.

Lower concurrent runs, lower debate rounds, or split staging/prod keys. Keep
agent-specific model configs in `configs/agents.yaml` rather than hardcoding
models in code.

### How do we keep tools from calling external network by accident?

Leave `MARS_ENABLE_NETWORK_TOOLS=false`. If enabling network search, set
`MARS_WEB_SEARCH_PROVIDER`, API keys, and allowlists. Treat this as a production
change with review.

### Can multiple users share one backend?

Yes for a small trusted team, but V0 is not full tenant isolation. Use external
access control, keep project workspaces separate, and avoid simultaneous writes
to the same external research repo.

### Can we run all of MARS on Vercel?

Not recommended for V0. The frontend fits Vercel well. The backend needs
persistent state, long-running workers, WebSocket-style events, and local/GPU
execution hooks, which are better served by a long-running host.
