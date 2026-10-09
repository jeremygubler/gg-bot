# GG-Bot

Selbst gehosteter Discord-Bot als Ersatz für MEE6: Moderation, Automod, Logging, Leveling,
Rollen, Tickets, Benachrichtigungen und mehr, mit Web-Dashboard. Läuft per Docker Compose.

Architektur und Entscheidungen: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Stand: **Phase 1 (Fundament)**. Die Feature-Phasen folgen.

## Schnellstart

1. Anwendung im [Developer Portal](https://discord.com/developers/applications) anlegen,
   unter *Bot* den Token erzeugen.
2. Bot einladen: Scopes `bot` und `applications.commands` (Rechte siehe unten).
3. Konfigurieren und starten:

   ```bash
   cp .env.example .env      # DISCORD_TOKEN und POSTGRES_PASSWORD setzen
   docker compose up -d --build
   ```

   Der Dienst `migrate` legt das Schema an, danach starten `bot` und `api`.
   Das Dashboard-Gerüst ist unter `http://<host>:8080` erreichbar (Port: `WEB_PORT`).

4. Auf dem Server `/help` ausprobieren.

TLS und Domain liegen bewusst ausserhalb von Compose (dein vorhandener Reverse Proxy zeigt auf `WEB_PORT`).

## Befehle (Phase 1)

| Befehl | Wer | Wirkung |
|---|---|---|
| `/help` | alle | Zeigt die Befehle, die du nutzen darfst (nach Berechtigung gefiltert) |
| `/config language <de\|en>` | *Manage Server* | Sprache des Bots auf diesem Server |
| `/config timezone <name>` | *Manage Server* | Zeitzone des Servers (IANA, z. B. `Europe/Zurich`) |

Befehlsnamen sind englisch, Beschreibungen werden in Discord auf Deutsch und Englisch angezeigt.

## Einstellungen

Alle Einstellungen kommen aus der Umgebung (`.env`), siehe [.env.example](.env.example).
Pro Server gespeicherte Einstellungen (Modul `general`: `embed_color`) werden später im
Dashboard bearbeitet.

| Variable | Bedeutung |
|---|---|
| `DISCORD_TOKEN` | Bot-Token (wird nie geloggt) |
| `POSTGRES_*` | Zugang zur Datenbank; Compose baut daraus `DATABASE_URL` |
| `DEFAULT_LOCALE` | Startsprache neuer Server (`de`/`en`) |
| `LOG_LEVEL`, `LOG_FORMAT` | `json` (Standard, für Logsammler) oder `console` |
| `DEV_GUILD_ID` | Befehle sofort auf einer Test-Guild statt global (Entwicklung) |
| `SHARD_COUNT`, `SHARD_IDS` | Sharding; leer = automatisch in einem Prozess |

## Benötigte Bot-Rechte

Phase 1 braucht keine Server-Rechte ausser *Slash-Commands verwenden*. Gateway-Intents:
nur *Guilds* (nicht privilegiert). Spätere Phasen fügen Rechte und Intents hinzu und
nennen sie hier; *Server Members* und *Message Content* müssen dann im Developer Portal
freigeschaltet werden.

## Entwicklung

Voraussetzungen: Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL 16, Redis 7, Node 22.

```bash
uv sync --all-extras
export DATABASE_URL=postgresql+asyncpg://ggbot:ggbot@localhost:5432/ggbot
uv run alembic upgrade head

uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest          # braucht Postgres (DB ggbot_test) und Redis (DB 15)

cd web && npm ci && npm test && npm run build
```

Testdatenbanken lassen sich über `GGBOT_TEST_DATABASE_URL` und `GGBOT_TEST_REDIS_URL` umstellen.
Die Tests bauen das Schema über die echten Alembic-Migrationen auf.

### Struktur

```
src/ggbot/core   Settings, DB, Redis, Services, i18n  (strict typisiert, kein discord/fastapi)
src/ggbot/bot    Discord-Layer: Cogs, Fehlerbehandlung, Befehls-Sync
src/ggbot/api    FastAPI (Phase 1: /healthz)
web/             React-Dashboard (Phase 10)
migrations/      Alembic
```

Neue Befehle: Beschreibung mit `d("cmd.<name>.desc")` setzen und den Schlüssel in
`de.toml` **und** `en.toml` anlegen. Ein Test schlägt fehl, wenn eine Sprache fehlt.
