# Stock_bot

Telegram-бот для личного учета товаров (склад/магазин), продаж (корзина), долгов, PDF-инвойсов и бэкапов.

## Требования к серверу

### Временная зона

Timestamps в таблицах `carts` и `invoices` записываются по **локальному времени сервера** (`datetime('now','localtime')` в SQLite).
Для корректного отображения времени в UI установите системную временную зону **до** запуска бота:

```bash
timedatectl set-timezone Asia/Ashgabat
```

> **Примечание:** бот не меняет системную TZ автоматически — это необходимо настроить вручную на сервере.

## Change log

История изменений ведётся в файле [CHANGELOG.md](CHANGELOG.md).

### Как добавить запись после мержа PR

После каждого смержённого PR добавьте строку в `CHANGELOG.md`:

1. Найдите или создайте секцию с текущей датой `## YYYY-MM-DD`.
2. Добавьте пункт: `- <краткое описание> ([#N](https://github.com/XSFORM/Stock_bot/pull/N))`.

### Полезные команды git

```bash
# Список последних 20 мерджей с датой и заголовком
git log --merges --oneline --format="%as  %s" -20

# Мерджи за последние 7 дней
git log --merges --oneline --after="7 days ago" --format="%as  %s"

# Какие файлы изменились в конкретном мерже
git show --stat <commit-sha>
```

## Безопасность: nginx Basic Auth

По умолчанию веб-интерфейс доступен на публичном IP без авторизации.
Рекомендуется закрыть его с помощью HTTP Basic Auth через nginx.

### 1. Установите необходимые пакеты

```bash
sudo apt update
sudo apt install -y nginx apache2-utils
```

### 2. Создайте файл паролей

Замените `myuser` на желаемое имя пользователя:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd myuser
```

Чтобы добавить ещё одного пользователя (без флага `-c`):

```bash
sudo htpasswd /etc/nginx/.htpasswd anotheruser
```

### 3. Настройте nginx

Создайте конфигурационный файл, например `/etc/nginx/sites-available/stockweb`:

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN_OR_IP;

    auth_basic "Stock Bot";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> Замените `YOUR_DOMAIN_OR_IP` на ваш домен или публичный IP-адрес.
> Порт `5000` — стандартный для Flask; измените при необходимости.

Готовый пример файла с комментариями: [`docs/nginx-basic-auth.conf`](docs/nginx-basic-auth.conf).

### 4. Активируйте конфигурацию и перезапустите nginx

```bash
sudo ln -s /etc/nginx/sites-available/stockweb /etc/nginx/sites-enabled/
sudo nginx -t          # проверка конфигурации
sudo systemctl reload nginx
```

### 5. Рекомендация: HTTPS (Let's Encrypt)

Basic Auth передаёт учётные данные в открытом виде при использовании HTTP.
Настоятельно рекомендуется включить HTTPS с помощью Let's Encrypt:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_DOMAIN
```

После этого certbot автоматически добавит SSL-сертификат и настроит редирект с HTTP на HTTPS.

---

## Установка на Ubuntu одной командой

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/XSFORM/Stock_bot/main/install.sh)
```

---

## Admin / Settings

The web UI includes an **Admin → Settings** section (`/admin/settings`) with the following features.

### 1. Site Lock (shared password)

Protect the entire app behind a single shared password.

**Quick start — bootstrap via environment variable:**

```env
# .env
SITE_LOCK_PASSWORD=your-initial-password
```

On first startup the password is hashed and stored in the database. After that, update it from the **Admin → Settings → Site Lock** UI (the env var is only read once, when no password is stored yet).

**How it works:**

* When **Site Lock** is enabled, every page redirects to `/unlock` unless a valid session cookie is present.
* Passwords are stored as PBKDF2-SHA256 hashes — never in plaintext.
* Session duration is configurable (default 24 h).
* The **"Invalidate all existing sessions"** checkbox logs out all current users immediately.
* `/unlock` and `/static/*` are always accessible (bypass the lock).

### 2. Language selector (EN / RU / TM)

* A language dropdown is shown in the navbar on every page.
* The choice is persisted in a browser cookie (`ui_lang`, 1-year TTL).
* Admin can set the **default language** for new visitors in Settings → Default Language.

**Adding or updating translations:**

Open `app/i18n.py`. Every translatable string is a key in the `TRANSLATIONS` dict:

```python
TRANSLATIONS = {
    "en": { "my_new_key": "Hello" },
    "ru": { "my_new_key": "Привет" },
    "tm": { "my_new_key": "Salam" },
}
```

Keys missing from `ru` or `tm` automatically fall back to the English value. After adding a key, use it in templates via `{{ t.my_new_key }}`.

### 3. Dark mode / theme toggle

* A theme selector (☀️ Light / 🌙 Dark / 🖥️ System) is shown in the navbar.
* The choice is persisted in a browser cookie (`ui_theme`, 1-year TTL).
* Bootstrap 5's `data-bs-theme` attribute is used — no extra CSS required for components.
* Admin can set the **default theme** for new visitors in Settings → Default Theme.

### 4. Background image

* The background image can be replaced by uploading a JPG/PNG from **Settings → Background Image**.
* The uploaded file overwrites `app/web/static/bg.jpg`.
* Options: enable/disable background, background-size (cover / contain), overlay opacity.
