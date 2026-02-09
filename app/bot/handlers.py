import re
import shlex

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.bot.states import ClientAdd, ProductAdd
from app.config import settings
from app.constants import WAREHOUSES
from app.db.sqlite import (
    add_client,
    add_product,
    cart_add,
    cart_finish_from_shop,
    cart_remove,
    cart_show,
    cart_start,
    init_db,
    list_clients,
    list_products,
    move_all,
    move_all_auto_shop,
    move_stock,
    receive_stock,
)
from app.services.backup import make_backup
from app.services.invoice_pdf import generate_invoice_pdf

router = Router()

ACTIVE_CLIENT: str | None = None
ACTIVE_CART_SOURCE: str = "CHINA"  # CHINA | DEALER (по умолчанию Китай)

DEFAULT_BRAND = "SONIFER"

BRAND_PREFIX = {
    "SONIFER": "SF-",
    "RAF": "R-",
    "VGR": "V-",
    "SOKANY": "SK-",
    "BABYVERSE": "BA-",
    "MOSER": "MS-",
}


def _is_admin(message: Message) -> bool:
    try:
        return int(message.from_user.id) == int(settings.admin_id)
    except Exception:
        return False


def _brands_kb() -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text="✅ SONIFER"),
            KeyboardButton(text="RAF"),
            KeyboardButton(text="VGR"),
        ],
        [
            KeyboardButton(text="SOKANY"),
            KeyboardButton(text="BABYVERSE"),
            KeyboardButton(text="MOSER"),
        ],
        [KeyboardButton(text="✍️ Другое (вручную)")],
        [KeyboardButton(text="/cancel")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _normalize_brand(text: str) -> str:
    t = text.strip().upper()
    t = re.sub(r"[^A-Z0-9\-]", "", t)
    return t


def _normalize_model(model_text: str, prefix: str) -> str:
    t = model_text.strip().replace(" ", "")
    if not t:
        return t

    if prefix and re.fullmatch(r"\d+", t):
        return (prefix + t).lower()

    m = re.fullmatch(r"([A-Za-z]{1,5})-?(\d+)", t)
    if m:
        letters = m.group(1).upper()
        digits = m.group(2)
        if prefix:
            pref_letters = prefix.rstrip("-").upper()
            if letters == pref_letters:
                return (prefix + digits).lower()
        return f"{letters}-{digits}".lower()

    return t.lower()


def _parse_price(text: str) -> float:
    return float(text.strip().replace(",", "."))


def _parse_qty(text: str) -> float:
    return float(text.strip().replace(",", "."))


def _warehouse_help() -> str:
    return ", ".join(sorted(WAREHOUSES.keys()))


def _require_active_client() -> str | None:
    global ACTIVE_CLIENT
    return ACTIVE_CLIENT


def _shop_for_source() -> str:
    global ACTIVE_CART_SOURCE
    return "SHOP_CHINA" if ACTIVE_CART_SOURCE == "CHINA" else "SHOP_DEALER"


@router.message(Command("start"))
async def cmd_start(message: Message):
    if not _is_admin(message):
        return
    init_db()
    await message.answer("✅ Stock_bot запущен")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("❎ Отменено. Можно вводить команды заново.", reply_markup=ReplyKeyboardRemove())


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not _is_admin(message):
        return

    text = (
        "<b>Stock_bot — команды</b>\n\n"
        "<b>Основное</b>\n"
        "/start — запуск\n"
        "/cancel — отмена ввода\n"
        "/help — помощь\n"
        "/ping — проверка\n"
        "/backup — бэкап базы + PDF\n\n"
        "<b>Клиенты</b>\n"
        "/clients — список\n"
        "/client_add ИМЯ — добавить\n\n"
        "<b>Товары</b>\n"
        "/product_add — мастер добавления\n"
        "/products — список\n\n"
        "<b>Поступление</b>\n"
        "/receive CHINA BRAND MODEL QTY — приход из Китая на CHINA_DEPOT\n"
        "/receive DEALER BRAND MODEL QTY — приход от диллера на DEALER_DEPOT\n"
        "/receive WAREHOUSE BRAND MODEL QTY — приход на указанный склад\n\n"
        "<b>Остатки</b>\n"
        "/stock — по всем складам\n"
        "/stock WAREHOUSE — по складу\n\n"
        "<b>Перемещение</b>\n"
        "/move FROM TO BRAND MODEL QTY\n"
        "/move_all FROM — перенести ВСЁ (CHINA_DEPOT→SHOP_CHINA, DEALER_DEPOT→SHOP_DEALER)\n"
        "/move_all FROM TO — перенести ВСЁ в указанный склад\n\n"
        "<b>Корзина (продажа)</b>\n"
        "/cart_start CLIENT_NAME — выбрать клиента и начать корзину\n"
        "/cart_source CHINA|DEALER — выбрать из какого магазина продаём\n"
        "/cart_add BRAND MODEL QTY [wh|wh10|custom] [custom_price]\n"
        "/cart_show — показать корзину\n"
        "/cart_remove BRAND MODEL — удалить 1 позицию\n"
        "/cart_finish — списать из SHOP_CHINA/SHOP_DEALER + PDF + backup\n"
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
async def cmd_client_add(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) >= 2 and parts[1].strip():
        name = parts[1].strip()
        try:
            add_client(name)
            await message.answer(f"✅ Клиент добавлен: {name}")
        except Exception as e:
            await message.answer(f"❌ Ошибка при добавлении клиента: {e}")
        return

    await state.set_state(ClientAdd.waiting_name)
    await message.answer(
        "Введите имя клиента одним сообщением.\nПример: ali\n\nОтмена: /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ClientAdd.waiting_name)
async def client_add_wait_name(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    name = (message.text or "").strip()
    if not name or name.startswith("/"):
        await message.answer("Введите имя текстом. Отмена: /cancel")
        return

    try:
        add_client(name)
        await message.answer(f"✅ Клиент добавлен: {name}")
    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении клиента: {e}")
        return
    finally:
        await state.clear()


@router.message(Command("products"))
async def cmd_products(message: Message):
    if not _is_admin(message):
        return
    init_db()
    rows = list_products()
    if not rows:
        await message.answer("Товаров пока нет. Добавь: /product_add")
        return
    lines = ["<b>Товары:</b>"]
    for r in rows:
        lines.append(
            f"• {r['brand']} {r['model']} — {r['name']} (wh={float(r['wh_price']):.2f}$ / wh10={float(r['wh10_price']):.2f}$)"
        )
    await message.answer("\n".join(lines))


@router.message(Command("product_add"))
async def cmd_product_add(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    init_db()

    try:
        args = shlex.split(message.text)
        if len(args) >= 5:
            _, brand, model, name, wh_price = args[:5]
            add_product(brand, model, name, _parse_price(wh_price))
            await message.answer(f"✅ Товар добавлен: {brand} {model}")
            return
    except Exception:
        pass

    await state.clear()
    await state.set_state(ProductAdd.waiting_brand)
    await state.update_data(brand=DEFAULT_BRAND)
    await message.answer(
        "Ок, добавляем товар.\n\n1/4) Выберите БРЕНД (по умолчанию SONIFER)\nОтмена: /cancel",
        reply_markup=_brands_kb(),
    )


@router.message(ProductAdd.waiting_brand)
async def product_add_brand(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    raw = (message.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await message.answer("❎ Отменено.", reply_markup=ReplyKeyboardRemove())
        return

    if raw.startswith("✍️"):
        await message.answer(
            "Введите БРЕНД вручную (например: Sonifer)\nОтмена: /cancel",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    brand = _normalize_brand(raw) or DEFAULT_BRAND
    await state.update_data(brand=brand)

    prefix = BRAND_PREFIX.get(brand, "")
    await state.set_state(ProductAdd.waiting_model)

    hint = f"\nПодсказка: можно написать только номер (например: 8040) — сделаю {prefix}8040." if prefix else ""
    await message.answer(
        f"2/4) Введите МОДЕЛЬ (например: {prefix.lower()}8040){hint}\nОтмена: /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ProductAdd.waiting_model)
async def product_add_model(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    model_in = (message.text or "").strip()
    if model_in == "/cancel":
        await state.clear()
        await message.answer("❎ Отменено.", reply_markup=ReplyKeyboardRemove())
        return

    data = await state.get_data()
    brand = str(data.get("brand", DEFAULT_BRAND)).upper()
    prefix = BRAND_PREFIX.get(brand, "")

    model = _normalize_model(model_in, prefix)
    if not model or model.startswith("/"):
        await message.answer("Введите модель текстом. Пример: sf-8040\nОтмена: /cancel")
        return

    await state.update_data(model=model)
    await state.set_state(ProductAdd.waiting_name)
    await message.answer(
        "3/4) Введите НАЗВАНИЕ (можно коротко), или '-' чтобы пропустить.\nОтмена: /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ProductAdd.waiting_name)
async def product_add_name(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    name = (message.text or "").strip()
    if name == "/cancel":
        await state.clear()
        await message.answer("❎ Отменено.", reply_markup=ReplyKeyboardRemove())
        return

    if not name:
        await message.answer("Введите название или '-' чтобы пропустить.\nОтмена: /cancel")
        return

    data = await state.get_data()
    if name == "-":
        name = data.get("model", "")

    await state.update_data(name=name)
    await state.set_state(ProductAdd.waiting_price)
    await message.answer(
        "4/4) Введите ЦЕНУ ПРИХОДА (wh) в USD.\nПример: 12.50\nОтмена: /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ProductAdd.waiting_price)
async def product_add_price(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    raw = (message.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await message.answer("❎ Отменено.", reply_markup=ReplyKeyboardRemove())
        return

    try:
        price = _parse_price(raw)
        if price <= 0:
            raise ValueError("price <= 0")
    except Exception:
        await message.answer("Цена должна быть числом, например 12.50\nОтмена: /cancel")
        return

    data = await state.get_data()
    brand = data.get("brand", DEFAULT_BRAND)
    model = data.get("model", "")
    name = data.get("name", "")

    try:
        add_product(str(brand), str(model), str(name), float(price))
        await message.answer(f"✅ Товар добавлен: {brand} {model}")
    except Exception as e:
        await message.answer(f"❌ Ошибка добавления товара: {e}")
        return
    finally:
        await state.clear()


@router.message(Command("receive"))
async def cmd_receive(message: Message):
    if not _is_admin(message):
        return

    init_db()

    parts = message.text.split()
    if len(parts) != 5:
        await message.answer(
            "Формат:\n"
            "/receive CHINA BRAND MODEL QTY\n"
            "/receive DEALER BRAND MODEL QTY\n"
            "/receive WAREHOUSE BRAND MODEL QTY\n\n"
            f"Склады: {_warehouse_help()}"
        )
        return

    _, src, brand, model, qty_s = parts
    src_u = src.strip().upper()

    if src_u in ("CHINA", "CN"):
        warehouse = "CHINA_DEPOT"
    elif src_u in ("DEALER", "DILLER", "SUPPLIER", "LOCAL"):
        warehouse = "DEALER_DEPOT"
    else:
        warehouse = src_u

    try:
        qty = _parse_qty(qty_s)
    except Exception:
        await message.answer("QTY должно быть числом, пример: 10 или 2.5")
        return

    ok, err = receive_stock(warehouse, brand, model, qty)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    await message.answer(f"✅ Приход: {warehouse} +{qty} шт — {brand} {model}")


@router.message(Command("stock"))
async def cmd_stock(message: Message):
    if not _is_admin(message):
        return

    init_db()
    parts = message.text.split(maxsplit=1)
    wh = parts[1].strip().upper() if len(parts) > 1 else None
    try:
        from app.db.sqlite import get_stock_text
        await message.answer(get_stock_text(wh))
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


@router.message(Command("move_all"))
async def cmd_move_all(message: Message):
    """
    /move_all FROM
    /move_all FROM TO
    """
    if not _is_admin(message):
        return

    init_db()
    parts = message.text.split()
    if len(parts) not in (2, 3):
        await message.answer(
            "Формат:\n"
            "/move_all FROM\n"
            "/move_all FROM TO\n\n"
            f"Склады: {_warehouse_help()}"
        )
        return

    if len(parts) == 2:
        _, src = parts
        ok, err, moved, dst = move_all_auto_shop(src)
        if not ok:
            await message.answer(f"❌ {err}")
            return
        await message.answer(f"✅ Перенесено: {moved} позиций из {src.upper()} в {dst}. {src.upper()} очищен.")
        return

    _, src, dst = parts
    ok, err, moved = move_all(src, dst)
    if not ok:
        await message.answer(f"❌ {err}")
        return
    await message.answer(f"✅ Перенесено: {moved} позиций из {src.upper()} в {dst.upper()}. {src.upper()} очищен.")


@router.message(Command("cart_start"))
async def cmd_cart_start(message: Message):
    if not _is_admin(message):
        return

    global ACTIVE_CLIENT

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Формат: /cart_start CLIENT_NAME")
        return

    client_name = parts[1].strip()
    try:
        cart_start(client_name)
        ACTIVE_CLIENT = client_name
        await message.answer(f"🧺 Корзина начата. Клиент: <b>{client_name}</b>", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        await message.answer(f"❌ Ошибка корзины: {e}")


@router.message(Command("cart_source"))
async def cmd_cart_source(message: Message):
    if not _is_admin(message):
        return

    global ACTIVE_CART_SOURCE

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Формат: /cart_source CHINA или /cart_source DEALER")
        return

    src = parts[1].strip().upper()
    if src not in ("CHINA", "DEALER"):
        await message.answer("Источник должен быть CHINA или DEALER")
        return

    ACTIVE_CART_SOURCE = src
    await message.answer(f"✅ Источник продажи: <b>{ACTIVE_CART_SOURCE}</b> (склад списания: {_shop_for_source()})")


@router.message(Command("cart_add"))
async def cmd_cart_add(message: Message):
    if not _is_admin(message):
        return

    client_name = _require_active_client()
    if not client_name:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return

    parts = message.text.split()
    if len(parts) < 4:
        await message.answer("Формат: /cart_add BRAND MODEL QTY [wh|wh10|custom] [custom_price]")
        return

    _, brand, model, qty_s = parts[:4]
    price_mode = parts[4] if len(parts) >= 5 else "wh"
    custom_price = None

    if price_mode.lower() == "custom":
        if len(parts) < 6:
            await message.answer("Для custom нужно указать custom_price: /cart_add ... custom 15.00")
            return
        try:
            custom_price = _parse_price(parts[5])
        except Exception:
            await message.answer("custom_price должен быть числом, пример: 15.00")
            return

    try:
        qty = _parse_qty(qty_s)
    except Exception:
        await message.answer("QTY должно быть числом, пример: 2 или 2.5")
        return

    ok, err = cart_add(client_name, brand, model, qty, price_mode, custom_price)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    await message.answer(
        f"✅ Добавлено в корзину ({client_name}): {brand} {model} × {qty} ({price_mode})\n"
        f"Источник продажи: {ACTIVE_CART_SOURCE} (спишется из {_shop_for_source()})"
    )


@router.message(Command("cart_show"))
async def cmd_cart_show(message: Message):
    if not _is_admin(message):
        return

    client_name = _require_active_client()
    if not client_name:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return

    ok, text = cart_show(client_name)
    if not ok:
        await message.answer(f"❌ {text}")
        return

    await message.answer(text)


@router.message(Command("cart_remove"))
async def cmd_cart_remove(message: Message):
    if not _is_admin(message):
        return

    client_name = _require_active_client()
    if not client_name:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /cart_remove BRAND MODEL")
        return

    _, brand, model = parts
    ok, err = cart_remove(client_name, brand, model)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    await message.answer(f"✅ Удалено из корзины ({client_name}): {brand} {model}")


@router.message(Command("cart_finish"))
async def cmd_cart_finish(message: Message):
    if not _is_admin(message):
        return

    global ACTIVE_CLIENT

    client_name = _require_active_client()
    if not client_name:
        await message.answer("Сначала выбери клиента: /cart_start CLIENT_NAME")
        return

    shop = _shop_for_source()
    ok, err, invoice, items = cart_finish_from_shop(client_name, shop)
    if not ok:
        await message.answer(f"❌ {err}")
        return

    try:
        pdf_path = generate_invoice_pdf(invoice, items)
        await message.answer_document(open(pdf_path, "rb"))
    except Exception as e:
        await message.answer(f"⚠️ Инвойс создан, но PDF не сгенерировался: {e}")

    try:
        backup_path = make_backup()
        await message.answer_document(open(backup_path, "rb"))
    except Exception as e:
        await message.answer(f"⚠️ Продажа завершена, но backup не сделал: {e}")

    await message.answer(
        f"✅ Продажа завершена. Инвойс #{int(invoice['number']):06d}\n"
        f"Клиент: {client_name}\n"
        f"Склад списания: {shop}\n"
        f"Сумма: {float(invoice['total']):.2f} {invoice['currency']}"
    )

    ACTIVE_CLIENT = None