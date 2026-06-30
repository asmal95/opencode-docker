#!/usr/bin/env python3
"""Background worker that dispatches cron jobs to OpenCode.

Runs as an asyncio task. Checks for due cron jobs every 60 seconds,
dispatches them to OpenCode, and delivers results to Telegram users.
"""
import asyncio
import logging
import base64

import httpx

from aiogram import Bot

from config import settings
from cron_scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Retry configuration for OpenCode calls
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds, exponential backoff base

# Per-chat session tracking
_session_map: dict[int, str] = {}

# Wakeup event for background worker (set from cron_scheduler or bot)
wakeup: asyncio.Event = asyncio.Event()


async def _get_or_create_session(bot: Bot, chat_id: int) -> str | None:
    """Get or create a session for the given chat ID."""
    if chat_id in _session_map:
        return _session_map[chat_id]

    credentials = f"opencode:{settings.OPENCODE_SERVER_PASSWORD}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}",
    }

    async with httpx.AsyncClient(
        base_url=settings.OPENCODE_API_URL,
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers=headers,
    ) as client:
        try:
            resp = await client.get("/session")
            resp.raise_for_status()
            sessions = resp.json()
            title = f"chat:{chat_id}"
            for s in sessions:
                if s.get("title", "").startswith(title):
                    _session_map[chat_id] = s["id"]
                    return s["id"]
        except Exception as e:
            logger.error(f"Error finding session for chat {chat_id}: {e}")

    return None


async def _call_opencode(prompt: str, session_id: str) -> str | None:
    """Send a prompt to OpenCode and get the response text."""
    credentials = f"opencode:{settings.OPENCODE_SERVER_PASSWORD}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}",
        "Content-Type": "application/json",
    }

    url = f"{settings.OPENCODE_API_URL}/session/{session_id}/message"
    body = {
        "parts": [{"type": "text", "text": prompt}],
    }

    async with httpx.AsyncClient(
        base_url=settings.OPENCODE_API_URL,
        timeout=httpx.Timeout(300.0, connect=10.0),
        headers=headers,
    ) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        response_data = resp.json()

    parts = response_data.get("parts", [])
    text_parts = [p for p in parts if p.get("type") == "text"]
    if text_parts:
        return text_parts[0].get("text", "")
    return None


async def _dispatch_job(bot: Bot, scheduler: CronScheduler, job: dict) -> None:
    """Dispatch a single cron job to OpenCode and deliver result.

    Uses a two-phase commit:
      Phase 1: mark is_running=1 and calculate next_run (before dispatch)
      Phase 2: mark last_run, run_count, is_running=0 (after successful delivery)
    This prevents duplicate dispatch if the worker crashes between get and mark.
    """
    job_id = job["jobId"]
    job_name = job["name"]
    prompt = job["payload"].get("prompt", "")
    delivery = job.get("delivery", {})

    channel = delivery.get("channel", "telegram")
    to_target = delivery.get("to", "")

    # Support "chat:<id>", "user:<id>", "user:current", or direct numeric chat_id
    chat_id = None

    if delivery.get("chat_id"):
        try:
            chat_id = int(delivery["chat_id"])
        except (ValueError, TypeError):
            pass

    if chat_id is None and to_target.startswith("chat:"):
        try:
            chat_id = int(to_target.split("chat:")[1])
        except ValueError:
            pass

    if chat_id is None and to_target.startswith("user:"):
        user_val = to_target.split("user:")[1]
        if user_val == "current":
            prompt_payload = job["payload"].get("prompt", "")
            try:
                chat_id = int(prompt_payload.split("Chat ID: ")[1].split(" ")[0])
            except (ValueError, IndexError, AttributeError):
                pass
        else:
            try:
                chat_id = int(user_val)
            except ValueError:
                pass

    if chat_id is None:
        logger.error(f"Invalid delivery target. Got 'to={to_target}', delivery={delivery}. Expected 'chat:<id>', 'user:<id>', 'user:current' (with Chat ID in prompt), or 'chat_id' in delivery.")
        return
    logger.info(f"Dispatching cron job '{job_name}' to chat {chat_id}")

    # Phase 1: mark job as running (sets next_run, is_running=1)
    await scheduler.mark_job_ran(job_id, completed=False)

    session_id = await _get_or_create_session(bot, chat_id)
    if not session_id:
        logger.error(f"Cannot dispatch job {job_id}: no session for chat {chat_id}")
        return

    response_text = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response_text = await _call_opencode(prompt, session_id)
            if response_text:
                break
        except Exception as e:
            logger.error(f"Error calling OpenCode for job {job_id} (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

    if not response_text:
        logger.error(f"Job {job_id} failed after {MAX_RETRIES} attempts")
        return

    # Deliver result via Telegram
    for chunk in [response_text[i:i+4096] for i in range(0, len(response_text), 4096)]:
        await bot.send_message(chat_id, chunk)

    logger.info(f"Cron job '{job_name}' delivered to chat {chat_id}")

    # Phase 2: mark job as completed
    await scheduler.mark_job_ran(job_id, completed=True)


async def _dispatch_job_with_timeout(bot: Bot, scheduler: CronScheduler, job: dict) -> None:
    """Dispatch a job with an overall timeout to prevent indefinite blocking."""
    try:
        await asyncio.wait_for(
            _dispatch_job(bot, scheduler, job),
            timeout=600.0,
        )
    except asyncio.TimeoutError:
        logger.error(f"Job dispatch timed out for {job['jobId']} ({job['name']})")
    except Exception as e:
        logger.error(f"Unexpected error dispatching job {job['jobId']}: {e}")


async def run_scheduler_loop(bot: Bot, scheduler: CronScheduler) -> None:
    """Main loop: check for due cron jobs, with wakeup support.

    Sleeps up to 60 seconds between checks, but wakes immediately when
    wakeup.set() is called (e.g., after cron_add, cron_delete, cron_run).
    """
    logger.info("Background worker loop started")

    while True:
        try:
            due_jobs = await scheduler.get_due_jobs()
            if due_jobs:
                logger.info(f"Found {len(due_jobs)} due cron job(s)")
                for job in due_jobs:
                    await _dispatch_job_with_timeout(bot, scheduler, job)
                    wakeup.set()
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")

        # Wait for wakeup or 60s timeout, whichever comes first.
        # asyncio.wait ensures we don't miss a wakeup that fires right after clear().
        sleep_task = asyncio.create_task(asyncio.sleep(60.0))
        wait_task = asyncio.create_task(wakeup.wait())
        done, pending = await asyncio.wait(
            {sleep_task, wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
