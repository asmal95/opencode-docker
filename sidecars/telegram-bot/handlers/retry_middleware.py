#!/usr/bin/env python3
import asyncio
import logging
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0


class RetryMiddleware(BaseMiddleware):
    """Retry transient Telegram network errors with exponential backoff."""

    def __init__(self, max_retries: int = _MAX_RETRIES, base_delay: float = _BASE_DELAY) -> None:
        super().__init__()
        self._max_retries = max_retries
        self._base_delay = base_delay

    async def __call__(self, handler, event, data):
        last_exc = None

        for attempt in range(self._max_retries + 1):
            try:
                return await handler(event, data)
            except TelegramNetworkError as e:
                last_exc = e
                if attempt == self._max_retries:
                    break
                delay = self._base_delay * (2 ** attempt)
                logger.warning(
                    "Telegram network error on %s (attempt %d/%d), retrying in %.1fs: %s",
                    type(event).__name__,
                    attempt + 1,
                    self._max_retries + 1,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[unreachable]
