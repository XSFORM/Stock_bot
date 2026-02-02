import shlex

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

# текущий выбранный клиент для корзины (только для тебя, один админ)
ACTIVE_CLIENT: str | None = None


def _is_admin(message: Message) -> bool:
    try:
        return int(message.from_user.id) == int(settings.admin_id)
    except Exception:
        return False


@router.message(Command("start"))
async def cmd_start(message: Message):
    if not _is_admin(message):
        return
    init_db()
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
        "/product_add BRAND MODEL \"NAME\" WHOLESALE_PRICE\n"
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
        "/cart_start CLIENT_NAME — выбрать клиента и начать корзину\n"
        "/cart_add BRAND MODEL QTY [wh|wh10|custom] [custom_price]\n"
        "пример:\n"
        "/cart_add sonifer sf-8040 2 wh\n"
        "/cart_add sonifer sf-8040 2 wh10\n"
        "/cart_add sonifer sf-8040 2 custom 15.00\n"
        "/cart_show — показать корзину\n"
        "/cart_remove BRAND MODEL — удалить 1 позицию\n"
        "/cart_finish — списать из SHOP + инвойс PDF\n"
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
    init_db()
    rows = list_clients()
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
    init_db()
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Формат: /client_add Имя\nПример: /client_add ali")
        return
    name = parts[1].strip()
    try:
        add_client(name)
        await message.answer(f"✅ Клиент добавлен: {name}")
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении клиента: {e}")


@router.message(Command("products"))
async def cmd_products(message: Message):
    if not _is_admin(message):
        return
    init_db()
    rows = list_products()
    if not rows:
        await message.answer("Товаров пока нет. Добавь: /product_add ...")
        return
    lines = ["<b>Товары:</b>"]
    for r in rows:
        lines.append(
            f"• {r['brand']} {r['model']} — {r['name']} (wh={float(r['wh_price']):.2f}$ / wh10={float(r['wh10_price']):.2f}$)"
        )
    await message.answer("\n".join(lines))


@router.message(Command("product_add"))
async def cmd_product_add(message: Message):
    if not _is_admin(message):
        return
    init_db()
    try:
        args = shlex.split(message.text)
        # ['/product_add', 'brand', 'model', 'name with spaces', '12.50']
        if len(args) < 5:
            raise ValueError
        _, brand, model, name, wh_price = args[0], args[1], args[2], args[3], args[4]
        add_product(brand, model, name, float(wh_price))
        await message.answer(f"✅ Товар добавлен: {brand} {model}")
    except ValueError:
        await message.answer('Формат: /product_add BRAND MODEL "NAME" WHOLESALE_PRICE')
    except Exception as e:
        await message.answer(f"❌ Ошибка добавления товара: {e}")


@router.message(Command("stock"))
async def cmd_stock(message: Message):
    if not _is_admin(message):
        return
    init_db()
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
    init_db()
    parts = message.text.split()
    if len(parts) != 6:
        await message.answer("Формат: /move FROM TO BRAND MODEL QTY")
        return
    _, w_from, w_to, brand, model, qty = parts
    ok, err = move_stock(w_from, w_to, brand, model, float(qty))
    if not ok:
        await message.answer(f"❌ {err}")
        return
    await message.answer(f"✅ Перемещено: {brand} {model} {qty} из {w_from} в {w_to}")


@router.message(Command("cart_start"))
async def cmd_cart_start(message: Message):
    global ACTIVE_CLIENT
    if not _is_admin(message):
        return
    init_db()
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Формат: /cart_start CLIENT_NAME")
        return
    client = parts[1].strip()
    try:
        cart_start(client)
        ACTIVE_CLIENT = client
        await message.answer(f"✅ Корзина начата. Клиент: <b>{client}</b>")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("cart_add"))
async def cmd_cart_add(message: Message):
    if not _is_admin(message):
        return
    init_db()
    if not ACTIVE_CLIENT:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return

    parts = message.text.split()
    # /cart_add brand model qty [mode] [custom_price]
    if len(parts) < 4:
        await message.answer("Формат: /cart_add BRAND MODEL QTY [wh|wh10|custom] [custom_price]")
        return

    brand, model, qty = parts[1], parts[2], parts[3]
    mode = parts[4].lower() if len(parts) >= 5 else "wh"
    custom_price = float(parts[5]) if (mode == "custom" and len(parts) >= 6) else None

    ok, err = cart_add(ACTIVE_CLIENT, brand, model, float(qty), mode, custom_price)
    if not ok:
        await message.answer(f"❌ {err}")
        return
    await message.answer(f"✅ Добавлено: {brand} {model} x{qty} ({mode})")


@router.message(Command("cart_show"))
async def cmd_cart_show(message: Message):
    if not _is_admin(message):
        return
    init_db()
    if not ACTIVE_CLIENT:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return
    ok, text = cart_show(ACTIVE_CLIENT)
    if not ok:
        await message.answer(f"❌ {text}")
        return
    await message.answer(text)


@router.message(Command("cart_remove"))
async def cmd_cart_remove(message: Message):
    if not _is_admin(message):
        return
    init_db()
    if not ACTIVE_CLIENT:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /cart_remove BRAND MODEL")
        return
    _, brand, model = parts
    ok, err = cart_remove(ACTIVE_CLIENT, brand, model)
    if not ok:
        await message.answer(f"❌ {err}")
        return
    await message.answer(f"✅ Удалено: {brand} {model}")


@router.message(Command("cart_finish"))
async def cmd_cart_finish(message: Message):
    global ACTIVE_CLIENT
    if not _is_admin(message):
        return
    init_db()
    if not ACTIVE_CLIENT:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return

    ok, err, invoice, items = cart_finish(ACTIVE_CLIENT)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    pdf_path = generate_invoice_pdf(invoice, items)
    await message.answer(f"✅ Инвойс #{invoice['number']:06d} на {invoice['total']:.2f}$ готов.")
    await message.answer_document(open(pdf_path, "rb"))

    # после завершения — сброс активного клиента (чтобы случайно не продолжить)
    ACTIVE_CLIENT = None
