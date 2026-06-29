#!/usr/bin/env python3
import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from handlers import message_handler
from handlers.retry_middleware import RetryMiddleware
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_shutdown = False


async def _handle_signal() -> None:
    global _shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: (_shutdown := True))
    while not _shutdown:
        await asyncio.sleep(0.5)


async def main():
    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN is not set or invalid. Please set a valid Telegram bot token in the environment.")
        logger.error("Container will now exit. Set a valid token to run the bot.")
        return
    
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.message.middleware(RetryMiddleware())

    dp.include_router(message_handler.router(bot))
    
    logger.info("Starting Telegram bot...")
    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(bot)
    await _handle_signal()
    await bot.session.close()
    from handlers.message_handler import close_client
    await close_client()

if __name__ == "__main__":
    asyncio.run(main())