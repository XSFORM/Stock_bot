import shlex
from aiogram.fsm.context import FSMContext

from app.bot.states import ClientAdd, ProductAdd


from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.db.sqlite import (
    add_client,
    add_product,
    init_db,
    list_clients,
    list_products,
    move_stock,
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
    

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("❎ Отменено. Можно вводить команды заново.")
   


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not _is_admin(message):
        return

    text = (
        "<b>Stock_bot — команды</b>\n\n"
        "<b>Основное</b>\n"
        "/start — запуск\n"
        "/cancel — отмена ввода"
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
async def cmd_client_add(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    parts = message.text.split(maxsplit=1)

    # 1) Если сразу передали имя: /client_add ali
    if len(parts) >= 2 and parts[1].strip():
        name = parts[1].strip()
        try:
            add_client(name)
            await message.answer(f"✅ Клиент добавлен: {name}")
        except Exception as e:
            await message.answer(f"❌ Ошибка при добавлении клиента: {e}")
        return

    # 2) Если нажали просто /client_add — включаем пошаговый режим
    await state.set_state(ClientAdd.waiting_name)
    await message.answer(
        "Введите имя клиента одним сообщением.\n"
        "Пример: ali\n\n"
        "Отмена: /cancel"
    )


@router.message(ClientAdd.waiting_name)
async def client_add_wait_name(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    name = (message.text or "").strip()
    if not name or name.startswith("/"):
        await message.answer("Имя не похоже на имя 🙂 Введите имя текстом. Отмена: /cancel")
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
        await message.answer("Товаров пока нет. Добавь: /product_add ...")
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

    # Поддержим старый формат:
    # /product_add brand model "Name" 12.50
    parts = message.text.split(maxsplit=4)
    if len(parts) >= 5:
        brand = parts[1].strip()
        model = parts[2].strip()
        name = parts[3].strip().replace('"', "")
        wh_price = parts[4].strip()

        try:
            add_product(brand, model, name, float(wh_price))
            await message.answer(f"✅ Товар добавлен: {brand} {model} ({float(wh_price):.2f}$)")
        except Exception as e:
            await message.answer(f"❌ Ошибка добавления товара: {e}")
        return

    # Если нажали просто /product_add — пошаговый режим
    await state.clear()
    await state.set_state(ProductAdd.waiting_brand)
    await message.answer(
        "Ок, добавляем товар.\n\n"
        "1/4) Введите БРЕНД (например: sonifer)\n"
        "Отмена: /cancel"
    )


@router.message(ProductAdd.waiting_brand)
async def product_add_brand(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    brand = (message.text or "").strip()
    if not brand or brand.startswith("/"):
        await message.answer("Введите бренд текстом. Пример: sonifer\nОтмена: /cancel")
        return

    await state.update_data(brand=brand)
    await state.set_state(ProductAdd.waiting_model)
    await message.answer("2/4) Введите МОДЕЛЬ (например: sf-8040)\nОтмена: /cancel")


@router.message(ProductAdd.waiting_model)
async def product_add_model(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    model = (message.text or "").strip()
    if not model or model.startswith("/"):
        await message.answer("Введите модель текстом. Пример: sf-8040\nОтмена: /cancel")
        return

    await state.update_data(model=model)
    await state.set_state(ProductAdd.waiting_name)
    await message.answer(
        "3/4) Введите НАЗВАНИЕ (можно коротко),\n"
        "или отправьте '-' чтобы пропустить.\n"
        "Пример: Blender 800W\n"
        "Отмена: /cancel"
    )


@router.message(ProductAdd.waiting_name)
async def product_add_name(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    name = (message.text or "").strip()
    if not name:
        await message.answer("Введите название или '-' чтобы пропустить.\nОтмена: /cancel")
        return

    # Если пропустили — сделаем имя = model (удобно, чтобы не было пусто)
    data = await state.get_data()
    if name == "-":
        name = data.get("model", "")

    await state.update_data(name=name)
    await state.set_state(ProductAdd.waiting_price)
    await message.answer(
        "4/4) Введите ЦЕНУ ПРИХОДА (wh) в USD.\n"
        "Пример: 12.50\n"
        "Отмена: /cancel"
    )


@router.message(ProductAdd.waiting_price)
async def product_add_price(message: Message, state: FSMContext):
    if not _is_admin(message):
        return

    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
        if price <= 0:
            raise ValueError("price <= 0")
    except Exception:
        await message.answer("Цена должна быть числом, например 12.50\nОтмена: /cancel")
        return

    data = await state.get_data()
    brand = data["brand"]
    model = data["model"]
    name = data["name"]

    try:
        add_product(brand, model, name, price)
        await message.answer(
            f"✅ Товар добавлен:\n"
            f"{brand} {model}\n"
            f"Название: {name}\n"
            f"Цена прихода: {price:.2f}$"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка добавления товара: {e}")
        return
    finally:
        await state.clear()



@router.message(Command("stock"))
async def cmd_stock(message: Message):
    if not _is_admin(message):
        return
    init_db()
    parts = message.text.split(maxsplit=1)
    wh = parts[1].strip().upper() if len(parts) > 1 else None
    try:
        from app.db.sqlite import get_stock_text
        text = get_stock_text(wh)
        await message.answer(text)
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




    # после завершения — сброс активного клиента (чтобы случайно не продолжить)
    ACTIVE_CLIENT = None
