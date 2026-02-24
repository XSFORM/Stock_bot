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

## Установка на Ubuntu одной командой

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/XSFORM/Stock_bot/main/install.sh)
