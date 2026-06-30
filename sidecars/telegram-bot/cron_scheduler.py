#!/usr/bin/env python3
"""Cron task scheduler with SQLite persistence.

Manages scheduled cron jobs: create, list, delete, run, and next-run calculation.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from croniter import croniter

logger = logging.getLogger(__name__)

# Default DB path (passed via CronScheduler(db_path) constructor)
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
            "jobId": row["id"],
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
                "jobId": row["id"],
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
            logger.warning(f"Cron job '{job_id}' not found for mark_job_ran")
            return

        try:
            iterator = croniter(row["schedule"], now)
            next_run = iterator.get_next(datetime)
            next_run_iso = next_run.isoformat()
        except (ValueError, KeyError) as e:
            logger.error(f"Invalid schedule for job {job_id} ({row['schedule']}): {e}. Disabling job.")
            await self.db.execute("UPDATE cron_jobs SET enabled = 0, updated_at = ? WHERE id = ?", (now_iso, job_id))
            await self.db.commit()
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
