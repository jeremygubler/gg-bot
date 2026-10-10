#!/usr/bin/env bash
# Gibt den Abschnitt einer Version aus CHANGELOG.md aus (ohne die Überschrift).
#   scripts/release-notes.sh 0.11.0
# CHANGELOG=pfad.md liest eine andere Datei (Tests).
set -euo pipefail
cd "$(dirname "$0")/.."
version="${1:?Aufruf: $0 <version>}"
notes="$(awk -v v="$version" '
  /^## \[/ { if (found) exit; found = index($0, "[" v "]") > 0; next }
  found { print }
' "${CHANGELOG:-CHANGELOG.md}")"
# Leer heisst: Abschnitt fehlt oder ist leer. Ein Release ohne Beschreibung soll nicht entstehen.
if [ -z "$(printf '%s' "$notes" | tr -d '[:space:]')" ]; then
  echo "CHANGELOG.md hat keinen Abschnitt für Version $version" >&2
  exit 1
fi
printf '%s\n' "$notes"
