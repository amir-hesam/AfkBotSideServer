#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "  DonutSMP AFK Bot - VPS Worker Installer "
echo "  Target: Ubuntu 22.04 (1 Core / 1GB RAM) "
echo "=========================================="

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash install.sh)"
  exit 1
fi

INSTALL_DIR="/opt/donut-afk"
MCC_DIR="$INSTALL_DIR/mcc"
INSTANCES_DIR="$INSTALL_DIR/instances"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Create 2GB Swap if not present (critical for 1GB RAM VPS)
if ! swapon --show | grep -q '/swapfile'; then
  echo "[1/6] Creating 2GB swapfile for 1GB RAM stability..."
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl vm.swappiness=10
  echo 'vm.swappiness=10' >> /etc/sysctl.conf
else
  echo "[1/6] Swap already configured, skipping."
fi

# 2. Install system dependencies
echo "[2/6] Installing system packages..."
apt-get update -y
apt-get install -y curl wget python3 python3-venv python3-pip libicu-dev ca-certificates

# 3. Install Minecraft Console Client (MCC) official Linux x64 binary
echo "[3/6] Installing Minecraft Console Client (MCC)..."
mkdir -p "$MCC_DIR" "$INSTANCES_DIR"
cd "$MCC_DIR"
curl -fsSL https://mccteam.github.io/install.sh | sh
# Ensure binary is executable regardless of name (MinecraftClient or Mcc.Cli)
chmod +x "$MCC_DIR"/* || true

# 4. Copy worker files
echo "[4/6] Setting up Python Worker API..."
cp "$SCRIPT_DIR/worker.py" "$INSTALL_DIR/worker.py"
cp "$SCRIPT_DIR/DonutBotController.cs" "$INSTALL_DIR/DonutBotController.cs"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# 5. Create config.env if not exists
if [ ! -f "$INSTALL_DIR/config.env" ]; then
  API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
  WEBHOOK_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
  cat > "$INSTALL_DIR/config.env" <<EOF
WORKER_PORT=8080
API_KEY=$API_KEY
CPANEL_WEBHOOK_URL=https://YOUR-DOMAIN.COM/webhook_vps.php
WEBHOOK_SECRET=$WEBHOOK_SECRET
MCC_DIR=$MCC_DIR
INSTANCES_DIR=$INSTANCES_DIR
SCRIPT_TEMPLATE=$INSTALL_DIR/DonutBotController.cs
SERVER_HOST=donutsmp.net
EOF
  echo "Generated $INSTALL_DIR/config.env"
fi

# 6. Install systemd service
echo "[5/6] Creating systemd service..."
cat > /etc/systemd/system/donut-worker.service <<EOF
[Unit]
Description=DonutSMP AFK MCC Worker API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/config.env
ExecStart=$INSTALL_DIR/venv/bin/uvicorn worker:app --host 0.0.0.0 --port 8080 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable donut-worker
systemctl restart donut-worker

# Open firewall port if ufw is active
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 8080/tcp
fi

echo "[6/6] Installation complete!"
echo "------------------------------------------"
echo "Important: Edit /opt/donut-afk/config.env and set CPANEL_WEBHOOK_URL,"
echo "then run: systemctl restart donut-worker"
echo ""
echo "Your generated credentials (save these in Admin Panel Settings):"
grep -E '^(API_KEY|WEBHOOK_SECRET|WORKER_PORT)=' "$INSTALL_DIR/config.env"
echo "------------------------------------------"
