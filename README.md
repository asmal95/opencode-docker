# OpenCode Telegram Bot

Deploy OpenCode AI with a Telegram Bot interface using Docker.

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/asmal95/opencode-docker.git
cd opencode-docker
cp .env.example .env
nano .env
```

### 2. Edit .env

Set your credentials:
- `TELEGRAM_BOT_TOKEN` — Telegram bot token from [@BotFather](https://t.me/BotFather)
- `OPENAI_COMPATIBLE_BASE_URL` — Your AI provider endpoint (see below)
- `OPENAI_COMPATIBLE_API_KEY` — Your API key
- `OPENCODE_SERVER_PASSWORD` — Server authentication password
- `MCP_SERVER_TOKEN` — MCP server authentication token (required for cron features)

### 3. Start

```bash
docker compose -f docker-compose.deploy.yaml up -d
```

### 4. Verify

```bash
docker compose -f docker-compose.deploy.yaml logs -f
```

Your Telegram bot should respond to messages.

## Docker Images

- `asmal95/opencode-platform:latest` — OpenCode AI server
- `asmal95/telegram-bot:latest` — Telegram bot sidecar

## Requirements

- Docker 20.10+
- Docker Compose 2.0+
- Telegram bot token
- AI API key (any OpenAI-compatible provider)
- Server password (`OPENCODE_SERVER_PASSWORD`)

## AI Provider Setup

The bot works with any OpenAI-compatible API. Examples:

| Provider | BASE_URL |
|----------|----------|
| OpenRouter | `https://api.openrouter.ai/v1` |
| Ollama (local) | `http://host.docker.internal:11434/v1` |
| llama.cpp | `http://YOUR_IP:8080/v1` |
| LM Studio | `http://localhost:1234/v1` |

## Server Authentication

OpenCode server uses HTTP Basic Auth:

```bash
OPENCODE_SERVER_PASSWORD=your-strong-password
```

- Username: `opencode` (default)
- The Telegram bot automatically includes the auth header
- Protects the API from unauthorized access

## MCP Server & Cron Jobs

The Telegram Bot includes an MCP (Model Context Protocol) server for scheduling recurring tasks. The OpenCode agent can use MCP tools to create reminders, scheduled reports, and periodic checks.

### Available MCP Tools

- **cron_add** — Schedule a recurring task
- **cron_list** — List all scheduled jobs
- **cron_delete** — Delete a scheduled job
- **cron_run** — Manually trigger a job immediately

### Environment Variables

Additional variables for MCP server:

- `MCP_SERVER_PORT` — Port for MCP server (default: `8765`)
- `MCP_SERVER_TOKEN` — Authentication token for MCP requests (**required**)
- `MCP_SERVER_DB` — Path to SQLite database for cron storage (default: `/opt/bot/cron.db`)

### Usage Example

Ask the bot to schedule a task:

> "Remind me every day at 9 AM to check the server status"

The agent will create a cron job that automatically executes and delivers results to your chat.

## Deployment Options

| File | Use case |
|------|----------|
| `docker-compose.deploy.yaml` | DockerHub images (recommended) |
| `docker-compose.yaml` | Build from source (with bot) |

## Management

```bash
# Logs
docker compose -f docker-compose.deploy.yaml logs -f

# Restart
docker compose -f docker-compose.deploy.yaml restart

# Stop
docker compose -f docker-compose.deploy.yaml down

# Update
docker compose -f docker-compose.deploy.yaml pull
docker compose -f docker-compose.deploy.yaml up -d
```

## Auto Deployment

```bash
bash deploy.sh
```

## Architecture

```
Telegram User --> Telegram Bot (aiogram) --> OpenCode (port 4096, Basic Auth) --> AI Provider
```

- **Telegram Bot**: Receives messages from Telegram, forwards to OpenCode
- **OpenCode**: Processes requests, manages sessions, calls AI providers
- **AI Provider**: Any OpenAI-compatible API (Ollama, OpenRouter, llama.cpp, etc.)

## Security

- Non-root containers
- Isolated Docker network
- `read_only` filesystem
- `no-new-privileges` enabled
- Phone-home domains blocked
- HTTP Basic Auth on OpenCode server
- API keys in environment variables only

## License

MIT
