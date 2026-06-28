# Quick Start Guide - Telegram Bot on VPS

## Prerequisites

- Ubuntu/Debian VPS with at least 2GB RAM and 20GB disk
- Docker and Docker Compose installed
- Telegram Bot Token
- AI API key (any OpenAI-compatible provider)
- Server password (for OpenCode Basic Auth)

## Installation Steps

### 1. Install Docker on VPS

```bash
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo apt install docker-compose-plugin -y
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Create Project Directory

```bash
mkdir -p ~/opencode-bot/configs/bot
cd ~/opencode-bot
```

### 3. Create docker-compose.yaml

```bash
cat > docker-compose.yaml << 'EOF'
services:
  opencode:
    image: asmal95/opencode-platform:latest
    command: ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
    ports:
      - "4096:4096"
    environment:
      OPENCODE_DISABLE_AUTOUPDATE: "true"
      OPENCODE_DISABLE_MODELS_FETCH: "true"
      OPENCODE_DISABLE_SHARE: "true"
      OPENCODE_SERVER_PASSWORD: ${OPENCODE_SERVER_PASSWORD}
      OPENAI_COMPATIBLE_BASE_URL: ${OPENAI_COMPATIBLE_BASE_URL:-}
      OPENAI_COMPATIBLE_API_KEY: ${OPENAI_COMPATIBLE_API_KEY:-}
    volumes:
      - opencode-data:/home/coder/.local/share/opencode
      - opencode-cache:/home/coder/.cache/opencode
      - ./configs/bot:/opt/opencode-config:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
    networks:
      - opencode-net

  telegram-bot:
    image: asmal95/telegram-bot:latest
    depends_on:
      - opencode
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      OPENCODE_API_URL: http://opencode:4096
      OPENCODE_SERVER_PASSWORD: ${OPENCODE_SERVER_PASSWORD}
    restart: unless-stopped
    networks:
      - opencode-net

volumes:
  opencode-data:
    driver: local
  opencode-cache:
    driver: local

networks:
  opencode-net:
    driver: bridge
EOF
```

### 4. Create .env file

```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=your_api_key_here
OPENCODE_SERVER_PASSWORD=your_server_password_here
EOF
```

### 5. Create Provider Configuration

```bash
cat > configs/bot/opencode.jsonc << 'EOF'
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
EOF
```

### 6. Edit .env with your credentials

```bash
nano .env
```

Replace with your actual values.

### 7. Start the containers

```bash
docker compose up -d
```

### 8. Check the logs

```bash
docker compose logs opencode
docker compose logs telegram-bot
docker compose logs -f
```

### 9. Verify

```bash
docker compose ps
```

Expected output:

```
NAME                            IMAGE                          STATUS
opencode-bot-opencode-1         asmal95/opencode-platform:latest   Up
opencode-bot-telegram-bot-1     asmal95/telegram-bot:latest        Up
```

## Server Authentication

OpenCode server uses HTTP Basic Auth:

```bash
OPENCODE_SERVER_PASSWORD=your-strong-password
```

- Username: `opencode` (default)
- All API requests require auth header
- The Telegram bot automatically includes it

## Provider Examples

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

## Managing the Service

```bash
docker compose down
docker compose restart
docker compose logs -f telegram-bot
docker compose pull
docker compose up -d
```

## Troubleshooting

### Bot not responding

```bash
docker compose logs telegram-bot
docker compose logs opencode
```

### Authentication error (401)

```bash
# Check password is set in both containers
docker compose -f docker-compose.prebuilt.yaml exec opencode env | grep SERVER_PASSWORD
docker compose -f docker-compose.prebuilt.yaml logs telegram-bot | grep -i "401\|auth"
```

### Provider connection issues

```bash
# From host
curl http://host.docker.internal:11434/v1/models

# From container
docker exec opencode curl http://host.docker.internal:11434/v1/models

# Check config
docker exec opencode cat /opt/opencode-config/opencode.jsonc
```

### Permission issues

```bash
sudo chown -R $USER:$USER ~/opencode-bot
```

### Clean restart (removes all data)

```bash
docker compose down -v
docker compose up -d
```

## Security Notes

1. Keep `.env` file private — don't commit to git
2. Use strong server password for `OPENCODE_SERVER_PASSWORD`
3. Use strong API tokens
4. Consider restricting access to port 4096 via firewall
5. Regularly update Docker and images
6. Monitor logs and resource usage

## Additional Documentation

- **Custom Providers**: `docs/CUSTOM_PROVIDERS.md`
- **Cheat Sheet**: `docs/PROVIDER_CHEATSHEET.md`
- **Main README**: `README.md`
