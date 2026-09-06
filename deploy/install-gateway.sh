#!/usr/bin/env bash
# Installation de la passerelle Calendar (a executer root, via `sudo bash -s`).
# Aucun secret ne transite par le terminal de l'operateur : le jeton statique est
# genere sur la machine dans /opt/calendar-mcp/calendar.env (0600).
set -euo pipefail

APP=/opt/calendar-mcp
SRC="$(cd "$(dirname "$0")/.." && pwd)"

# --- compte dedie --------------------------------------------------------------
if ! id calendar-app >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir "$APP" --shell /usr/sbin/nologin calendar-app
fi
install -d -o calendar-app -g calendar-app "$APP"
install -d -o calendar-app -g calendar-app "$APP/oauth" "$APP/tests"

# --- code + venv ----------------------------------------------------------------
rm -rf "$APP/calendar_gateway"
cp -r "$SRC/calendar_gateway" "$APP/"
cp "$SRC/requirements.txt" "$APP/"
cp -r "$SRC"/tests/* "$APP/tests/" 2>/dev/null || true

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"
chown -R calendar-app:calendar-app "$APP"

# --- environnement (secrets generes sur place, jamais affiches) -------------------
TOKEN=$("$APP/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
umask 077
cat > "$APP/calendar.env" <<ENV
CALENDAR_MCP_PORT=8790
CALENDAR_MCP_ISSUER=https://mcp.example.org
CALENDAR_MCP_UPSTREAM=http://127.0.0.1:3000
CALENDAR_MCP_TOKEN=${TOKEN}
CALENDAR_MCP_TOKEN_SCOPES=calendar:lecture calendar:ecriture
ENV
chown calendar-app:calendar-app "$APP/calendar.env"
chmod 600 "$APP/calendar.env"

# --- unite systemd ----------------------------------------------------------------
cat > /etc/systemd/system/calendar-mcp-gateway.service <<UNIT
[Unit]
Description=Calendar MCP auth gateway (OAuth colocalise + proxy upstream)
After=network-online.target
Wants=network-online.target

[Service]
User=calendar-app
Group=calendar-app
WorkingDirectory=$APP
EnvironmentFile=$APP/calendar.env
ExecStart=$APP/venv/bin/python -m calendar_gateway.server
Restart=always
RestartSec=3

# --- Isolation (calque sur vault-mcp.service) ---
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
RemoveIPC=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
# Boucle locale uniquement : nginx est le seul point d'entree externe.
IPAddressAllow=localhost
IPAddressDeny=any
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
SystemCallArchitectures=native
# Seul l'etat OAuth est ecrit (registre clients, jetons, consentements).
ReadWritePaths=$APP/oauth
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now calendar-mcp-gateway.service
sleep 2
systemctl --no-pager --lines=5 status calendar-mcp-gateway.service | tail -8
echo "OK : passerelle installee"
