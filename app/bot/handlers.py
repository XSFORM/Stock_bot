from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.bot.keyboards import main_kb
from app.bot.states import CARTS, Cart, CartItem
from app.constants import WAREHOUSES, PRICE_WHOLESALE, PRICE_WHOLESALE_10, PRICE_CUSTOM
from app.db.sqlite import (
    ensure_admin,
    add_client,
    list_clients,
    add_product,
    list_products,
    get_stock_text,
    move_stock,
    find_product,
    get_client_by_name,
    create_invoice_from_cart,
    get_debt_usd,
    add_payment,
)
from app.services.backup import make_backup_zip

router = Router()

HELP_TEXT = """
<b>Stock_bot — команды</b>

<b>Основное</b>
/start — запуск
/help — помощь
/ping — проверка
/backup — бэкап базы + PDF

<b>Клиенты</b>
/clients — список
/client_add ИМЯ — добавить

<b>Товары</b>
/product_add BRAND MODEL NAME WHOLESALE_PRICE
пример:
/product_add sonifer sf-8040 "Blender 800W" 12.50
/products — список

<b>Остатки</b>
/stock — по всем складам
/stock WAREHOUSE — по складу (CHINA_DEPOT / WAREHOUSE / SHOP)

<b>Перемещение</b>
/move FROM TO BRAND MODEL QTY
пример:
/move CHINA_DEPOT WAREHOUSE sonifer sf-8040 10

<b>Корзина (продажа)</b>
/cart_start CLIENT_NAME — начать
/cart_add BRAND MODEL QTY [price=wh|wh10|custom] [custom_price]
пример:
/cart_add sonifer sf-8040 2 wh
/cart_show — показать
/cart_remove BRAND MODEL — удалить
/cart_finish — списать из SHOP + инвойс PDF + долг

<b>Долги/оплаты</b>
/debt CLIENT_NAME — долг
/pay CLIENT_NAME AMOUNT — оплата (USD)
""".strip()


def _is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == settings.admin_tg_id


@router.message(Command("start"))
async def cmd_start(message: Message):
    if not _is_admin(message):
        await message.answer("Доступ запрещен.")
        return
    ensure_admin()
    await message.answer("✅ Stock_bot запущен", reply_markup=main_kb())


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not _is_admin(message):
        return
    await message.answer(HELP_TEXT)


@router.message(Command("ping"))
async def cmd_ping(message: Message):
    if not _is_admin(message):
        return
    await message.answer("pong ✅")


@router.message(Command("clients"))
async def cmd_clients(message: Message):
    if not _is_admin(message):
        return
    rows = list_clients()
    if not rows:
        await message.answer("Клиентов пока нет. Добавь: /client_add Имя")
        return
    text = "<b>Клиенты:</b>\n" + "\n".join([f"• {r['name']}" for r in rows])
    await message.answer(text)


@router.message(Command("client_add"))
async def cmd_client_add(message: Message):
    if not _is_admin(message):
        return
    parts = message.text.split(maxsplit=1) if message.text else []
    if len(parts) < 2:
        await message.answer("Формат: /client_add Имя")
        return
    name = parts[1].strip()
    add_client(name)
    await message.answer(f"✅ Клиент добавлен: {name}")


@router.message(Command("products"))
async def cmd_products(message: Message):
    if not _is_admin(message):
        return
    rows = list_products()
    if not rows:
        await message.answer("Товаров пока нет. Добавь: /product_add ...")
        return
    lines = []
    for r in rows:
        lines.append(f"• <b>{r['brand']}</b> {r['model']} — {r['name']} | wh={r['wh_price']:.2f} | wh10={r['wh10_price']:.2f}")
    await message.answer("<b>Товары:</b>\n" + "\n".join(lines))


@router.message(Command("product_add"))
async def cmd_product_add(message: Message):
    if not _is_admin(message):
        return
    # /product_add brand model "name" 12.50
    raw = message.text or ""
    # Простая разборка: brand model далее всё до последнего как name, а последнее - цена
    parts = raw.split()
    if len(parts) < 5:
        await message.answer('Формат: /product_add BRAND MODEL "NAME" WHOLESALE_PRICE')
        return

    brand = parts[1].strip().lower()
    model = parts[2].strip().lower()

    # цена последняя
    try:
        wh_price = float(parts[-1])
    except ValueError:
        await message.answer("Цена должна быть числом. Пример: 12.50")
        return

    name = " ".join(parts[3:-1]).strip().strip('"').strip("'")
    add_product(brand=brand, model=model, name=name, wh_price=wh_price)
    await message.answer(f"✅ Товар добавлен: {brand} {model} — {name} (wh={wh_price:.2f}, wh10=+10%)")


@router.message(Command("stock"))
async def cmd_stock(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    wh = parts[1].strip().upper() if len(parts) > 1 else None
    if wh and wh not in WAREHOUSES:
        await message.answer("Склад должен быть: CHINA_DEPOT / WAREHOUSE / SHOP")
        return
    await message.answer(get_stock_text(warehouse=wh))


@router.message(Command("move"))
async def cmd_move(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    # /move FROM TO brand model qty
    if len(parts) != 6:
        await message.answer("Формат: /move FROM TO BRAND MODEL QTY")
        return

    src = parts[1].upper()
    dst = parts[2].upper()
    brand = parts[3].lower()
    model = parts[4].lower()
    try:
        qty = float(parts[5])
    except ValueError:
        await message.answer("QTY должно быть числом.")
        return

    if src not in WAREHOUSES or dst not in WAREHOUSES:
        await message.answer("Склады: CHINA_DEPOT / WAREHOUSE / SHOP")
        return

    ok, err = move_stock(src, dst, brand, model, qty)
    if not ok:
        await message.answer(f"❌ Не получилось: {err}")
        return
    await message.answer(f"✅ Перемещение: {qty} шт {brand} {model} | {src} → {dst}")


@router.message(Command("cart_start"))
async def cmd_cart_start(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /cart_start CLIENT_NAME")
        return
    client_name = parts[1].strip()
    client = get_client_by_name(client_name)
    if not client:
        await message.answer("❌ Клиент не найден. Сначала добавь: /client_add Имя")
        return

    CARTS[settings.admin_tg_id] = Cart(client_id=client["id"], client_name=client["name"])
    await message.answer(f"🧺 Корзина начата для клиента: <b>{client['name']}</b>\nДобавляй: /cart_add BRAND MODEL QTY ...")


@router.message(Command("cart_add"))
async def cmd_cart_add(message: Message):
    if not _is_admin(message):
        return
    cart = CARTS.get(settings.admin_tg_id)
    if not cart:
        await message.answer("Сначала начни корзину: /cart_start CLIENT_NAME")
        return

    parts = (message.text or "").split()
    # /cart_add brand model qty [price=wh|wh10|custom] [custom_price]
    if len(parts) < 4:
        await message.answer("Формат: /cart_add BRAND MODEL QTY [wh|wh10|custom] [custom_price]")
        return

    brand = parts[1].lower()
    model = parts[2].lower()
    try:
        qty = float(parts[3])
    except ValueError:
        await message.answer("QTY должно быть числом.")
        return

    price_mode = parts[4].lower() if len(parts) >= 5 else PRICE_WHOLESALE
    custom_price = None
    if price_mode not in (PRICE_WHOLESALE, PRICE_WHOLESALE_10, PRICE_CUSTOM):
        await message.answer("price должен быть: wh / wh10 / custom")
        return
    if price_mode == PRICE_CUSTOM:
        if len(parts) < 6:
            await message.answer("Для custom укажи цену: /cart_add ... custom 13.99")
            return
        try:
            custom_price = float(parts[5])
        except ValueError:
            await message.answer("custom_price должно быть числом.")
            return

    prod = find_product(brand, model)
    if not prod:
        await message.answer("❌ Товар не найден. Добавь: /product_add ...")
        return

    if price_mode == PRICE_WHOLESALE:
        price = float(prod["wh_price"])
    elif price_mode == PRICE_WHOLESALE_10:
        price = float(prod["wh10_price"])
    else:
        price = float(custom_price)

    cart.items.append(
        CartItem(
            brand=brand,
            model=model,
            name=prod["name"],
            qty=qty,
            price=price,
            price_mode=price_mode,
        )
    )
    await message.answer(f"✅ Добавлено в корзину: {brand} {model} x{qty} | {price_mode} = {price:.2f}")


@router.message(Command("cart_show"))
async def cmd_cart_show(message: Message):
    if not _is_admin(message):
        return
    cart = CARTS.get(settings.admin_tg_id)
    if not cart or not cart.items:
        await message.answer("Корзина пустая.")
        return
    lines = []
    total = 0.0
    for it in cart.items:
        line = it.qty * it.price
        total += line
        lines.append(f"• {it.brand} {it.model} — {it.name} | {it.qty} x {it.price:.2f} = {line:.2f} ({it.price_mode})")
    await message.answer(
        f"<b>Корзина</b> для <b>{cart.client_name}</b>:\n" + "\n".join(lines) + f"\n\n<b>Итого:</b> {total:.2f} {settings.currency}"
    )


@router.message(Command("cart_remove"))
async def cmd_cart_remove(message: Message):
    if not _is_admin(message):
        return
    cart = CARTS.get(settings.admin_tg_id)
    if not cart:
        await message.answer("Корзины нет. /cart_start CLIENT_NAME")
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: /cart_remove BRAND MODEL")
        return
    brand = parts[1].lower()
    model = parts[2].lower()

    before = len(cart.items)
    cart.items = [x for x in cart.items if not (x.brand == brand and x.model == model)]
    after = len(cart.items)

    if before == after:
        await message.answer("Не нашел такую позицию в корзине.")
    else:
        await message.answer("✅ Удалено.")


@router.message(Command("cart_finish"))
async def cmd_cart_finish(message: Message):
    if not _is_admin(message):
        return
    cart = CARTS.get(settings.admin_tg_id)
    if not cart or not cart.items:
        await message.answer("Корзина пустая.")
        return

    ok, result = create_invoice_from_cart(cart)
    if not ok:
        await message.answer(f"❌ Ошибка: {result}")
        return

    invoice_id, pdf_path, total = result
    CARTS.pop(settings.admin_tg_id, None)

    await message.answer(
        f"✅ Продажа завершена.\n"
        f"Инвойс: <b>#{invoice_id}</b>\n"
        f"Итого: <b>{total:.2f} {settings.currency}</b>\n"
        f"PDF: {pdf_path}"
    )


@router.message(Command("debt"))
async def cmd_debt(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /debt CLIENT_NAME")
        return
    name = parts[1].strip()
    client = get_client_by_name(name)
    if not client:
        await message.answer("Клиент не найден.")
        return
    debt = get_debt_usd(client["id"])
    await message.answer(f"💳 Долг клиента <b>{client['name']}</b>: <b>{debt:.2f} {settings.currency}</b>")


@router.message(Command("pay"))
async def cmd_pay(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: /pay CLIENT_NAME AMOUNT")
        return
    name = parts[1].strip()
    try:
        amount = float(parts[2])
    except ValueError:
        await message.answer("AMOUNT должно быть числом.")
        return
    client = get_client_by_name(name)
    if not client:
        await message.answer("Клиент не найден.")
        return
    add_payment(client["id"], amount)
    debt = get_debt_usd(client["id"])
    await message.answer(f"✅ Оплата учтена. Новый долг: <b>{debt:.2f} {settings.currency}</b>")


@router.message(Command("backup"))
async def cmd_backup(message: Message):
    if not _is_admin(message):
        return
    path = make_backup_zip()
    await message.answer(f"✅ Бэкап готов: {path}")
