import os
import sqlite3
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.db.sqlite import (
    add_client,
    add_product,
    get_stock,
    init_db,
    list_clients,
    list_products,
    move_stock,
    cart_start,
    cart_add,
    cart_show,
    cart_remove,
    cart_finish,
)
from app.services.backup import make_backup
from app.services.invoice_pdf import generate_invoice_pdf

router = Router()


def _is_admin(message: Message) -> bool:
    try:
        return int(message.from_user.id) == int(settings.admin_id)
    except Exception:
        return False


def _db_path() -> str:
    # Prefer explicit env var, otherwise use default location used by install.sh
    return os.getenv("DB_PATH", "/opt/stock_bot/app/db/stock.db")


def _ensure_clients_table() -> None:
    """Create minimal 'clients' table if it doesn't exist yet."""
    db_path = _db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


@router.message(Command("start"))
async def cmd_start(message: Message):
    if not _is_admin(message):
        return
    await message.answer("✅ Stock_bot запущен")


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not _is_admin(message):
        return

    text = (
        "<b>Stock_bot — команды</b>\n\n"
        "<b>Основное</b>\n"
        "/start — запуск\n"
        "/help — помощь\n"
        "/ping — проверка\n"
        "/backup — бэкап базы + PDF\n\n"
        "<b>Клиенты</b>\n"
        "/clients — список\n"
        "/client_add ИМЯ — добавить\n\n"
        "<b>Товары</b>\n"
        "/product_add BRAND MODEL NAME WHOLESALE_PRICE\n"
        "пример:\n"
        "/product_add sonifer sf-8040 \"Blender 800W\" 12.50\n"
        "/products — список\n\n"
        "<b>Остатки</b>\n"
        "/stock — по всем складам\n"
        "/stock WAREHOUSE — по складу (CHINA_DEPOT / WAREHOUSE / SHOP)\n\n"
        "<b>Перемещение</b>\n"
        "/move FROM TO BRAND MODEL QTY\n"
        "пример:\n"
        "/move CHINA_DEPOT WAREHOUSE sonifer sf-8040 10\n\n"
        "<b>Корзина (продажа)</b>\n"
        "/cart_start CLIENT_NAME — начать\n"
        "/cart_add BRAND MODEL QTY [price=wh|wh10|custom]\n"
        "[custom_price]\n"
        "пример:\n"
        "/cart_add sonifer sf-8040 2 wh\n"
        "/cart_show — показать\n"
        "/cart_remove BRAND MODEL — удалить\n"
        "/cart_finish — списать из SHOP + инвойс PDF + долг\n"
    )
    await message.answer(text)


@router.message(Command("ping"))
async def cmd_ping(message: Message):
    if not _is_admin(message):
        return
    await message.answer("pong ✅")


@router.message(Command("backup"))
async def cmd_backup(message: Message):
    if not _is_admin(message):
        return
    try:
        file_path = make_backup()
        await message.answer_document(open(file_path, "rb"))
    except Exception as e:
        await message.answer(f"❌ Ошибка бэкапа: {e}")


@router.message(Command("clients"))
async def cmd_clients(message: Message):
    if not _is_admin(message):
        return

    # In a fresh install the DB may exist but tables may not be created yet.
    _ensure_clients_table()

    try:
        rows = list_clients()
    except Exception as e:
        # If DB schema wasn't created for some reason, try once more after creating table.
        if "no such table: clients" in str(e).lower():
            _ensure_clients_table()
            rows = list_clients()
        else:
            await message.answer(f"❌ Ошибка при чтении клиентов: {e}")
            return

    if not rows:
        await message.answer("Клиентов пока нет. Добавь: /client_add Имя")
        return

    lines = ["<b>Клиенты:</b>"]
    for r in rows:
        lines.append(f"• {r['name']}")
    await message.answer("\n".join(lines))


@router.message(Command("client_add"))
async def cmd_client_add(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /client_add Имя\nПример: /client_add ali")
        return

    name = parts[1].strip()
    if not name:
        await message.answer("Формат: /client_add Имя\nПример: /client_add ali")
        return

    _ensure_clients_table()

    try:
        add_client(name)
    except Exception as e:
        if "no such table: clients" in str(e).lower():
            _ensure_clients_table()
            add_client(name)
        else:
            await message.answer(f"❌ Ошибка при добавлении клиента: {e}")
            return

    await message.answer(f"✅ Клиент добавлен: {name}")


@router.message(Command("products"))
async def cmd_products(message: Message):
    if not _is_admin(message):
        return
    rows = list_products()
    if not rows:
        await message.answer("Товаров пока нет. Добавь: /product_add ...")
        return
    lines = ["<b>Товары:</b>"]
    for r in rows:
        lines.append(f"• {r['brand']} {r['model']} — {r['name']} (wh={r['wholesale_price']:.2f}$)")
    await message.answer("\n".join(lines))


@router.message(Command("product_add"))
async def cmd_product_add(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split(maxsplit=4)
    if len(parts) < 5:
        await message.answer('Формат: /product_add BRAND MODEL "NAME" WHOLESALE_PRICE')
        return
    brand, model, name, wh_price = parts[1], parts[2], parts[3], parts[4]
    try:
        add_product(brand, model, name.replace('"', ""), float(wh_price))
        await message.answer(f"✅ Товар добавлен: {brand} {model}")
    except Exception as e:
        await message.answer(f"❌ Ошибка добавления товара: {e}")


@router.message(Command("stock"))
async def cmd_stock(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    wh = parts[1].strip().upper() if len(parts) > 1 else None
    try:
        rows = get_stock(wh)
        if not rows:
            await message.answer("Пусто.")
            return
        lines = ["<b>Остатки:</b>"]
        for r in rows:
            lines.append(f"{r['warehouse']}: {r['brand']} {r['model']} — {r['qty']}")
        await message.answer("\n".join(lines))
    except Exception as e:
        await message.answer(f"❌ Ошибка остатков: {e}")


@router.message(Command("move"))
async def cmd_move(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split()
    if len(parts) != 6:
        await message.answer("Формат: /move FROM TO BRAND MODEL QTY")
        return
    _, w_from, w_to, brand, model, qty = parts
    try:
        move_stock(w_from.upper(), w_to.upper(), brand, model, int(qty))
        await message.answer(f"✅ Перемещено: {brand} {model} {qty} из {w_from} в {w_to}")
    except Exception as e:
        await message.answer(f"❌ Ошибка перемещения: {e}")


@router.message(Command("cart_start"))
async def cmd_cart_start(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /cart_start CLIENT_NAME")
        return
    client = parts[1].strip()
    try:
        cart_start(client)
        await message.answer(f"🧺 Корзина начата для: {client}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("cart_add"))
async def cmd_cart_add(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("Формат: /cart_add BRAND MODEL QTY [wh|wh10|custom] [custom_price]")
        return
    brand, model, qty = parts[1], parts[2], int(parts[3])
    price_mode = parts[4] if len(parts) >= 5 else "wh"
    custom_price = float(parts[5]) if (len(parts) >= 6) else None
    try:
        cart_add(brand, model, qty, price_mode, custom_price)
        await message.answer(f"✅ Добавлено в корзину: {brand} {model} x{qty}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("cart_show"))
async def cmd_cart_show(message: Message):
    if not _is_admin(message):
        return
    try:
        txt = cart_show()
        await message.answer(txt)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("cart_remove"))
async def cmd_cart_remove(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /cart_remove BRAND MODEL")
        return
    brand, model = parts[1], parts[2]
    try:
        cart_remove(brand, model)
        await message.answer(f"🗑 Удалено: {brand} {model}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("cart_finish"))
async def cmd_cart_finish(message: Message):
    if not _is_admin(message):
        return
    try:
        result = cart_finish()
        pdf_path = generate_invoice_pdf(result["invoice"])
        await message.answer_document(open(pdf_path, "rb"))
        await message.answer(f"✅ Готово. Итог: {result['total']:.2f}$\nДолг: {'да' if result['debt'] else 'нет'}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
