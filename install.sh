#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/XSFORM/Stock_bot.git"
APP_DIR="/opt/stock_bot"
PYTHON_BIN="python3"

echo "[1/9] Updating system packages..."
sudo apt-get update -y
sudo apt-get install -y git curl $PYTHON_BIN $PYTHON_BIN-venv $PYTHON_BIN-pip \
  nginx certbot python3-certbot-nginx apache2-utils

echo "[2/9] Creating app directory: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER":"$USER" "$APP_DIR"

if [ -d "$APP_DIR/.git" ]; then
  echo "[3/9] Repo already exists. Pulling latest..."
  cd "$APP_DIR"
  git pull
else
  echo "[3/9] Cloning repo..."
  git clone "$REPO_URL" "$APP_DIR"
  cd "$APP_DIR"
fi

echo "[4/9] Creating folders..."
sudo mkdir -p "$APP_DIR/data" "$APP_DIR/exports" "$APP_DIR/backups"
sudo chown -R "$USER":"$USER" "$APP_DIR/data" "$APP_DIR/exports" "$APP_DIR/backups"

ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "[5/9] Creating .env (will ask token/id)..."
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
  echo "[5/9] .env already exists, skipping."
fi

echo "[6/9] Creating venv and installing requirements..."
if [ ! -d "$APP_DIR/venv" ]; then
  $PYTHON_BIN -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[7/9] Installing systemd services..."
sudo cp "$APP_DIR/app/systemd/stockbot.service" "/etc/systemd/system/stockbot.service"
sudo cp "$APP_DIR/app/systemd/stockweb.service" "/etc/systemd/system/stockweb.service"
sudo systemctl daemon-reload
sudo systemctl enable stockbot stockweb
sudo systemctl restart stockbot stockweb

# ── Step 8: nginx + Basic Auth + HTTPS (interactive only) ──────────────────────
if [ -t 0 ]; then
  echo ""
  echo "[8/9] Setting up nginx + Basic Auth + HTTPS (Let's Encrypt)..."

  read -rp "Enter domain name [admin.sonifer.net.ru]: " DOMAIN
  DOMAIN="${DOMAIN:-admin.sonifer.net.ru}"

  read -rp "Enter Basic Auth username [admin]: " AUTH_USER
  AUTH_USER="${AUTH_USER:-admin}"

  read -rsp "Enter Basic Auth password: " AUTH_PASS
  echo ""

  # Write nginx site config (HTTP block; certbot will add the HTTPS block)
  NGINX_CONF="/etc/nginx/sites-available/stockweb"
  sudo tee "$NGINX_CONF" > /dev/null <<NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;

    auth_basic "Hasapcy";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # Pocket Price PWA — token-based access, no Basic Auth prompt.
    location /price {
        auth_basic off;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Pocket Price API — token-based access, no Basic Auth prompt.
    location /api/price {
        auth_basic off;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Static assets (icons, images) used by Pocket Price PWA.
    location /static/ {
        auth_basic off;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # All other routes (ERP UI, admin, products, stock, …) — Basic Auth required.
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF

  # Create .htpasswd (overwrite on fresh install)
  printf '%s\n' "$AUTH_PASS" | sudo htpasswd -ci /etc/nginx/.htpasswd "$AUTH_USER"

  # Enable site
  if [ ! -L /etc/nginx/sites-enabled/stockweb ]; then
    sudo ln -s "$NGINX_CONF" /etc/nginx/sites-enabled/stockweb
  fi

  # Validate config and reload nginx
  sudo nginx -t && sudo systemctl reload nginx

  # Obtain Let's Encrypt certificate and configure HTTPS redirect
  echo ""
  echo "Running certbot — follow the prompts to obtain a free HTTPS certificate."
  sudo certbot --nginx -d "$DOMAIN" || \
    echo "⚠  certbot did not complete. Re-run later: sudo certbot --nginx -d $DOMAIN"

  echo ""
  echo "✅ nginx + Basic Auth + HTTPS setup done."
  echo "   Site: https://$DOMAIN"
  echo "   To renew certificates: sudo certbot renew"
  echo "   To add more Basic Auth users: sudo htpasswd /etc/nginx/.htpasswd <username>"
else
  echo "[8/9] Non-interactive mode detected — skipping nginx/HTTPS setup."
  echo "      To configure HTTPS + Basic Auth later, run:"
  echo "        sudo bash $APP_DIR/scripts/setup-https.sh"
fi

echo ""
echo "[9/9] Done!"
echo "Check status:  sudo systemctl status stockbot stockweb --no-pager"
echo "Bot logs:      sudo journalctl -u stockbot -n 200 --no-pager"
echo "Web logs:      sudo journalctl -u stockweb -n 200 --no-pager"
