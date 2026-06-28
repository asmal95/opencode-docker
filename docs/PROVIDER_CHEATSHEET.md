# Provider Configuration Cheat Sheet

## Quick Setup

### OpenRouter (Cloud)

```bash
# .env
TELEGRAM_BOT_TOKEN=123456:ABC-DEFxxx
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=sk-or-xxx
```

### Ollama (Local)

```bash
# .env
TELEGRAM_BOT_TOKEN=123456:ABC-DEFxxx
OPENAI_COMPATIBLE_BASE_URL=http://host.docker.internal:11434/v1
```

### llama.cpp / LM Studio

```bash
# .env
TELEGRAM_BOT_TOKEN=123456:ABC-DEFxxx
OPENAI_COMPATIBLE_BASE_URL=http://YOUR_IP:PORT/v1
```

## Provider Templates

### OpenAI-Compatible Provider

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

## Network Quick Reference

| Scenario | baseURL Example |
|----------|----------------|
| Linux localhost | `http://host.docker.internal:PORT` |
| Docker bridge IP | `http://172.17.0.1:PORT` |
| Specific IP | `http://192.168.x.x:PORT` |
| Host networking | Use `localhost` directly |

### Get Docker Bridge IP

```bash
ip addr show docker0
# Look for "inet" line, e.g., 172.17.0.1
```

### Enable host.docker.internal

Add to docker-compose.yaml:

```yaml
services:
  opencode:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

## Quick Commands

### Test Provider Connection

```bash
# From host
curl http://YOUR_IP:PORT/v1/models

# From inside container
docker exec opencode curl http://YOUR_IP:PORT/v1/models
```

### Troubleshoot

```bash
# Check container to host connectivity
docker exec opencode ping host.docker.internal

# Check DNS
docker exec opencode nslookup google.com

# View container network
docker network inspect opencode-bot_opencode-net
```

## Environment Variables Reference

### Required
```bash
TELEGRAM_BOT_TOKEN=your_bot_token
```

### Provider Configuration
```bash
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=sk-or-xxx
```

## Quick Deployment Checklist

- [ ] Docker installed and running
- [ ] Docker Compose installed
- [ ] Telegram bot created and token obtained
- [ ] AI provider configured
- [ ] Network connectivity verified
- [ ] Environment variables set in `.env`
- [ ] `docker compose -f docker-compose.prebuilt.yaml up -d` successful
- [ ] Container status: `Up`
- [ ] Bot responds to test message

## File Structure

```
opencode-bot/
├── docker-compose.yaml
├── .env
└── configs/
    └── bot/
        └── opencode.jsonc
```

## Minimal Setup

```bash
# Create directory structure
mkdir -p configs/bot

# Create docker-compose.yaml
docker compose -f docker-compose.prebuilt.yaml up -d

# Start everything
docker compose -f docker-compose.prebuilt.yaml up -d
docker compose -f docker-compose.prebuilt.yaml logs -f
```
