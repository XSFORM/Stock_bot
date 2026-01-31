import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from app.config import settings
from app.db.sqlite import init_db
from app.bot.handlers import router

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    init_db()

    bot = Bot(token=settings.bot_token, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
