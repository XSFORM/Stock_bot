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

## Установка на Ubuntu одной командой

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/XSFORM/Stock_bot/main/install.sh)
