#!/usr/bin/env bash
# Sichert die Postgres-Datenbank (alles, was zählt) in eine Datei unter backups/.
#
#   scripts/backup.sh                 # Docker Compose, wie im Betrieb
#   PG_EXEC= PGHOST=… scripts/backup.sh   # ohne Docker, gegen einen erreichbaren Postgres
#
# Redis wird absichtlich nicht gesichert: Zwischenspeicher, Sitzungen und Zähler; alles Dauerhafte
# (Einstellungen, Fälle, XP, Panels, geplante Jobs) liegt in Postgres. Die `.env` mit den Geheimnissen
# gehört in dein Passwort-Archiv, nicht in dieses Backup.
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP:-14}"
# Leer lassen, um pg_dump direkt aufzurufen (PGHOST, PGPASSWORD … aus der Umgebung).
PG_EXEC="${PG_EXEC-docker compose exec -T postgres}"
PG_USER="${POSTGRES_USER:-ggbot}"
PG_DB="${POSTGRES_DB:-ggbot}"

umask 077
mkdir -p "$DIR"
file="$DIR/ggbot-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial="$file.partial"
# Ein abgebrochener Lauf darf keine halbe Datei zurücklassen, die später für ein Backup gehalten wird.
trap 'rm -f "$partial"' EXIT

# shellcheck disable=SC2086  # PG_EXEC ist absichtlich ein Befehl aus mehreren Wörtern
$PG_EXEC pg_dump -U "$PG_USER" -d "$PG_DB" --format=custom --no-owner > "$partial"
# Ein Backup, das sich nicht lesen lässt, ist keines: gleich prüfen.
# shellcheck disable=SC2086
$PG_EXEC pg_restore --list < "$partial" > /dev/null
[ -s "$partial" ] || { echo "Backup ist leer" >&2; exit 1; }
mv "$partial" "$file"
trap - EXIT

# Die neuesten $KEEP behalten.
# shellcheck disable=SC2012
ls -1t "$DIR"/ggbot-*.dump | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f -- "$old"; done

echo "$file"
