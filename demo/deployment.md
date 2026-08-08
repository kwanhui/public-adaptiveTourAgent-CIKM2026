# Deployment Notes

This is a single-machine demo. No multi-tenant, no auth, no persistence beyond
SQLite. The scope is matched to a CIKM demo-track reviewer experience: clone,
run one command, click around in the browser.

For a public URL reviewers can hit, the recommended target is **Hugging Face
Spaces** (Docker SDK). Setup is documented below; alternatives (Railway,
Fly.io) follow the same pattern with their own dashboards.

## Hugging Face Spaces (recommended for the public demo URL)

The repo ships with a `Dockerfile`. When you create the HF Space, add the
Space's YAML frontmatter (`sdk: docker`, `app_port: 7860`) to its README so
HF uses the Docker SDK and the right port. Once pushed to a HF Space's git
remote, every subsequent push rebuilds the Space.

### One-time setup

1. **Create the Space** at <https://huggingface.co/new-space>:
   - SDK: `Docker`
   - Name: `adaptive-tour-agent` (or anonymized for double-blind submission)
   - Visibility: Public (so reviewers can hit it without an HF account)
2. **Add Space secrets** at *Space → Settings → Variables and secrets*:
   - `OPENAI_API_KEY` = your project-scoped key (server-side only)
   - `MAX_USD_PER_SESSION` = `0.20` (overrides the $0.50 default)
   - `MAX_PLANS_PER_HOUR` = `10` (enables the per-IP rate limiter)
3. **Cap your OpenAI project** at $5-10/month hard limit in the OpenAI
   dashboard so a hammered URL can't drain you.

### First push

```bash
# From the repo root, with the HF write token in the URL:
HF_USER=your-hf-username
HF_SPACE=adaptive-tour-agent
HF_TOKEN=hf_...

git remote add space "https://${HF_USER}:${HF_TOKEN}@huggingface.co/spaces/${HF_USER}/${HF_SPACE}"
git push space main
```

The Space starts building immediately. The build log is visible in the
Space's "Logs" tab; first build takes 3-5 minutes.

### Subsequent pushes

```bash
git push space main
```

(or alias `git push origin main && git push space main` if you want a
single command.)

### GitHub Actions (optional)

A workflow can automate the push, but do not store the HF write token in a
public repository's Actions secrets. The maintainers' Space is deployed by a
manually dispatched workflow in a separate private repository, which holds
`HF_TOKEN`, checks out this repository at a chosen ref, and force-pushes it
to the Space as a single snapshot commit. If you fork this repo and your fork
is private, the simple in-repo mirror works; if it is public, keep the
token-holding workflow somewhere private and pull the public content in.

### URL the reviewers see

`https://huggingface.co/spaces/<your-username>/adaptive-tour-agent`

When idle, the Space sleeps after 48h. The first reviewer hit triggers a
~10s cold start; subsequent hits are instant.

### What the Dockerfile does

`Dockerfile` builds a slim Python 3.11 image, installs the package
(no dev deps), and runs `python -m adaptivetouragent.app --host 0.0.0.0
--port $PORT`. HF Spaces routes traffic to `$PORT=7860` by default; set
`app_port: 7860` in the Space frontmatter to match.

The build context is trimmed by `.dockerignore` (no tests, no docs, no
demo media). Image size is ~120 MB.

## Local (developer machine)

```bash
git clone https://github.com/kwanhui/public-adaptiveTourAgent-CIKM2026.git
cd public-adaptiveTourAgent-CIKM2026
python3 -m venv venv && source venv/bin/activate
make install
export OPENAI_API_KEY=sk-...
make demo
```

Open http://localhost:8080.

## Headless box (for the live demo URL)

A 1 vCPU / 1 GB RAM VM is enough; the LLM call is the bottleneck.

```bash
# As root once:
apt-get update && apt-get install -y python3 python3-venv git

# As the runtime user:
git clone https://github.com/kwanhui/public-adaptiveTourAgent-CIKM2026.git /srv/atau
cd /srv/atau
python3 -m venv venv && source venv/bin/activate
make install
```

Drop a systemd unit at `/etc/systemd/system/atau.service`:

```ini
[Unit]
Description=AdaptTour (Real-Time Adaptive Tour Recommendation with Agentic AI)
After=network.target

[Service]
User=atau
WorkingDirectory=/srv/atau
EnvironmentFile=/srv/atau/.env
ExecStart=/srv/atau/venv/bin/python -m adaptivetouragent.app --host 127.0.0.1 --port 8080
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then front it with nginx for TLS (the SSE stream needs `proxy_buffering off`):

```nginx
location / {
  proxy_pass http://127.0.0.1:8080;
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_buffering off;
  proxy_read_timeout 3600s;
}
```

## Cost cap

Set in the unit file environment:

```
MAX_USD_PER_SESSION=0.20
```

`/replan` returns 429 when a session exceeds the cap. Defaults to $0.50.

## Safety guardrails for a public URL

- Use a project-scoped OpenAI API key with a hard monthly cap.
- Set `MAX_PLANS_PER_HOUR` (default off, recommend 10 for HF Spaces): the
  bundled `PerIPRateLimiter` middleware caps how often one IP can hit
  `/plan`. `/replan` is implicitly capped because it requires a session.
- Disable the live-weather adapter on the demo URL to avoid hammering Open-Meteo.
