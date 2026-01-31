from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/help"), KeyboardButton(text="/stock")],
            [KeyboardButton(text="/clients"), KeyboardButton(text="/products")],
            [KeyboardButton(text="/backup"), KeyboardButton(text="/ping")],
        ],
        resize_keyboard=True,
    )
