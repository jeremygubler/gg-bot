# Ein Image für bot, api und migrate; der Start-Befehl kommt aus docker-compose.yml.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Erst nur die Abhängigkeiten: Dieser Layer bleibt bei Code-Änderungen im Cache.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev --all-extras

# Schrift für die Rank-Card (Umlaute und viele Sprachen); ohne sie fällt Pillow auf seine eingebaute zurück.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --no-create-home ggbot
USER ggbot
