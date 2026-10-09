# GG-Bot

Selbst gehosteter Discord-Bot als Ersatz für MEE6: Moderation, Automod, Logging, Leveling,
Rollen, Tickets, Benachrichtigungen und mehr, mit Web-Dashboard. Läuft per Docker Compose.

Architektur und Entscheidungen: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Stand: **Phase 2 (Moderation)**. Die weiteren Feature-Phasen folgen.

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

## Befehle

Befehlsnamen sind englisch, Beschreibungen erscheinen in Discord auf Deutsch und Englisch.
Die Spalte *Wer* ist die Standard-Berechtigung; sie lässt sich in den Server-Einstellungen
(Integrationen → GG-Bot) pro Befehl und Rolle ändern. `/help` zeigt jedem nur, was er nutzen darf.
Antworten auf Moderationsbefehle sehen nur die ausführende Person (ephemer); `/lock` und `/unlock`
antworten öffentlich.

### Allgemein (Phase 1)

| Befehl | Wer | Wirkung |
|---|---|---|
| `/help` | alle | Befehle, die du nutzen darfst |
| `/config language <de\|en>` | *Manage Server* | Sprache des Bots auf diesem Server |
| `/config timezone <name>` | *Manage Server* | Zeitzone des Servers (IANA, z. B. `Europe/Zurich`) |

### Moderation (Phase 2)

Jede Aktion bekommt eine **Case-Nummer** (pro Server fortlaufend). Reihenfolge: Hierarchie und Rechte
prüfen → Betroffene per DM informieren (best effort) → Aktion ausführen → Case speichern.
Scheitert die Aktion bei Discord, entsteht kein Case.
Dauern schreibt man mit Einheit: `30m`, `2h`, `1d12h`, `1w`.

| Befehl | Wer | Wirkung |
|---|---|---|
| `/warn <member> [reason]` | *Moderate Members* | Verwarnung; zählt für die Eskalation |
| `/unwarn <case>` | *Moderate Members* | Verwarnung zählt nicht mehr (bleibt in der Historie) |
| `/timeout <member> <duration> [reason]` | *Moderate Members* | Timeout, 1 s bis 28 Tage |
| `/untimeout <member> [reason]` | *Moderate Members* | Timeout beenden |
| `/kick <member> [reason]` | *Kick Members* | Kick |
| `/ban <user> [reason] [delete_days]` | *Ban Members* | Bann, auch per ID für Nicht-Mitglieder; Nachrichten der letzten 0–7 Tage löschen |
| `/tempban <user> <duration> [reason] [delete_days]` | *Ban Members* | Bann mit automatischem Unban (1 Minute bis 365 Tage) |
| `/softban <member> [reason] [delete_days]` | *Ban Members* | Bann und sofortiger Unban, löscht 1–7 Tage Nachrichten |
| `/unban <user> [reason]` | *Ban Members* | Bann aufheben (setzt auch einen laufenden Tempban ausser Kraft) |
| `/case <number>` | *Moderate Members* | Einen Fall anzeigen |
| `/history <user> [page]` | *Moderate Members* | Alle Fälle eines Nutzers, mit aktiven Verwarnungen und Notizen |
| `/note add\|list\|remove` | *Moderate Members* | Private Moderator-Notizen zu einem Nutzer |
| `/purge <amount> [author] [contains] [bots] [attachments] [links] [embeds]` | *Manage Messages* | Löscht die letzten passenden Nachrichten (1–500, höchstens 1000 werden durchsucht). Nur Nachrichten unter 14 Tagen, angeheftete bleiben. Pro Server läuft nur ein Purge gleichzeitig |
| `/slowmode <seconds> [channel]` | *Manage Channels* | Slowmode, 0 = aus (max. 6 Stunden) |
| `/lock [channel]` / `/unlock [channel]` | *Manage Channels* | Sperrt den Kanal für @everyone. `/unlock` stellt den vorherigen Zustand exakt wieder her |

`contains` und `links` in `/purge` brauchen den *Message Content Intent* (siehe unten); ohne ihn
meldet der Befehl das, statt still nichts zu löschen.

**Eskalation und Einstellungen** (*Manage Server*):

| Befehl | Wirkung |
|---|---|
| `/modconfig escalation add <warns> <action> [duration]` | Bei **genau** `warns` aktiven Verwarnungen führt der Bot `timeout`, `kick`, `ban` oder `tempban` aus. Gleiche Anzahl ersetzt die Regel |
| `/modconfig escalation remove <warns>` / `list` | Regel entfernen / alle anzeigen |
| `/modconfig warn_expiry <days>` | Verwarnungen zählen nach so vielen Tagen nicht mehr (`0` = nie) |
| `/modconfig dm <on\|off>` | Betroffene per Direktnachricht informieren |

Beispiel: `escalation add 3 timeout 1h` und `escalation add 5 kick` ergibt: 3. Verwarnung → 1 h Timeout,
5. Verwarnung → Kick. Die Folgeaktion hat einen eigenen Case (Moderator: der Bot, Verweis auf die
auslösende Verwarnung). Scheitert sie (z. B. Rolle zu niedrig), bleibt die Verwarnung bestehen und
der Moderator bekommt die Fehlermeldung.

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
| `ENABLE_MESSAGE_CONTENT_INTENT` | `true` schaltet den privilegierten *Message Content Intent* ein (nur nach Freigabe im Developer Portal) |
| `DEV_GUILD_ID` | Befehle sofort auf einer Test-Guild statt global (Entwicklung) |
| `SHARD_COUNT`, `SHARD_IDS` | Sharding; leer = automatisch in einem Prozess |

## Benötigte Bot-Rechte

| Recht | Wofür |
|---|---|
| *Slash-Commands verwenden* | alle Befehle |
| *Kick Members* | `/kick`, Eskalation `kick` |
| *Ban Members* | `/ban`, `/tempban`, `/softban`, `/unban`, automatischer Unban |
| *Moderate Members* | `/timeout`, `/untimeout`, Eskalation `timeout` |
| *Manage Messages* + *Read Message History* | `/purge` |
| *Manage Channels* | `/slowmode` |
| *Manage Threads* | `/slowmode` in Threads |
| *Manage Roles* | `/lock`, `/unlock` (ändern die Kanalrechte von @everyone) |

**Rollen-Reihenfolge:** Die Rolle des Bots muss in der Rollenliste **über** den Rollen der Mitglieder
stehen, die er moderieren soll. Sonst verweigert Discord die Aktion und der Bot meldet das.

Gateway-Intents: *Guilds* (nicht privilegiert). Optional *Message Content* (privilegiert) für die
Purge-Filter `contains` und `links`: im Developer Portal unter *Bot → Privileged Gateway Intents*
aktivieren und `ENABLE_MESSAGE_CONTENT_INTENT=true` setzen. Ohne die Freigabe startet der Bot mit
`true` nicht. *Server Members* wird erst von späteren Phasen gebraucht.

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
