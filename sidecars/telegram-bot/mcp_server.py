#!/usr/bin/env python3
"""MCP server exposing cron scheduling tools to the OpenCode agent.

Runs as a Streamable HTTP server on a configurable port.
The OpenCode agent connects to this server via MCP protocol.
"""
import json
import logging
from typing import Any, Callable, Awaitable

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from cron_scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Shared scheduler instance (set from bot.py)
_scheduler: CronScheduler | None = None

# Dispatch callback set from bot.py to dispatch jobs from the background worker
_dispatch_callback: Callable[..., Awaitable[None]] | None = None


def set_scheduler(scheduler: CronScheduler):
    """Set the shared scheduler instance."""
    global _scheduler
    _scheduler = scheduler


def set_dispatch_callback(callback: Callable[..., Awaitable[None]]):
    """Set the callback used by cron_run to dispatch jobs."""
    global _dispatch_callback
    _dispatch_callback = callback


async def get_scheduler() -> CronScheduler:
    """Get the shared scheduler instance."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized. Call set_scheduler() first.")
    return _scheduler


class TokenAuth(Middleware):
    """Validates Bearer token from Authorization header on every tool call."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        from config import settings
        token = settings.MCP_SERVER_TOKEN
        if not token:
            return await call_next(context)

        headers = get_http_headers() or {}
        auth = headers.get("authorization", "")
        expected = f"Bearer {token}"
        if auth != expected:
            raise ToolError("Unauthorized: invalid or missing MCP server token")

        return await call_next(context)


# Create FastMCP server instance
mcp = FastMCP("opencode-gateway")
mcp.add_middleware(TokenAuth())


def _parse_json(value: Any) -> dict:
    """Parse a dict or JSON string into a dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


@mcp.tool()
async def cron_add(
    name: str,
    schedule: str,
    payload: Any,
    delivery: Any,
    enabled: bool = True,
) -> dict[str, Any]:
    """Schedule a recurring or one-time task (REMINDERS, REPORTS, CHECKS).

    USE THIS for: "remind me to X", "every day at 9am do Y", "check Z every hour".
    NEVER use bash/sleep for scheduling — this is the correct tool.

    Args:
        name: Short job name (e.g., "Morning briefing", "DB backup check")
        schedule: Cron expression (e.g., "0 9 * * *" = daily 9 AM, "0 */2 * * *" = every 2 hours)
        payload: Dict with 'prompt' key. The prompt IS the instruction the agent will execute.
                 CRITICAL: Include Chat ID in the prompt for delivery: "Execute this. Chat ID: 123456789"
        delivery: Dict {"channel": "telegram", "to": "user:current"}. Use "user:current" to send results
                  back to the chat where the cron job was created. The Chat ID must be in the prompt.
        enabled: Whether the job is active (default: True)

    Returns:
        Dict with jobId, schedule, next_run, and message confirming creation.
    """
    scheduler = await get_scheduler()
    payload_dict = _parse_json(payload)
    delivery_dict = _parse_json(delivery)
    result = await scheduler.add_job(name, schedule, payload_dict, delivery_dict, enabled=enabled)
    logger.info(f"Cron job added: {name} ({result['jobId']}), next run: {result['next_run']}")
    return result


@mcp.tool()
async def cron_list(enabled_only: bool = False) -> list[dict[str, Any]]:
    """List all scheduled cron jobs with their schedules, next run times, and status.

    Args:
        enabled_only: If True, only show enabled jobs (default: False)

    Returns:
        List of job dicts: {jobId, name, schedule, next_run, enabled, run_count, ...}
    """
    scheduler = await get_scheduler()
    jobs = await scheduler.list_jobs(enabled_only=enabled_only)
    logger.info(f"Cron jobs listed: {len(jobs)} total, {sum(1 for j in jobs if j['enabled'])} enabled")
    return jobs


@mcp.tool()
async def cron_delete(job_id: str) -> dict[str, Any]:
    """Delete a scheduled cron job. Use when the user wants to cancel a reminder or stop a recurring task.

    Args:
        job_id: The cron job ID (from cron_list output)

    Returns:
        Dict with success status and deletion confirmation message
    """
    scheduler = await get_scheduler()
    result = await scheduler.delete_job(job_id)
    logger.info(f"Cron job deleted: {job_id}")
    return result


@mcp.tool()
async def cron_run(job_id: str) -> dict[str, Any]:
    """Manually trigger a scheduled cron job immediately (one-time execution).

    Use when the user asks to "run now" or "execute now" a specific cron job.

    Args:
        job_id: The cron job ID (from cron_list output)

    Returns:
        Dict with jobId, name, dispatched=True, and confirmation message
    """
    scheduler = await get_scheduler()
    job = await scheduler.run_job(job_id)
    logger.info(f"Cron job manually triggered: {job['jobId']} ({job['name']})")

    if _dispatch_callback is None:
        logger.error("No dispatch callback configured. Cannot dispatch cron_run.")
        return {"error": "Dispatch callback not configured"}

    await _dispatch_callback(job)

    return {
        "jobId": job["jobId"],
        "name": job["name"],
        "dispatched": True,
        "message": f"Cron job '{job['name']}' dispatched for immediate execution",
    }
