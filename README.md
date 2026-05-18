# ROnergate

Multi-server Discord bot. Each feature lives in its own cog. Currently:

- [Girldle](ronergate/cogs/girldle/README.md) — daily puzzle results scraper,
  Glicko-2 leaderboard, per-server channels, global or per-server view.

## Run locally

```bash
uv venv --python 3.14
uv pip install -e ".[dev]"
cp .env.example .env  # fill in DISCORD_TOKEN etc.
ronergate
```

## Tests

```bash
pytest
```

## Deploy

CI publishes `ghcr.io/antistrategie/ronergate:latest` on every push to `main`.

On the host, you only need two files: `docker-compose.yml` and `.env`.

```yaml
# docker-compose.yml
services:
  ronergate:
    image: ghcr.io/antistrategie/ronergate:latest
    container_name: ronergate
    restart: unless-stopped
    env_file: .env
    volumes:
      - ronergate-data:/app/data
    pull_policy: always

volumes:
  ronergate-data:
```

```bash
# .env (see .env.example)
DISCORD_TOKEN=your-bot-token
DB_PATH=/app/data/bot.sqlite
COGS=girldle
CONTROL_CHANNEL_ID=000000000000000000
```

To deploy a new version:

```bash
docker compose pull && docker compose up -d
```

## Running multiple bots from one image

Add another service with its own token and (optionally) a narrower cog list.
Both services share the SQLite volume so they share the leaderboard.

```yaml
services:
  ronergate:
    image: ghcr.io/antistrategie/ronergate:latest
    env_file: .env.ronergate
    volumes: [ronergate-data:/app/data]
  girldle:
    image: ghcr.io/antistrategie/ronergate:latest
    env_file: .env.girldle  # different token, COGS=girldle
    volumes: [ronergate-data:/app/data]
```
