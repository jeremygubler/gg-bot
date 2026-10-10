# GG-Bot

Selbst gehosteter Discord-Bot als Ersatz für MEE6: Moderation, Automod, Logging, Leveling,
Rollen, Tickets, Benachrichtigungen und mehr, mit Web-Dashboard. Läuft per Docker Compose.

Architektur und Entscheidungen: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Stand: **Phase 10 (Dashboard) komplett**. Die weiteren Feature-Phasen folgen.

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

### Rollen-Panels (Phase 6)

Drei Arten, Rollen selbst zu wählen. Panels baust du Schritt für Schritt mit Slash-Befehlen:
`/rolepanel create`, dann `/rolepanel add` für jede Rolle, dann `/rolepanel publish`.

| Art | So wählen Mitglieder |
|---|---|
| `button` | Ein Button pro Rolle (bis 25) |
| `select` | Ein Auswahlmenü (bis 25 Einträge, mit Beschreibung) |
| `reaction` | Reaktion auf die Nachricht (bis 20 Emojis) |

**Modi:** `normal` (umschalten), `unique` (genau eine Rolle des Panels; eine neue ersetzt die alte), `verify` (nur
hinzufügen, nie entziehen, z. B. für Regel-Bestätigung).

| Befehl | Zweck |
|---|---|
| `/rolepanel create <title> <kind> [mode] [description] [message_id] [channel]` | Panel anlegen. Mit `message_id` und `channel` wird eine **vorhandene** Nachricht zum Reaktions-Panel (der Bot setzt keine eigene) |
| `/rolepanel add <panel> <role> [label] [emoji] [description]` | Rolle hinzufügen; Buttons und Menüeinträge brauchen `label`, Reaktionen ein `emoji` (normal oder eigenes des Servers) |
| `/rolepanel remove <panel> <role>` | Rolle entfernen; die veröffentlichte Nachricht wird angepasst |
| `/rolepanel list` | Alle Panels (höchstens 25 pro Server) |
| `/rolepanel publish <panel> [channel]` | Panel posten; erneutes Veröffentlichen ersetzt die alte Nachricht |
| `/rolepanel delete <panel>` | Panel und seine Nachricht löschen (eine übernommene fremde Nachricht bleibt) |

Standardrecht: *Rollen verwalten*. Wer ein Panel baut, kann nur Rollen unterhalb der eigenen höchsten Rolle aufnehmen
(der Server-Besitzer alle), und der Bot nur Rollen unterhalb seiner eigenen. Die Buttons und Menüs überleben Neustarts
des Bots. Klicks haben 2 s Cooldown pro Mitglied.

### Willkommen, Abschied und Autorollen (Phase 6)

Standardmässig **aus**. Standardrecht für alle `/welcome`-Befehle: *Server verwalten*.

| Befehl | Zweck |
|---|---|
| `/welcome join set <channel> [message] [card]` / `off` | Willkommensnachricht (mit Bild, wenn `card`) |
| `/welcome leave set <channel> [message]` / `off` | Abschiedsnachricht |
| `/welcome dm set [message]` / `off` | Direktnachricht an neue Mitglieder |
| `/welcome autorole add <role> [bots]` / `remove <role>` | Rolle für jedes neue Mitglied (`bots`: stattdessen für Bots), höchstens 10 je Liste |
| `/welcome card [accent] [background] [template]` | Farben (Hex, z. B. `#ff8800`) und Vorlage des Willkommensbilds |
| `/welcome preview` | Zeigt dir die Begrüssung, nur für dich sichtbar; sendet nichts in den Kanal |
| `/welcome status` | Übersicht |

**Platzhalter** (höchstens 300 Zeichen, unbekannte werden abgelehnt): Willkommen `{user}` (Erwähnung), `{name}`, `{server}`,
`{count}` (Mitgliederzahl); Abschied `{name}`, `{server}`, `{count}`; DM `{name}`, `{server}`.

**Verhalten:**
- **Mitglieder-Screening:** Wer das Screening des Servers noch nicht bestanden hat, bekommt weder Nachricht noch DM noch
  Autorolle. Alles folgt, sobald er es besteht (so umgeht eine Autorolle das Screening nicht).
- **Bots** werden nicht begrüsst und haben keinen Abschied; sie bekommen nur die Bot-Autorollen.
- **Raid-Modus** (Automod): Begrüssung, Abschied, DM und Autorollen für Menschen ruhen, damit Raider weder den Kanal fluten
  noch Rechte bekommen.
- Wer innerhalb von 2 s wieder geht, wird nicht begrüsst. Höchstens 20 Nachrichten pro Minute und Server; darüber fallen
  nur die Nachrichten weg, Autorollen werden weiter vergeben.
- Erwähnt wird nur das neue Mitglied, nie @everyone oder Rollen (ein Nickname wie `@everyone` pingt nicht).
- Das Willkommensbild nutzt Farben und eingebaute Vorlagen (`solid`, `gradient`, `stripes`, `dots`), keine hochgeladenen Bilder.

### Tickets (Phase 7)

Jedes Ticket ist ein **privater Kanal**, den nur der Ersteller, die Support-Rollen der Kategorie und der Bot sehen. Aufbau:
`/ticketpanel create`, dann `/ticketpanel category add` (ein Button pro Kategorie, bis 10), dann `/ticketpanel publish`.
Standardrecht für `/ticketpanel`: *Server verwalten*. Die `/ticket`-Befehle gelten im Ticket; wer sie nutzen darf, prüft der Bot dort.

| Befehl | Zweck |
|---|---|
| `/ticketpanel create <title> [description]` | Panel anlegen (bis 10 pro Server) |
| `/ticketpanel category add <panel> <name> <role> [emoji] [description] [parent] [welcome] [questions]` | Kategorie: `role` ist die Support-Rolle, `parent` die Discord-Kategorie für die Kanäle, `welcome` der Begrüssungstext (`{user}` `{category}` `{number}`), `questions` bis zu 5 Fragen getrennt durch `;` (erscheinen als Formular vor dem Ticket) |
| `/ticketpanel category remove\|role_add\|role_remove` | Kategorie entfernen; weitere Support-Rollen (bis 10; die letzte lässt sich nicht entfernen; `@everyone` ist nicht erlaubt) |
| `/ticketpanel list`, `publish <panel> [channel]`, `delete <panel>` | Übersicht; posten (ersetzt die alte Nachricht); löschen (bestehende Tickets bleiben) |
| `/ticketpanel settings …`, `status` | Einstellungen und Statistik, siehe unten |
| `/ticket close [reason]` oder Button | Ticket schliessen (Ersteller, Support, Verwalter) |
| `/ticket claim`, `unclaim` | Ticket übernehmen / abgeben (nur Support; ein Ticket hat höchstens einen Bearbeiter, bei gleichzeitigem Klick gewinnt genau einer) |
| `/ticket add <member>`, `remove <member>` | Mitglied zum Ticket holen / entfernen (nur Support; der Ersteller bleibt) |
| `/ticket transcript <number>` | Transkript eines geschlossenen Tickets (Ersteller, Support, Verwalter) |

**Einstellungen** (`/ticketpanel settings`, jeder Parameter einzeln änderbar): `log_channel` (Transkript und Schliess-Meldung),
`max_open` (offene Tickets pro Mitglied, Standard 3), `auto_close_hours` (0 = aus), `close_delay` (Sekunden bis der Kanal gelöscht wird,
Standard 10), `feedback` (Bewertung per DM, 1–5 Sterne), `dm_transcript`, `creators_may_close`, `ping_support`, `retention_days`
(Transkripte nach so vielen Tagen löschen, 0 = behalten), `name_template` (`{number}` `{category}` `{user}`, Standard `ticket-{number}`).

**Schliessen:** Der Bot liest den Kanalverlauf (bis 2000 Nachrichten), erzeugt eine eigenständige HTML-Datei, speichert sie zusammen mit dem
Geschlossen-Status in einer Transaktion und schickt sie in den Log-Kanal und (wenn eingestellt) per DM an den Ersteller. **Erst danach** wird der
Kanal gelöscht (nach `close_delay`). Scheitert das Transkript, bleibt das Ticket offen und der Kanal erhalten. Wird ein Ticket-Kanal von Hand
gelöscht, wird das Ticket ohne Transkript geschlossen. Ohne *Message Content Intent* sind fremde Nachrichten im Transkript leer
(`ENABLE_MESSAGE_CONTENT_INTENT=true`, siehe Intents).

**Datenschutz:** Transkripte enthalten die Ticket-Nachrichten und liegen in der Datenbank, bis `retention_days` abläuft (oder für immer bei 0).
Das Transkript ist eine Datei ohne Skripte; Nutzereingaben sind maskiert.

### Benachrichtigungen: Twitch und YouTube (Phase 8)

Der Bot kündigt Streams und neue Videos in einem Kanal deiner Wahl an. Standardrecht für `/notify`: *Server verwalten*.

| Befehl | Zweck |
|---|---|
| `/notify twitch add <streamer> <channel> [message] [role]` | Ankündigen, wenn der Streamer live geht. `streamer` ist Name, `@Name` oder Kanal-Link |
| `/notify youtube add <youtube_channel> <channel> [message] [role]` | Neue Videos ankündigen. Eingabe: Kanal-ID (`UC…`), `@Handle` oder Kanal-Link |
| `/notify twitch remove`, `/notify youtube remove` | Wieder entfernen (mit Auswahl der vorhandenen) |
| `/notify list` | Alle Benachrichtigungen des Servers (bis 25) |
| `/notify test <subscription>` | Schickt eine Beispiel-Meldung in den Zielkanal: prüft Rechte, Rolle und Vorlage |

**Vorlagen** (höchstens 300 Zeichen, unbekannte Platzhalter werden abgelehnt): Twitch `{streamer}` `{title}` `{game}` `{url}` `{viewers}`;
YouTube `{channel}` `{title}` `{url}`. Ohne Vorlage gilt ein Standardtext. Mit `role` wird bei jeder Meldung diese Rolle erwähnt; sonst wird **niemand**
gepingt (auch dann nicht, wenn ein Stream-Titel `@everyone` enthält).

**Verhalten:**
- **Was schon da ist, wird nicht angekündigt:** Läuft der Streamer beim Hinzufügen gerade, oder hat der Kanal ältere Videos, gibt es dafür keine Meldung.
- **Twitch** wird alle 60 s abgefragt (bis 100 Streamer pro Anfrage, auch über alle Server zusammen). Dieselbe Stream-ID wird nur einmal angekündigt. Nach dem Ende
  (3 Abfragen in Folge offline, so überstehen kurze Verbindungsabbrüche) wird die Meldung als „Stream beendet“ markiert.
- **YouTube** wird alle 5 Minuten pro Kanal abgefragt (öffentlicher Feed, kein Schlüssel). Es werden nur Videos der letzten 48 Stunden angekündigt, höchstens 3 pro
  Abfrage, älteste zuerst. Erkannt werden neue Uploads; einen Live-Status liefert der Feed nicht.
- Ist ein Anbieter nicht erreichbar, ändert sich nichts („nicht erreichbar“ ist nicht „offline“), und die nächste Abfrage holt alles nach.
- Kann der Bot nicht senden (Rechte oder Kanal fehlen), versucht er es für diese Meldung nicht dauernd wieder; bei einem vorübergehenden Fehler schon.

**Twitch einrichten (Betreiber):** Auf <https://dev.twitch.tv/console> eine Anwendung anlegen und `TWITCH_CLIENT_ID` und `TWITCH_CLIENT_SECRET` in der `.env` setzen.
Ohne beides sind die Twitch-Befehle aus (YouTube geht trotzdem). Es wird keine öffentliche URL gebraucht.

### Eigene Befehle (Phase 9a)

Eigene Textbefehle pro Server. Aufruf mit **einem** Befehl: `/c <name> [args]` (mit Autovervollständigung der Befehle des Servers). So braucht ein neuer
Befehl keine Neu-Synchronisation und keinen Message-Content-Intent.

| Befehl | Zweck |
|---|---|
| `/c <name> [args]` | Einen eigenen Befehl ausführen (alle Mitglieder) |
| `/customcmd add <name> <response> [ephemeral] [role] [cooldown]` | Anlegen (bis 50, Name `a-z 0-9 - _`, höchstens 32 Zeichen). Standardrecht *Server verwalten* |
| `/customcmd edit`, `remove`, `list`, `info` | Ändern (`clear_role` entfernt die Rollenpflicht), löschen, auflisten, Details |

**Antwort** (bis 1500 Zeichen, `\n` ergibt einen Zeilenumbruch): Text mit den Platzhaltern `{user}` (Erwähnung des Aufrufers), `{name}`, `{server}`, `{channel}`, `{args}`, `{members}`,
`{uses}` (Zähler) und `{random:a|b|c}` (zufällige Wahl, bis 20 Möglichkeiten). `{{` und `}}` sind die Klammern selbst. Mehr gibt es bewusst nicht: keine Bedingungen, keine
Funktionen, kein Code. Unbekannte Platzhalter werden beim Speichern abgelehnt. Was jemand als `args` tippt, wird nur eingesetzt, nie ausgewertet, und es wird **niemand** gepingt
(auch nicht über `@everyone` oder `<@123>` in den Argumenten). Optional: nur mit einer Rolle nutzbar, nur für den Aufrufer sichtbar, Wartezeit pro Mitglied und Befehl (Standard 5 s).

### Umfragen (Phase 9a)

`/poll create <question> <options> [duration] [multiple]` legt eine **native Discord-Umfrage** an (2–10 Antworten, getrennt durch `;`, Dauer 1 Stunde bis 32 Tage, Standard 24h).
Discord zählt, beendet und zeigt das Ergebnis selbst, auch wenn der Bot gerade aus ist; der Bot speichert dafür nichts. `/poll end <message_id>` beendet eine Umfrage **des Bots** vorzeitig.
Standardrecht: *Umfragen senden*.

### Giveaways (Phase 9a)

`/giveaway start <prize> <duration> [winners] [channel] [role]`, `end`, `reroll [count]`, `cancel`, `list`. Standardrecht *Server verwalten*.
Mitglieder klicken auf **Teilnehmen** (zweiter Klick zieht zurück; mit `role` nur für Mitglieder mit dieser Rolle). Das Ende steht fest (Scheduler, übersteht Neustarts);
`end` zieht sofort. Gezogen wird zufällig, aber **nur unter denen, die beim Ende noch auf dem Server sind**, keine Bots sind und die geforderte Rolle haben. Ein Giveaway wird genau
einmal gezogen. `reroll` zieht neue Gewinner, nie die alten. Höchstens 10 laufende Giveaways, 1–20 Gewinner, bis 60 Tage.

### Erinnerungen (Phase 9a)

`/remind me <when> <text>` (z. B. `30m`, `2h`, `1d`; 1 Minute bis 1 Jahr), `/remind list`, `/remind cancel`. Die Erinnerung kommt in den Kanal, in dem du sie angelegt hast (nur du
wirst gepingt); geht das nicht, per DM. Bis 25 offene pro Person und Server.

### Starboard (Phase 9b)

Nachrichten mit genug Sternen werden in einen eigenen Kanal kopiert (Text, Autor, erstes Bild, Link zum Original, Sternzahl). Standardmässig **aus**. Standardrecht *Server verwalten*.

| Befehl | Zweck |
|---|---|
| `/starboard set <channel> [emoji] [threshold]` | Einschalten; Emoji (Standard ⭐, auch eigene Emojis) und Schwelle (Standard 3) |
| `/starboard options [self_star] [ignore_nsfw]` | Dürfen Autoren die eigene Nachricht sternen (Standard nein)? NSFW-Kanäle ignorieren (Standard ja) |
| `/starboard ignore <channel>` | Umschalten: Sterne in diesem Kanal zählen nicht |
| `/starboard off`, `status` | Ausschalten, Übersicht |

Gezählt wird **jeder Nutzer höchstens einmal**; Bots, der Starboard-Kanal selbst (sonst kopierten sich Kopien endlos) und ignorierte Kanäle zählen nicht. Fällt der Stand unter die
Schwelle, verschwindet die Kopie und kommt bei genug Sternen wieder. Wird das Original gelöscht, wird auch die Kopie gelöscht. **Gezählt werden nur Sterne ab dem Einschalten**: ältere
Reaktionen kennt der Bot nicht. Ohne *Message Content Intent* zeigt die Kopie keinen Text, nur Autor, Bild und Link.

### Geburtstage und Privatsphäre (Phase 9b)

| Befehl | Zweck |
|---|---|
| `/birthday set <day> <month>`, `remove`, `show [member]`, `upcoming` | Für alle. Gespeichert werden **nur Tag und Monat, kein Jahr**: aus den Daten lässt sich kein Alter ableiten |
| `/birthdayconfig set <channel> [role] [message]`, `off`, `clear_role` | Standardrecht *Server verwalten*: Kanal der Glückwünsche, optional eine Rolle für den Tag, Text mit `{user}` `{name}` `{server}` |
| `/privacy show`, `/privacy delete <confirm>` | Zeigt, was über dich auf diesem Server gespeichert ist (XP, Geburtstag, offene Erinnerungen), und löscht es (Bestätigung: das Wort `DELETE`) |

Der Tag wechselt nach der **Zeitzone des Servers** (`/config timezone`). Pro Tag und Server gibt es genau eine Ankündigung, auch nach einem Neustart. Wer am 29. Februar geboren ist,
feiert in anderen Jahren am 28. Februar. Die Rolle haben genau die Geburtstagskinder von heute (der Bot braucht dafür *Rollen verwalten*, seine Rolle muss über ihr stehen). Gepingt wird nur
das Geburtstagskind. Bots haben keinen Geburtstag.

### Stat-Kanäle (Phase 9b)

`/statchannel add <kind> [template]` legt einen Sprachkanal an (niemand kann beitreten), dessen Name eine Zahl zeigt: `members`, `humans`, `bots`, `channels`, `roles`, `boosts`. Die Vorlage braucht
`{count}`, z. B. `Mitglieder: {count}`. Der Name wird alle 10 Minuten aktualisiert (Discord erlaubt nur wenige Umbenennungen), und nur, wenn er sich geändert hat. `humans` und `bots` brauchen den
*Server Members Intent* (`ENABLE_MEMBERS_INTENT=true`), weil Discord diese Zahlen sonst nicht verlässlich liefert. `/statchannel remove`, `list`. Standardrecht *Kanäle verwalten*, bis 5 pro Server.

### Geplante Nachrichten (Phase 9b)

`/schedule once <when> <channel> <text>` sendet einmal später, `/schedule repeat <every> <channel> <text> [first]` immer wieder (Abstand 5 Minuten bis 30 Tage), `list`, `remove`. Standardrecht
*Server verwalten*, bis 25 aktive. `\n` im Text ergibt einen Zeilenumbruch. Eine geplante Nachricht pingt **nie** (auch kein `@everyone` im Text). War der Bot länger aus, werden verpasste
Wiederholungen nicht nachgeholt (keine Flut), die nächste kommt im alten Raster. Zeiten sind relativ ("in 2h"); feste Uhrzeiten wie "jeden Tag um 9" gibt es nicht.

## Dashboard (Phase 10)

Das Dashboard läuft unter `http://<host>:8080`. Die API (`api`-Dienst, im Browser unter `/api/…`)
meldet über Discord an und liest und schreibt die Server-Einstellungen aller Module; die
Oberfläche baut ihre Formulare aus den Schemas der Einstellungsmodelle. Sie umfasst Serverauswahl
(mit Einladungslink für Server ohne Bot), ein Formular je Modul (General, Moderation, Automod,
Server-Log, Leveling, Willkommen, Tickets, Geburtstage, Starboard), Kanal- und Rollenauswahl aus
dem Bot-Snapshot, Fehleranzeige am Feld und das Änderungsprotokoll. Panels, Kartenvorschau und das
öffentliche Leaderboard sind in 10c dazugekommen (unten).

### Panels, Vorschau und öffentliche Rangliste (Phase 10c)

- **Panels** (*Rollen-Panels*, *Ticket-Panels* in der Seitenleiste): anlegen, bearbeiten, löschen, Einträge
  bzw. Kategorien und Support-Rollen pflegen und in einem Kanal veröffentlichen. Der Bot setzt die Nachricht
  und meldet Erfolg oder Fehler (z. B. fehlende Rechte im Kanal) zurück ins Dashboard. Änderungen an einem
  veröffentlichten Panel aktualisieren die Discord-Nachricht. **Rollen-Panels dürfen nur Eigentümer und
  Administratoren ändern** (sie vergeben Rollen); Ticket-Panels jeder mit *Server verwalten*. Angeboten werden
  nur Rollen, die der Bot vergeben darf (nicht @everyone, nicht verwaltet, unter seiner höchsten Rolle).
- **Kartenvorschau:** unter *Leveling* und *Willkommen & Abschied* zeigt das Dashboard die Karte so, wie der
  Bot sie zeichnet (mit Beispieldaten), und folgt den Farben und der Vorlage im Formular.
- **Öffentliche Rangliste:** *Leveling → Öffentliche Rangliste* gibt die Top 100 unter
  `<DASHBOARD_URL>/leaderboard/<Server-ID>` ohne Anmeldung frei (standardmässig aus). Angezeigt werden Platz,
  Anzeigename, Avatar, Level und XP; der Bot legt sie nur kurz im Zwischenspeicher ab, in der Datenbank
  steht nichts davon. Mitglieder, die den Server verlassen haben, fehlen; wer `/privacy delete` nutzt,
  ebenfalls. Steht ein weiterer Reverse Proxy vor dem Dashboard, `PROXY_HOPS=2` setzen, sonst teilen sich alle
  Besucher ein Abruflimit.

Einrichten:

1. Im Developer Portal unter *OAuth2* einen Redirect eintragen:
   `<DASHBOARD_URL>/api/auth/callback`; Client-ID und -Secret in die `.env`
   (`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`).
2. `DASHBOARD_URL` auf die öffentliche Adresse setzen (https), `SESSION_SECRET` mit
   `openssl rand -hex 32` erzeugen. Ohne diese Werte läuft die API, bietet aber kein Login an.
3. Zugriff haben Server-Eigentümer und Mitglieder mit *Server verwalten* oder *Administrator*,
   aber nur auf Servern, auf denen der Bot ist. Entzogene Rechte wirken nach höchstens einer Minute.

Jede Änderung wird mit Vorher/Nachher protokolliert (im Dashboard unter *Änderungsprotokoll*, `GET /guilds/{id}/audit`).
Discord-IDs stehen im JSON als Strings (JavaScript verfälscht Zahlen über 2^53).

## Einstellungen

Alle Einstellungen kommen aus der Umgebung (`.env`), siehe [.env.example](.env.example).
Pro Server gespeicherte Einstellungen (Modul `general`: `embed_color`) werden im
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
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | Zugang zur Twitch-API für die Twitch-Benachrichtigungen (das Secret wird nie geloggt) |
| `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` | OAuth2-Zugang des Dashboards (Secret wird nie geloggt) |
| `DASHBOARD_URL` | öffentliche Adresse des Dashboards, daraus entsteht der Redirect `<URL>/api/auth/callback` |
| `SESSION_SECRET` | signiert das Sitzungs-Cookie, mind. 32 Zeichen (wird nie geloggt) |
| `PROXY_HOPS` | Proxys vor der API, die ihre Adresse an `X-Forwarded-For` anhängen (1 = nginx aus Compose, 2 = zusätzlich dein Reverse Proxy); für das Limit der öffentlichen Rangliste |
| `COOKIE_SECURE` | `false` nur für lokale Entwicklung ohne https |
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
| *Manage Messages* | Automod löscht Nachrichten; Reaktions-Panels im Modus `unique` (räumen die übrigen Reaktionen des Mitglieds auf) |
| *Send Messages*, *Embed Links* im Zielkanal | Benachrichtigungen (mit *Mention Roles* oder einer erwähnbaren Rolle, wenn eine Rolle erwähnt werden soll) |
| *Manage Channels* | Stat-Kanäle: Kanal anlegen und umbenennen |
| *Manage Roles* | Geburtstagsrolle (Rolle des Bots über ihr) |
| *Send Messages*, *Embed Links* im Starboard- und Geburtstagskanal | Starboard-Kopien, Glückwünsche |
| *Manage Channels*, *Manage Roles*, *Manage Messages* | Tickets: private Kanäle mit Rechten anlegen, löschen und die Panel-Rechte setzen |
| *Send Messages*, *Embed Links*, *Attach Files*, *Read Message History* im Log-Kanal und im Ticket | Tickets: Begrüssung, Transkript-Datei |
| *Manage Roles* | Rollen-Panels und Autorollen: die Rolle des Bots muss **über** den vergebenen Rollen stehen |
| *Send Messages*, *Embed Links*, *Add Reactions* im Panel-Kanal | `/rolepanel publish` (Reaktionen nur bei Reaktions-Panels) |
| *Send Messages*, *Attach Files* im Willkommenskanal | Willkommensnachricht mit Bild, Abschied |
| *View Audit Log* | Event-Log: Moderator und Grund bei Bann, Kick, Rollen, Kanälen (optional) |
| *Send Messages*, *Embed Links* (und *Attach Files* für `message_bulk`) in den Log-Kanälen | Event-Log |
| *Send Messages* | Automod-Hinweise und Meldungen (optional; ohne wird nur geloggt) |
| *Moderate Members*, *Kick Members*, *Ban Members* | Automod-Aktionen (Timeout, Raid/Konto-Alter) |

**Rollen-Reihenfolge:** Die Rolle des Bots muss in der Rollenliste **über** den Rollen der Mitglieder
stehen, die er moderieren soll. Sonst verweigert Discord die Aktion und der Bot meldet das.

Gateway-Intents ohne Freigabe: *Guilds*, *Guild Messages* (für XP pro Nachricht, ohne Inhalt), *Guild Reactions* (Reaktions-Rollen), *Voice States*, *Guild Invites*, *Guild Moderation* und die Emoji-Daten (alle nicht privilegiert). Zusätzlich, jeweils im Developer Portal unter *Bot → Privileged
Gateway Intents* freischalten **und** per Umgebungsvariable einschalten (ohne Freigabe startet der Bot mit `true` nicht):

| Intent | Variable | Wofür |
|---|---|---|
| *Message Content* | `ENABLE_MESSAGE_CONTENT_INTENT=true` | Automod-Nachrichtenregeln, `/purge contains`/`links`, Log der Nachrichten-Arten, **Inhalt der Ticket-Transkripte** |
| *Server Members* | `ENABLE_MEMBERS_INTENT=true` | Anti-Raid, Konto-Alter, Log von Beitritt, Austritt und Mitgliedsänderungen; zuverlässigere Voice-XP und `/leveling sync`; **Willkommen, Abschied und Autorollen** (ohne den Intent bleiben sie stumm) |

## Entwicklung

Voraussetzungen: Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL 16, Redis 7, Node 22.

```bash
uv sync --all-extras
export DATABASE_URL=postgresql+asyncpg://ggbot:ggbot@localhost:5432/ggbot
uv run alembic upgrade head

uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest          # braucht Postgres (DB ggbot_test) und Redis (DB 15)

cd web && npm ci && npm run lint && npm test && npm run build
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
