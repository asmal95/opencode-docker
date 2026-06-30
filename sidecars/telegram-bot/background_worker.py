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
    """Dispatch a single cron job to OpenCode and deliver result."""
    job_id = job["id"]
    job_name = job["name"]
    prompt = job["payload"].get("prompt", "")
    delivery = job.get("delivery", {})

    channel = delivery.get("channel", "telegram")
    to_target = delivery.get("to", "")

    if channel != "telegram":
        logger.warning(f"Unsupported delivery channel: {channel}. Skipping job {job_id}")
        return

    if not to_target.startswith("chat:"):
        logger.error(f"Invalid delivery target format: {to_target}. Expected 'chat:<id>'")
        return

    chat_id = int(to_target.split("chat:")[1])
    logger.info(f"Dispatching cron job '{job_name}' to chat {chat_id}")

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

    # Mark job as ran
    await scheduler.mark_job_ran(job_id)


async def run_scheduler_loop(bot: Bot, scheduler: CronScheduler) -> None:
    """Main loop: check for due cron jobs every 60 seconds."""
    logger.info("Background worker loop started")

    while True:
        try:
            due_jobs = await scheduler.get_due_jobs()
            if due_jobs:
                logger.info(f"Found {len(due_jobs)} due cron job(s)")
                for job in due_jobs:
                    await _dispatch_job(bot, scheduler, job)
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")

        await asyncio.sleep(60)
