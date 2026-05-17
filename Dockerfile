FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    DB_PATH=/app/data/bot.sqlite

COPY pyproject.toml ./
COPY ronergate ./ronergate

RUN uv pip install --system --no-cache .

RUN mkdir -p /app/data

CMD ["ronergate"]
