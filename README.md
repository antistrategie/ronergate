# ROnergate

Discord bot for the Antistratégie server. 

## Run locally

```bash
uv venv --python 3.14
uv pip install -e ".[dev]"
cp .env.example .env  # fill in
ronergate
```

## Deploy

```bash
docker compose up -d
```

## Tests

```bash
pytest
```
