# OpenCode Docker Platform - Prebuilt Images

Ready-to-use Docker images for OpenCode AI with Telegram Bot integration.

## Quick Start

### Option 1: One-Command Deployment

```bash
git clone https://github.com/asmal95/opencode-docker.git
cd opencode-docker
bash deploy.sh
```

### Option 2: Manual Setup

1. **Clone and configure**
```bash
git clone https://github.com/asmal95/opencode-docker.git
cd opencode-docker
cp .env.prebuilt.example .env
nano .env
```

2. **Start the service**
```bash
docker compose -f docker-compose.prebuilt.yaml up -d
```

3. **Verify**
```bash
docker compose -f docker-compose.prebuilt.yaml logs -f
```

## Docker Images

- `asmal95/opencode-platform:latest` — OpenCode AI server
- `asmal95/telegram-bot:latest` — Telegram bot sidecar

## Requirements

- Docker 20.10+
- Docker Compose 2.0+
- Telegram bot token
- AI API key (any OpenAI-compatible provider)
- Server password (`OPENCODE_SERVER_PASSWORD`)

## Environment Variables

```bash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=your_api_key_here
OPENCODE_SERVER_PASSWORD=your_server_password_here
```

## Server Authentication

OpenCode server uses HTTP Basic Auth to protect the API:

```bash
OPENCODE_SERVER_PASSWORD=your-strong-password
```

## Management

```bash
docker compose -f docker-compose.prebuilt.yaml logs -f telegram-bot
docker compose -f docker-compose.prebuilt.yaml restart
docker compose -f docker-compose.prebuilt.yaml down
docker compose -f docker-compose.prebuilt.yaml pull
docker compose -f docker-compose.prebuilt.yaml up -d
```

## Architecture

```
Telegram User --> Telegram Bot (aiogram) --> OpenCode API (port 4096, Basic Auth) --> AI Provider
```

## Features

- Telegram bot interface
- OpenCode AI integration
- Persistent storage (volumes)
- Auto-restart on failure
- Production-ready
- HTTP Basic Auth protection
- Minimal configuration required

## Security

- Non-root containers
- Isolated network
- Read-only filesystem
- No-new-privileges enabled
- Server authentication (Basic Auth)
- API keys in environment variables only

## License

MIT
