#!/usr/bin/env bash
# setup-https.sh — configure nginx + Basic Auth + HTTPS (Let's Encrypt) for Stock Bot.
#
# Run this script if HTTPS setup was skipped during install (e.g. non-interactive install),
# or to re-configure the domain / credentials on an existing server.
#
# Usage (interactive):
#   sudo bash /opt/stock_bot/scripts/setup-https.sh
#
# Usage (automated / non-interactive via env vars):
#   DOMAIN=example.com AUTH_USER=admin AUTH_PASS=secret \
#     sudo -E bash /opt/stock_bot/scripts/setup-https.sh

set -euo pipefail

# Accept values from environment or prompt interactively.
DOMAIN="${DOMAIN:-}"
AUTH_USER="${AUTH_USER:-}"
AUTH_PASS="${AUTH_PASS:-}"

if [ -z "$DOMAIN" ]; then
  read -rp "Enter domain name [admin.sonifer.net.ru]: " DOMAIN
  DOMAIN="${DOMAIN:-admin.sonifer.net.ru}"
fi

if [ -z "$AUTH_USER" ]; then
  read -rp "Enter Basic Auth username [admin]: " AUTH_USER
  AUTH_USER="${AUTH_USER:-admin}"
fi

if [ -z "$AUTH_PASS" ]; then
  read -rsp "Enter Basic Auth password: " AUTH_PASS
  echo ""
fi

echo ""
echo "==> Installing required packages..."
apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx apache2-utils

# Write nginx site config (HTTP block; certbot will add the HTTPS block)
NGINX_CONF="/etc/nginx/sites-available/stockweb"
echo "==> Writing nginx config: $NGINX_CONF"
tee "$NGINX_CONF" > /dev/null <<NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;

    auth_basic "Stock Bot";
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

# Create / update .htpasswd
echo "==> Creating /etc/nginx/.htpasswd for user '$AUTH_USER'..."
printf '%s\n' "$AUTH_PASS" | htpasswd -ci /etc/nginx/.htpasswd "$AUTH_USER"

# Enable site
if [ ! -L /etc/nginx/sites-enabled/stockweb ]; then
  ln -s "$NGINX_CONF" /etc/nginx/sites-enabled/stockweb
fi

# Validate config and reload nginx
nginx -t && systemctl reload nginx

# Obtain / renew Let's Encrypt certificate and configure HTTPS redirect
echo ""
echo "==> Running certbot — follow the prompts to obtain a free HTTPS certificate."
certbot --nginx -d "$DOMAIN" || \
  echo "⚠  certbot did not complete. Re-run later: sudo certbot --nginx -d $DOMAIN"

echo ""
echo "✅ Done!"
echo "   Site:            https://$DOMAIN"
echo "   Renew certs:     sudo certbot renew"
echo "   Add Basic Auth user: sudo htpasswd /etc/nginx/.htpasswd <username>"
