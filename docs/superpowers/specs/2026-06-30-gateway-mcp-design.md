# Gateway MCP Server Design

> **Goal:** Add cron-based task scheduling to the Telegram Bot via an MCP server, enabling the OpenCode agent to plan recurring tasks (daily news, reminders, etc.).

## Architecture

```
Telegram User --> Telegram Bot (Gateway) --> OpenCode (code agent)
                         ↑
                    Streamable HTTP
                    MCP Server
                    (port 8765)
                         ↑
                    SQLite (cron tasks)
                         ↑
              Background worker
              (checks cron queue every minute)
```

### Components

1. **Telegram Bot** — becomes the Gateway:
   - Current polling to Telegram (unchanged)
   - **+** Streamable HTTP MCP Server (port 8765)
   - **+** SQLite for cron tasks
   - **+** Background worker (checks cron queue every minute)
   - **+** HTTP endpoint `/hooks/wake` (for external wake triggers)

2. **OpenCode** — unchanged container:
   - Connects to MCP server via Streamable HTTP
   - Agent gets tools: `cron_add`, `cron_list`, `cron_delete`, `cron_run`
   - Agent can discover and manage scheduled tasks

3. **Transition to mode B** — when ready, the MCP server + scheduler move to a separate gateway container. Current architecture supports this cleanly.

## MCP Tools (via FastMCP)

Agent interacts with the gateway via these MCP tools:

### `cron_add`

Add or update a cron job. Agent calls this to schedule tasks.

```json
{
  "action": "add",
  "name": "Daily news summary",
  "schedule": "0 9 * * *",
  "payload": {"prompt": "Собери ежедневную сводку новостей"},
  "delivery": {"channel": "telegram", "to": "chat:123456"}
}
```

Parameters:
- `action` (string): "add"
- `name` (string): Human-readable job name
- `schedule` (string): Cron expression (e.g. "0 9 * * *")
- `payload` (object): JSON with `prompt` and optional fields
- `delivery` (object): Where to send result, `{"channel": "telegram", "to": "chat:<id>"}`
- `enabled` (bool, optional): Default true

Returns: `{ jobId, message }`

### `cron_list`

List all cron jobs (optionally filtered by status).

```json
{ "action": "list", "enabled": true }
```

Returns: array of jobs with `{ jobId, name, schedule, payload, enabled, next_run, run_count }`

### `cron_delete`

Delete a cron job by ID.

```json
{ "action": "delete", "jobId": "abc123" }
```

Returns: `{ success: true, message }`

### `cron_run`

Manually trigger a cron job (one-time execution).

```json
{ "action": "run", "jobId": "abc123" }
```

Returns: `{ success: true, jobId, messageId }`

## SQLite Schema

One table for cron jobs:

```sql
CREATE TABLE cron_jobs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  schedule TEXT NOT NULL,       -- cron expression
  payload TEXT NOT NULL,         -- JSON: {prompt, ...}
  delivery TEXT NOT NULL,        -- JSON: {channel, to}
  enabled INTEGER DEFAULT 1,
  next_run TEXT NOT NULL,        -- ISO 8601 timestamp
  last_run TEXT,
  run_count INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_next_run ON cron_jobs(next_run, enabled);
```

## Background Worker

Runs in a background asyncio task every 60 seconds:

1. SELECT FROM cron_jobs WHERE enabled=1 AND next_run <= NOW()
2. For each due task: POST to OpenCode with `payload.prompt`
3. Receive response from OpenCode (same flow as user message)
4. Deliver result to user via Telegram Bot API (already exists)
5. Update next_run (recalculate for recurring), last_run, run_count

If OpenCode is unavailable: retry up to 3 times with exponential backoff, log error.

## Data Flow

1. User sends message to Telegram
2. Bot forwards to OpenCode, agent processes
3. Agent decides to schedule: calls MCP `cron_add`
4. MCP server saves task in SQLite, calculates `next_run`
5. Background worker checks every minute
6. When time comes: worker POSTs to OpenCode
7. Agent processes, returns result
8. Worker delivers result to Telegram user

## Configuration

### Environment Variables

New:
- `MCP_SERVER_PORT` — port for MCP server (default: 8765)
- `MCP_SERVER_TOKEN` — auth token for MCP requests
- `MCP_SERVER_DB` — path to SQLite DB (default: /opt/bot/cron.db)

Existing:
- `TELEGRAM_BOT_TOKEN` — unchanged
- `OPENCODE_API_URL` — unchanged
- `OPENCODE_SERVER_PASSWORD` — unchanged

### OpenCode Config (opencode.jsonc)

New section for MCP server connection:

```jsonc
{
  "mcp_servers": {
    "gateway": {
      "url": "http://telegram-bot:8765/mcp",
      "token": "{env:MCP_SERVER_TOKEN}"
    }
  },
  "tools": {
    "allow": ["cron_add", "cron_list", "cron_delete", "cron_run"]
  }
}
```

Note: This config structure is a proposal for OpenCode. If OpenCode doesn't support MCP yet, we'll document this for future implementation.

### Docker Compose

Add port mapping for MCP server:
```yaml
ports:
  - "8765:8765"
```

## Libraries

- `fastmcp` — official Python MCP SDK (Streamable HTTP transport)
- `aiosqlite` — async SQLite
- `apscheduler` or `croniter` — cron expression parsing
- `httpx` — already in requirements.txt, for calling OpenCode

## Security

- MCP server: Bearer token auth via `Authorization: Bearer <token>` header
- SQLite file: read-only for container (but writable in /opt/bot)
- OpenCode calls: use existing HTTP Basic Auth
- Background worker: internal only, no external exposure

## Notes

- The current bot architecture is polling-based, which is already always-on. Adding a background worker is a natural extension.
- When transitioning to mode B (separate gateway container), the MCP server and scheduler simply move to a new container. The Telegram bot becomes a thin transport layer.
- The `/hooks/wake` endpoint can be added later for external services to trigger tasks (e.g., GitHub webhook → cron add).
