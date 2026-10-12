# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach
[SemVer](https://semver.org/lang/de/). Ein Eintrag je Version, die neueste oben. Ein Release entsteht
durch ein Tag `vX.Y.Z`; die Beschreibung kommt aus dem passenden Abschnitt hier
(`scripts/release-notes.sh`).

## [Unreleased]

### Neu (Phase 12a)
- Verifizierung (`/verification`): Rolle per Knopf oder Bild-Code, drei Versuche je zehn Minuten.
- Auto-Löschen (`/autodelete`): Nachrichten nach einer Frist löschen, mit Bestätigung, Schutz für Angeheftete und
  Panel-Nachrichten und Obergrenzen je Lauf.

## [0.11.0] - 2026-10-10

Erste getaggte Version: alle Feature-Phasen (1 bis 10) plus Härtung und Betrieb (11).

### Neu: Betrieb und Härtung (Phase 11)
- `/metrics` im Prometheus-Format (Bot lebt, Server, Latenz, Befehle, Fehler, Scheduler-Rückstand),
  nur mit `METRICS_TOKEN`, von nginx nicht nach aussen gereicht. `/healthz` nennt Version und Botzustand.
- Backup und Restore der Datenbank (`scripts/backup.sh`, `scripts/restore.sh`), in CI als echter Durchlauf.
- Sicherheitshärtung des Dashboards: ID-Parameter auf den Datenbankbereich begrenzt (vorher 500 auf der
  öffentlichen Rangliste), Sicherheitsköpfe und `no-store` an der API, Content-Security-Policy im
  nginx, Zugriffsprotokoll ohne Query (der Anmelde-Code stand sonst im Log), Avatare nur von Discords CDN,
  Besucheradresse für das Limit nur als echte IP.
- Container: unprivilegierter nginx, `cap_drop: ALL`, `no-new-privileges`, Schreibschutz für API und Web,
  Log-Rotation.
- Abhängigkeits-Audit (pip-audit, npm audit) in CI und wöchentlich; Vitest 5 behebt die gemeldeten
  Funde der Test-Werkzeuge.
- Release-Ablauf: Tag `vX.Y.Z` baut und veröffentlicht die Images und das GitHub-Release.

### Enthalten aus den früheren Phasen
- Moderation, Automod, Server-Log, Leveling (MEE6-Kurve), Rollen-Panels, Willkommen, Tickets,
  Twitch- und YouTube-Benachrichtigungen, eigene Befehle, Umfragen, Giveaways, Erinnerungen, Starboard,
  Geburtstage, Stat-Kanäle, geplante Nachrichten, `/privacy`.
- Dashboard: Discord-Login, schema-getriebene Einstellungen für alle Module, Änderungsprotokoll,
  Panel-Verwaltung, Kartenvorschau, öffentliche Rangliste.
