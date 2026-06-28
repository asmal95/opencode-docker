# AGENTS.md

Instructions for AI agents working on this project.

## Project Overview

Docker deployment platform for [OpenCode AI](https://opencode.ai) with Telegram Bot interface. Works with any OpenAI-compatible API (OpenRouter, Ollama, llama.cpp, etc.).

### Architecture

```
Telegram User --> Telegram Bot (aiogram) --> OpenCode (port 4096, Basic Auth) --> AI Provider
```

Two containers on shared Docker network `opencode-net`:
- **opencode** — AI processing engine
- **telegram-bot** — Telegram interface

## Key Files

| File | Purpose |
|------|---------|
| `docker-compose.deploy.yaml` | Production deployment (DockerHub images) |
| `docker-compose.ollama.yaml` | Prebuilt + `host.docker.internal` support |
| `docker-compose.yaml` | Local build from Dockerfile |
| `docker-compose.override-bot.yaml` | Override for bot scenario (used with `docker-compose.yaml`) |
| `Dockerfile` | Base image (opencode-base stage) |
| `Dockerfile.full` | Extended image with dev tools |
| `configs/bot/opencode.jsonc` | Provider config — single `openai-compatible` provider |
| `configs/base/opencode.jsonc` | Minimal placeholder config |
| `sidecars/telegram-bot/` | Bot sidecar (Python/aiogram) |
| `entrypoint.sh` | UID/GID mapping + privilege dropping |

## Deployment Modes

| Command | Use when |
|---------|----------|
| `docker compose -f docker-compose.deploy.yaml up -d` | Use DockerHub images (fastest) |
| `docker compose -f docker-compose.ollama.yaml up -d` | Need `host.docker.internal` (Ollama on same machine) |
| `docker compose -f docker-compose.yaml -f docker-compose.override-bot.yaml up -d` | Build from source |

## Configuration

### Environment Variables

Required:
- `TELEGRAM_BOT_TOKEN` — Telegram bot token from @BotFather
- `OPENCODE_SERVER_PASSWORD` — HTTP Basic Auth password for OpenCode server

Provider:
- `OPENAI_COMPATIBLE_BASE_URL` — e.g. `https://api.openrouter.ai/v1` or `http://host.docker.internal:11434/v1`
- `OPENAI_COMPATIBLE_API_KEY` — API key (may be empty for local Ollama)

Optional:
- `PUID`/`PGID` — user/group ID mapping (default: 1000)
- `PROJECT_DIR` — workspace mount path (default: `.`)
- `SCENARIO` — config scenario (default: `base`, use `bot` for full config)

### Provider Config

The config uses `{env:VAR}` syntax for env var injection:

```jsonc
{
  "providers": {
    "openai-compatible": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "{env:OPENAI_COMPATIBLE_BASE_URL}",
        "apiKey": "{env:OPENAI_COMPATIBLE_API_KEY}"
      }
    }
  }
}
```

### .env Files

- `.env` — active environment
- `.env.example` — template (copy to `.env`)


Never commit `.env`.

## Important Constraints

1. **No Anthropic/OpenRouter-specific config** — provider is unified via `openai-compatible`
2. **No dead code** — remove unused variables, files, and documentation
3. **Security** — all compose files use `read_only: true`, `no-new-privileges`, minimal `cap_add`
4. **Server auth** — OpenCode protected by `OPENCODE_SERVER_PASSWORD` (HTTP Basic Auth)
5. **Bot sends auth header** — `message_handler.py` includes `Authorization: Basic` header
6. **API paths** — OpenCode uses singular paths: `/session`, `/session/{id}/message`
7. **Docker images** — push to `asmal95/opencode-platform` and `asmal95/telegram-bot` on DockerHub
8. **Windows dev** — LF/CRLF warnings are normal on Windows, no action needed

## Common Tasks

### Update Docker images
```bash
docker build -t asmal95/opencode-platform:latest -f Dockerfile .
docker build -t asmal95/telegram-bot:latest -f sidecars/telegram-bot/Dockerfile sidecars/telegram-bot
docker push asmal95/opencode-platform:latest
docker push asmal95/telegram-bot:latest
```

### Change provider
Edit `configs/bot/opencode.jsonc` or set env vars. For local Ollama:
```bash
OPENAI_COMPATIBLE_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_COMPATIBLE_API_KEY=
```

### Quick VPS deploy
```bash
bash deploy.sh
```

## Do Not

- Do not add Anthropic, OpenRouter-specific provider config
- Do not add hardcoded IPs (use env vars)
- Do not remove security settings (read_only, no-new-privileges, cap_drop/add)
- Do not add GITHUB_TOKEN/GITHUB_REPO (removed as dead code)
- Do not remove OPENCODE_SERVER_PASSWORD auth
- Do not commit .env files
