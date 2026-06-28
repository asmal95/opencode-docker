# Custom Provider Configuration Guide

This guide shows how to configure any OpenAI-compatible AI provider for your OpenCode Telegram Bot.

## Supported Providers

Any API with an OpenAI-compatible endpoint works:

| Provider | Type | BASE_URL Example |
|----------|------|-----------------|
| OpenRouter | Cloud API | `https://api.openrouter.ai/v1` |
| Ollama | Local | `http://host.docker.internal:11434/v1` |
| llama.cpp | Local | `http://YOUR_IP:8080/v1` |
| LM Studio | Local | `http://localhost:1234/v1` |

## Quick Setup

### Step 1: Create Configuration

Edit `configs/bot/opencode.jsonc`:

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
  },
  "permissions": {
    "write": "allow",
    "edit": "allow",
    "bash": "ask"
  },
  "tools": {
    "allowed": ["*"]
  },
  "headless": true
}
```

### Step 2: Update Environment Variables

Edit `.env`:

```bash
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=your_api_key_here
OPENCODE_SERVER_PASSWORD=your_server_password_here
```

### Step 3: Deploy

```bash
docker compose -f docker-compose.prebuilt.yaml up -d
```

## Server Authentication

OpenCode server uses HTTP Basic Auth to protect the API:

```bash
# Default username is "opencode", change with OPENCODE_SERVER_USERNAME
OPENCODE_SERVER_PASSWORD=your-strong-password
```

The Telegram bot automatically includes `Authorization: Basic <credentials>` header.

## Network Configuration

### Docker Container to Host

**Problem**: Containers can't access host via `localhost`.

**Solutions**:

#### Option 1: host.docker.internal (recommended)
```yaml
services:
  opencode:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```
```jsonc
"baseURL": "http://host.docker.internal:11434/v1"
```

#### Option 2: Docker bridge IP
```bash
ip addr show docker0
# Look for "inet" — usually 172.17.0.1
```
```jsonc
"baseURL": "http://172.17.0.1:11434/v1"
```

#### Option 3: Host network mode
```yaml
services:
  opencode:
    network_mode: "host"
```

#### Option 4: Container-to-container
If your AI provider runs in Docker too:
```jsonc
"baseURL": "http://ollama:11434/v1"
```

## Examples

### OpenRouter (Cloud)

```bash
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=sk-or-xxx
```

### Ollama (Local VPS)

```bash
OPENAI_COMPATIBLE_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_COMPATIBLE_API_KEY=sk-not-needed
```

### Ollama (Local Network)

```bash
OPENAI_COMPATIBLE_BASE_URL=http://192.168.1.100:11434/v1
OPENAI_COMPATIBLE_API_KEY=sk-not-needed
```

### llama.cpp

```bash
OPENAI_COMPATIBLE_BASE_URL=http://YOUR_IP:8080/v1
OPENAI_COMPATIBLE_API_KEY=sk-not-needed
```

## Troubleshooting

### Authentication Error (401)

```bash
# Check server password is set
docker compose -f docker-compose.prebuilt.yaml exec opencode env | grep SERVER_PASSWORD

# Check bot config has the password
docker compose -f docker-compose.prebuilt.yaml logs telegram-bot
```

### Connection Refused

```bash
# From host
curl http://YOUR_BASE_URL/v1/models

# From container
docker exec opencode curl http://YOUR_BASE_URL/v1/models
```

### Provider Not Available

```bash
# Check JSON syntax
cat configs/bot/opencode.jsonc

# Restart containers
docker compose -f docker-compose.prebuilt.yaml restart

# Check logs
docker compose -f docker-compose.prebuilt.yaml logs opencode
```

## Best Practices

1. **Security**: Never commit `.env` to git
2. **Server Password**: Use a strong random password for `OPENCODE_SERVER_PASSWORD`
3. **Testing**: Test provider connectivity before deploying
4. **Monitoring**: Monitor provider usage and costs
5. **Updates**: Keep Docker images updated

## Additional Resources

- [Ollama Documentation](https://github.com/ollama/ollama)
- [OpenRouter Documentation](https://openrouter.ai/docs)
- [OpenCode Server Docs](https://opencode.ai/docs/server/)
- [Docker Networking](https://docs.docker.com/network/)
