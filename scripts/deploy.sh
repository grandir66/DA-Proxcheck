#!/usr/bin/env bash
# deploy.sh — scheletro (standard ~/Progetti/CLAUDE.md, OSX → Linux)
#
# I byte partono da `git archive origin/<ramo>`, mai dal working tree Mac.
# Copiare le guardie (sporchi, HEAD==origin, conferma live, backup, health)
# da Integra3cx/integra3cx/scripts/deploy.sh — non reinventarle.
#
#   bash scripts/deploy.sh              # dry-run, non tocca il server
#   bash scripts/deploy.sh --applica    # unpack + restart sul Linux
#
# Adattare: HOST, DIR, UNITA, SPEDITE (path nel repo da archiviare).

set -euo pipefail
cd "$(dirname "$0")/.."

echo "Scheletro: imposta HOST/DIR/SPEDITE e copia le guardie da INTEGRA."
echo "Vedi ~/Progetti/CLAUDE.md — sezione «Sviluppo su Mac, runtime Linux»."
exit 2
