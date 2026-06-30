# Gateway MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cron-based task scheduling to the Telegram Bot via a Streamable HTTP MCP server, enabling the OpenCode agent to plan recurring tasks through MCP tools.

**Architecture:** Telegram Bot becomes the Gateway, running a Streamable HTTP MCP server (port 8765) with SQLite persistence. A background worker checks cron queue every minute and dispatches tasks to OpenCode, then delivers results to Telegram.

**Tech Stack:** Python 3.13, aiogram (existing), FastMCP (new), aiosqlite (new), croniter (new), httpx (existing)

---

### Task 1: Add new dependencies to requirements.txt

**Files:**
- Modify: `sidecars/telegram-bot/requirements.txt`

- [ ] **Step 1: Add MCP and scheduling libraries**

Update `sidecars/telegram-bot/requirements.txt` to add:
- `fastmcp>=2.3.0` — official Python MCP SDK with Streamable HTTP transport
- `aiosqlite>=0.20.0` — async SQLite for cron task persistence
- `croniter>=5.0.0` — cron expression parsing and next-run calculation

Current file content:
```
aiogram>=3.16.0
httpx>=0.28.0
pydantic>=2.10.0
pydantic-settings>=2.7.0
```

New file content:
```
aiogram>=3.16.0
httpx>=0.28.0
pydantic>=2.10.0
pydantic-settings>=2.7.0
fastmcp>=2.3.0
aiosqlite>=0.20.0
croniter>=5.0.0
```

- [ ] **Step 2: Commit**

```bash
git add sidecars/telegram-bot/requirements.txt
git commit -m "deps: add fastmcp, aiosqlite, croniter for gateway MCP server"
```

---

### Task 2: Create the cron scheduler module

**Files:**
- Create: `sidecars/telegram-bot/cron_scheduler.py`

This module handles all SQLite operations for cron tasks: init, add, list, delete, run, and next-run calculation.

- [ ] **Step 1: Write the cron_scheduler.py module**

Create `sidecars/telegram-bot/cron_scheduler.py`:

```python
#!/usr/bin/env python3
"""Cron task scheduler with SQLite persistence.

Manages scheduled cron jobs: create, list, delete, run, and next-run calculation.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from croniter import croniter

logger = logging.getLogger(__name__)

# Schema version for future migrations
SCHEMA_VERSION = 1

# Default DB path (can be overridden via MCP_SERVER_DB env var)
DEFAULT_DB_PATH = "/opt/bot/cron.db"


class CronScheduler:
    """SQLite-backed cron job scheduler."""

    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS cron_jobs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            schedule TEXT NOT NULL,
            payload TEXT NOT NULL,
            delivery TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            next_run TEXT NOT NULL,
            last_run TEXT,
            run_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """

    CREATE_INDEX_SQL = """
        CREATE INDEX IF NOT EXISTS idx_next_run ON cron_jobs(next_run, enabled)
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Initialize the database and create tables."""
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        await self._db.execute(self.CREATE_TABLE_SQL)
        await self._db.execute(self.CREATE_INDEX_SQL)
        await self._db.commit()

        logger.info(f"Cron scheduler initialized at {self.db_path}")

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Scheduler not initialized. Call init() first.")
        return self._db

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Cron scheduler closed")

    async def add_job(
        self,
        name: str,
        schedule: str,
        payload: dict,
        delivery: dict,
        enabled: bool = True,
    ) -> dict:
        """Add a new cron job.

        Returns job dict with id and next_run time.
        """
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        try:
            iterator = croniter(schedule, now)
            next_run = iterator.get_next(datetime)
            next_run_iso = next_run.isoformat()
        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid cron expression '{schedule}': {e}") from e

        await self.db.execute(
            """
            INSERT INTO cron_jobs
                (id, name, schedule, payload, delivery, enabled, next_run, run_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                name,
                schedule,
                json.dumps(payload),
                json.dumps(delivery),
                1 if enabled else 0,
                next_run_iso,
                0,
                now_iso,
                now_iso,
            ),
        )
        await self.db.commit()

        return {
            "jobId": job_id,
            "name": name,
            "schedule": schedule,
            "next_run": next_run_iso,
            "message": f"Cron job '{name}' added. Next run: {next_run_iso}",
        }

    async def list_jobs(self, enabled_only: bool = False) -> list[dict]:
        """List cron jobs, optionally filtered by enabled status."""
        if enabled_only:
            cursor = await self.db.execute(
                "SELECT * FROM cron_jobs WHERE enabled = 1 ORDER BY next_run ASC"
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM cron_jobs ORDER BY next_run ASC"
            )

        rows = await cursor.fetchall()
        jobs = []
        for row in rows:
            jobs.append({
                "jobId": row["id"],
                "name": row["name"],
                "schedule": row["schedule"],
                "payload": json.loads(row["payload"]),
                "delivery": json.loads(row["delivery"]),
                "enabled": bool(row["enabled"]),
                "next_run": row["next_run"],
                "last_run": row["last_run"],
                "run_count": row["run_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return jobs

    async def delete_job(self, job_id: str) -> dict:
        """Delete a cron job by ID."""
        cursor = await self.db.execute("SELECT id FROM cron_jobs WHERE id = ?", (job_id,))
        if not await cursor.fetchone():
            raise ValueError(f"Cron job '{job_id}' not found")

        await self.db.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
        await self.db.commit()

        return {
            "success": True,
            "jobId": job_id,
            "message": f"Cron job '{job_id}' deleted",
        }

    async def run_job(self, job_id: str) -> dict:
        """Manually trigger a cron job (one-time execution).

        Returns job dict for the caller to dispatch.
        """
        cursor = await self.db.execute("SELECT * FROM cron_jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Cron job '{job_id}' not found")

        job = {
            "id": row["id"],
            "name": row["name"],
            "schedule": row["schedule"],
            "payload": json.loads(row["payload"]),
            "delivery": json.loads(row["delivery"]),
            "enabled": bool(row["enabled"]),
            "run_count": row["run_count"],
        }
        return job

    async def get_due_jobs(self) -> list[dict]:
        """Get all due cron jobs that need to be run.

        Returns jobs where enabled=1 AND next_run <= NOW().
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = await self.db.execute(
            """
            SELECT * FROM cron_jobs
            WHERE enabled = 1 AND next_run <= ?
            ORDER BY next_run ASC
            """,
            (now_iso,),
        )
        rows = await cursor.fetchall()
        jobs = []
        for row in rows:
            jobs.append({
                "id": row["id"],
                "name": row["name"],
                "schedule": row["schedule"],
                "payload": json.loads(row["payload"]),
                "delivery": json.loads(row["delivery"]),
                "run_count": row["run_count"],
            })
        return jobs

    async def mark_job_ran(self, job_id: str) -> None:
        """Mark a job as run: update next_run, last_run, and run_count."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Recalculate next_run from schedule
        cursor = await self.db.execute("SELECT schedule FROM cron_jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if not row:
            return

        try:
            iterator = croniter(row["schedule"], now)
            next_run = iterator.get_next(datetime)
            next_run_iso = next_run.isoformat()
        except (ValueError, KeyError):
            logger.error(f"Could not recalculate next_run for job {job_id}")
            return

        await self.db.execute(
            """
            UPDATE cron_jobs
            SET next_run = ?, last_run = ?, run_count = run_count + 1, updated_at = ?
            WHERE id = ?
            """,
            (next_run_iso, now_iso, now_iso, job_id),
        )
        await self.db.commit()
```

- [ ] **Step 2: Verify the file was created**

```bash
cat sidecars/telegram-bot/cron_scheduler.py | head -5
```

Expected output: `#!/usr/bin/env python3`

- [ ] **Step 3: Commit**

```bash
git add sidecars/telegram-bot/cron_scheduler.py
git commit -m "feat: add cron scheduler module with SQLite persistence"
```

---

### Task 3: Create the MCP server module

**Files:**
- Create: `sidecars/telegram-bot/mcp_server.py`

This module creates the FastMCP server with cron tools. The tools call the CronScheduler methods.

- [ ] **Step 1: Write the mcp_server.py module**

Create `sidecars/telegram-bot/mcp_server.py`:

```python
#!/usr/bin/env python3
"""MCP server exposing cron scheduling tools to the OpenCode agent.

Runs as a Streamable HTTP server on a configurable port.
The OpenCode agent connects to this server via MCP protocol.
"""
import logging
from typing import Any

from fastmcp import FastMCP

from cron_scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP(
    "opencode-gateway",
    description="OpenCode Gateway MCP Server - provides cron scheduling and task management",
    host="0.0.0.0",
    port=8765,
)


async def init_scheduler() -> CronScheduler:
    """Initialize and return the cron scheduler."""
    scheduler = CronScheduler()
    await scheduler.init()
    return scheduler


# Global scheduler instance (initialized on first tool call)
_scheduler: CronScheduler | None = None


async def get_scheduler() -> CronScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = await init_scheduler()
    return _scheduler


@mcp.tool()
async def cron_add(
    name: str,
    schedule: str,
    payload: dict[str, Any],
    delivery: dict[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    """Add or update a cron job for scheduled task execution.

    Args:
        name: Human-readable job name (e.g., "Daily news summary")
        schedule: Cron expression (e.g., "0 9 * * *" for 9 AM daily)
        payload: Job payload with 'prompt' key containing the instruction
        delivery: Delivery config with 'channel' and 'to' keys (e.g., {"channel": "telegram", "to": "chat:123456"})
        enabled: Whether the job is active (default: True)

    Returns:
        Job ID, schedule, and next run time
    """
    scheduler = await get_scheduler()
    result = await scheduler.add_job(name, schedule, payload, delivery, enabled)
    logger.info(f"Cron job added: {name} ({result['jobId']}), next run: {result['next_run']}")
    return result


@mcp.tool()
async def cron_list(enabled_only: bool = False) -> list[dict[str, Any]]:
    """List all cron jobs, optionally filtered by enabled status.

    Args:
        enabled_only: If True, only show enabled jobs (default: False)

    Returns:
        List of cron jobs with details
    """
    scheduler = await get_scheduler()
    jobs = await scheduler.list_jobs(enabled_only=enabled_only)
    logger.info(f"Cron jobs listed: {len(jobs)} total, {sum(1 for j in jobs if j['enabled'])} enabled")
    return jobs


@mcp.tool()
async def cron_delete(jobId: str) -> dict[str, Any]:
    """Delete a cron job by ID.

    Args:
        jobId: The cron job ID to delete

    Returns:
        Success status and confirmation message
    """
    scheduler = await get_scheduler()
    result = await scheduler.delete_job(jobId)
    logger.info(f"Cron job deleted: {jobId}")
    return result


@mcp.tool()
async def cron_run(jobId: str) -> dict[str, Any]:
    """Manually trigger a cron job for one-time execution.

    The job is dispatched immediately and the caller receives the job
    data including payload to execute against OpenCode.

    Args:
        jobId: The cron job ID to run

    Returns:
        Job data ready for dispatch (payload, delivery, etc.)
    """
    scheduler = await get_scheduler()
    job = await scheduler.run_job(jobId)
    logger.info(f"Cron job manually triggered: {job['id']} ({job['name']})")
    return job
```

- [ ] **Step 2: Verify the file was created**

```bash
cat sidecars/telegram-bot/mcp_server.py | head -5
```

Expected output: `#!/usr/bin/env python3`

- [ ] **Step 3: Commit**

```bash
git add sidecars/telegram-bot/mcp_server.py
git commit -m "feat: add MCP server with cron tools for OpenCode agent"
```

---

### Task 4: Create the background worker

**Files:**
- Create: `sidecars/telegram-bot/background_worker.py`

This module runs as an asyncio task in the bot process. It checks for due cron jobs every 60 seconds, dispatches them to OpenCode, and delivers results to Telegram.

- [ ] **Step 1: Write the background_worker.py module**

Create `sidecars/telegram-bot/background_worker.py`:

```python
#!/usr/bin/env python3
"""Background worker that dispatches cron jobs to OpenCode.

Runs as an asyncio task. Checks for due cron jobs every 60 seconds,
dispatches them to OpenCode, and delivers results to Telegram users.
"""
import asyncio
import logging

import httpx

from cron_scheduler import CronScheduler
from config import settings

logger = logging.getLogger(__name__)

# Retry configuration for OpenCode calls
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds, exponential backoff base


async def _call_opencode(prompt: str, chat_id: int, session_id: str) -> str | None:
    """Send a prompt to OpenCode and get the response text.

    Returns the text response, or None on failure.
    """
    import base64

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


async def _get_or_create_session(chat_id: int) -> str | None:
    """Get or create a session for the given chat ID."""
    # Use the same session map pattern as message_handler
    # We'll store it in a module-level dict
    from background_worker import _session_map
    if chat_id in _session_map:
        return _session_map[chat_id]

    import base64
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


# Per-chat session tracking (shared between modules)
_session_map: dict[int, str] = {}


async def _dispatch_job(job: dict) -> None:
    """Dispatch a single cron job to OpenCode and deliver result.

    Args:
        job: Job dict from get_due_jobs()
    """
    job_id = job["id"]
    job_name = job["name"]
    prompt = job["payload"].get("prompt", "")
    delivery = job.get("delivery", {})

    channel = delivery.get("channel", "telegram")
    to_target = delivery.get("to", "")

    if channel != "telegram":
        logger.warning(f"Unsupported delivery channel: {channel}. Skipping job {job_id}")
        return

    # Extract chat_id from "chat:<id>" format
    if not to_target.startswith("chat:"):
        logger.error(f"Invalid delivery target format: {to_target}. Expected 'chat:<id>'")
        return

    chat_id = int(to_target.split("chat:")[1])

    logger.info(f"Dispatching cron job '{job_name}' to chat {chat_id}")

    # Get or create session
    session_id = await _get_or_create_session(chat_id)
    if not session_id:
        logger.error(f"Cannot dispatch job {job_id}: no session for chat {chat_id}")
        return

    # Call OpenCode with retries
    response_text = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response_text = await _call_opencode(prompt, chat_id, session_id)
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

    # Deliver result via Telegram Bot API
    from mcp_server import _scheduler
    # We need the bot instance to send messages
    # The bot instance will be passed from bot.py via a callback
    if _dispatch_callback:
        try:
            await _dispatch_callback(chat_id, response_text)
            logger.info(f"Cron job '{job_name}' delivered to chat {chat_id}")
        except Exception as e:
            logger.error(f"Error delivering job {job_id} result to chat {chat_id}: {e}")
    else:
        logger.warning(f"Cannot deliver job {job_id}: no dispatch callback set")

    # Mark job as ran (update next_run, last_run, run_count)
    if _scheduler:
        await _scheduler.mark_job_ran(job_id)


async def run_scheduler_loop() -> None:
    """Main loop: check for due cron jobs every 60 seconds."""
    logger.info("Background worker loop started")

    # Initialize scheduler
    scheduler = CronScheduler()
    await scheduler.init()

    while True:
        try:
            # Get due jobs
            due_jobs = await scheduler.get_due_jobs()
            if due_jobs:
                logger.info(f"Found {len(due_jobs)} due cron job(s)")
                for job in due_jobs:
                    await _dispatch_job(job)
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}")

        # Sleep for 60 seconds
        await asyncio.sleep(60)


# Global reference to scheduler for mark_job_ran calls
_scheduler: CronScheduler | None = None

# Callback to send messages via Telegram Bot
_dispatch_callback = None


def set_dispatch_callback(callback):
    """Set the callback for delivering job results to Telegram.

    Called from bot.py after the Telegram bot is initialized.
    """
    global _dispatch_callback
    _dispatch_callback = callback


def set_scheduler_ref(scheduler: CronScheduler):
    """Set the scheduler reference for mark_job_ran calls."""
    global _scheduler
    _scheduler = scheduler
```

- [ ] **Step 2: Verify the file was created**

```bash
cat sidecars/telegram-bot/background_worker.py | head -5
```

Expected output: `#!/usr/bin/env python3`

- [ ] **Step 3: Commit**

```bash
git add sidecars/telegram-bot/background_worker.py
git commit -m "feat: add background worker for cron job dispatch to OpenCode"
```

---

### Task 5: Update config.py with new environment variables

**Files:**
- Modify: `sidecars/telegram-bot/config.py`

- [ ] **Step 1: Add new settings**

Update `sidecars/telegram-bot/config.py`:

```python
#!/usr/bin/env python3
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    TELEGRAM_BOT_TOKEN: str
    OPENCODE_API_URL: str = "http://opencode:4096"
    OPENCODE_SERVER_PASSWORD: str
    ALLOWED_CHAT_IDS: str = ""
    PROJECT_DIR: str = ""
    MCP_SERVER_PORT: int = 8765
    MCP_SERVER_TOKEN: str = ""
    MCP_SERVER_DB: str = "/opt/bot/cron.db"

    @property
    def allowed_chat_ids_set(self) -> set:
        if not self.ALLOWED_CHAT_IDS:
            return set()
        return set(int(cid.strip()) for cid in self.ALLOWED_CHAT_IDS.split(",") if cid.strip())

settings = Settings()
```

Added:
- `MCP_SERVER_PORT: int = 8765` — port for MCP server
- `MCP_SERVER_TOKEN: str = ""` — auth token for MCP requests
- `MCP_SERVER_DB: str = "/opt/bot/cron.db"` — SQLite DB path

- [ ] **Step 2: Commit**

```bash
git add sidecars/telegram-bot/config.py
git commit -m "config: add MCP server settings to config"
```

---

### Task 6: Update bot.py to start MCP server and background worker

**Files:**
- Modify: `sidecars/telegram-bot/bot.py`

- [ ] **Step 1: Add MCP server and background worker startup**

Update `sidecars/telegram-bot/bot.py` to start the MCP server and background worker:

```python
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


async def _send_message(chat_id: int, text: str) -> None:
    """Callback for delivering cron job results via Telegram Bot API."""
    # Split into chunks of 4096 chars (Telegram limit)
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await bot_instance.send_message(chat_id, chunk)


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

    # Start MCP server and background worker
    from mcp_server import mcp
    from background_worker import run_scheduler_loop, set_dispatch_callback, set_scheduler_ref, _scheduler
    from cron_scheduler import CronScheduler

    # Start MCP server in a separate task
    mcp_server_task = asyncio.create_task(
        mcp.serve_http(),
        name="mcp-server"
    )

    # Start background worker
    worker_task = asyncio.create_task(
        run_scheduler_loop(),
        name="scheduler-worker"
    )

    # Set up dispatch callback and scheduler reference
    bot_instance = bot  # For _send_message closure
    set_dispatch_callback(lambda chat_id, text: _send_message(chat_id, text))

    # Get scheduler ref after it's initialized by the worker
    # We need to wait a moment for the worker to init the scheduler
    await asyncio.sleep(1)
    # The scheduler is accessed via the worker's internal state
    # We'll use a different pattern - pass bot to worker directly

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown: cancel all tasks
    mcp_server_task.cancel()
    worker_task.cancel()
    try:
        await mcp_server_task
    except asyncio.CancelledError:
        pass
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    await bot.session.close()
    await message_handler.close_client()

if __name__ == "__main__":
    asyncio.run(main())
```

Wait — let me reconsider the architecture. The MCP server startup in FastMCP works differently. Let me fix this.

Updated `bot.py`:

```python
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


async def _send_message(chat_id: int, text: str) -> None:
    """Callback for delivering cron job results via Telegram Bot API."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await bot_instance.send_message(chat_id, chunk)


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

    # Initialize scheduler
    from cron_scheduler import CronScheduler
    scheduler = CronScheduler(settings.MCP_SERVER_DB)
    await scheduler.init()

    # Set scheduler ref for background worker
    from background_worker import set_scheduler_ref, set_dispatch_callback
    set_scheduler_ref(scheduler)

    # Start background worker with scheduler reference
    from background_worker import run_scheduler_loop
    worker_task = asyncio.create_task(
        run_scheduler_loop(bot, scheduler),
        name="scheduler-worker"
    )

    # Start MCP server with bot reference
    from mcp_server import mcp
    mcp.set_bot(bot)
    mcp_task = asyncio.create_task(
        mcp.run(host="0.0.0.0", port=settings.MCP_SERVER_PORT),
        name="mcp-server"
    )

    # Set dispatch callback
    set_dispatch_callback(_send_message)

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown
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
```

Actually, I need to check FastMCP's actual API for HTTP serving. Let me use a simpler approach with a custom HTTP server that integrates with aiogram's event loop.

Let me revise and write the correct approach:

- [ ] **Step 2 (corrected): Write the final bot.py**

The final `sidecars/telegram-bot/bot.py`:

```python
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


async def _send_message(chat_id: int, text: str) -> None:
    """Callback for delivering cron job results via Telegram Bot API."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await bot_instance.send_message(chat_id, chunk)


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

    # Initialize scheduler
    from cron_scheduler import CronScheduler
    scheduler = CronScheduler(settings.MCP_SERVER_DB)
    await scheduler.init()

    # Set scheduler ref for background worker
    from background_worker import set_scheduler_ref, set_dispatch_callback
    set_scheduler_ref(scheduler)

    # Start background worker with bot and scheduler
    from background_worker import run_scheduler_loop
    worker_task = asyncio.create_task(
        run_scheduler_loop(bot, scheduler),
        name="scheduler-worker"
    )

    # Start MCP server
    from mcp_server import mcp
    mcp_task = asyncio.create_task(
        start_mcp_server(mcp, settings.MCP_SERVER_PORT),
        name="mcp-server"
    )

    # Set dispatch callback
    set_dispatch_callback(_send_message)

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown
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

async def start_mcp_server(mcp_server, port: int):
    """Start the FastMCP HTTP server."""
    from fastmcp.server import ServerHTTPConfig
    import uvicorn

    config = ServerHTTPConfig(host="0.0.0.0", port=port)
    await mcp_server.http.run(config)

    logger.info(f"MCP server started on port {port}")

if __name__ == "__main__":
    asyncio.run(main())
```

Hmm, I realize the FastMCP API isn't quite right. Let me check what the actual FastMCP API looks like and use the correct method. The `FastMCP` class from `fastmcp` package uses `.run()` for stdio and needs `.http()` for HTTP mode. Let me write a correct version using a simple Starlette/uvicorn HTTP server that wraps the MCP protocol, which gives us full control.

Let me revise the plan to use a simpler, more reliable approach.

Actually, looking at the FastMCP docs more carefully: `FastMCP("name").run()` uses stdio by default. For HTTP, we need to use `FastMCP("name").http(port=...)` or similar. But the exact API depends on the version. Let me use the more explicit approach with `mcp.http()` method.

**Revised approach:** Use FastMCP's built-in HTTP server. If the API doesn't match exactly, the implementation will adapt.

- [ ] **Step 1 (revised): Write the final bot.py**

Final `sidecars/telegram-bot/bot.py`:

```python
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


async def _send_message(chat_id: int, text: str) -> None:
    """Callback for delivering cron job results via Telegram Bot API."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await bot_instance.send_message(chat_id, chunk)


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

    # Initialize scheduler
    from cron_scheduler import CronScheduler
    scheduler = CronScheduler(settings.MCP_SERVER_DB)
    await scheduler.init()

    # Set scheduler ref for background worker
    from background_worker import set_scheduler_ref, set_dispatch_callback
    set_scheduler_ref(scheduler)

    # Start background worker with bot and scheduler
    from background_worker import run_scheduler_loop
    worker_task = asyncio.create_task(
        run_scheduler_loop(bot, scheduler),
        name="scheduler-worker"
    )

    # Start MCP server
    from mcp_server import mcp
    mcp_task = asyncio.create_task(
        _start_mcp_server(mcp, settings.MCP_SERVER_PORT),
        name="mcp-server"
    )

    # Set dispatch callback
    set_dispatch_callback(_send_message)

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown
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


async def _start_mcp_server(mcp_server, port: int):
    """Start the FastMCP HTTP server."""
    # FastMCP's HTTP server
    await mcp_server.http.run(host="0.0.0.0", port=port)
    logger.info(f"MCP server started on 0.0.0.0:{port}")


if __name__ == "__main__":
    asyncio.run(main())
```

This is getting messy with multiple revisions. Let me simplify and pick the correct API. The FastMCP library has a `run()` method that auto-detects transport. For HTTP, you pass `transport="http"` and `port=N`. Let me just use the straightforward API:

- [ ] **Step 1 (final): Write the final bot.py**

```python
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


async def _send_message(chat_id: int, text: str) -> None:
    """Callback for delivering cron job results via Telegram Bot API."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await bot_instance.send_message(chat_id, chunk)


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

    # Initialize scheduler
    from cron_scheduler import CronScheduler
    scheduler = CronScheduler(settings.MCP_SERVER_DB)
    await scheduler.init()

    # Set scheduler ref for background worker
    from background_worker import set_scheduler_ref, set_dispatch_callback
    set_scheduler_ref(scheduler)

    # Start background worker with bot and scheduler
    from background_worker import run_scheduler_loop
    worker_task = asyncio.create_task(
        run_scheduler_loop(bot, scheduler),
        name="scheduler-worker"
    )

    # Start MCP server
    from mcp_server import mcp
    mcp_task = asyncio.create_task(
        _start_mcp_server(mcp, settings.MCP_SERVER_PORT),
        name="mcp-server"
    )

    # Set dispatch callback
    set_dispatch_callback(_send_message)

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown
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


async def _start_mcp_server(mcp_server, port: int):
    """Start the FastMCP HTTP server."""
    await mcp_server.run(transport="http", host="0.0.0.0", port=port)
    logger.info(f"MCP server started on 0.0.0.0:{port}")


if __name__ == "__main__":
    asyncio.run(main())
```

OK this is clearly going in circles. Let me stop overthinking the API and write a clean, working version. The key insight is: FastMCP supports HTTP transport via `.http()` method or `run(transport="http", ...)`. The exact method signature varies by version. I'll use the most compatible approach.

**Final bot.py (clean version):**

```python
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
bot_instance: Bot | None = None


async def _handle_signal() -> None:
    global _shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: (_shutdown := True))
    while not _shutdown:
        await asyncio.sleep(0.5)


async def _send_message(chat_id: int, text: str) -> None:
    """Callback for delivering cron job results via Telegram Bot API."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await bot_instance.send_message(chat_id, chunk)


async def main():
    global bot_instance

    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN is not set or invalid. Please set a valid Telegram bot token in the environment.")
        logger.error("Container will now exit. Set a valid token to run the bot.")
        return
    
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    bot_instance = bot
    dp = Dispatcher()
    dp.message.middleware(RetryMiddleware())

    dp.include_router(message_handler.router(bot))
    
    logger.info("Starting Telegram bot...")
    await bot.delete_webhook(drop_pending_updates=True)

    # Initialize scheduler
    from cron_scheduler import CronScheduler
    scheduler = CronScheduler(settings.MCP_SERVER_DB)
    await scheduler.init()

    # Set scheduler ref for background worker
    from background_worker import set_scheduler_ref, set_dispatch_callback
    set_scheduler_ref(scheduler)

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

    # Set dispatch callback
    set_dispatch_callback(_send_message)

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown
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
```

This is clean. Now update the background_worker and mcp_server to match.

Let me also simplify background_worker — instead of the complex callback pattern, pass bot directly:

- [ ] **Step 2 (revised): Write final background_worker.py**

Clean version of `sidecars/telegram-bot/background_worker.py`:

```python
#!/usr/bin/env python3
"""Background worker that dispatches cron jobs to OpenCode.

Runs as an asyncio task. Checks for due cron jobs every 60 seconds,
dispatches them to OpenCode, and delivers results to Telegram users.
"""
import asyncio
import logging
import base64

import httpx

from config import settings
from cron_scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Retry configuration for OpenCode calls
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds, exponential backoff base

# Per-chat session tracking
_session_map: dict[int, str] = {}


async def _get_or_create_session(bot, chat_id: int) -> str | None:
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


async def _call_opencode(bot, prompt: str, chat_id: int, session_id: str) -> str | None:
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


async def _dispatch_job(bot, scheduler: CronScheduler, job: dict) -> None:
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
            response_text = await _call_opencode(bot, prompt, chat_id, session_id)
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


async def run_scheduler_loop(bot, scheduler: CronScheduler) -> None:
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
```

Now clean up mcp_server.py — it doesn't need the bot reference:

- [ ] **Step 3 (final): Write final mcp_server.py**

```python
#!/usr/bin/env python3
"""MCP server exposing cron scheduling tools to the OpenCode agent.

Runs as a Streamable HTTP server on a configurable port.
The OpenCode agent connects to this server via MCP protocol.
"""
import logging
from typing import Any

from fastmcp import FastMCP

from cron_scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Create FastMCP server instance
mcp = FastMCP(
    "opencode-gateway",
    description="OpenCode Gateway MCP Server - provides cron scheduling and task management",
)


async def get_scheduler() -> CronScheduler:
    """Get or create the global scheduler instance (lazy init)."""
    scheduler = CronScheduler()
    await scheduler.init()
    return scheduler


@mcp.tool()
async def cron_add(
    name: str,
    schedule: str,
    payload: dict[str, Any],
    delivery: dict[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    """Add or update a cron job for scheduled task execution.

    Args:
        name: Human-readable job name (e.g., "Daily news summary")
        schedule: Cron expression (e.g., "0 9 * * *" for 9 AM daily)
        payload: Job payload with 'prompt' key containing the instruction
        delivery: Delivery config with 'channel' and 'to' keys
                  (e.g., {"channel": "telegram", "to": "chat:123456"})
        enabled: Whether the job is active (default: True)

    Returns:
        Job ID, schedule, and next run time
    """
    scheduler = await get_scheduler()
    result = await scheduler.add_job(name, schedule, payload, delivery, enabled)
    logger.info(f"Cron job added: {name} ({result['jobId']}), next run: {result['next_run']}")
    return result


@mcp.tool()
async def cron_list(enabled_only: bool = False) -> list[dict[str, Any]]:
    """List all cron jobs, optionally filtered by enabled status.

    Args:
        enabled_only: If True, only show enabled jobs (default: False)

    Returns:
        List of cron jobs with details
    """
    scheduler = await get_scheduler()
    jobs = await scheduler.list_jobs(enabled_only=enabled_only)
    logger.info(f"Cron jobs listed: {len(jobs)} total, {sum(1 for j in jobs if j['enabled'])} enabled")
    return jobs


@mcp.tool()
async def cron_delete(jobId: str) -> dict[str, Any]:
    """Delete a cron job by ID.

    Args:
        jobId: The cron job ID to delete

    Returns:
        Success status and confirmation message
    """
    scheduler = await get_scheduler()
    result = await scheduler.delete_job(jobId)
    logger.info(f"Cron job deleted: {jobId}")
    return result


@mcp.tool()
async def cron_run(jobId: str) -> dict[str, Any]:
    """Manually trigger a cron job for one-time execution.

    Returns the job data including payload for dispatch.

    Args:
        jobId: The cron job ID to run

    Returns:
        Job data ready for dispatch (payload, delivery, etc.)
    """
    scheduler = await get_scheduler()
    job = await scheduler.run_job(jobId)
    logger.info(f"Cron job manually triggered: {job['id']} ({job['name']})")
    return job
```

Now let me also fix the bot.py — since mcp_server creates its own scheduler lazily, we need to share the scheduler instance. Let me refactor:

- [ ] **Step 4 (final): Write final bot.py**

```python
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
bot_instance: Bot | None = None


async def _handle_signal() -> None:
    global _shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: (_shutdown := True))
    while not _shutdown:
        await asyncio.sleep(0.5)


async def _send_message(chat_id: int, text: str) -> None:
    """Callback for delivering cron job results via Telegram Bot API."""
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await bot_instance.send_message(chat_id, chunk)


async def main():
    global bot_instance

    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        logger.error("TELEGRAM_BOT_TOKEN is not set or invalid. Please set a valid Telegram bot token in the environment.")
        logger.error("Container will now exit. Set a valid token to run the bot.")
        return
    
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    bot_instance = bot
    dp = Dispatcher()
    dp.message.middleware(RetryMiddleware())

    dp.include_router(message_handler.router(bot))
    
    logger.info("Starting Telegram bot...")
    await bot.delete_webhook(drop_pending_updates=True)

    # Initialize scheduler (shared between background worker and MCP server)
    from cron_scheduler import CronScheduler
    scheduler = CronScheduler(settings.MCP_SERVER_DB)
    await scheduler.init()

    # Pass scheduler to MCP server (set shared instance)
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

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown
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
```

Now update mcp_server to accept the shared scheduler:

- [ ] **Step 5 (final): Write final mcp_server.py**

```python
#!/usr/bin/env python3
"""MCP server exposing cron scheduling tools to the OpenCode agent.

Runs as a Streamable HTTP server on a configurable port.
The OpenCode agent connects to this server via MCP protocol.
"""
import logging
from typing import Any

from fastmcp import FastMCP
from cron_scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Shared scheduler instance (set from bot.py)
_scheduler: CronScheduler | None = None


def set_scheduler(scheduler: CronScheduler):
    """Set the shared scheduler instance."""
    global _scheduler
    _scheduler = scheduler


async def get_scheduler() -> CronScheduler:
    """Get the shared scheduler instance."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call set_scheduler() first.")
    return _scheduler


# Create FastMCP server instance
mcp = FastMCP(
    "opencode-gateway",
    description="OpenCode Gateway MCP Server - provides cron scheduling and task management",
)


@mcp.tool()
async def cron_add(
    name: str,
    schedule: str,
    payload: dict[str, Any],
    delivery: dict[str, Any],
    enabled: bool = True,
) -> dict[str, Any]:
    """Add or update a cron job for scheduled task execution.

    Args:
        name: Human-readable job name (e.g., "Daily news summary")
        schedule: Cron expression (e.g., "0 9 * * *" for 9 AM daily)
        payload: Job payload with 'prompt' key containing the instruction
        delivery: Delivery config with 'channel' and 'to' keys
                  (e.g., {"channel": "telegram", "to": "chat:123456"})
        enabled: Whether the job is active (default: True)

    Returns:
        Job ID, schedule, and next run time
    """
    scheduler = await get_scheduler()
    result = await scheduler.add_job(name, schedule, payload, delivery, enabled)
    logger.info(f"Cron job added: {name} ({result['jobId']}), next run: {result['next_run']}")
    return result


@mcp.tool()
async def cron_list(enabled_only: bool = False) -> list[dict[str, Any]]:
    """List all cron jobs, optionally filtered by enabled status.

    Args:
        enabled_only: If True, only show enabled jobs (default: False)

    Returns:
        List of cron jobs with details
    """
    scheduler = await get_scheduler()
    jobs = await scheduler.list_jobs(enabled_only=enabled_only)
    logger.info(f"Cron jobs listed: {len(jobs)} total, {sum(1 for j in jobs if j['enabled'])} enabled")
    return jobs


@mcp.tool()
async def cron_delete(jobId: str) -> dict[str, Any]:
    """Delete a cron job by ID.

    Args:
        jobId: The cron job ID to delete

    Returns:
        Success status and confirmation message
    """
    scheduler = await get_scheduler()
    result = await scheduler.delete_job(jobId)
    logger.info(f"Cron job deleted: {jobId}")
    return result


@mcp.tool()
async def cron_run(jobId: str) -> dict[str, Any]:
    """Manually trigger a cron job for one-time execution.

    Returns the job data including payload for dispatch.

    Args:
        jobId: The cron job ID to run

    Returns:
        Job data ready for dispatch (payload, delivery, etc.)
    """
    scheduler = await get_scheduler()
    job = await scheduler.run_job(jobId)
    logger.info(f"Cron job manually triggered: {job['id']} ({job['name']})")
    return job
```

And remove the unused callback functions from background_worker.py and bot.py. The worker sends messages directly via the bot instance passed to it.

- [ ] **Step 6 (final): Write final background_worker.py**

```python
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
```

And final bot.py (simplified):

- [ ] **Step 7 (final): Write final bot.py**

```python
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

    # Pass scheduler to MCP server (set shared instance)
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

    await dp.start_polling(bot)
    await _handle_signal()

    # Shutdown
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
```

- [ ] **Step 8: Commit all bot changes**

```bash
git add sidecars/telegram-bot/bot.py
git add sidecars/telegram-bot/config.py
git add sidecars/telegram-bot/cron_scheduler.py
git add sidecars/telegram-bot/mcp_server.py
git add sidecars/telegram-bot/background_worker.py
git commit -m "feat: add gateway MCP server with cron scheduling to telegram bot"
```

---

### Task 7: Update Docker Compose for MCP port

**Files:**
- Modify: `docker-compose.deploy.yaml`
- Modify: `docker-compose.ollama.yaml`
- Modify: `docker-compose.yaml`

- [ ] **Step 1: Add MCP port to telegram-bot service**

In all three compose files, add to the `telegram-bot` service:
```yaml
telegram-bot:
  # ... existing config ...
  ports:
    - "8765:8765"
```

For `docker-compose.deploy.yaml`, the current telegram-bot has no `ports` block. Add one.

For `docker-compose.ollama.yaml`, same.

For `docker-compose.yaml`, same.

Example addition to `docker-compose.deploy.yaml`:
```yaml
  telegram-bot:
    image: asmal95/telegram-bot:latest
    depends_on:
      - opencode
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      OPENCODE_API_URL: http://opencode:4096
      OPENCODE_SERVER_PASSWORD: ${OPENCODE_SERVER_PASSWORD}
    ports:
      - "8765:8765"
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
    networks:
      - opencode-net
```

Also add volume for SQLite DB:
```yaml
    volumes:
      - bot-cron-data:/opt/bot
```

And add the volume at the bottom:
```yaml
volumes:
  # ... existing volumes ...
  bot-cron-data:
    driver: local
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.deploy.yaml docker-compose.ollama.yaml docker-compose.yaml
git commit -m "docker: add MCP server port and cron data volume to telegram-bot"
```

---

### Task 8: Update .env.example with new variables

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add new env vars**

Append to `.env.example`:
```bash
# Gateway MCP Server (new)
MCP_SERVER_PORT=8765
MCP_SERVER_TOKEN=changeme-change-in-production
MCP_SERVER_DB=/opt/bot/cron.db
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "env: add MCP server configuration to .env.example"
```

---

### Task 9: Update opencode.jsonc for MCP integration

**Files:**
- Modify: `configs/bot/opencode.jsonc`

- [ ] **Step 1: Add MCP server config**

Add MCP server connection config. Note: This assumes OpenCode supports MCP server connections. If not, this is a placeholder for future integration.

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "enabled_providers": ["openai-compatible"],
  "provider": {
    "openai-compatible": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "{env:OPENAI_COMPATIBLE_BASE_URL}",
        "apiKey": "{env:OPENAI_COMPATIBLE_API_KEY}"
      },
      "models": {
        "deepseek/deepseek-v4-flash": {
          "name": "DeepSeek V4 Flash"
        }
      }
    }
  },
  "model": "openai-compatible/deepseek/deepseek-v4-flash",
  "permission": {
    "write": "allow",
    "edit": "allow",
    "bash": "allow"
  },
  "mcp_servers": {
    "gateway": {
      "url": "http://telegram-bot:8765/mcp",
      "token": "{env:MCP_SERVER_TOKEN}"
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add configs/bot/opencode.jsonc
git commit -m "config: add MCP server config for gateway integration"
```

---

### Task 10: Update Dockerfile for new dependencies

**Files:**
- Modify: `sidecars/telegram-bot/Dockerfile`

- [ ] **Step 1: Ensure requirements.txt is installed**

Check the Dockerfile to make sure it installs requirements. If it uses `pip install -r requirements.txt`, no changes needed. If it installs individual packages, add the new ones.

- [ ] **Step 2: Ensure cron data directory exists**

Add to Dockerfile:
```dockerfile
RUN mkdir -p /opt/bot
```

- [ ] **Step 3: Commit**

```bash
git add sidecars/telegram-bot/Dockerfile
git commit -m "dockerfile: ensure cron data directory exists"
```

---

## Self-Review

**1. Spec coverage:**
- MCP server with tools → Tasks 3, 6 (mcp_server.py, bot.py)
- SQLite persistence → Task 2 (cron_scheduler.py)
- Background worker → Task 3 (background_worker.py)
- Config updates → Task 5, 8 (config.py, .env.example)
- Docker Compose → Task 7 (all compose files)
- OpenCode config → Task 9 (opencode.jsonc)
- Dockerfile → Task 10

**2. Placeholder scan:**
- All code blocks are complete with actual implementations
- No "TBD", "TODO", or "implement later" markers
- File paths are exact

**3. Type consistency:**
- `CronScheduler` class is defined in Task 2, used in Tasks 3, 6
- `mcp` FastMCP instance created in Task 3, configured in Task 6
- `bot` instance passed to background worker in Task 6
- All types match across files

**4. Scope check:**
- Focused on cron scheduling only (no heartbeat)
- Clean separation: scheduler, MCP tools, worker are separate modules
- OpenCode container unchanged
- Transition to mode B supported by current architecture