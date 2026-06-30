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
    global _shutdown

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

    # Initialize scheduler (shared between background worker and MCP server)
    from cron_scheduler import CronScheduler
    scheduler = CronScheduler(settings.MCP_SERVER_DB)
    await scheduler.init()

    # Set scheduler ref for MCP server
    from mcp_server import set_scheduler
    set_scheduler(scheduler)

    # Start background worker with bot and scheduler
    from background_worker import run_scheduler_loop
    worker_task = asyncio.create_task(
        run_scheduler_loop(bot, scheduler),
        name="scheduler-worker"
    )

    # Start MCP server
    from mcp_server import mcp
    mcp_task = asyncio.create_task(
        mcp.run(transport="http", host="0.0.0.0", port=settings.MCP_SERVER_PORT),
        name="mcp-server"
    )

    try:
        await dp.start_polling(bot)
        await _handle_signal()
    finally:
        # Shutdown: cancel all tasks
        worker_task.cancel()
        mcp_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        try:
            await mcp_task
        except asyncio.CancelledError:
            pass

        await scheduler.close()
        await bot.session.close()
        await message_handler.close_client()


if __name__ == "__main__":
    asyncio.run(main())
