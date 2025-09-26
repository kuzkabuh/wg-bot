import asyncio
import logging
import os
import platform
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# uvloop (если доступен и не Windows)
if platform.system() != "Windows":
    try:
        import uvloop  # type: ignore
        uvloop.install()
    except Exception:
        pass

import app.context as ctx
from app.handlers.user import register_user_handlers
from app.handlers.admin import register_admin_handlers
from app.webhook import start_wgd_webhook
from app.services.sync import resync_from_runtime

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("wg-bot")


def _ensure_dir(path: str | None):
    if path:
        os.makedirs(path, exist_ok=True)


def ensure_dirs():
    _ensure_dir(getattr(ctx.SET, "DATA_DIR", None))
    _ensure_dir(getattr(ctx.SET, "CONFIGS_DIR", None))
    _ensure_dir(getattr(ctx.SET, "QRCODES_DIR", None))


def _validate_env() -> None:
    missing = []
    if not getattr(ctx.SET, "BOT_TOKEN", None):
        missing.append("BOT_TOKEN")
    if not getattr(ctx.SET, "WG_INTERFACE", None):
        missing.append("WG_INTERFACE")
    if not getattr(ctx.SET, "WG_CONFIG", None):
        missing.append("WG_CONFIG")
    if not getattr(ctx.SET, "WG_PRIVATE_SUBNET_V4", None):
        missing.append("WG_PRIVATE_SUBNET_V4")
    if not getattr(ctx.SET, "WG_PRIVATE_SUBNET_V6", None):
        missing.append("WG_PRIVATE_SUBNET_V6")

    if missing:
        for m in missing:
            logger.error("Required env var missing: %s", m)
        sys.exit(1)


async def main():
    # 1) env & контекст
    load_dotenv()
    ctx.init_context()
    _validate_env()

    # 2) подготовка директорий и БД
    ensure_dirs()
    await ctx.DBH.init()

    # 3) начальная синхронизация с живым состоянием WireGuard
    try:
        await resync_from_runtime()
    except Exception as e:
        logger.warning("Initial WG resync failed: %s", e)

    # 4) старт локального HTTP для вебхуков WGDashboard (если включено)
    webhook_task = asyncio.create_task(start_wgd_webhook())

    # 5) Bot + Dispatcher
    bot = Bot(
        token=ctx.SET.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    register_user_handlers(dp)
    register_admin_handlers(dp)

    allowed_updates = dp.resolve_used_update_types()
    logger.info("Starting bot... allowed_updates=%s", allowed_updates)

    # 6) запуск поллинга
    try:
        async with bot:
            await dp.start_polling(
                bot,
                allowed_updates=allowed_updates,
            )
    finally:
        # аккуратное завершение вебхука
        try:
            webhook_task.cancel()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
