import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.context import SET
from app.handlers import user as user_handlers
from app.handlers import admin as admin_handlers
from app.services.payments import handle_buy_callback
from app.services import background

async def main():
    bot = Bot(SET.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    # Роутеры
    dp.include_router(user_handlers.router)
    dp.include_router(admin_handlers.router)

    # Глобальный обработчик оплат (inline callbacks 'buy:*')
    @dp.callback_query(lambda c: c.data and c.data.startswith("buy:"))
    async def _buy(c):
        await handle_buy_callback(c)

    # Фоновая зачистка просроченных устройств
    asyncio.create_task(background.expire_sweeper())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
