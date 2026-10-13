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
| 3 | Regeln liegen in `guild_settings` (Modul `automod`); `automod_hits` (guild_id, user_id, channel_id, rule, actions, created_at; Statistik, 30 Tage Aufbewahrung) |
| 4 | Keine eigenen Tabellen: Einstellungen (Modul `logging`, Kanal pro Ereignisart). Nachrichteninhalte werden **nicht** in der Datenbank gespeichert |
| 5 | `xp_members` (guild_id, user_id, xp, msg_count, voice_seconds, last_xp_at; **kein** gespeichertes Level), `rank_card_styles` (guild_id, user_id, JSONB). Belohnungen, Faktoren und Ausnahmen liegen in den Settings (Modul `leveling`). Keine `xp_voice_sessions`, siehe Abschnitt 16 |
| 6 | `role_panels` (guild_id, channel_id, message_id, kind button\|select\|reaction, mode, title, description, external), `role_panel_items` (panel_id → CASCADE, role_id, label, emoji, emoji_key, description, position; Unique `(panel_id, role_id)` und `(panel_id, emoji_key)`). Reaktions-Rollen sind Panels der Art `reaction`, keine eigene Tabelle. Willkommen, Abschied und Autorollen liegen in den Settings (Modul `welcome`) |
| 7 | `ticket_panels`, `ticket_categories` (Support-Rollen, Fragen, Begrüssung als JSONB), `ticket_counters` (Nummer je Server), `tickets` (Kategorie-Name und Support-Rollen als Kopie, damit sie das Löschen der Kategorie überleben), `ticket_transcripts` (HTML), `ticket_feedback`. Keine `ticket_messages`: das Transkript entsteht beim Schliessen aus dem Kanalverlauf, siehe Abschnitt 18 |
| 8 | `notify_subscriptions` (guild_id, provider, ref, Zielkanal, Vorlage, Rolle; **dazu der Zustand pro Abo:** `last_key`, `live`, `misses`, `seen`, `message_id`). Kein getrenntes `notify_state`: der Zustand gehört zum Abo, so ist "Zustand ändern" eine Zeile und atomar, siehe Abschnitt 19 |
| 9a | `custom_commands`, `giveaways`, `giveaway_entries`, `reminders`. **Keine** `polls`/`poll_votes`: Umfragen sind native Discord-Umfragen, siehe Abschnitt 20 |
| 9b | `starboard_entries`, `starboard_votes`, `birthdays` (nur Tag und Monat), `stat_channels`, `scheduled_messages`; dazu `/privacy` zum Einsehen und Löschen, siehe Abschnitt 21 |
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
- **Öffentliches Leaderboard** (umgesetzt in Abschnitt 24): Unauthentifizierte Route `GET /public/{guild_id}/leaderboard`,
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
7. **Datenschutz:** Nachrichteninhalte liegen nur im Arbeitsspeicher des Bots (Phase 4, Event-Log) bzw. in Ticket-Transkripten (Phase 7); der Bot speichert keine allgemeinen
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

## 14. Automod (Phase 3)

- **Aufbau:** Erkennung rein in `core/automod/` (`text.py` Normalisierung, `rules.py` Prüfungen), Entscheidung und
  Zähler in `core/services/automod.py`, Umsetzung (löschen, verwarnen, Timeout, Kick) im Cog. Verwarnen und Timeout
  laufen über den `ModerationService` mit dem Bot als Moderator: Cases, DM und Eskalation gelten also auch hier.
- **Intents:** Nachrichten-Regeln brauchen *Message Content*, Beitritts-Regeln *Server Members* (beide privilegiert,
  beide per Umgebungsvariable zuschaltbar). Fehlt ein Intent, **verweigert** `/automod enable` die betroffenen Regeln,
  `/automod status` zeigt die Lücke, und der Start loggt eine Warnung. Es gibt keinen Zustand, in dem eine Regel an
  ist, aber nichts sieht.
- **Regeln und Reihenfolge:** Bei mehreren Treffern entscheidet die spezifischste Regel:
  `words > invites > links > mentions > caps > duplicates > spam`. Spam- und Duplikat-Zähler laufen für jede
  Nachricht weiter, auch wenn eine andere Regel zuerst greift.
- **Zähler:** Redis-Sorted-Sets pro (Server, Nutzer) bzw. (Server, Nutzer, Text-Hash), Zeitfenster über den Score.
  Nach einem Treffer wird die gelöschte Welle aus dem Zähler genommen, sonst löste jede weitere Nachricht erneut aus.
  Bearbeitungen prüfen nur den Inhalt und zählen nicht zur Rate.
- **Bestrafen höchstens einmal je Welle:** Löschen passiert bei jeder Nachricht, Verwarnen/Timeout höchstens einmal
  pro (Nutzer, Regel) und 30 Sekunden (`SET NX EX`). Ebenso der Hinweis im Kanal und der Eintrag im Meldekanal.
- **Wortfilter:** Einträge sind `word` (ganze Wörter/Wortfolgen), `wildcard` (`*`, `?` je Wort) oder `regex`. Alle
  Muster laufen mit **RE2** (`google-re2`): garantiert lineare Laufzeit, daher kann ein Nutzer-Muster den Bot nicht
  einfrieren. Dafür gibt es keine Lookarounds und Rückreferenzen. Muster werden beim Anlegen geprüft; eines, das auf
  leeren Text passt (`a*`, `*`), wird abgelehnt, weil es jede Nachricht träfe. Texte werden vor dem Vergleich
  normalisiert (NFKD, ohne Akzente und unsichtbare Zeichen, kleingeschrieben).
- **Links:** Hostnamen werden aus URLs gelesen (Zugangsdaten-Trick `gut.de@boese.com` zählt als `boese.com`);
  eine Domain trifft sich selbst und ihre Subdomains, nicht `evilexample.com`. Ohne `http://`/`www.` zählt `wort.wort`
  nur mit bekannter Endung, sonst wäre ein vergessenes Leerzeichen nach einem Punkt schon ein Link.
- **Anti-Raid:** Beitritte pro Server in einem Sorted-Set. Ab `joins` Beitritten innerhalb `seconds` beginnt der
  Raid-Modus (Redis-Schlüssel mit Ablauf, `SET NX` entscheidet bei gleichzeitigen Beitritten, wer ihn auslöst). Die
  ganze Welle bekommt die Aktion, danach jeder weitere Beitritt, bis der Modus abläuft oder `/automod raid end`.
  Aktionen laufen in Fünferblöcken mit Pause. Die Warnung geht in den Meldekanal, sonst in den Systemkanal.
- **Konto-Alter:** Konten jünger als `min_days` bekommen die Aktion (genau `min_days` ist alt genug). Bots sind ausgenommen.
- **Statistik:** `automod_hits`, Aufräumen höchstens stündlich beim Schreiben (Redis-Sperre), ohne eigenen Job.
- **Prozess-Cache:** Die Einstellungen werden pro Nachricht gebraucht und deshalb 5 s im Prozess gehalten; das
  `settings.changed`-Event und jeder Befehl leeren ihn sofort.

## 15. Event-Log (Phase 4)

- **Aufbau:** `core/logs/entries.py` formatiert Einträge aus reinen Daten (Schnappschüsse, Diffs), `core/services/eventlog.py`
  entscheidet, ob und wohin ein Ereignis geht, der Cog `eventlog` übersetzt Discord-Objekte und Ereignisse. Der Sender
  (`bot/eventlog_send.py`) schreibt in die Kanäle.
- **17 Ereignisarten, je ein eigener Kanal:** message_delete/edit/bulk, member_join/leave/update/ban/unban,
  role_create/delete/update, channel_create/delete/update, voice, invite_create/delete. Eine Art ohne Kanal ist aus.
  Ein Kick wird als Variante von `member_leave` gezeigt (Audit-Log-Abgleich), nicht als eigene Art.
- **Keine Nachrichten in der Datenbank.** Inhalt gelöschter/bearbeiteter Nachrichten stammt aus dem Nachrichten-Cache
  des Bots (`MESSAGE_CACHE_SIZE`, Standard 5000, nur im Speicher). Ist eine Nachricht nicht (mehr) im Cache, etwa nach einem
  Neustart, steht im Log ehrlich „Inhalt nicht mehr bekannt“, nichts Erfundenes. Das ist die Abwägung gegen ein
  Redis-/DB-Archiv: kein Vorrat privater Nachrichten, dafür Lücken nach Neustarts.
- **Intents:** Nachrichten-Arten brauchen *Message Content*, `member_join/leave/update` brauchen *Server Members*.
  `/logging set` verweigert eine einzelne Art ohne den Intent; bei `all` werden solche Arten übersprungen und genannt;
  `/logging status` markiert bereits gesetzte, aber blinde Arten. `voice_states`, `invites` und `moderation` sind nicht
  privilegiert und immer an.
- **Wer war es?** Für Ban/Unban, Kick, Rollen, Kanäle und Mitgliedsänderungen fragt der Cog das Audit-Log ab, nach 1 s Pause
  (der Eintrag entsteht kurz nach dem Ereignis), nur für Einträge der letzten 20 s und höchstens drei Abfragen gleichzeitig.
  Ohne das Recht *Audit-Log ansehen* fehlt die Angabe, es wird nichts geraten. Die Abfrage läuft erst, nachdem feststeht, dass
  die Art überhaupt an ist. Nachrichten-Löschungen haben keinen Ausführenden: das Audit-Log fasst sie nur gesammelt zusammen.
- **Rauschen vermeiden:** Umsortieren von Rollen/Kanälen, Stummschalten in Sprachkanälen und reine Link-Vorschau-
  Bearbeitungen ergeben keinen Eintrag. Das Löschen in einem Log-Kanal selbst wird nicht protokolliert (Rückkopplung).
  Bots werden bei Nachrichten und Sprache standardmässig weggelassen.
- **Massenlöschungen als Datei:** Bulk-Delete (auch durch `/purge`) erzeugt einen Eintrag mit einer Textdatei aller bekannten
  Nachrichten (Zeit, Autor, Inhalt, Anhänge), unbekannte nur als ID.
- **Flut-Schutz:** Pro Kanal ein Worker mit höchstens 100 wartenden Einträgen. Was darüber hinaus kommt (Raid, Massenaktionen),
  wird verworfen und am Ende in einer Zeile gemeldet. Fehlt das Schreibrecht, wird der Rest verworfen statt endlos versucht.

## 16. Leveling (Phase 5)

- **Kurve:** MEE6-kompatibel: von Level n zu n+1 braucht man 5·n² + 50·n + 100 XP. Das Level wird immer aus den XP
  berechnet (`core/leveling/xp.py`, Binärsuche, Obergrenze Level 1000), nie gespeichert: es gibt keine zweite Wahrheit,
  die nach einem Import oder einer Korrektur auseinanderlaufen könnte. Menge (Standard 15–25), Cooldown (60 s) und
  globaler Faktor sind pro Server einstellbar, die Kurve selbst nicht.
- **Atomare Vergabe:** `xp = xp + :betrag` in der Datenbank (`INSERT … ON CONFLICT DO UPDATE … RETURNING`), begrenzt auf
  0 … MAX_XP. Parallele Gewinne gehen nicht verloren, das Level davor ergibt sich aus dem Ergebnis.
- **Text-XP:** Cooldown pro (Server, Nutzer) über `SET NX EX` in Redis, gesetzt erst nach den Ausschlüssen: eine
  ausgenommene Nachricht startet keinen Cooldown. Ausnahmen (Kanal inkl. Elternkanal bei Threads, Rolle) gelten vor allem.
- **Faktoren:** globaler Faktor × höchster Rollenfaktor × höchster Kanalfaktor. Rollenfaktoren **stapeln nicht**: sonst
  bekäme jemand mit mehreren kleinen Boosts das Produkt, das niemand so eingestellt hat.
- **Voice-XP ohne Sitzungstabelle:** Ein Takt pro Minute geht über die Sprachkanäle, die der Bot im Speicher hat, und
  vergibt XP an alle, die dann dort sind. Das übersteht Neustarts ohne Aufräumen (es gibt keine offenen Sitzungen) und
  braucht keine Tabelle. Bedingungen: nicht stumm/taub, nicht im AFK-Kanal, kein Bot, und (Standard) mindestens ein
  weiterer Mensch im Kanal. Mitglieder, die der Bot nicht kennt (ohne Members-Intent evtl. nicht im Cache), bekommen
  lieber **kein** XP, als dass Rollen-Ausnahmen übergangen würden; das wird einmal pro Server geloggt.
- **Belohnungen:** `stack` (alle erreichten Rollen) oder `replace` (nur die höchste, frühere Belohnungsrollen werden
  entzogen). Die Entscheidung ist eine reine Funktion (`reward_plan`); sie fasst nie Rollen an, die keine Belohnung sind.
  Rollen, die der Bot nicht vergeben darf (verwaltet, nicht unter seiner höchsten Rolle), lehnt `/leveling reward add`
  sofort ab; später gelöschte oder blockierte Rollen werden übersprungen und geloggt, `/leveling reward list` warnt.
  `/leveling sync` wendet die Regeln auf alle Mitglieder mit XP an (BulkGate, Fünferblöcke mit Pause, höchstens 5000).
- **Level-Up-Nachricht:** Vorlage mit `{user}`, `{name}`, `{level}`, `{xp}`; unbekannte Platzhalter und Formatangaben werden
  beim Speichern abgelehnt. **Erwähnungen:** `{name}` ist ein frei wählbarer Nickname, deshalb darf jede Ankündigung nur das
  Mitglied selbst anpingen, nie @everyone oder Rollen. Zusätzlich ist das der Standard des ganzen Bots.
- **Rank-Card:** Pillow, reine Funktion (`render/rank_card.py`), Ergebnis gecacht (LRU) und im Thread gerendert. Vorlagen
  (solid, gradient, stripes, dots) und Farben werden im Code gezeichnet, es gibt keine Bilddateien und kein Laden fremder
  Bilder. Der Avatar kommt über discord.py (Discord-CDN), ist auf 8 MB begrenzt und fällt bei Fehlern auf einen
  Platzhalter mit Initial zurück. Der Name schrumpft erst in der Schrift und wird erst dann gekürzt; seine Breite richtet
  sich nach dem Zahlenblock (Rang, Level), damit grosse Zahlen ihn nie überdecken. Schrift: DejaVu (im Docker-Image
  installiert), sonst Pillows eingebaute.
- **Bestenliste:** Sortierung nach XP absteigend, bei Gleichstand nach Nutzer-ID; derselbe Massstab gilt für den Rang in `/rank`.
  Nutzer ohne XP erscheinen nicht.
- **Import:** `core/leveling/importer.py` liest JSON (auch die Form `{"players": [...]}` bzw. eine Liste solcher Seiten)
  und CSV (Komma, Semikolon, Tab); `xp` hat Vorrang, sonst wird aus `level` zurückgerechnet. Doppelte Nutzer: höchster Wert.
  Unbrauchbare Zeilen werden gezählt und gemeldet. Grenzen: 5 MB, 100 000 Zeilen, XP gedeckelt. Modi `max` (Standard, der
  höhere Wert bleibt), `replace`, `add`. Der Schreibvorgang ist eine Transaktion: bei einem Fehler bleibt alles unverändert.
- **Intents:** XP pro Nachricht braucht nur das nicht privilegierte `guild_messages` (jetzt immer an), keinen Nachrichteninhalt.

## 17. Rollen und Willkommen (Phase 6)

- **Entscheidung getrennt von Discord:** Was bei einem Klick, einer Auswahl oder einer Reaktion mit den Rollen passiert, sind reine
  Funktionen (`core/roles/plan.py`: `plan_toggle`, `plan_select`, `plan_reaction_added/removed`). Sie liefern ein `Change(add, remove)`
  und fassen nie Rollen an, die nicht zum Panel gehören. Das Umsetzen (`bot/panels.py: apply_change`) prüft nur noch, ob der Bot die Rolle
  vergeben darf (verwaltet, `@everyone`, nicht unter seiner höchsten Rolle → übersprungen und gemeldet). Entfernen vor Hinzufügen: bei `unique`
  hat das Mitglied nie kurz zwei Rollen des Panels.
- **Modi:** `normal` schaltet um, `unique` hält genau eine, `verify` fügt nur hinzu. Beim Auswahlmenü ist die Auswahl der *gewünschte Stand*
  (min_values 0, damit sich alles abwählen lässt); Werte ausserhalb des Panels werden ignoriert, auch wenn ein manipulierter Client sie schickt.
- **Persistenz:** Buttons und Menüs sind `DynamicItem`s mit `custom_id` `gg:role:{panel}:{item}` bzw. `gg:rolesel:{panel}`. Beim Start wird nur die
  Klasse registriert, keine View pro Nachricht; der Zustand steht in der Datenbank. Ein Klick lädt das Panel mit `guild_id` aus der Interaktion: eine
  fremde Panel-ID gibt es für einen anderen Server nicht. Entfernte Einträge lassen alte Buttons mit einer freundlichen Meldung ins Leere laufen.
- **Reaktions-Rollen:** `on_raw_reaction_add/remove` (Intent `guild_reactions`, nicht privilegiert). Weil das für jede Reaktion des Servers eintrifft,
  hält `RolePanelService` pro Server eine Tabelle "Nachricht → Panel" nur der Reaktions-Panels (60 s, bei eigenen Änderungen sofort geleert); alles
  andere endet ohne Datenbankzugriff. Emojis werden über `emoji_key` verglichen (eigene Emojis über ihre ID, Unicode ohne Variationsselektor).
  `is_emoji` ist eine Näherung ohne Emoji-Tabelle (weist Text und Satzzeichen ab); das letzte Wort hat Discord beim Setzen, abgelehnte Reaktionen
  meldet `/rolepanel publish`.
- **Berechtigung:** `ensure_grantable`: der Bot darf die Rolle vergeben **und** der Akteur steht über ihr (Besitzer ausgenommen). Sonst wären Panels
  und Autorollen ein Weg zur Rechteausweitung für jeden mit "Rollen verwalten".
- **Eigene und fremde Nachrichten:** Ein Reaktions-Panel kann eine vorhandene Nachricht übernehmen (`external`). Der Bot ändert oder löscht sie nie.
  Wird die Nachricht eines Panels gelöscht (`on_raw_message_delete`, auch Massenlöschung), gilt das Panel als unveröffentlicht, die Einträge bleiben.
- **Willkommen** (`bot/cogs/welcome.py`, Entscheidung in `core/welcome/rules.py`): Beitritt und "Screening bestanden" (`pending` → nicht mehr) führen
  in denselben Ablauf; `WelcomeService.claim` (`SET NX`, 60 s) verhindert, dass beides oder ein doppeltes Gateway-Ereignis doppelt begrüsst.
  Pending-Mitglieder bekommen nichts (sie sähen die Kanäle nicht, eine Rolle umginge das Screening). Im Raid-Modus (`AutomodService.raid_active`)
  ruht alles für Menschen. Bots bekommen nur ihre eigenen Autorollen. 2 s Wartezeit und ein erneuter Blick, ob das Mitglied noch da ist.
  Drosselung: höchstens 20 Nachrichten/Minute/Server (Redis-Zähler), gedrosselt wird nur die Nachricht, nie die Rolle.
- **Vorlagen:** gemeinsame Prüfung in `core/messages.py` (feste Platzhalter, keine Format- oder Attributzugriffe, 300 Zeichen), auch vom Leveling
  genutzt. Pro Nachrichtenart ein eigener Satz erlaubter Felder (der Abschied kennt kein `{user}`). Erwähnungen: nur das neue Mitglied (Nickname
  und Servername sind frei wählbar), beim Abschied niemand.
- **Willkommensbild:** `render/welcome_card.py` teilt Zeichenhilfen (`render/common.py`) mit der Rank-Card, ist eine reine Funktion mit LRU-Cache und
  läuft im Thread. Nur Farben und Vorlagen aus dem Code, kein Laden fremder Bilder; der Avatar kommt von Discord (8 MB-Grenze, Platzhalter bei Fehlern).
  Der Kartenstil (`core/settings/card.py`) ist für Rank-Card und Willkommensbild derselbe Typ.
- **Intents:** Reaktionen: `guild_reactions` (nicht privilegiert). Beitritt, Austritt und Screening brauchen den privilegierten *Server Members Intent*
  (`ENABLE_MEMBERS_INTENT`); ohne ihn bleibt Willkommen stumm, was das README sagt.

## 18. Tickets (Phase 7)

- **Ein privater Kanal pro Ticket.** Sichtbar für Ersteller, Support-Rollen der Kategorie und den Bot; `@everyone` ist verdeckt und lässt sich nicht als Support-Rolle eintragen.
  Die Support-Rollen werden ins Ticket kopiert: spätere Änderungen an der Kategorie (oder deren Löschung) betreffen offene Tickets nicht.
- **Anlegen in drei Schritten:** (1) `reserve` prüft die Grenze und zieht eine Nummer (atomar: `INSERT … ON CONFLICT DO UPDATE … RETURNING`), (2) der Cog legt den
  Kanal an, (3) `register` trägt das Ticket ein. Die Grenze "offene Tickets pro Mitglied" gilt in Schritt 3 endgültig, unter einer Postgres-Advisory-Sperre je (Server, Nutzer):
  fünf gleichzeitige Klicks ergeben genau ein Ticket. Scheitert Schritt 3, wird der Kanal wieder gelöscht (kein Kanal ohne Ticket); Nummern können dabei Lücken lassen,
  doppelt vergeben werden sie nie. Zusätzlich 5 s Klick-Cooldown.
- **Schliessen ist eine Transaktion:** Status auf `closed` (nur wenn noch `open`, `UPDATE … WHERE status='open' RETURNING`) und Transkript speichern gehören zusammen. Wer zu spät kommt,
  bekommt "bereits geschlossen" und erzeugt kein zweites Transkript, keine zweite DM, keinen zweiten Job. Das Transkript wird **vor** der Transaktion erzeugt: scheitert das Rendern,
  bleibt das Ticket offen und der Kanal bestehen. Das Löschen des Kanals ist ein Scheduler-Job (`ticket.delete_channel`), er überlebt also Neustarts; der Job löscht nur Kanäle
  geschlossener Tickets.
- **Übernehmen:** `UPDATE … WHERE claimed_by IS NULL`: bei gleichzeitigem Klick gewinnt genau ein Support-Mitglied. Abgeben darf der Bearbeiter oder ein Verwalter.
- **Berechtigung im Ticket** ist eine reine Funktion (`core/tickets/rules.py`): Support = Support-Rolle der Kategorie oder *Server verwalten*; Schliessen zusätzlich der Ersteller
  (abschaltbar). Fremde Tickets sehen für andere Nutzer wie "gibt es nicht" aus (Transkript, Bewertung).
- **Transkript** (`core/tickets/transcript.py`): eigenständiges HTML ohne Skripte und ohne fremde Ressourcen (Content-Security-Policy `default-src 'none'`). Alles Nutzergeschriebene ist
  maskiert, Anhangslinks gibt es nur für http(s), höchstens 2000 Nachrichten und 4000 Zeichen je Nachricht. Es braucht den Message Content Intent, sonst sind fremde Inhalte leer.
- **Auto-Close:** Ein Takt alle 5 Minuten; Aktivität kommt aus `channel.last_message_id` (Zeit steckt in der Snowflake), es gibt keine Schreibzugriffe pro Nachricht. Ein Ticket ohne
  Nachricht gilt ab Erstellung. Fehlt der Kanal, schliesst `on_guild_channel_delete` das Ticket (ohne Transkript, denn der Verlauf liegt nur auf Discord). Aufbewahrung der Transkripte
  (`retention_days`) prüft derselbe Takt einmal täglich.
- **Bewertung:** Fünf persistente Knöpfe in der DM (`gg:tkfb:{guild}:{ticket}:{stars}`); nur der Ersteller des geschlossenen Tickets, nur einmal (Primärschlüssel `ticket_id`).
- **Komponenten:** `DynamicItem`s `gg:tkopen:{category}`, `gg:tk:close|claim`, `gg:tkfb:…`. Formulare (Fragen, Schliessgrund) sind Modals. Fehler laufen durch `component_error`.

## 19. Benachrichtigungen (Phase 8)

- **Anbieter-Schnittstelle:** `core/notify/` kennt kein Discord. `TwitchClient` (Helix, App-Token) und `YouTubeClient` (öffentlicher Atom-Feed) liefern `StreamInfo`/`VideoInfo`;
  Fehler sind `ProviderError` mit den Schlüsseln `not_found`, `unavailable`, `not_configured`. Die Entscheidungen sind reine Funktionen in `rules.py`
  (`plan_stream`, `new_videos`); der `Poller` (`poller.py`) verbindet Anbieter, Zustand (Service) und einen `Announcer` (Protokoll), den `bot/notifier.py` mit Discord umsetzt.
  Dadurch lässt sich alles mit simuliertem HTTP (`httpx.MockTransport`) und einem Sender zum Mitschreiben testen.
- **Entscheidungen (mit dir abgestimmt):** Twitch nur per Polling (kein EventSub, keine öffentliche URL nötig); YouTube per RSS-Feed (kein Schlüssel, keine Quote; dafür kein Live-Status).
- **Eine Abfrage für alle Server:** `all_for_provider` liest bewusst über Server hinweg (die Frage "ist X live?" ist pro Streamer, nicht pro Server), jeder Eintrag wird danach mit seiner
  eigenen `guild_id` behandelt. Twitch: eine Anfrage je 100 Streamer, YouTube: eine je Feed (mit Redis-Tor `SET NX EX 300` je Kanal, das gilt auch prozessübergreifend), höchstens 5 gleichzeitig.
- **Exakt einmal, ohne Verlust bei kurzen Fehlern:** Vor dem Senden wird der Zustand atomar beansprucht (`UPDATE … WHERE last_key IS NOT DISTINCT FROM :alt` bzw. `seen = :alt`); wer
  verliert, sendet nicht. Scheitert das Senden vorübergehend (`retry`), wird der Anspruch zurückgenommen (nur wenn der Stand noch der eigene ist) und die nächste Runde versucht es neu.
  Ist es dauerhaft unmöglich (`drop`: Kanal weg, keine Rechte), bleibt der Anspruch, damit nicht jede Minute neu versucht wird. Bei mehreren Prozessen sendet nur der, dem der Server gehört
  (`available`).
- **Stream-Zustand:** Dieselbe Stream-ID ist derselbe Stream. "Offline" zählt erst nach 3 Abfragen in Folge (Twitch lässt Streams bei Abbrüchen kurz verschwinden); erst dann wird die
  Meldung als beendet markiert. Ein Anbieterfehler ändert den Zustand nie.
- **Video-Zustand:** Pro Abo merkt sich der Bot die letzten 50 Video-IDs. Neu = nicht darin. Alles Neue gilt danach als bekannt, auch was wegen Alter (> 48 h) oder Obergrenze
  (3 je Abfrage) nicht angekündigt wird, sonst käme es immer wieder. Beim Hinzufügen gilt der aktuelle Feed als bekannt.
- **Eingaben:** Twitch-Logins `[a-z0-9_]{3,25}`, YouTube-Kanal-IDs `UC…` (24 Zeichen) und `@Handle`; Muster mit `\A…\Z`, weil `$` einen angehängten Zeilenumbruch durchliesse. Der Bot fragt
  **nur** `youtube.com`, `api.twitch.tv` und `id.twitch.tv` an: freie Adressen werden abgelehnt, nur die daraus gelesene Kanal-ID zählt (kein SSRF).
- **XML:** Feeds werden mit `defusedxml` gelesen (Entity-Tricks und externe Entitäten werden abgelehnt), mit 1 MB Grenze; kaputte Einträge werden einzeln übersprungen.
- **Geheimnisse:** Das Twitch-Secret ist `SecretStr`, wird als Log-Geheimnis registriert und nur im Body der Token-Anfrage gesendet. Antworten auf abgelehnte Zugangsdaten werden nie
  weitergegeben oder geloggt.
- **Erwähnungen:** Titel und Namen stammen von Dritten. Gepingt wird höchstens die eingestellte Rolle (`allowed_mentions`), `@everyone` als Rolle wird abgelehnt.

## 20. Engagement, Teil a (Phase 9a)

- **Phase 9 in zwei Teilen (mit dir abgestimmt):** 9a Eigene Befehle, Umfragen, Giveaways, Erinnerungen. 9b Starboard, Geburtstage, Stat-Kanäle, geplante Nachrichten.
- **Eigene Befehle ohne Logik:** Die Vorlage ist eine Liste aus Text und festen Platzhaltern (`core/customcmd/template.py`), kein Interpreter. Es gibt nichts auszuwerten, also nichts
  auszubrechen: unbekannte Platzhalter, Verschachtelung, Format- und Attributzugriffe (`{name.__class__}`) werden beim Speichern abgelehnt. Argumente des Aufrufers werden eingesetzt,
  aber nie erneut ausgewertet. Zufall kommt als Funktion herein (Regel: reine Module bestimmen weder Zeit noch Zufall). **Ein** Slash-Befehl `/c <name>` statt eines Befehls pro Eintrag:
  Discord-Befehle pro Server wären bei jeder Änderung eine Synchronisation (und Rate-Limit-Thema). Der Zähler `uses` wird atomar hochgezählt (`UPDATE … SET uses = uses + 1 RETURNING`).
- **Umfragen sind native Discord-Umfragen** (statt eigener Tabellen `polls`/`poll_votes`): Discord führt Stimmen, Dauer und Ergebnis, auch wenn der Bot aus ist; der Bot müsste nichts
  nachbauen, nichts speichern und keine Knöpfe pflegen. Preis: keine eigenen Auswertungen und keine anonyme Abstimmung (die Discord nicht kennt). `/poll end` fasst nur Umfragen des Bots an.
- **Giveaways:** Das Ende ist ein Scheduler-Job (`giveaway.end`, übersteht Neustarts). Beenden ist atomar (`UPDATE … WHERE NOT ended RETURNING`): Knopf, `/giveaway end` und Job können
  gleichzeitig auslösen, gezogen wird genau einmal. Die Ziehung (`core/giveaway/rules.py`) mischt die Teilnehmer zufällig (Zufall injiziert, sortierte Eingabe), geprüft wird dann der Reihe nach,
  bis genug da sind (Mitglied noch auf dem Server, kein Bot, Rolle noch vorhanden; höchstens 200 Prüfungen, ohne Members-Intent mit REST-Nachfrage). Teilnahme ist ein Schalter mit Primärschlüssel
  `(giveaway_id, user_id)`, also auch bei gleichzeitigen Klicks nie doppelt.
- **Erinnerungen:** Ein Scheduler-Job je Erinnerung. Ausliefern ist atomar (`UPDATE … WHERE state='pending' RETURNING`): doppelte Läufe und abgebrochene Erinnerungen liefern nichts. Kanal vor DM;
  ist beides unmöglich, gilt sie als ausgeliefert (sonst liefe der Scheduler endlos im Backoff). Gepingt wird nur der Ersteller; der Text kann `@everyone` enthalten, ohne dass es wirkt.

## 21. Engagement, Teil b (Phase 9b)

- **Starboard zählt selbst, statt Reaktionen abzufragen:** `starboard_votes` hat den Primärschlüssel `(guild_id, message_id, user_id)`, jeder Stern zählt also genau einmal und die Zahl kommt aus
  der Datenbank, nicht aus REST-Abfragen der Reaktionsliste. Preis: Sterne vor dem Einschalten sind dem Bot unbekannt. Die Frühprüfung (Emoji, Einstellung) endet für fast alle Reaktionen
  ohne Datenbank- oder REST-Zugriff. Welche Sterne zählen, ist eine reine Funktion (`core/community/starboard.py`): keine Bots, nie der Starboard-Kanal selbst, keine ignorierten oder
  (wählbar) NSFW-Kanäle, kein Autoren-Stern.
- **Schwelle auch bei Gleichzeitigkeit:** Jede Transaktion sieht beim Zählen nur ihren eigenen Stand; zählten gleichzeitige Sterne mit diesem Wert, könnte die Schwelle knapp verpasst werden.
  Darum wird **nach dem Commit neu gezählt**, und die zuletzt abgeschlossene Transaktion sieht alle. Gepostet wird genau einmal (`claim_post`: `UPDATE … WHERE star_message_id IS NULL AND NOT posting`);
  scheitert das Senden, wird der Anspruch freigegeben, und der nächste Stern versucht es erneut.
- **Bearbeitungen werden gedrosselt:** Höchstens eine Änderung der Sternzahl alle 3 s je Nachricht (Redis `SET NX`); der Rest wird mit **einem** Job (`starboard.refresh`, 6 s später) auf den Endstand gezogen.
  Wird die Kopie von Hand gelöscht, wird sie beim nächsten Stern neu gepostet.
- **Geburtstage speichern kein Jahr** (`birthdays`: Tag, Monat). Der Tag wechselt nach der Zeitzone des Servers; der 29. Februar rückt in Nicht-Schaltjahren auf den 28. (Regel in `core/community/birthday.py`,
  dieselbe Regel in der Abfrage). Eine Ankündigung je Server und Tag wird per Redis-Tor (`SET NX`, 3 Tage) beansprucht, bevor gesendet wird. Die Rolle wird bei jedem Durchlauf abgeglichen:
  genau die Geburtstagskinder von heute haben sie.
- **`/privacy`** löst das Versprechen aus Abschnitt 12.7 ein: zeigt XP, Geburtstag und offene Erinnerungen des Nutzers auf diesem Server und löscht sie auf ausdrückliche Bestätigung (das Wort `DELETE`).
  Andere Nutzer und andere Server bleiben unberührt.
- **Stat-Kanäle:** Umbenennung höchstens alle 10 Minuten und nur bei geänderter Zahl (Discord erlaubt etwa zwei Umbenennungen je Kanal und zehn Minuten). `humans`/`bots` nur mit Members-Intent,
  sonst gäbe der unvollständige Cache falsche Zahlen. Vorlage und Kanal werden **vor** dem Anlegen geprüft (kein Kanal, der gleich wieder verschwindet); scheitert der Eintrag danach, wird der Kanal
  gelöscht. Ein gelöschter Stat-Kanal räumt seinen Eintrag beim nächsten Durchlauf auf.
- **Geplante Nachrichten:** Ein Scheduler-Job je Termin mit dem Termin im Payload. `claim_run` schreibt den Termin atomar fort (`UPDATE … WHERE next_run = :alt`): doppelte Jobs, abgebrochene und
  veraltete Termine senden nichts. Wiederkehrende planen den nächsten Termin im alten Raster **nach jetzt** (`core/community/schedule.py`), verpasste Runden werden nicht nachgeholt. Gesendet wird nie mit
  Erwähnungen.

## 22. Dashboard-API (Phase 10a)

Umgesetzt in `src/ggbot/api/`. Abweichungen und Ergänzungen zu Abschnitt 9:

- **Routen** (nginx entfernt das Präfix `/api`): `GET /auth/login`, `GET /auth/callback`,
  `POST /auth/logout`, `GET /me`, `GET /modules`, `GET /modules/{m}/schema`,
  `GET /guilds/{id}/snapshot`, `GET|PUT /guilds/{id}/settings/{m}`, `GET /guilds/{id}/audit`.
- **Schema-getriebene Formulare:** `/modules/{m}/schema` liefert `model_json_schema()` des
  Pydantic-Modells aus `core/settings/registry.py`; die Oberfläche baut daraus das Formular.
  Ein Test erzwingt, dass jedes `SettingsModel` im Register steht. Kanal- und Rollenfelder
  tragen `x-picker: "channel" | "role"` (Schlüssel von Zuordnungen: `x-key-picker`); die Auswahl
  kommt aus dem Redis-Snapshot.
- **IDs sind Strings im JSON.** Discord-IDs liegen über 2^53; JavaScript würde eine JSON-Zahl
  stillschweigend verfälschen. `core/settings/types.py` (`Snowflake`, `ChannelId`, `RoleId`)
  liest Zahl *und* Ziffernstring (alte gespeicherte Stände bleiben gültig), prüft den String
  streng (`\A[0-9]{1,20}\Z`, sonst liesse Pydantics Zahlenparser `" 1"` oder `"-1"` durch) und
  schreibt im JSON einen String. Im Python-Objekt bleibt es `int`. Das gilt auch für den Snapshot.
- **Sitzung:** Cookie `gg_session` = `<sid>.<HMAC-SHA256>` (Geheimnis `SESSION_SECRET`),
  Daten in Redis (7 Tage, fest; ein Neuladen der Server-Liste verlängert nicht). Das
  Access-Token bleibt serverseitig und dient dazu, die Server-Liste **höchstens 60 s alt** zu
  halten: entzogene Rechte wirken innerhalb einer Minute. Antwortet Discord mit 401, wird die
  Sitzung gelöscht; bei anderen Fehlern wird abgelehnt (503), statt mit einer womöglich
  veralteten Rechteliste weiterzuarbeiten. Abmelden widerruft das Token bei Discord (best effort).
- **Login-CSRF:** der OAuth-`state` liegt signiert in einem kurzlebigen Cookie und wird beim
  Rücksprung verglichen; ein untergeschobener Code trägt den State des Angreifers nicht.
- **CSRF bei Schreibzugriffen:** Header `X-Requested-With: ggbot` Pflicht (ohne eigenes Token;
  eine fremde Origin kann ihn nur über einen CORS-Preflight setzen, den die API nicht freigibt).
- **Zugriff:** Eigentümer oder `Manage Server`/`Administrator` **und** Snapshot vorhanden
  (= Bot auf dem Server). Beides ergibt 404, damit niemand über 403 erfährt, wo der Bot ist.
  Der Bot löscht den Snapshot beim Verlassen.
- **Schreiben:** validiert über das Modell (422 mit Feldpfad und Grund, **ohne** Eingabewerte),
  begrenzt auf 30 Schreibvorgänge/Minute und Nutzer, ohne Änderung kein Audit und kein Event.
  Audit-Eintrag und Speichern liegen in einer Transaktion unter einem Advisory-Lock je
  (Server, Modul), damit das Protokoll bei gleichzeitigem Speichern eine lückenlose Kette bleibt.
- **Nicht geprüft:** ob eine gesetzte Kanal-/Rollen-ID auf dem Server existiert. Der Bot muss
  mit gelöschten Kanälen ohnehin umgehen können, die Auswahl der Oberfläche kommt aus dem Snapshot.
- **Fehlerformat:** `{"detail": {"error": "<schlüssel>"}}`; Texte und Übersetzung gehören der Oberfläche.

## 23. Dashboard-Oberfläche (Phase 10b)

Umgesetzt in `web/src/` (React 18, Vite, Tailwind, nur `react`/`react-dom` zur Laufzeit).

- **Schema-getriebene Formulare:** `schema.ts` übersetzt das JSON-Schema eines Moduls in eine
  kleine Beschreibung (`compile`), `form/SchemaForm.tsx` rendert sie. Verstanden wird genau,
  was die Modelle erzeugen: `$ref`, „Wert oder null“ (`anyOf`), Enums, Zahlen mit Grenzen, Text,
  Listen (Auswahl-Chips, Textzeilen, Objektkarten), Zuordnungen, verschachtelte Objekte
  (einklappbar, `enabled` in der Kopfzeile). Alles andere wirft einen `SchemaError`, statt ein
  Feld stillschweigend zu verlieren; der Test „jedes Modul lässt sich bauen“ fängt neue
  Schemaformen schon im CI.
- **Hinweise vom Server** (`core/settings/`): `x-picker` (`channel`/`role`), `x-key-picker`,
  `x-keys` (feste Schlüssel, z. B. Ereignisarten), `x-key-type`/`x-key-label` (Level als Schlüssel),
  `x-widget` (`color`, `textarea`). Sie ändern die Validierung nicht.
- **Fixture-Abgleich:** `web/src/test/schemas.json` enthält die echten Schemas und Standardwerte;
  `tests/unit/test_web_schema_fixtures.py` schlägt fehl, wenn sie veralten
  (`UPDATE_FIXTURES=1 pytest tests/unit/test_web_schema_fixtures.py` schreibt neu).
- **Auswahlfelder** kommen aus dem Bot-Snapshot. Eine gespeicherte ID, die es nicht mehr gibt
  (gelöschter Kanal), bleibt als „Unbekannt (ID)“ sichtbar und wird beim Speichern nicht verworfen.
  `@everyone` wird nie als Rolle angeboten. IDs bleiben in der Oberfläche **immer Strings**.
- **Speichern:** sendet den vollständigen Modulstand; 422 wird pro Feld angezeigt (Pfad aus
  der API, Gruppen klappen auf), Modellregeln ohne Ort stehen oben. Ungespeicherte Änderungen
  werden bei Navigation und beim Schliessen des Tabs abgefragt.
- **Sitzung:** jede 401 führt zurück zum Login; „Login nicht eingerichtet“ ist ein eigener Zustand.
- **Einladungslink** für Server ohne Bot: `GET /me` liefert `client_id` und die benötigten Rechte
  (`INVITE_PERMISSIONS`, per Test mit `discord.Permissions` abgeglichen).
- **Texte:** Deutsch/Englisch (`i18n.tsx`, Browsersprache, umschaltbar, in `localStorage`).
  Ein Test erzwingt Beschriftungen für jedes Formularfeld, jede Auswahl und jedes Modul sowie
  für jeden Fehlerschlüssel der API.
- **Routing** ohne Bibliothek (`/`, `/g/<id>`, `/g/<id>/m/<modul>`, `/g/<id>/audit`); Pfade
  werden streng geparst. nginx liefert dafür `index.html` für alle unbekannten Pfade.
- **Bewusst nicht in 10b:** Panels (Rollen/Tickets), Kartenvorschau, öffentliches Leaderboard
  (alles 10c); Beschreibungstexte/Platzhalterhilfen zu einzelnen Feldern.

## 24. Panels, Kartenvorschau, öffentliche Rangliste (Phase 10c)

- **Panels im Dashboard** (`api/routers/panels.py`): Rollen- und Ticket-Panels anlegen, bearbeiten,
  löschen, Einträge/Kategorien/Support-Rollen pflegen, veröffentlichen. Alles läuft durch dieselben
  Dienste wie die Slash-Befehle (`RolePanelService`, `TicketService`), `UserError` kommt als 422 mit
  übersetzter Meldung zurück (Kopf `X-Locale`, sonst Standardsprache). Die Formulare sind hier bewusst
  **nicht** schema-getrieben: Panels sind Tabellenzeilen mit eigener Logik, keine Einstellungsmodule.
- **Wer darf was:** Ticket-Panels: *Server verwalten* (wie `/ticketpanel`). **Rollen-Panels:
  Eigentümer oder Administrator**, weil ein Panel Rollen vergibt und die API die Rollenhierarchie des
  Nutzers nicht kennt (Slash-Befehle prüfen sie über das Mitglied). Lesen dürfen alle mit Zugriff.
- **Prüfungen ohne Discord-Objekte** (`api/policy.py`, rein): vergebbare Rolle = nicht @everyone, nicht
  verwaltet, **unter** der höchsten Rolle des Bots; Kanal muss ein Textkanal sein, die Ticket-Kategorie
  ein Kanal-Container, Emoji muss wie eines aussehen, Fragen wie bei `/ticketpanel`. Grundlage ist der
  Snapshot, der dafür `bot_top_position` trägt (0 = unbekannt = nichts vergebbar).
- **Veröffentlichen läuft über den Bot:** die API prüft, setzt den Status `pending` und schickt
  `panel.publish` (`kind`, `action` = `publish`/`refresh`/`delete`, Kanal, Nachricht). Der Bot führt aus,
  prüft seine Rechte im Kanal wie bei `/rolepanel publish` und schreibt das Ergebnis in Redis
  (`core/panelstatus.py`, 10 Minuten): `done`, `done` mit Hinweis (abgelehnte Reaktionen) oder `error`
  mit i18n-Schlüssel. Die Oberfläche fragt den Status ab. Nach jeder Änderung an einem veröffentlichten
  Panel folgt ein `refresh`; beim Löschen ein `delete` mit Kanal und Nachricht (das Panel ist dann weg).
  Slash-Befehl und Event teilen sich `_check_publishable` und `_publish`.
- **Kartenvorschau** (`api/routers/preview.py`): `POST /guilds/{id}/preview/{rank|welcome}` mit dem
  `CardStyle` aus dem Formular liefert ein PNG, gezeichnet von `ggbot.render` wie im Bot, mit
  Beispieldaten und ohne Avatar (kein Abruf bei Discord). Limit 60/min und Nutzer; Zeichnen im Thread.
  Die Oberfläche zeigt die Vorschau unter Leveling und Willkommen und fragt nach 400 ms Ruhe.
- **Öffentliche Rangliste:** `GET /public/{guild_id}/leaderboard`, Seite `/leaderboard/<id>`. Nur wenn
  `LevelingSettings.enabled` **und** `public_leaderboard` (Standard aus); ein nicht freigegebener Server
  und ein unbekannter sind für Besucher gleich (404). Namen und Avatare kennt nur der Bot: er legt die
  Top 100 für 2 Stunden in Redis (`core/publicboard.py`), aufgefrischt alle 10 Minuten und sofort bei
  Änderung der Einstellung; Ausschalten löscht den Zwischenspeicher. **Nichts davon steht in der
  Datenbank.** Mitglieder, die nicht mehr auf dem Server sind, fehlen; wer `/privacy delete` nutzt,
  verschwindet (seine XP-Zeile ist weg). Pro Lauf höchstens 25 Nachfragen nach unbekannten Mitgliedern,
  der Rest erscheint ohne Namen. Antwort ohne Nutzer-IDs (nur die Avatar-URL enthält die ID, wie in
  Discord selbst öffentlich). Limit 60/min pro Besucher; Besucher = Eintrag in `X-Forwarded-For`, `PROXY_HOPS`
  von rechts (Standard 1 = der nginx aus Compose; hinter einem weiteren Proxy 2), weil alles davor vom
  Client stammt.
- **Nicht umgesetzt:** Eintrag eines Panels nachträglich ändern (wie bei den Slash-Befehlen: entfernen und
  neu anlegen), Reihenfolge ändern, Reaktions-Panels an fremden Nachrichten anlegen (nur per Slash-Befehl,
  weil sie die Nachricht prüfen müssen), persönliches Ausblenden aus der öffentlichen Liste.

## 25. Betrieb und Härtung (Phase 11)

- **Lebenszeichen und Zähler** (`core/status.py`): der Bot schreibt alle 10 s Version, Serverzahl,
  Shards und Latenz als JSON nach Redis (30 s gültig); der Docker-Healthcheck prüft nur, ob der
  Schlüssel da ist. Zwei Zähler (`commands`, `command_errors`) zählt der Bot per `INCR`; Fehler zählen
  nur echte Bugs (mit Fehler-ID), nicht falsche Eingaben oder fehlende Rechte. Es gibt bewusst keinen
  zweiten HTTP-Server im Bot. Ein Fehler beim Schreiben kostet nie einen Befehl und beendet den Herzschlag nicht.
- **`/metrics`** (`api/metrics.py`): Prometheus-Textformat ohne zusätzliche Bibliothek. Nur mit
  `METRICS_TOKEN` (Bearer, Vergleich zeitkonstant); ohne gesetztes Token gibt es den Endpunkt nicht (404).
  nginx beantwortet `/api/metrics` selbst mit 404; Prometheus ruft `api:8000/metrics` im Compose-Netz auf.
  Metriken: `gg_info`, `gg_{db,redis,bot}_up`, `gg_bot_{guilds,shards,latency_seconds,uptime_seconds}`,
  `gg_bot_commands_total`, `gg_bot_command_errors_total`, `gg_scheduler_jobs{state}`,
  `gg_scheduler_overdue_jobs` und `gg_scheduler_oldest_overdue_seconds` (fällig seit über 60 s und nicht
  abgearbeitet = Scheduler hängt). `/healthz` nennt zusätzlich Version und `bot: ok|down`; der Bot zählt
  **nicht** zum HTTP-Status (fällt er aus, bleibt das Dashboard erreichbar).
  Vorschlag für Alarme: `gg_bot_up == 0` seit 2 min; `gg_scheduler_overdue_jobs > 0` seit 5 min;
  `increase(gg_bot_command_errors_total[15m]) > 0`.
- **Gefundene Lücken beim Sicherheitsdurchgang** (selbst durchgesehen, `security-review` lief hier nicht,
  weil kein `origin/HEAD` existiert):
  1. *Zu grosse IDs gaben 500* (Postgres lehnt Zahlen über int64 ab), auch auf der öffentlichen Rangliste.
     Jetzt `api/ids.py` (`Id`), Körper-IDs prüft `Snowflake`; Test je Route.
  2. *Der einmalige Anmelde-Code stand im Zugriffsprotokoll* (uvicorn loggt die Query). Jetzt
     `--no-access-log` und ein eigenes Protokoll mit Methode, Pfad, Status, Dauer, nie der Query.
  3. *Besucheradresse als Redis-Schlüssel*: beliebige Zeichenketten aus `X-Forwarded-For`; jetzt nur echte IPs.
  4. *Avatar-URLs* aus dem Zwischenspeicher gehen nur noch durch, wenn sie von `cdn.discordapp.com` kommen.
  5. Antworten der API: `Cache-Control: no-store` (sofern nicht anders gesetzt), `nosniff`, `X-Frame-Options`, `no-referrer`.
- **nginx** (`web/nginx.conf`, `web/security-headers.conf`): unprivilegierte Variante (Port 8080 im Container,
  `WEB_PORT` bleibt aussen), Content-Security-Policy (`default-src 'none'`, Skripte und Stile nur von sich, Bilder
  von sich, `blob:` und Discords CDN), `nosniff`, `X-Frame-Options: DENY`, `Permissions-Policy`, Hash-Dateien
  ewig cachebar, `index.html` nie. **HSTS** gehört an den TLS-beendenden Proxy davor. Die CSP wurde im echten
  Browser gegen alle Seiten geprüft (keine Verstösse; mit entferntem CDN schlägt dieselbe Prüfung an).
- **Container**: `cap_drop: ALL`, `no-new-privileges`, Log-Rotation (10 MB × 3), `read_only` mit `tmpfs /tmp`
  für API und Web. Postgres und Redis bleiben bei den Standardwerten der offiziellen Images. Der Bot läuft nicht
  `read_only`: das liess sich ohne echten Discord-Token nicht erproben.
- **Backup/Restore** (`scripts/backup.sh`, `scripts/restore.sh`): `pg_dump -Fc` mit sofortiger Leseprobe
  (`pg_restore --list`), Rechte 600, Rotation (`BACKUP_KEEP`, Standard 14), halbfertige Dateien werden entfernt.
  Restore prüft die Datei **vor** dem Eingriff, verlangt `RESTORE` (oder `--yes`), hält Bot und API an, spielt ein,
  leert Redis (sonst zeigt der Einstellungs-Cache noch eine Stunde den alten Stand) und startet neu; bei einem
  Fehler wird weder geleert noch gestartet. Redis wird nicht gesichert (Zwischenspeicher). Getestet gegen echtes
  Postgres (`tests/integration/test_backup_scripts.py`) und in CI auf dem laufenden Compose-Stack.
- **Last und Ausfälle** (`tests/integration/test_load.py`): 5 Worker ziehen 300 Jobs gleichzeitig, jeder läuft
  genau einmal; scheiternde Handler laufen genau einmal erneut; ein Worker, der nach dem Ziehen stirbt, verliert
  keine Jobs (Pacht läuft ab, andere übernehmen); 500 Ereignisse mit Müll dazwischen kommen vollständig und
  in Reihenfolge an. Hinweis: `SKIP LOCKED` ist für den Durchsatz da, nicht für die Korrektheit (mit
  gewöhnlichem `FOR UPDATE` bliebe es genau einmal, aber Worker warteten aufeinander).
- **Abhängigkeiten**: `audit.yml` prüft Laufzeit-Pakete (`pip-audit`) und alle npm-Pakete (`npm audit --audit-level=high`)
  bei jedem Push und wöchentlich. Beim ersten Lauf: Python 0 Funde; npm 2 kritische in den Test-Werkzeugen
  (Tinypool in Vitest 3), behoben durch Vitest 5.
- **Release**: Version steht in `pyproject.toml`, `src/ggbot/__init__.py`, `web/package.json`, beiden Lockfiles;
  ein Test erzwingt, dass sie gleich sind und `CHANGELOG.md` einen Abschnitt dafür hat. Ein Tag `vX.Y.Z` löst
  `release.yml` aus: Tag, Versionen und Changelog prüfen, Images nach `ghcr.io/<owner>/gg-bot[-web]` (Version und
  `latest`), GitHub-Release mit dem Changelog-Abschnitt. **Der Workflow ist nicht erprobt** (kein Registry-Zugang
  hier); der erste Tag zeigt es.

## 26. Verifizierung und Auto-Löschen (Phase 12a)

Phase 12 liefert weitere MEE6-Funktionen in drei Teilen: 12a Verifizierung und Auto-Löschen, 12b Vorschläge, Sticky-
Nachrichten und temporäre Sprachkanäle, 12c Embed-Builder und Ankündigungen im Dashboard.

- **Verifizierung** (`core/settings/verification.py`, `core/services/verification.py`, `bot/verification.py`,
  `bot/cogs/verification.py`): Panel mit persistentem Knopf (`gg:verify:start`, DynamicItem, überlebt Neustarts).
  Modus `button` vergibt die Rolle sofort; Modus `code` schickt ein Bild (`render/verify_code.py`, Startwert von
  `secrets`, Text nie im Chat) und einen Eingabeknopf (`gg:verify:enter`) mit Fenster. Der Code liegt in Redis (5 min).
  **Versuche:** der Zähler wird vor dem Vergleich atomar erhöht (`INCR`), nach `MAX_ATTEMPTS` (3) gilt eine Sperre
  von 10 Minuten; die Versuche zählen über mehrere Bilder hinweg, sonst liesse sich durch ständig neue Bilder raten.
  Der letzte Fehlversuch sperrt sofort. Das Bild gibt es höchstens alle 20 s (`Cooldown`).
  Die Rolle wird nur vergeben, wenn der Bot sie vergeben darf (`assignable`); sonst eine Meldung an den Nutzer und
  eine Warnung im Log. `setup` verlangt zusätzlich, dass der Akteur die Rolle weitergeben darf (`ensure_grantable`).
  `panel_message_id` steht in den Einstellungen (Schutz vor Auto-Löschen), im Dashboard-Formular aber versteckt
  (`x-hidden` im Schema; die Oberfläche lässt solche Felder aus und gibt den Wert unverändert zurück).
- **Auto-Löschen** (`core/settings/autodelete.py`, `core/autodelete/rules.py`, `bot/cogs/autodelete.py`):
  Regeln je Kanal (10 Minuten bis 90 Tage, eine Regel je Kanal, höchstens 20), Standard aus. Die Auswahl der zu
  löschenden Nachrichten ist eine reine Funktion (`plan`): zu junge bleiben auch dann, wenn der Aufrufer sie liefert;
  angeheftete (wenn gewünscht) und geschützte (Panel-Nachrichten der Rollen-, Ticket- und Verifizierungs-Panels) bleiben;
  unter 14 Tagen minus eine Stunde Reserve gesammelt (Discord-Grenze), älter einzeln; je Lauf und Kanal höchstens 200
  gesammelt und 20 einzeln. Der Lauf je Kanal wird über `SET NX` gesperrt (mehrere Shards, langsamer Vorlauf).
  Ein Fehler (`Forbidden`, `HTTPException`) beendet den Kanal, nicht den Lauf; ein Server beendet nie die übrigen.
  `/autodelete add` verlangt `confirm: True`, weil das Löschen endgültig ist.
- **Dashboard:** beide Module erscheinen über das Register (schema-getrieben, mit DE/EN-Beschriftung).
- **Bewusst offen:** Rauswurf unverifizierter Mitglieder nach einer Frist (bräuchte Beitritts-Ereignisse, also den
  privilegierten Members-Intent); Schutz laufender Giveaways, Umfragen und Starboard-Kopien vor dem Auto-Löschen.

## 27. Vorschläge, Sticky und temporäre Sprachkanäle (Phase 12b)

- **Vorschläge** (`core/settings/suggestions.py`, `core/suggestions/rules.py`, `core/services/suggestions.py`,
  `bot/suggestions.py`, `bot/cogs/suggestions.py`; Tabellen `suggestions`, `suggestion_votes`, `suggestion_counters`):
  Nummern je Server über einen Zähler (`INSERT … ON CONFLICT DO UPDATE … RETURNING`, also auch bei gleichzeitigen
  Aufrufen lückenlos und eindeutig). Abstimmen über persistente Knöpfe (`gg:sug:(up|down):<id>`, DynamicItem), nicht
  über Reaktionen: eine Stimme je Person ist durch den Primärschlüssel `(suggestion_id, user_id)` erzwungen, die
  Zählung kommt aus der Datenbank, das Embed wird bei jedem Klick neu gebaut. Die Umschaltregel (`toggle`) ist rein.
  **Entscheiden** ist ein bedingtes `UPDATE … WHERE status = 'pending' RETURNING`: bei zwei gleichzeitigen
  Entscheidungen gewinnt genau eine, die andere bekommt `error.suggest_decided`. Danach nehmen Vorschläge keine
  Stimmen mehr an (Dienst prüft den Status; die Knöpfe werden zusätzlich entfernt).
- **Sticky** (`core/services/sticky.py`, `bot/cogs/sticky.py`; Tabelle `sticky_messages`, Schlüssel = Kanal): Jede
  Nachricht im Server fragt `channel_ids` an, das 30 s im Prozess zwischengespeichert wird (eigene Änderungen
  invalidieren sofort). Der Neupost wird über `SET NX EX 8` gebündelt und als persistenter Job `sticky.repost`
  geplant; der Job liest den Stand erneut (zwischenzeitlich entfernt = nichts tun). Eigene Nachrichten des Bots lösen
  nichts aus, sonst gäbe es eine Schleife. Die Obergrenze (10) wird vor dem Schreiben geprüft; zwei gleichzeitige
  Neuanlagen an der Grenze könnten sie um eins überschreiten – bewusst nicht gesperrt, der Schaden ist gering.
- **Temporäre Sprachkanäle** (`core/settings/tempvoice.py`, `core/tempvoice/rules.py`, `core/services/tempvoice.py`,
  `bot/cogs/tempvoice.py`; Tabelle `temp_voice_channels`): Reagiert nur, wenn sich der Kanal ändert. Gelöscht wird
  ausschliesslich, was in der Tabelle steht, und nur von dem Aufruf, dessen `DELETE` die Zeile wirklich entfernt hat
  (`remove` liefert `True` genau einmal). Der Anzeigename kommt vom Nutzer und läuft durch `clean_name` (keine
  Steuerzeichen, gekürzt, nie leer). Beim Start (`on_ready`) gleicht `reconcile` die Tabelle mit Discord ab.
- **Dashboard:** `suggestions` und `tempvoice` erscheinen über das Register; Sticky-Nachrichten nur per Befehl.
- **Bewusst offen:** Übergabe des Besitzes, wenn der Besitzer geht; Sticky mit Dashboard-Seite.

## 28. Embed-Builder und Ankündigungen (Phase 12c)

- **Datenmodell** (`core/db/models/announcements.py`, Migration 0011): eine Zeile je Ankündigung mit Name, Text,
  Embed (JSONB), Zustand (`draft`, `scheduled`, `sent`, `failed`), Kanal, Nachrichten-ID, Zeitpunkt und `rev`.
  Die Nachrichtenform ist `core/announce/message.py` (rein, pydantic): Grenzen von Discord, https-Pflicht für Bilder,
  Gesamtgrösse 6000 und „nie eine leere Nachricht“ (`normalize`); ein Embed ohne Inhalt wird zu `None`.
- **Senden über den Scheduler** (`core/services/announcements.py`, `bot/cogs/announcements.py`): Die API fasst Discord
  nie an. `schedule` setzt den Zustand per bedingtem `UPDATE` (nur aus Entwurf, Geplant oder Fehlgeschlagen), erhöht
  `rev` und legt einen Job `announce.sync {id, rev}` an; „sofort“ ist derselbe Weg mit dem Zeitpunkt „jetzt“. Der Job
  sendet nur, wenn Zustand und `rev` noch passen: ein abgesagter oder neu geplanter Auftrag läuft ins Leere.
  `mark_sent` und `mark_failed` sind ebenfalls an `rev` gebunden; wurde zwischen Senden und Eintragen abgesagt,
  löscht der Bot die gerade gesendete Kopie wieder. Dauerhafte Fehler (Kanal weg, Rechte, Discord lehnt ab) setzen
  `failed` mit einem Übersetzungsschlüssel; nur „Server nicht verfügbar“ wirft und wird vom Scheduler wiederholt.
- **Bearbeiten Gesendeter:** `update` auf eine gesendete Ankündigung legt einen Job mit der aktuellen `rev` an; er
  erkennt an `message_id`, dass abzugleichen ist, und bearbeitet die Discord-Nachricht. Ein Fehler (z. B. in Discord
  gelöscht) steht als `error` an der gesendeten Ankündigung, ohne den Zustand zu ändern.
- **Erwähnungen** sind beim Senden und Bearbeiten immer ausgeschaltet (`AllowedMentions.none()`).
- **API** (`api/routers/announcements.py`): CRUD, `send` (Kanal, optional Zeitpunkt **mit Zeitzone**), `cancel`. Der
  Kanal wird gegen den Snapshot geprüft (Textkanal); jede Einplanung, Absage und Bearbeitung einer gesendeten
  Nachricht schreibt einen Eintrag ins Änderungsprotokoll. Schreibzugriffe sind auf 60 je Minute und Nutzer begrenzt.
- **Oberfläche** (`web/src/announcements/`): Liste und Editor mit Live-Vorschau (`EmbedPreview`, ohne fremde Bilder).
  Die Liste hält die Oberfläche selbst, damit ein Neuladen den Editor nicht abbaut. Senden ist gesperrt, solange es
  ungespeicherte Änderungen gibt. Bei einer fälligen Ankündigung fragt der Editor alle 2 s nach dem Ergebnis des Bots.
- **Bewusst offen:** mehrere Embeds je Nachricht, Anhänge, Wiederholungen, Vorlagen aus bestehenden Discord-Nachrichten.
