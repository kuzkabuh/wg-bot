"""Entry point for the WireGuard Telegram bot.

This script sets up the runtime environment, validates configuration,
initialises the database, synchronises the runtime WireGuard state,
starts the webhook server for WireGuard Dashboard events, and finally
starts the Telegram bot using aiogram's polling mechanism.  It
supports running under uvloop on non‑Windows platforms for improved
performance.
"""

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

# uvloop (if available and not Windows)
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


def _ensure_dir(path: str | None) -> None:
    """Create a directory if the given path is not ``None``.

    This helper is idempotent and ignores failures such as the
    directory already existing.
    """
    if path:
        os.makedirs(path, exist_ok=True)


def ensure_dirs() -> None:
    """Create the data, configs and QR codes directories if needed."""
    _ensure_dir(getattr(ctx.SET, "DATA_DIR", None))
    _ensure_dir(getattr(ctx.SET, "CONFIGS_DIR", None))
    _ensure_dir(getattr(ctx.SET, "QRCODES_DIR", None))


def _validate_env() -> None:
    """Check that all required environment variables are set.

    On missing variables, logs an error and exits the program.
    """
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


async def main() -> None:
    """Main asynchronous entry point for the bot.

    This function performs the following steps:

    1. Load environment variables and initialise context
    2. Validate required configuration
    3. Prepare directories and initialise the database
    4. Perform an initial synchronisation of runtime peers
    5. Start the optional webhook server
    6. Create the bot and dispatcher, register handlers and start polling
    7. On shutdown, cleanly cancel the webhook server task
    """
    # 1) env & context
    load_dotenv()
    ctx.init_context()
    _validate_env()
    # 2) prepare directories and database
    ensure_dirs()
    await ctx.DBH.init()
    # 3) initial synchronisation with live WireGuard state
    try:
        await resync_from_runtime()
    except Exception as e:
        logger.warning("Initial WG resync failed: %s", e)
    # 4) start local HTTP server for WG Dashboard webhooks (if enabled)
    webhook_task = asyncio.create_task(start_wgd_webhook())
    # 5) Bot + Dispatcher
    bot = Bot(
        token=ctx.SET.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    # Register admin handlers before user handlers so that admin commands
    # and buttons (e.g. the persistent «Админ-панель» button) are
    # processed prior to the catch‑all handlers in the user router.
    register_admin_handlers(dp)
    register_user_handlers(dp)
    allowed_updates = dp.resolve_used_update_types()
    logger.info("Starting bot... allowed_updates=%s", allowed_updates)
    # 6) start polling
    try:
        async with bot:
            await dp.start_polling(
                bot,
                allowed_updates=allowed_updates,
            )
    finally:
        # graceful shutdown of webhook
        try:
            webhook_task.cancel()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")