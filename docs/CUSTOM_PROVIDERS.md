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
```

### Step 3: Deploy

```bash
docker compose -f docker-compose.prebuilt.yaml up -d
```

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

### Connection Refused

```bash
# From host
curl YOUR_BASE_URL/models

# From container
docker exec opencode curl YOUR_BASE_URL/models
```

### Invalid API Key

```bash
# Check environment variables
docker compose -f docker-compose.prebuilt.yaml exec opencode env | grep OPENAI

# Check config
docker exec opencode cat /opt/opencode-config/opencode.jsonc
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
2. **Testing**: Test provider connectivity before deploying
3. **Monitoring**: Monitor provider usage and costs
4. **Updates**: Keep Docker images updated

## Additional Resources

- [Ollama Documentation](https://github.com/ollama/ollama)
- [OpenRouter Documentation](https://openrouter.ai/docs)
- [OpenCode Configuration](https://opencode.ai/docs)
- [Docker Networking](https://docs.docker.com/network/)
