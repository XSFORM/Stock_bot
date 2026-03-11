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

> **Начиная с версии 2026-03-11**, `install.sh` настраивает nginx + Basic Auth + HTTPS **автоматически** при интерактивной установке.  
> Раздел ниже актуален для ручной настройки или кастомных сценариев.

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
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> Замените `YOUR_DOMAIN_OR_IP` на ваш домен или публичный IP-адрес.
> Порт `8000` — используется FastAPI (uvicorn).

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

Установщик выполнит следующие шаги:

| Шаг | Описание |
|-----|---------|
| 1 | Обновление системных пакетов (включая `nginx`, `certbot`, `apache2-utils`) |
| 2–7 | Клонирование репозитория, создание `.env`, venv, запуск сервисов |
| **8** | **Интерактивная настройка nginx + Basic Auth + HTTPS (Let's Encrypt)** |

После установки запускаются два systemd-сервиса:

| Сервис | Назначение |
|--------|-----------|
| `stockbot` | Telegram-бот (модуль `app.main`) |
| `stockweb` | Веб-интерфейс FastAPI / uvicorn на `127.0.0.1:8000` |

```bash
sudo systemctl status stockbot stockweb --no-pager
```

---

## Автоматическая настройка HTTPS + Basic Auth

При **интерактивном** запуске `install.sh` шаг 8 автоматически:

1. Создаёт конфигурацию nginx с Basic Auth (`/etc/nginx/sites-available/stockweb`).
2. Создаёт файл паролей `/etc/nginx/.htpasswd` (по введённым логину/паролю).
3. Активирует сайт, проверяет конфигурацию nginx и перезагружает его.
4. Запускает `certbot --nginx` для получения сертификата Let's Encrypt и настройки редиректа HTTP → HTTPS.

На шаге 8 установщик запросит:

| Параметр | По умолчанию |
|----------|-------------|
| Домен | `admin.sonifer.net.ru` |
| Basic Auth логин | `admin` |
| Basic Auth пароль | *(обязательно ввести)* |

### Неинтерактивная установка (например, CI/CD)

Если `stdin` не является TTY, шаг 8 пропускается. Для последующей настройки HTTPS запустите:

```bash
sudo bash /opt/stock_bot/scripts/setup-https.sh
```

Или передайте параметры через переменные окружения (без интерактивных запросов):

```bash
DOMAIN=example.com AUTH_USER=admin AUTH_PASS=secret \
  sudo -E bash /opt/stock_bot/scripts/setup-https.sh
```

### Продление сертификатов

Certbot настраивает автоматическое продление через systemd timer или cron.  
Для ручного продления:

```bash
sudo certbot renew
```

Проверить, что автопродление работает:

```bash
sudo certbot renew --dry-run
```

### Добавление пользователей Basic Auth

```bash
# Добавить нового пользователя (без флага -c, чтобы не перезаписать файл)
sudo htpasswd /etc/nginx/.htpasswd newuser
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

**Translation coverage:**

The i18n system covers the entire UI including:
- Navigation menu items (Products, Stock, Receive, Move, Move all, Sale, Return, Brands, Clients, Invoices, History, Admin)
- Page titles and section headers
- Table column headers (Brand, Model, Name, Qty, Unit Price, Total, etc.)
- Form labels and placeholders
- Buttons (Save, Cancel, Add, Edit, Delete, Archive, etc.)
- Empty state messages and confirmation dialogs
- Badge/status labels (RECEIVE, SALE, RETURN, INVOICE, ADJUSTMENT, ARCHIVED)

Data fetched from the database (client names, product names, warehouse codes, invoice numbers, etc.) is **not** translated — it appears as stored.

**Where translation files live:**

All translations live in a single file: `app/i18n.py`

Keys are organized with comments by section:
- `nav_*` — navigation links
- `btn_*` — buttons
- `lbl_*` — form labels / table headers
- `badge_*` — status badges
- `confirm_*` — confirmation dialog messages
- `products_*`, `stock_*`, `receive_*`, `sale_*`, `return_*`, etc. — page-specific strings

### 3. Dark mode / theme toggle

* A theme selector (☀️ Light / 🌙 Dark / 🖥️ System) is shown in the navbar.
* The choice is persisted in a browser cookie (`ui_theme`, 1-year TTL).
* Bootstrap 5's `data-bs-theme` attribute is used — no extra CSS required for components.
* Admin can set the **default theme** for new visitors in Settings → Default Theme.

### 4. Background image

* The background image can be replaced by uploading a JPG/PNG from **Settings → Background Image**.
* The uploaded file overwrites `app/web/static/bg.jpg`.
* Options: enable/disable background, background-size (cover / contain), overlay opacity.
