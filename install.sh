#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/XSFORM/Stock_bot.git"
APP_DIR="/opt/stock_bot"
SERVICE_NAME="stockbot"
PYTHON_BIN="python3"

echo "[1/8] Updating system packages..."
sudo apt-get update -y
sudo apt-get install -y git curl $PYTHON_BIN $PYTHON_BIN-venv $PYTHON_BIN-pip

echo "[2/8] Creating app directory: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER":"$USER" "$APP_DIR"

if [ -d "$APP_DIR/.git" ]; then
  echo "[3/8] Repo already exists. Pulling latest..."
  cd "$APP_DIR"
  git pull
else
  echo "[3/8] Cloning repo..."
  git clone "$REPO_URL" "$APP_DIR"
  cd "$APP_DIR"
fi

echo "[4/8] Creating folders..."
sudo mkdir -p "$APP_DIR/data" "$APP_DIR/exports" "$APP_DIR/backups"
sudo chown -R "$USER":"$USER" "$APP_DIR/data" "$APP_DIR/exports" "$APP_DIR/backups"

ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "[5/8] Creating .env (will ask token/id)..."
  read -rp "Enter BOT_TOKEN: " BOT_TOKEN
  read -rp "Enter ADMIN_TG_ID: " ADMIN_TG_ID

  cat > "$ENV_FILE" <<EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_TG_ID=$ADMIN_TG_ID

DB_PATH=$APP_DIR/data/stock.db
EXPORT_DIR=$APP_DIR/exports
BACKUP_DIR=$APP_DIR/backups

CURRENCY=USD
DECIMALS=2
EOF
  echo ".env created at $ENV_FILE"
else
  echo "[5/8] .env already exists, skipping."
fi

echo "[6/8] Creating venv and installing requirements..."
if [ ! -d "$APP_DIR/venv" ]; then
  $PYTHON_BIN -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[7/8] Installing systemd service..."
sudo cp "$APP_DIR/app/systemd/stockbot.service" "/etc/systemd/system/$SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "[8/8] Done!"
echo "Check status:  sudo systemctl status $SERVICE_NAME --no-pager"
echo "Logs:          sudo journalctl -u $SERVICE_NAME -n 200 --no-pager"
