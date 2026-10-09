#!/bin/bash
# fleet-session node installer — the ONE command:
#   curl -sSL https://raw.githubusercontent.com/SuperInstance/fleet-session/main/node/install.sh | bash -s <node-name>
#
# What it does: generates this node's bearer token, writes config (600),
# fetches poll.sh, registers with the worker as PENDING, and installs
# supervision (systemd user service preferred, cron @reboot fallback).
# The node can do nothing until approved — approval is the access grant.
# Override the worker URL with FLEET_WORKER_URL env for testing.
set -euo pipefail

NODE="${1:-}"
if ! [[ "$NODE" =~ ^[a-z0-9-]{1,40}$ ]]; then
  echo "usage: install.sh <node-name>  (lowercase letters, digits, dashes)" >&2
  exit 1
fi

WORKER_URL="${FLEET_WORKER_URL:-https://fleet-session.casey-digennaro.workers.dev}"
RAW_BASE="https://raw.githubusercontent.com/SuperInstance/fleet-session/main"
DIR="$HOME/.fleet-session"
mkdir -p "$DIR"

# 1. token (256-bit bearer secret, never leaves this box except to the worker)
if command -v openssl >/dev/null 2>&1; then
  TOKEN=$(openssl rand -hex 32)
else
  TOKEN=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
fi

cat > "$DIR/config" <<EOF
WORKER_URL=$WORKER_URL
NODE=$NODE
TOKEN=$TOKEN
EOF
chmod 600 "$DIR/config"

# 2. poller
curl -sSL --fail "$RAW_BASE/node/poll.sh" -o "$DIR/poll.sh"
chmod +x "$DIR/poll.sh"

# 3. register as PENDING (approval happens out-of-band; nothing runs until then)
REG=$(curl -s -m 20 -X POST "$WORKER_URL/v1/nodes/register" \
  -H 'Content-Type: application/json' \
  --data-binary "{\"node\":\"$NODE\",\"token\":\"$TOKEN\"}" || true)
echo "register: $REG"

# 4. supervision
start_now() { nohup "$DIR/poll.sh" >/dev/null 2>&1 & echo "poller started (pid $!)"; }

if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  SVC_DIR="$HOME/.config/systemd/user"
  mkdir -p "$SVC_DIR"
  cat > "$SVC_DIR/fleet-session.service" <<EOF
[Unit]
Description=fleet-session node daemon
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=%h/.fleet-session/poll.sh
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now fleet-session.service
  echo "supervision: systemd user service (fleet-session.service)"
else
  (crontab -l 2>/dev/null | grep -v "fleet-session/poll.sh"; \
   echo "@reboot $DIR/poll.sh") | crontab -
  # best-effort: also start cron's daemon now if present but not running
  if command -v service >/dev/null 2>&1; then sudo -n service cron start >/dev/null 2>&1 || true; fi
  # kill any stale poller, start fresh
  pkill -f "fleet-session/poll.sh" 2>/dev/null || true
  start_now
  echo "supervision: cron @reboot (+ started now)"
fi

echo "OK: node '$NODE' installed. It is PENDING until approved."
echo "Config: $DIR/config (mode 600). Poller: $DIR/poll.sh"
