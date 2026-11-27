#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "  DonutSMP AFK Bot - Easy VPS Installer (Ubuntu)  "
echo "=================================================="

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash install.sh"
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
  sysctl vm.swappiness=10 >/dev/null
  echo 'vm.swappiness=10' >> /etc/sysctl.conf
else
  echo "[1/6] Swap already configured, skipping."
fi

# 2. Wait for any background apt lock and install dependencies
echo "[2/6] Installing system packages..."
while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
  echo "Waiting for background apt/unattended-upgrades to finish..."
  sleep 3
done
apt-get update -y
apt-get install -y curl wget python3 python3-venv python3-pip libicu-dev ca-certificates

# 3. Install Minecraft Console Client (MCC)
echo "[3/6] Installing Minecraft Console Client (MCC)..."
mkdir -p "$MCC_DIR" "$INSTANCES_DIR"
cd "$MCC_DIR"
curl -fsSL https://mccteam.github.io/install.sh | sh
chmod +x "$MCC_DIR"/* || true

# 4. Copy worker files and strip CRLF
echo "[4/6] Setting up Python Worker API..."
cp "$SCRIPT_DIR/worker.py" "$INSTALL_DIR/worker.py"
cp "$SCRIPT_DIR/DonutBotController.cs" "$INSTALL_DIR/DonutBotController.cs"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
sed -i 's/\r$//' "$INSTALL_DIR/worker.py" "$INSTALL_DIR/DonutBotController.cs" "$INSTALL_DIR/requirements.txt"

if [ ! -d "$INSTALL_DIR/venv" ]; then
  python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

# 5. Create or update config.env
echo "[5/6] Configuring environment..."
if [ ! -f "$INSTALL_DIR/config.env" ]; then
  API_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
  WEBHOOK_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(16))')

  read -rp "Enter your cPanel domain (e.g. example.com, or press Enter to set later): " DOMAIN_INPUT
  if [ -n "$DOMAIN_INPUT" ]; then
    DOMAIN_INPUT="${DOMAIN_INPUT#https://}"
    DOMAIN_INPUT="${DOMAIN_INPUT#http://}"
    DOMAIN_INPUT="${DOMAIN_INPUT%%/*}"
    WEBHOOK_URL="https://${DOMAIN_INPUT}/webhook_vps.php"
  else
    WEBHOOK_URL="https://YOUR-DOMAIN.COM/webhook_vps.php"
  fi

  cat > "$INSTALL_DIR/config.env" <<EOF
WORKER_PORT=8080
API_KEY=$API_KEY
CPANEL_WEBHOOK_URL=$WEBHOOK_URL
WEBHOOK_SECRET=$WEBHOOK_SECRET
MCC_DIR=$MCC_DIR
INSTANCES_DIR=$INSTANCES_DIR
SCRIPT_TEMPLATE=$INSTALL_DIR/DonutBotController.cs
SERVER_HOST=donutsmp.net
EOF
else
  echo "Existing config.env found, keeping your API keys."
fi

# 6. Install & start systemd service
echo "[6/6] Starting donut-worker systemd service..."
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
systemctl enable donut-worker >/dev/null 2>&1
systemctl restart donut-worker

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 8080/tcp >/dev/null 2>&1 || true
fi

PUBLIC_IP=$(curl -s --max-time 4 ifconfig.me || echo "YOUR_VPS_IP")
echo ""
echo "=================================================="
echo "  Installation Complete! Save these in cPanel:    "
echo "=================================================="
echo "VPS URL        : http://${PUBLIC_IP}:8080"
grep -E '^(API_KEY|WEBHOOK_SECRET|CPANEL_WEBHOOK_URL)=' "$INSTALL_DIR/config.env"
echo "=================================================="
