# Deployment guide

This app has two containers:

| Container | What it is | Port |
| --- | --- | --- |
| **wr-core** | the Python cleaning engine (from `service/`) — does the actual work | 8765 (internal only) |
| **wr-web** | nginx serving the web UI and proxying `/api/*` to wr-core | 80 (public) |

Only **wr-web** is exposed. The browser talks to nginx; nginx talks to the
engine on a private network. Same origin, so there is **no CORS to configure**.

```
Browser ──HTTP──▶ wr-web (nginx :80)
                    ├── /            static UI  (web/)
                    └── /api/*  ───▶ wr-core (:8765)   [private network]
```

---

## TL;DR — where do I put my API key?

There is exactly **one** place, and it is **optional**:

1. In `deploy/`, copy the template:  `cp .env.example .env`
2. Open `.env` and set:
   ```
   WATERMARKS_SERVER_API_KEY=<a long random string>
   ```
   Generate one with `openssl rand -hex 32`.
3. Restart: `docker compose up -d`.
4. Open the site, click **Settings** (top-right), paste the **same** value into
   the "API key" field, Save.

That is the whole auth story. If you leave `WATERMARKS_SERVER_API_KEY` empty,
the service is open (fine for a private/local box, not for the public internet).

> Note: this project does **not** need any third-party AI provider key to
> clean files. The core cleaning (Unicode, C2PA, EXIF/XMP, doc props) is
>100% local. The optional `WATERMARKS_REWRITE_*` and `HF_TOKEN` keys in the
> upstream README are only for the *optional* heavy features (statistical
> text rewrite, GPU pixel removal) that this web app does not enable by
> default. You can ignore them.

---

## Run locally (5 minutes)

Requires Docker Desktop (or Docker Engine + compose plugin).

```bash
cd deploy
cp .env.example .env          # optional; edit to set a key
docker compose up --build -d
```

Open **http://localhost:8080**. Drop a file, hit Inspect or Clean.

Stop: `docker compose down`.

---

## Deploy on AWS

You have three good options. **Option A (EC2)** is the simplest and the best
fit for AWS credits — one small VM runs the whole stack. Options B and C are
more "managed" if you prefer no server to maintain.

### Option A — EC2 (recommended, simplest)

**1. Launch an instance**

- AMI: *Amazon Linux 2023* (or Ubuntu 24.04)
- Type: `t3.small` is plenty (2 GB RAM). `t3.micro` works for light use.
- Storage: 20 GB gp3.
- Security group inbound rules:
  - `80` (HTTP) from `0.0.0.0/0`
  - `443` (HTTPS) from `0.0.0.0/0` — only if you add TLS (step 5)
  - `22` (SSH) from **your IP only**

**2. Install Docker** (Amazon Linux 2023)

```bash
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# log out and back in so the group takes effect
```

(Ubuntu: `sudo apt update && sudo apt install -y docker.io docker-compose-v2 git`)

**3. Get the code onto the box**

```bash
git clone <your-repo-url> ai-watermark-remover
cd ai-watermark-remover/deploy
```

(or `scp` the project folder up if it is not in git yet.)

**4. Configure and run**

```bash
cp .env.example .env
# edit .env:  set WATERMARKS_SERVER_API_KEY, and set WEB_PORT=80
nano .env
docker compose up --build -d
```

Set `WEB_PORT=80` in `.env` so the site answers on the standard port. Then
visit `http://<your-ec2-public-ip>/`.

**5. (Recommended) Add a domain + HTTPS**

Point a domain's A-record at the instance's Elastic IP, then put a TLS proxy
in front. Easiest is Caddy (automatic Let's Encrypt certs):

```bash
# keep the app on 8080 (WEB_PORT=8080 in .env), let Caddy own 80/443
sudo dnf install -y 'dnf-command(copr)'    # AL2023
# ...install caddy per https://caddyserver.com/docs/install
```

`/etc/caddy/Caddyfile`:
```
your-domain.com {
    reverse_proxy 127.0.0.1:8080
}
```
```bash
sudo systemctl enable --now caddy
```
Now `https://your-domain.com` is served with a valid certificate, and the app
still runs behind it on 8080.

**Auto-start on reboot:** compose already sets `restart: unless-stopped`, and
the Docker service is enabled, so the stack comes back after a reboot.

---

### Option B — AWS App Runner (fully managed, no server)

App Runner runs **one** container from an image. This app is two containers,
so the clean way is to publish each image to **ECR** and run wr-web as the App
Runner service, with wr-core as a second App Runner service that wr-web points
at. Simpler alternative: run just the engine on App Runner and host the static
UI on S3+CloudFront. For most people Option A or C is less fiddly than B.

Sketch:
```bash
# build & push both images to ECR
aws ecr create-repository --repository-name wr-core
aws ecr create-repository --repository-name wr-web
# docker build/tag/push each (see ECR "View push commands")
```
Then create an App Runner service from `wr-core`, note its URL, and set nginx's
`proxy_pass` to that URL when building `wr-web`. Set the API key as an App
Runner **environment variable** `WATERMARKS_SERVER_API_KEY` on the wr-core
service.

---

### Option C — ECS Fargate (managed, both containers together)

Run both containers in **one ECS task definition** (they share `localhost`),
front it with an Application Load Balancer.

1. Push both images to ECR (as in Option B).
2. Task definition with two containers:
   - `wr-core` — no published port, set env `WATERMARKS_SERVER_API_KEY` (pull
     it from **AWS Secrets Manager**, not plaintext).
   - `wr-web` — publishes 80; since both are in the same task, change nginx's
     `proxy_pass` to `http://127.0.0.1:8765/` (edit `deploy/nginx.conf` before
     building the image, because same-task containers reach each other on
     localhost, not by service name).
3. Service behind an ALB; ALB listener 443 with an ACM certificate for TLS.

This is the most "production" option and the most setup. For AWS credits and a
personal/small project, **Option A is the pragmatic choice.**

---

## Where every key / setting lives (summary)

| I want to… | Change this | Where |
| --- | --- | --- |
| Require an API key on the service | `WATERMARKS_SERVER_API_KEY` | `deploy/.env` (and paste same key in UI → Settings) |
| Change the public port | `WEB_PORT` | `deploy/.env` |
| Change the version shown in the header | `WATERMARKS_SERVER_VERSION` | `deploy/.env` |
| Point the UI at a different backend URL | "Service URL" field | UI → Settings (stored in the browser) |
| Change how nginx proxies / body-size limit | `location /api/`, `client_max_body_size` | `deploy/nginx.conf` |
| Enable optional GPU pixel removal / text rewrite | `HF_TOKEN`, `WATERMARKS_REWRITE_*` | upstream `compose.yaml` + root `.env` (advanced; see main README) |

On AWS, never bake the API key into an image. Use the platform's secret store:
- **EC2:** the `.env` file on the box (readable only by your user).
- **App Runner / ECS:** environment variable sourced from **AWS Secrets
  Manager** or **SSM Parameter Store**.

---

## Health checks

- Liveness: `GET /api/health` → `{"ok": true, "version": "..."}`
- Capabilities: `GET /api/capabilities` → which optional tools are active
- Use `/api/health` as the ALB / App Runner health check path.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| UI loads, status dot is red ("offline") | wr-core not up, or API key mismatch. Check `docker compose logs wr-core`. |
| `401` on API calls | The server has a key set but the UI doesn't (or vice-versa). Make them match in `.env` and UI Settings. |
| Large file upload fails | Raise `client_max_body_size` in `nginx.conf` and the engine's input cap (`WATERMARKS_MAX_INPUT_BYTES`). |
| Works locally, not on EC2 | Security-group inbound rule for port 80 missing. |
