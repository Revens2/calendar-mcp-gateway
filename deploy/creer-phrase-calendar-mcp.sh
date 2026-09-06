#!/usr/bin/env bash
# Pose la phrase de passe du consentement de la passerelle Calendar.
# Seule l'empreinte PBKDF2 est ecrite dans /opt/calendar-mcp/calendar.env (0600).
# A executer sur le VPS :  sudo bash chemin/du/repo/deploy/creer-phrase-calendar-mcp.sh
# La phrase est saisie sans echo, jamais transmise a l'agent, jamais ecrite en clair.
set -euo pipefail

APP=/opt/calendar-mcp
FICHIER="$APP/calendar.env"
VENV="$APP/venv"

echo "Saisissez la phrase de passe du consentement Calendar (aucun echo, >= 12 caracteres)."
read -r -s -p "Phrase : " P1
echo
read -r -s -p "Confirmation : " P2
echo
if [ "$P1" != "$P2" ]; then
    echo "Erreur : les deux saisies different." >&2
    exit 1
fi
if [ "${#P1}" -lt 12 ]; then
    echo "Erreur : phrase trop courte (12 caracteres minimum)." >&2
    exit 1
fi

HASH=$(PYTHONPATH="$APP" "$VENV/bin/python" -c \
    'import sys; from calendar_gateway.oauth import hacher_phrase; print(hacher_phrase(sys.argv[1]))' "$P1")

if grep -q '^CALENDAR_MCP_CONSENT_HASH=' "$FICHIER"; then
    sed -i "s|^CALENDAR_MCP_CONSENT_HASH=.*|CALENDAR_MCP_CONSENT_HASH=$HASH|" "$FICHIER"
else
    printf '\nCALENDAR_MCP_CONSENT_HASH=%s\n' "$HASH" >> "$FICHIER"
fi
chown calendar-app:calendar-app "$FICHIER"
chmod 600 "$FICHIER"
systemctl restart calendar-mcp-gateway.service
echo "Empreinte mise a jour et service redemarre."
