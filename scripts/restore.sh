#!/usr/bin/env bash
# Spielt ein Backup von scripts/backup.sh zurück. ÜBERSCHREIBT die aktuelle Datenbank.
#
#   scripts/restore.sh backups/ggbot-20261010T080000Z.dump
#   scripts/restore.sh --yes <datei>        # ohne Rückfrage (Automatisierung)
#
# Ablauf: Datei prüfen → Bot/API anhalten → einspielen → Redis leeren → Migrationen → starten.
# Redis wird geleert, weil dort zwischengespeicherte Einstellungen stehen, die sonst noch eine Stunde
# den alten Stand zeigen würden. Folge: alle Dashboard-Nutzer müssen sich neu anmelden.
set -euo pipefail
cd "$(dirname "$0")/.."

YES=no
if [ "${1:-}" = "--yes" ]; then YES=yes; shift; fi
FILE="${1:-}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "Aufruf: $0 [--yes] <backup.dump>" >&2; exit 2; }

PG_EXEC="${PG_EXEC-docker compose exec -T postgres}"
PG_USER="${POSTGRES_USER:-ggbot}"
PG_DB="${POSTGRES_DB:-ggbot}"
STOP_CMD="${STOP_CMD-docker compose stop bot api}"
FLUSH_CMD="${FLUSH_CMD-docker compose exec -T redis redis-cli FLUSHDB}"
START_CMD="${START_CMD-docker compose up -d migrate bot api}"

# Zuerst prüfen, dann erst etwas anfassen.
# shellcheck disable=SC2086
$PG_EXEC pg_restore --list < "$FILE" > /dev/null || { echo "Datei ist kein lesbares Backup" >&2; exit 1; }

if [ "$YES" != yes ]; then
  echo "Das ersetzt die Datenbank '$PG_DB' durch den Stand aus $FILE."
  printf "Zum Fortfahren RESTORE eintippen: "
  read -r answer
  [ "$answer" = "RESTORE" ] || { echo "Abgebrochen." >&2; exit 1; }
fi

# Die Befehle kommen aus der Umgebung des Betreibers und dürfen Anführungszeichen enthalten.
eval "$STOP_CMD"
# --clean --if-exists räumt vorhandene Objekte weg; ohne Dienste, die verbunden sind, klappt das sauber.
# shellcheck disable=SC2086
$PG_EXEC pg_restore -U "$PG_USER" -d "$PG_DB" --clean --if-exists --no-owner --exit-on-error < "$FILE"
eval "$FLUSH_CMD"
eval "$START_CMD"
echo "Zurückgespielt: $FILE"
