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
    result = await scheduler.add_job(name, schedule, payload, delivery, enabled=enabled)
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
async def cron_delete(job_id: str) -> dict[str, Any]:
    """Delete a cron job by ID.

    Args:
        job_id: The cron job ID to delete

    Returns:
        Success status and confirmation message
    """
    scheduler = await get_scheduler()
    result = await scheduler.delete_job(job_id)
    logger.info(f"Cron job deleted: {job_id}")
    return result


@mcp.tool()
async def cron_run(job_id: str) -> dict[str, Any]:
    """Manually trigger a cron job for one-time execution.

    Returns the job data including payload for dispatch.

    Args:
        job_id: The cron job ID to run

    Returns:
        Job data ready for dispatch (payload, delivery, etc.)
    """
    scheduler = await get_scheduler()
    job = await scheduler.run_job(job_id)
    logger.info(f"Cron job manually triggered: {job['jobId']} ({job['name']})")
    return job
