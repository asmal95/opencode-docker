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

### 3. Start

```bash
docker compose -f docker-compose.prebuilt.yaml up -d
```

### 4. Verify

```bash
docker compose -f docker-compose.prebuilt.yaml logs -f
```

Your Telegram bot should respond to messages.

## AI Provider Setup

The bot works with any OpenAI-compatible API. Examples:

| Provider | BASE_URL |
|----------|----------|
| OpenRouter | `https://api.openrouter.ai/v1` |
| Ollama (local) | `http://host.docker.internal:11434/v1` |
| llama.cpp | `http://YOUR_IP:8080/v1` |
| LM Studio | `http://localhost:1234/v1` |

## Deployment Options

| File | Use case |
|------|----------|
| `docker-compose.prebuilt.yaml` | Prebuilt images from DockerHub (recommended) |
| `docker-compose.quickstart.yaml` | Prebuilt + `host.docker.internal` support |
| `docker-compose.yaml` | Build from source |

## Management

```bash
# Logs
docker compose -f docker-compose.prebuilt.yaml logs -f

# Restart
docker compose -f docker-compose.prebuilt.yaml restart

# Stop
docker compose -f docker-compose.prebuilt.yaml down

# Update
docker compose -f docker-compose.prebuilt.yaml pull
docker compose -f docker-compose.prebuilt.yaml up -d
```

## Auto Deployment

```bash
bash deploy.sh
```

## Architecture

```
Telegram User --> Telegram Bot (aiogram) --> OpenCode (port 4096) --> AI Provider
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
- API keys in environment variables only

## License

MIT
