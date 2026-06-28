# Deploying to your VPS (Ubuntu/Debian + Docker)

The stack runs as 3 containers via Docker Compose:
- **app** — FastAPI backend (+ Chromium for heatmaps) and serves `web/index.html`
- **db** — TimescaleDB (Postgres 16), loads `schema.sql` on first boot
- **caddy** — reverse proxy with automatic free HTTPS

---

## 0. Before you start (do these once)

1. **Rotate the leaked football-data key.** The old key was committed to git, so
   treat it as compromised: log in to football-data.org and generate a new key.
2. **Get a free domain** (needed for HTTPS):
   - Go to https://www.duckdns.org, sign in, create a subdomain (e.g. `injurypred`).
   - Set its IP to your VPS's public IP. You now have `injurypred.duckdns.org`.

---

## 1. Install Docker on the VPS

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER        # then log out/in so the group applies
docker --version && docker compose version
```

(Optional, recommended on 2 GB RAM — add 2 GB swap so Chromium never OOM-kills:)
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 2. Get the code onto the VPS

```bash
git clone <your-repo-url> injuryprediction
cd injuryprediction
```
(If the code isn't pushed to a git remote yet, do that from your machine first —
see "Committing the code" below.)

## 3. Configure secrets

```bash
cp .env.production.example .env
nano .env
```
Fill in: `DOMAIN` (your duckdns name), a strong `POSTGRES_PASSWORD` (put the SAME
password into `DATABASE_URL`), `FOOTBALL_DATA_API_KEY` (the NEW one), `BSD_TOKEN`,
and set `CORS_ORIGINS=https://<your-domain>`.

## 4. Open the firewall (if enabled)

```bash
sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw allow OpenSSH
```

## 5. Launch

```bash
docker compose up -d --build
docker compose logs -f app      # watch startup; Ctrl-C to stop watching
```

Visit `https://<your-domain>` — Caddy gets an HTTPS certificate automatically
(give it ~30 seconds the first time). Health check: `https://<your-domain>/health`.

---

## Updating after a change (anytime)

On your machine: edit code, commit, push. Then on the VPS:
```bash
git pull
docker compose up -d --build
```
Only the changed layers rebuild; downtime is a few seconds.

## Database notes

- `schema.sql` runs **only on first boot** (when the DB volume is empty).
- To re-seed from scratch (DESTROYS data): `docker compose down -v` then `up`.
- Your seed/backfill scripts read keys from the environment now. Run one like:
  ```bash
  docker compose exec app python ../scripts/seed_database.py
  ```

## Committing the code (from your machine, first time)

Only 3 files are currently tracked. Commit the real project:
```bash
git add backend web ml scripts schema.sql docker-compose.yml Caddyfile \
        backend/Dockerfile .dockerignore .env.production.example DEPLOY.md .gitignore
git commit -m "Add Dockerized deployment stack"
git push
```
`.env` stays out of git (it's gitignored) — never commit real secrets.
