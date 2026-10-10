# GG-Bot

Selbst gehosteter Discord-Bot als Ersatz für MEE6: Moderation, Automod, Logging, Leveling,
Rollen, Tickets, Benachrichtigungen und mehr, mit Web-Dashboard. Läuft per Docker Compose.

Architektur und Entscheidungen: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Stand: **Phase 5 (Leveling)**. Die weiteren Feature-Phasen folgen.

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

### Automod (Phase 3)

Alle Regeln sind **standardmässig aus**. Moderatoren (wer *Nachrichten verwalten* darf) sind ausgenommen,
solange das nicht mit `/automod moderators false` geändert wird. Bots, Webhooks und Systemnachrichten werden ignoriert.
Nachrichten-Regeln brauchen den *Message Content Intent*, Beitritts-Regeln den *Server Members Intent*
(siehe „Benötigte Bot-Rechte“). Ohne den Intent lehnt `/automod enable` die Regel mit einer klaren Meldung ab.

| Regel | Erkennt | Einstellung |
|---|---|---|
| `spam` | zu viele Nachrichten in kurzer Zeit (Standard 5 in 5 s); die ganze Welle wird gelöscht | `/automod spam <messages> <window>` |
| `duplicates` | dieselbe Nachricht mehrfach (Gross/Klein und Leerzeichen egal; Standard 3 in 30 s) | `/automod duplicates <count> <window>` |
| `caps` | zu viele Grossbuchstaben (Standard > 70 % ab 10 Buchstaben, Ziffern und Zeichen zählen nicht) | `/automod caps <min_length> <percent>` |
| `invites` | Discord-Einladungen (`discord.gg`, `discord.com/invite`, …) ausser erlaubten Codes | `/automod invite allow\|disallow <code>` |
| `links` | Links nach Sperrliste oder Erlaubt-Liste, inkl. Subdomains | `/automod link mode\|add\|remove\|list` |
| `mentions` | zu viele verschiedene Erwähnungen (Nutzer + Rollen; Standard > 5) | `/automod mentions <maximum>` |
| `words` | Wörter, Platzhalter, reguläre Ausdrücke | `/automod word add\|remove\|list\|test` |
| `raid` | viele Beitritte in kurzer Zeit (Standard 5 in 10 s) → Raid-Modus | `/automod raid set\|end` |
| `accountage` | Konten jünger als N Tage | `/automod accountage <days> <action>` |

Steuerung: `/automod enable|disable <rule>`, `/automod status`, `/automod stats [days]`.

**Aktion pro Regel** (`/automod action <rule> [delete] [warn] [timeout_duration]`): löschen, verwarnen und/oder
Timeout, frei kombinierbar. Verwarnen läuft über `/warn`, hat also einen Case und löst die Eskalation aus. Beim
Raid und Konto-Alter heisst die Aktion `kick`, `ban` oder `timeout`.
Pro Regel und Treffer-Welle wird höchstens **einmal** verwarnt oder in den Timeout gesetzt (30 s Sperre),
gelöscht wird jede Nachricht.

**Ausnahmen:** `/automod exempt channel_add|channel_remove|role_add|role_remove <rule> <target>`; `rule` ist eine
einzelne Regel oder `all`. Threads erben die Ausnahme ihres Elternkanals.

**Meldekanal:** `/automod log <channel>` – jeder Treffer (einmal je Welle) mit Auslöser, Aktionen und Problemen.

**Wortfilter im Detail**
- `word`: ganzes Wort oder Wortfolge, ohne Gross/Klein, Akzente und unsichtbare Zeichen (`arger` trifft `Ärger`,
  `ass` trifft nicht `Klasse`).
- `wildcard`: `*` = beliebig viele, `?` = genau ein Zeichen, je Wort (`dumm*` trifft `dummkopf`).
- `regex`: reguläre Ausdrücke mit **RE2**. Garantiert lineare Laufzeit, also kein Einfrieren durch böse Muster, aber
  ohne Lookarounds und Rückreferenzen. Muster, die auf leeren Text passen (`a*`), werden abgelehnt.
- `/automod word test <text>` zeigt, was ein Text auslösen würde.

**Links im Detail:** Erkannt werden `http(s)://…`, `www.…` und `domain.endung` mit bekannter Endung (damit ein
vergessenes Leerzeichen nach einem Satzende nicht als Link zählt). Im Erlaubt-Modus ist auch eine Einladung ein
Link: `discord.gg` muss dann auf der Liste stehen. Ein Eintrag gilt für die Domain und alle ihre Subdomains, nicht für
ähnlich geschriebene (`evilexample.com`).

**Anti-Raid:** Ab N Beitritten in S Sekunden beginnt der Raid-Modus. Die ganze Welle bekommt die Aktion, ebenso jeder
weitere Beitritt, bis der Modus nach `minutes` abläuft oder `/automod raid end` ihn beendet. Die Warnung geht in den
Meldekanal, sonst in den Systemkanal des Servers.

### Server-Log (Phase 4)

Jede Ereignisart schreibt in einen **eigenen Kanal** (oder alle in denselben). Eine Art ohne Kanal ist aus.
Standardmässig ist nichts an.

| Art | Protokolliert | Braucht |
|---|---|---|
| `message_delete` | gelöschte Nachricht mit Autor, Kanal, Inhalt, Anhängen | Message Content |
| `message_edit` | Bearbeitung mit Vorher/Nachher und Link | Message Content |
| `message_bulk` | Massenlöschung (auch durch `/purge`) mit **Textdatei** aller bekannten Nachrichten | Message Content, *Dateien anhängen* im Log-Kanal |
| `member_join` | Beitritt mit Kontoalter und Mitgliederzahl | Server Members |
| `member_leave` | Austritt mit Rollen; als **Kick** erkannt, wenn das Audit-Log einen zeigt | Server Members |
| `member_update` | Rollen, Nickname, Timeout geändert | Server Members |
| `member_ban`, `member_unban` | Bann/Entbannung mit Moderator und Grund | – |
| `role_create`, `role_delete`, `role_update` | Rollen, bei Änderung mit Name, Farbe, Berechtigungen (Hinzugefügtes/Entferntes) | – |
| `channel_create`, `channel_delete`, `channel_update` | Kanäle, bei Änderung mit Name, Thema, Slowmode, Kategorie … | – |
| `voice` | Sprachkanal betreten, verlassen, wechseln | – |
| `invite_create`, `invite_delete` | Einladung mit Ersteller, Limit, Ablauf | – |

| Befehl | Wirkung |
|---|---|
| `/logging set <event> <channel>` | Kanal für eine Art wählen; `all` setzt alle Arten, die möglich sind (die übrigen werden genannt) |
| `/logging off <event>` | Art ausschalten; `all` schaltet alles aus |
| `/logging status` | Welche Art wohin geht, mit Warnungen (fehlender Intent, fehlende Schreibrechte, gelöschter Kanal, kein Audit-Log-Recht) |
| `/logging ignore channel_add\|channel_remove <channel>` | Nachrichten aus diesem Kanal nicht protokollieren |
| `/logging ignore bots <on\|off>` | Nachrichten und Sprachaktivität von Bots weglassen (Standard: ja) |

Alle brauchen *Manage Server*. Hinweise:
- **Wer war es:** Moderator und Grund stammen aus dem Audit-Log und erscheinen nur, wenn der Bot *Audit-Log ansehen* darf.
  Bei gelöschten Nachrichten gibt es keinen Ausführenden (das Audit-Log liefert ihn dort nicht verlässlich).
- **Inhalt gelöschter Nachrichten** kennt der Bot nur aus seinem **Speicher** (`MESSAGE_CACHE_SIZE`, Standard 5000 Nachrichten).
  Nach einem Neustart oder bei sehr alten Nachrichten steht im Log „Inhalt nicht mehr bekannt“. Es werden keine Nachrichten in
  der Datenbank gespeichert.
- **Datenschutz:** Log-Einträge enthalten Nachrichteninhalte. Wähle Kanäle, die nur das Team lesen darf.
- Kein Eintrag entsteht für reines Umsortieren von Rollen/Kanälen, Stummschaltung im Sprachkanal und Link-Vorschauen.
  Das Löschen im Log-Kanal selbst wird nicht protokolliert.
- Kommen sehr viele Einträge auf einmal (Raid, Massenaktion), schreibt der Bot höchstens 100 pro Kanal in die Warteschlange
  und meldet die verworfenen am Ende in einer Zeile.
- Falls keine Einladungs-Einträge ankommen, dem Bot im Kanal das Recht *Kanäle verwalten* geben (Discord liefert diese
  Ereignisse sonst unter Umständen nicht).

### Leveling (Phase 5)

Standardmässig **aus**. Mit `/leveling enable` einschalten. Jede Nachricht gibt zufällige XP (Standard 15–25, höchstens
einmal pro 60 s und Mitglied), das Level steigt nach der **MEE6-Kurve**: von Level n zu n+1 braucht man 5·n² + 50·n + 100 XP.
Das Level wird immer aus den XP berechnet, nie extra gespeichert.

| Befehl | Wer | Wirkung |
|---|---|---|
| `/rank [member]` | alle | Rank-Card als Bild: Level, Fortschritt, Rang |
| `/leaderboard [page]` | alle | Bestenliste, 10 pro Seite |
| `/card set [accent] [background] [template]` | alle | Eigene Farben (Hex, z. B. `#ff8800`) und Vorlage (`solid`, `gradient`, `stripes`, `dots`) |
| `/card reset` | alle | Zurück zum Server-Standard |

**Einstellungen** (*Manage Server*):

| Befehl | Wirkung |
|---|---|
| `/leveling enable\|disable`, `/leveling status` | Ein/aus, Übersicht |
| `/leveling xp <min> <max>` | XP-Bereich pro Nachricht |
| `/leveling cooldown <seconds>` | Mindestabstand zwischen zwei XP-Gewinnen aus Nachrichten (0 = jede) |
| `/leveling multiplier <factor>` | Globaler Faktor 0,1–10 auf alle XP |
| `/leveling voice <enabled> [xp_min] [xp_max] [requires_company]` | Voice-XP |
| `/leveling announce <off\|current\|channel\|dm> [channel]` | Wo Level-Ups angekündigt werden |
| `/leveling message [template]` | Vorlage mit `{user}`, `{name}`, `{level}`, `{xp}` (leer = Standard) |
| `/leveling noxp channel_add\|channel_remove\|role_add\|role_remove` | Kanäle/Rollen ohne XP (Threads erben vom Elternkanal) |
| `/leveling boost channel_set\|channel_remove\|role_set\|role_remove` | XP-Faktor pro Kanal oder Rolle (Rollen **stapeln nicht**, die höchste zählt) |
| `/leveling reward add <level> <role>`, `remove`, `list`, `mode <stack\|replace>` | Belohnungsrollen: `stack` behält alle, `replace` nur die höchste |
| `/leveling sync` | Belohnungsrollen auf alle Mitglieder mit XP anwenden (nach geänderten Regeln oder einem Import) |
| `/leveling give <member> <amount>`, `set_level <member> <level>` | XP geben oder abziehen, Level setzen; die Rollen passen sich an |
| `/leveling reset_user <user>`, `reset_all <confirm>` | XP löschen; `reset_all` verlangt das Wort `RESET` |
| `/leveling import <file> [mode]` | Import aus Datei |

**Voice-XP:** pro volle Minute (Standard 10–20 XP), aber nur wenn man nicht stumm oder taub ist, nicht im AFK-Kanal sitzt
und (Standard) mindestens ein weiterer Mensch im Kanal ist. Bots bekommen nie XP und zählen nicht als Gesellschaft.
Kanal-Faktoren und Ausnahmen gelten auch hier. Mitglieder, die der Bot nicht kennt, bekommen kein XP; mit dem
*Server Members Intent* ist das zuverlässig.

**Import:** `/leveling import` nimmt JSON oder CSV. Pro Eintrag brauchst du eine Nutzer-ID und `xp` (Gesamt-XP) oder `level`
(wird in XP umgerechnet).
- CSV: erste Zeile mit Spaltennamen, z. B. `id,xp` oder `user_id;level` (Komma, Semikolon oder Tab).
- JSON: eine Liste `[{"id": "123", "xp": 4675}, …]` oder die Form `{"players": [{"id": "123", "xp": 4675, "level": 10}, …]}`
  (so sieht, soweit ich weiss, die Antwort der öffentlichen MEE6-Rangliste aus; ich kann das von hier nicht prüfen). Auch
  eine Liste mehrerer solcher Seiten wird gelesen.
- Wie du die Daten aus MEE6 holst, hängt von MEE6 ab. Im Zweifel baust du dir eine CSV aus der Rangliste.
- Modi: `max` (Standard: der höhere Wert bleibt, sicher für wiederholte Importe), `replace` (Import überschreibt), `add` (addiert).
  Unbrauchbare Zeilen werden gezählt und gemeldet; bei einem Fehler bleibt alles unverändert. Grenzen: 5 MB, 100 000 Zeilen.
- Danach `/leveling sync`, damit die Belohnungsrollen vergeben werden.

Die Rank-Card nutzt die Schrift DejaVu (im Docker-Image enthalten). Sehr exotische Zeichen im Namen (Emoji, manche Schriften)
erscheinen dort evtl. als Kästchen.

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
| `ENABLE_MESSAGE_CONTENT_INTENT` | `true` schaltet den privilegierten *Message Content Intent* ein (nur nach Freigabe im Developer Portal). Für Automod-Nachrichtenregeln und die Purge-Filter `contains`/`links` |
| `MESSAGE_CACHE_SIZE` | Nachrichten im Arbeitsspeicher (100 bis 100000, Standard 5000); nur daraus zeigt das Event-Log den Inhalt gelöschter Nachrichten |
| `ENABLE_MEMBERS_INTENT` | `true` schaltet den privilegierten *Server Members Intent* ein (nur nach Freigabe). Für Anti-Raid und Konto-Alter |
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
| *Manage Roles* | `/lock`, `/unlock` (ändern die Kanalrechte von @everyone); Leveling-Belohnungsrollen (die Rolle des Bots muss **über** den Belohnungsrollen stehen) |
| *Attach Files*, *Embed Links* | Leveling: `/rank` (Bild), `/leaderboard` |
| *Manage Messages* | Automod löscht Nachrichten |
| *View Audit Log* | Event-Log: Moderator und Grund bei Bann, Kick, Rollen, Kanälen (optional) |
| *Send Messages*, *Embed Links* (und *Attach Files* für `message_bulk`) in den Log-Kanälen | Event-Log |
| *Send Messages* | Automod-Hinweise und Meldungen (optional; ohne wird nur geloggt) |
| *Moderate Members*, *Kick Members*, *Ban Members* | Automod-Aktionen (Timeout, Raid/Konto-Alter) |

**Rollen-Reihenfolge:** Die Rolle des Bots muss in der Rollenliste **über** den Rollen der Mitglieder
stehen, die er moderieren soll. Sonst verweigert Discord die Aktion und der Bot meldet das.

Gateway-Intents ohne Freigabe: *Guilds*, *Guild Messages* (für XP pro Nachricht, ohne Inhalt), *Voice States*, *Guild Invites*, *Guild Moderation* und die Emoji-Daten (alle nicht privilegiert). Zusätzlich, jeweils im Developer Portal unter *Bot → Privileged
Gateway Intents* freischalten **und** per Umgebungsvariable einschalten (ohne Freigabe startet der Bot mit `true` nicht):

| Intent | Variable | Wofür |
|---|---|---|
| *Message Content* | `ENABLE_MESSAGE_CONTENT_INTENT=true` | Automod-Nachrichtenregeln, `/purge contains`/`links`, Log der Nachrichten-Arten |
| *Server Members* | `ENABLE_MEMBERS_INTENT=true` | Anti-Raid, Konto-Alter, Log von Beitritt, Austritt und Mitgliedsänderungen; zuverlässigere Voice-XP und `/leveling sync` |

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
