# GG-Bot – Architektur

Status: freigegeben, Phase 1 umgesetzt. Abweichungen vom ersten Entwurf sind in den Abschnitten 6 (Intents) und 10 (CI) vermerkt.

Selbst gehosteter Discord-Bot als MEE6-Ersatz, Multi-Guild, Docker Compose.
Konventionen: Deutsch in Kommentaren, Docstrings, Commits und Doku; Bezeichner Englisch.

## 1. Leitentscheidungen

| Thema | Entscheidung |
|---|---|
| Repo-Form | Ein Monorepo, ein Python-Paket `ggbot` mit Teilpaketen, plus `web/` |
| Bot ↔ Dashboard | **Gemeinsame DB + Redis Pub/Sub.** Kein direkter Aufruf Bot ↔ API |
| Prozessmodell | Ein Bot-Prozess, aber **shard-fähig**: kein Zustand im Prozessspeicher |
| Schichten | Discord-Layer → Service → Repository. Services kennen kein `discord` |
| Settings | Pydantic-Modell pro Modul, als JSONB gespeichert, Redis-Cache + Invalidierung |
| Zeit | Alles UTC in der DB, Zeitzone pro Server nur für Anzeige und Zeitpläne |
| Sprache | Deutsch und Englisch, Katalog in `core/i18n`, pro Server einstellbar |

## 2. Ordnerstruktur

```
gg-bot/
├── pyproject.toml              # ein Paket, Extras: bot, api, dev
├── alembic.ini
├── docker-compose.yml
├── .env.example
├── README.md                   # Befehle, Einstellungen, Bot-Rechte (pro Feature)
├── docs/
│   └── ARCHITECTURE.md
├── migrations/                 # Alembic, eine Revision pro Phase (mindestens)
├── src/ggbot/
│   ├── core/                   # mypy strict, KEIN discord.py-Import
│   │   ├── config.py           # Umgebung → Settings (pydantic-settings), Secrets nie im Log
│   │   ├── db/
│   │   │   ├── engine.py       # async Engine, Session-Factory
│   │   │   ├── models/         # SQLAlchemy 2.x, ein Modul pro Feature
│   │   │   └── repos/          # Repositories, jede Methode nimmt guild_id
│   │   ├── redis.py            # Client, Key-Helfer (Namensraum "gg:")
│   │   ├── events.py           # Pub/Sub: typisierte Events (siehe 5.)
│   │   ├── settings/
│   │   │   ├── schema.py       # Pydantic-Modelle pro Modul
│   │   │   └── store.py        # laden/speichern/cachen/invalidieren
│   │   ├── i18n/               # de.toml, en.toml, Übersetzer
│   │   ├── errors.py           # UserError-Hierarchie (freundlich, übersetzbar)
│   │   ├── logging.py          # JSON-Logging, Secret-Filter
│   │   ├── scheduler.py        # persistente Jobs (siehe 6.)
│   │   ├── ratelimit.py        # Redis-Cooldowns, Queue für Massenaktionen
│   │   └── services/           # Geschäftslogik pro Feature, ohne Discord
│   │       ├── moderation.py  automod.py  leveling.py  tickets.py  ...
│   ├── bot/                    # Discord-Layer
│   │   ├── main.py             # Bot-Start, Cog-Laden, Shard-Konfiguration
│   │   ├── cogs/               # ein Cog pro Feature, dünn: parsen → Service → antworten
│   │   ├── views/              # persistente Views (custom_id-basiert)
│   │   ├── checks.py           # Berechtigung + Rollen-Hierarchie
│   │   ├── errors.py           # globaler Error-Handler (ephemer, nie Stacktrace)
│   │   ├── translator.py       # discord.app_commands.Translator → core/i18n
│   │   └── cache.py            # Guild-Snapshots (Kanäle/Rollen) nach Redis
│   ├── api/                    # FastAPI
│   │   ├── main.py
│   │   ├── auth/               # Discord-OAuth2, Session, Manage-Server-Check
│   │   ├── routers/            # ein Router pro Modul + public (Leaderboard)
│   │   └── deps.py             # Guild-Zugriff als Dependency
│   └── render/                 # Pillow: Rank-Card, Welcome-Card (rein, ohne Discord)
├── web/                        # React + Vite + TS + Tailwind
│   └── src/{pages,modules,components,api}
├── tests/
│   ├── unit/                   # Services, Settings, Render (ohne Discord, ohne DB wo möglich)
│   ├── integration/            # echte Postgres+Redis (CI-Services)
│   └── bot/                    # Cogs mit Fake-Interaction
└── .github/workflows/ci.yml
```

**Regel für Abhängigkeiten:** `bot` und `api` importieren `core`, nie einander.
`core` importiert weder `discord` noch `fastapi`. Ein Import-Linter-Check in der CI
erzwingt das (ruff `TID251` bzw. `import-linter`).

## 3. Schichten und Modulgrenzen

```
Discord-Event / Slash-Command
        │
   Cog (bot/cogs)     parst Eingabe, ruft Service, formatiert Antwort
        │
   Service (core/services)   Geschäftslogik, reine Datentypen rein/raus
        │
   Repository (core/db/repos)   SQL, immer mit guild_id
        │
   PostgreSQL / Redis
```

- Services bekommen **einfache Datentypen** (`int` für IDs, Dataclasses), nie `discord.Member`.
  Dinge, die Discord brauchen (Rolle vergeben, Nachricht senden), gibt der Service als
  **Aktionen zurück** (`ModerationResult(actions=[TimeoutMember(...), SendDM(...)])`)
  oder nutzt einen schmalen Port (`Protocol`), den die Cogs implementieren.
  Dadurch ist die Logik ohne Discord testbar.
- Berechtigungs- und Hierarchieprüfung liegt in `bot/checks.py` (Discord-Wissen),
  die Regelentscheidung (z. B. „darf Mod X Ziel Y sanktionieren“) bekommt der Service
  als Eingabe (`actor_top_role_pos`, `target_top_role_pos`, `is_owner`).
- Jeder Repository-Aufruf hat `guild_id` als **erstes Pflichtargument**. Tabellen mit
  Guild-Bezug haben `guild_id` im Primär- oder Unique-Key und einen Index darauf.

## 4. Datenmodell (Überblick)

Alle Tabellen: `id` (bigint, Snowflake oder Identity), `guild_id bigint NOT NULL`,
`created_at timestamptz`. Discord-IDs sind `bigint` (64 Bit, ohne Vorzeichenproblem
bei Snowflakes < 2^63).

| Phase | Tabellen |
|---|---|
| 1 | `guilds` (id, locale, timezone, joined_at, left_at), `guild_settings` (guild_id, module, data JSONB, version, updated_at), `dashboard_audit` (guild_id, user_id, module, before, after, at), `scheduled_jobs` |
| 2 | `mod_cases` (guild_id, case_no, type, target_id, moderator_id, reason, duration_seconds, expires_at, active, ref_case_no, created_at; Unique `(guild_id, case_no)`), `mod_case_counters` (atomare Case-Nummer pro Server), `mod_notes`, `channel_locks` (vorheriger Overwrite-Zustand für `/unlock`). Eskalationsregeln liegen in den Settings (Modul `moderation`) |
| 3 | Regeln liegen in `guild_settings` (Modul `automod`); `automod_hits` (Statistik, optional, mit Aufbewahrungsfrist) |
| 4 | Nur Settings (Kanal pro Event-Typ); `invite_snapshots` für Invite-Tracking |
| 5 | `xp_members` (guild_id, user_id, xp, level, msg_count, voice_seconds, last_xp_at), `xp_voice_sessions`, `level_rewards` in Settings, `rank_card_styles` (guild_id, user_id, JSONB) |
| 6 | `role_panels`, `role_panel_items`, `reaction_roles`, Willkommen in Settings |
| 7 | `ticket_panels`, `ticket_categories`, `tickets`, `ticket_messages` (nur für Transkript), `ticket_feedback`, `ticket_transcripts` |
| 8 | `notify_subscriptions` (Twitch/YouTube), `notify_state` (laufende Streams, Message-ID) |
| 9 | `custom_commands`, `scheduled_messages`, `giveaways`, `giveaway_entries`, `polls`, `poll_votes`, `reminders`, `stat_channels`, `starboard_entries`, `birthdays` |
| 10 | `dashboard_sessions` (falls Server-Sessions, siehe 9.), keine weiteren Fachtabellen |

Migrationen: Alembic, **eine oder mehrere Revisionen pro Phase**, jede mit `downgrade`.
Die CI prüft `alembic upgrade head` → `downgrade base` → `upgrade head` auf leerer DB.

### Settings

- `guild_settings` hält pro `(guild_id, module)` eine JSONB-Zeile, validiert durch das
  Pydantic-Modell des Moduls. Neue Felder haben Defaults, deshalb sind Schema-Erweiterungen
  abwärtskompatibel. Ein `version`-Feld erlaubt bei Bedarf Migrationen alter JSON-Stände.
- Lesen: `SettingsStore.get(guild_id, ModuleSettings)` → Redis (`gg:settings:{guild}:{module}`,
  TTL 1 h) → bei Miss DB → Cache füllen.
- Schreiben (Dashboard oder Bot-Befehl): validieren → DB in Transaktion → Cache löschen →
  Event `settings.changed` publizieren. Alle Bot-Prozesse löschen daraufhin lokale Caches.

## 5. Bot ↔ Dashboard-Kommunikation

Der Bot und die API laufen getrennt und sprechen **nie direkt miteinander**.

```
 Browser ── HTTPS ──► web (nginx, statische SPA, /api → api)
                         │
                       api (FastAPI) ──► PostgreSQL ◄── bot
                         │                               │
                         └──────► Redis ◄────────────────┘
                                  Pub/Sub · Cache · Cooldowns · Snapshots
```

**Kanäle** (Redis Pub/Sub, JSON, versioniertes Schema in `core/events.py`):

| Event | Von → An | Wirkung |
|---|---|---|
| `settings.changed {guild_id, module}` | api → bot | Bot verwirft Cache, lädt neu |
| `panel.publish {guild_id, panel_id}` | api → bot | Bot postet/aktualisiert Panel-Nachricht (Tickets, Rollen) |
| `guild.sync {guild_id}` | api → bot | Bot erneuert Guild-Snapshot |
| `cards.preview` | – | **nicht nötig**, siehe unten |

**Live-Daten für das Dashboard** (Kanäle, Rollen, Emojis für die Auswahlfelder): Der Bot
schreibt bei `on_guild_join`, Kanal-/Rollenänderungen und beim Start einen **Snapshot**
nach Redis (`gg:snapshot:{guild_id}`, nur ID, Name, Typ, Position, Farbe). Die API liest
nur diesen Snapshot. So braucht die API weder Bot-Token noch Gateway-Verbindung.

**Aktionen, die Discord brauchen** (Panel posten, Test-Willkommensnachricht senden):
Die API legt eine Zeile in `scheduled_jobs` bzw. publiziert ein Event; der Bot führt aus
und schreibt das Ergebnis zurück (Status-Feld, das das Dashboard pollt).
Fällt der Bot aus, bleibt der Job in der DB und läuft nach dem Neustart.

**Live-Vorschau:** Embeds rendert das Frontend selbst (reines HTML/CSS). Rank- und
Welcome-Cards rendert die API über denselben `ggbot.render`-Code wie der Bot, mit
Beispieldaten und den aktuellen Entwurfs-Einstellungen. Der Bot ist dafür nicht beteiligt.

**Vertrauensgrenze:** Das Dashboard prüft Rechte selbst (siehe 9.) und schreibt direkt in
die DB. Der Bot vertraut der DB, nicht dem Event: das Event ist nur ein Hinweis zum
Neuladen, die Daten sind bereits validiert.

## 6. Hintergrundarbeit und Sharding

**Kein Prozessspeicher-Zustand** für Fachlogik. Konkret:

- **Cooldowns, Spam-Zähler, Duplikat-Erkennung, Join-Rate, XP-Cooldown:** Redis
  (`INCR`/`EXPIRE`, Sorted Sets für Fenster).
- **Zeitgesteuerte Aktionen** (Tempban-Ende, Erinnerungen, Giveaway-Ende, geplante
  Nachrichten, Ticket-Auto-Close, Slowmode-Rücknahme): Tabelle `scheduled_jobs`
  (`id, guild_id, kind, run_at, payload JSONB, state, claimed_by, claimed_until, attempts`).
  Der Scheduler holt fällige Jobs mit `SELECT … FOR UPDATE SKIP LOCKED`, markiert sie
  als geclaimt, führt sie aus und setzt sie auf `done` oder `failed` (mit Retry/Backoff).
  Das überlebt Neustarts und funktioniert mit mehreren Shards ohne Doppelausführung.
- **Massenaktionen** (Purge, Rollenvergabe an viele, Bulk-Ban bei Raid): `core/ratelimit.py`
  stellt eine Queue mit festem Durchsatz bereit. discord.py hält die HTTP-Limits ein, die
  Queue verhindert zusätzlich, dass ein einzelner Befehl alle anderen Guilds blockiert.
- **Keine API-Calls pro Nachricht ohne Cache:** Settings kommen aus dem Cache, Mitglieder
  aus dem discord.py-Cache (`Intents.members` aktiv), Automod-Zähler aus Redis. Regeln,
  die nichts zu tun haben (Modul aus), werden vor jeder Arbeit abgebrochen.
- **Sharding:** `AutoShardedBot` mit Shard-Anzahl aus der Umgebung (Standard: automatisch,
  ein Prozess). Wer mehrere Prozesse will, setzt `SHARD_IDS`/`SHARD_COUNT`. Nichts im Code
  nimmt an, dass alle Guilds im selben Prozess liegen.
- **Gateway-Intents:** Der Bot fordert nur an, was die jeweilige Phase braucht. Phase 1:
  `guilds` (und `emojis_and_stickers` für den Snapshot), beides nicht privilegiert. Später
  kommen dazu: `members`, `message_content` (privilegiert, im Developer Portal freischalten),
  `guild_messages`, `voice_states`, `guild_scheduled_events`, `moderation`, `invites`,
  `guild_reactions`. Jede Phase nennt ihre Intents im README. So startet der Bot schon
  vor der Freischaltung, statt mit `PrivilegedIntentsRequired` abzubrechen.

## 7. Querschnittsthemen

**Fehlerbehandlung.** `core.errors.UserError(key, **params)` ist der einzige Fehler, der
dem Nutzer gezeigt wird; er trägt einen i18n-Schlüssel. Der globale Handler in
`bot/errors.py` macht daraus eine ephemere Antwort in der Server-Sprache. Jeder andere
Fehler wird als JSON mit Fehler-ID geloggt, der Nutzer sieht „Etwas ist schiefgelaufen
(Fehler-ID abc123)“, nie einen Stacktrace.

**Berechtigungen.** Jeder Befehl setzt `default_permissions` (sinnvolle Vorgabe für
Server-Admins, überschreibbar in Discord) **und** prüft zur Laufzeit: Bot-Rechte vorhanden,
Rollen-Hierarchie (Akteur > Ziel, Bot > Ziel, Ziel nicht Owner). Fehlende Bot-Rechte
werden mit dem Namen des Rechts gemeldet.

**Persistente Views.** Jede Component-`custom_id` hat das Schema
`gg:{modul}:{aktion}:{id}`. Beim Start registriert der Bot pro View-Klasse eine
`DynamicItem`/persistente View; der Zustand kommt aus der DB, nicht aus dem Objekt.
Tickets, Rollen-Panels, Giveaways und Umfragen überleben so Neustarts.

**i18n.** TOML-Kataloge `de.toml`/`en.toml`, Schlüsselparität wird per Test geprüft.
Slash-Command-Namen und -Beschreibungen laufen über einen `app_commands.Translator`
(DE/EN-Localizations). Antworten nutzen die Server-Sprache aus `guilds.locale`;
Befehlsnamen bleiben englisch (stabile Namen für Berechtigungen und Doku).

**Logging und Secrets.** `structlog` mit JSON-Renderer auf stdout. Ein Filter maskiert
Token-Muster und Werte aus `SecretStr`-Feldern. Secrets nur aus Umgebungsvariablen /
Docker-Secrets, `.env` ist in `.gitignore`, `.env.example` enthält nur Platzhalter.

**Zeit.** `timestamptz` in der DB, `datetime` immer timezone-aware. Pro Guild eine
`timezone` (IANA) für Geburtstage, geplante Nachrichten und Anzeige. Services bekommen
`now` als Parameter bzw. eine `Clock`-Abstraktion, damit Zeitlogik testbar ist.

**Rendering.** Pillow-Arbeit läuft in `asyncio.to_thread`, Ergebnisse von Rank-Cards
werden kurz gecacht (Schlüssel: Nutzer, XP, Style-Hash). Schriftarten und Hintergründe
liegen in `src/ggbot/render/assets/` mit Lizenzhinweis.

**Transkripte (Phase 7).** HTML-Dateien werden beim Schliessen erzeugt und auf einem
Docker-Volume (`/data/transcripts`) abgelegt; die DB hält nur Pfad, Grösse und Hash.
Zugriff nur über die API mit Berechtigungsprüfung bzw. als Anhang im Log-Kanal.

## 8. Phasen und ihre Bausteine

| Phase | Inhalt | Wichtigste Architekturteile |
|---|---|---|
| 1 | Fundament | `core/*`, Settings-Store, Error-Handler, Logging, `/help`, Compose, CI |
| 2 | Moderation | `mod_cases`, Moderation-Service, Scheduler (Tempban), Eskalation |
| 3 | Automod | Regel-Pipeline im Service, Redis-Zähler, Anti-Raid |
| 4 | Logging | Event-Router im Cog, Kanal pro Event-Typ, Bulk-Delete als Datei |
| 5 | Leveling | XP-Service, Voice-Sessions, `render/rank_card.py`, MEE6-Import |
| 6 | Rollen & Willkommen | Panel-Views, `render/welcome_card.py`, Autorolle mit Screening |
| 7 | Tickets | Panels, Modals, Transkript-Generator, Auto-Close-Jobs |
| 8 | Benachrichtigungen | Provider-Interface (`TwitchProvider`, `YouTubeProvider`), EventSub + Polling |
| 9 | Engagement | Custom-Command-Interpreter (Variablen/Bedingungen, sandboxed, ohne `eval`) |
| 10 | Dashboard | FastAPI-Router je Modul, React-Module, Audit-Log, öffentliches Leaderboard |

Das Dashboard (Phase 10) ist kein Umbau: Weil Settings von Anfang an typisiert und in der DB
liegen, ergänzt Phase 10 nur API und Frontend. Das JSON-Schema für die Formulare kommt aus den
Pydantic-Modellen (`model_json_schema()`), damit Bot und Dashboard nie auseinanderlaufen.

## 9. Dashboard-Backend

- **Login:** Discord OAuth2 (Scopes `identify guilds`). Nach dem Callback legt die API eine
  Session an: signiertes HttpOnly-Cookie (`SameSite=Lax`, `Secure`) mit Session-ID, Daten in
  Redis (TTL 7 Tage). Kein JWT im Browser. Das erlaubt serverseitiges Abmelden und hält Tokens
  vom Frontend fern. Discord-Access-Token wird nur serverseitig gehalten.
- **Zugriff pro Server:** `Manage Server` laut `guilds`-Antwort (`permissions & 0x20`) oder
  Owner, **und** der Bot ist auf dem Server. Die Prüfung läuft als FastAPI-Dependency bei
  *jeder* Guild-Route, nicht nur beim Login; Berechtigungen werden kurz gecacht (60 s).
- **CSRF:** Cookie `SameSite=Lax` plus Pflicht-Header `X-Requested-With` bzw. Double-Submit-Token
  bei schreibenden Routen.
- **Audit-Log:** Jede Änderung über das Dashboard schreibt in `dashboard_audit`
  (Nutzer, Modul, Vorher/Nachher als JSON-Diff).
- **Öffentliches Leaderboard:** Unauthentifizierte Route `GET /public/{guild_id}/leaderboard`,
  nur wenn der Server es im Dashboard aktiviert hat; Rate-Limit pro IP, keine privaten Felder.

## 10. Betrieb

Docker Compose mit fünf Dauerdiensten (bot, api, web, postgres, redis) und einem einmaligen Migrationsjob:

| Dienst | Image / Inhalt | Healthcheck |
|---|---|---|
| `postgres` | postgres:16 | `pg_isready` |
| `redis` | redis:7 | `redis-cli ping` |
| `migrate` | Bot-Image, `alembic upgrade head`, einmalig | Exit-Code |
| `bot` | Python 3.12 | Heartbeat-Key in Redis, den ein kleines Skript prüft |
| `api` | Python 3.12, uvicorn | `GET /healthz` (prüft DB + Redis) |
| `web` | nginx mit gebauter SPA, Proxy `/api` | `GET /` |

`bot` und `api` starten erst nach erfolgreichem `migrate`. Volumes: `pgdata`, `transcripts`.
Backups sind Aufgabe des Betreibers; das README beschreibt ein `pg_dump`-Beispiel.

**CI (GitHub Actions):** `ruff check`, `ruff format --check`, `mypy` (strict für `core/`,
Standard für den Rest), `pytest` mit Postgres- und Redis-Service-Containern,
Migrations-Roundtrip, Frontend: `tsc --noEmit`, `vitest`, `vite build` (ESLint kommt mit dem echten Dashboard in Phase 10),
Compose-Smoke-Test (Images bauen, `up --wait`, `/api/healthz` über nginx).

## 11. Teststrategie

- **Services:** reine Unit-Tests, Zeit und Zufall als Parameter, Randfälle (Hierarchie,
  Grenzwerte der Eskalation, XP-Kurve, Zeitzonen, abgelaufene Tempbans).
- **Repositories:** Integrationstests gegen echtes Postgres (kein SQLite-Ersatz, damit JSONB
  und `SKIP LOCKED` wirklich getestet werden). Test: Guild-Isolation, d. h. Daten von Guild A
  sind nie über Guild B erreichbar.
- **Cogs:** dünn gehalten; getestet mit Fake-Interactions, dass Berechtigungsfehler und
  `UserError` als ephemere Antwort ankommen.
- **Render:** Snapshot-Tests (Hash/Pixelvergleich mit Toleranz) für Rank- und Welcome-Card.
- **i18n:** Test auf identische Schlüsselmengen und Platzhalter in DE/EN.

## 12. Offene Punkte (bitte entscheiden)

Alles Folgende ist mit Standardwert vorbelegt und gilt, wenn du nichts anderes sagst.

1. **Paketmanager:** `uv` mit `pyproject.toml` und Lockfile. Alternative: pip-tools.
2. **Frontend-Server:** nginx im `web`-Container, der `/api` an `api` weiterreicht.
   Alternative: FastAPI liefert die SPA selbst aus (ein Container weniger).
3. **Reverse Proxy / TLS:** liegt ausserhalb von Compose (dein vorhandener Proxy im Homelab).
   Compose exponiert nur `web` auf einem konfigurierbaren Port.
4. **Slash-Command-Sync:** global beim Start, nur bei geändertem Command-Hash.
   Für Entwicklung optional auf eine Test-Guild beschränkt (`DEV_GUILD_ID`).
5. **Twitch ohne öffentliche URL (Phase 8):** Polling-Fallback alle 60 s für bis zu 100
   Streamer pro Request; EventSub-Webhook nur, wenn `PUBLIC_BASE_URL` gesetzt ist.
6. **MEE6-Import (Phase 5):** über die öffentliche Leaderboard-API von MEE6, die Server
   freigeben müssen; ob sie zum Zeitpunkt der Umsetzung noch erreichbar ist, prüfe ich dann
   und melde es, bevor ich baue.
7. **Datenschutz:** Nachrichteninhalte werden nur für Logging-Features (Phase 4) und Tickets
   (Phase 7) verarbeitet und nur dort gespeichert; der Bot speichert keine allgemeinen
   Nachrichtenverläufe. Aufbewahrungsfristen sind pro Server einstellbar. `/privacy` zeigt
   Nutzern, was gespeichert ist, und erlaubt das Löschen ihrer XP-/Geburtstagsdaten.

## 13. Moderation (Phase 2)

- **Ablauf einer Aktion:** Berechtigung/Hierarchie (Cog) → DM an das Ziel (best effort) → Discord-Aktion →
  Case speichern. Schlägt die Discord-Aktion fehl, entsteht **kein** Case. Der Service spricht Discord nur über
  den Port `ModerationPort` an, den der Cog implementiert; so ist die Logik ohne Discord testbar.
- **Case-Nummern:** pro Server lückenlos aufsteigend, vergeben über `mod_case_counters`
  (`INSERT … ON CONFLICT DO UPDATE … RETURNING`), also auch bei parallelen Aktionen eindeutig.
- **Tempban:** Case (`active`) + Job `mod.unban` in derselben Transaktion. Der Job prüft beim Ausführen, ob der
  Case noch aktiv ist; ein manuelles `/unban` setzt ihn nur auf inaktiv. Der automatische Unban erzeugt einen
  eigenen Case (`unban`, Moderator = Bot, `ref_case_no` zeigt auf den Tempban).
- **Eskalation:** Nach jeder Verwarnung zählt der Service die aktiven Verwarnungen (optional nur innerhalb von
  `warn_expiry_days`). Eine Regel feuert genau dann, wenn die Anzahl ihrem Schwellwert **entspricht**. Die
  Folgeaktion läuft als Bot mit eigenem Case (`ref_case_no` = auslösende Verwarnung). Scheitert sie, bleibt die
  Verwarnung bestehen und das Ergebnis meldet den Fehler.
- **Massenaktionen:** `core/bulk.py`: Chunking und ein Gate pro Server (eine Massenaktion zugleich, sonst
  freundlicher Fehler). `/purge` löscht nur Nachrichten, die jünger als 14 Tage sind (Bulk-Delete-Limit von Discord).
- **Lock/Unlock:** `/lock` merkt sich den vorherigen `send_messages`-Overwrite von @everyone, `/unlock` stellt ihn
  exakt wieder her (auch „explizit erlaubt“ bleibt erlaubt).
