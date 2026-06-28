# Quick VPS Deployment - 5 Steps

## Step 1: SSH to your VPS

```bash
ssh root@your-vps-ip
```

## Step 2: Install Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
newgrp docker
```

## Step 3: Create project directory and files

```bash
mkdir -p ~/opencode-bot/configs/bot
cd ~/opencode-bot
```

Create `docker-compose.yaml`:

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

  telegram-bot:
    image: asmal95/telegram-bot:latest
    depends_on:
      - opencode
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      OPENCODE_API_URL: http://opencode:4096
      OPENCODE_SERVER_PASSWORD: ${OPENCODE_SERVER_PASSWORD}
    restart: unless-stopped

volumes:
  opencode-data:
  opencode-cache:
EOF
```

## Step 4: Configure environment

Create `.env` file:

```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=your_api_key_here
OPENCODE_SERVER_PASSWORD=your_server_password_here
EOF
```

Create provider configuration:

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

Edit configuration:

```bash
nano .env
nano configs/bot/opencode.jsonc
```

Replace with your actual values.

## Step 5: Start and verify

```bash
docker compose up -d
docker compose logs -f
```

Press Ctrl+C to stop watching logs.

## Management Commands

```bash
docker compose ps
docker compose logs -f telegram-bot
docker compose restart
docker compose down
```

## Provider Setup Examples

### OpenRouter (Cloud)

```bash
# .env
OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1
OPENAI_COMPATIBLE_API_KEY=sk-or-xxx
```

### Local Ollama

```bash
# .env
OPENAI_COMPATIBLE_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_COMPATIBLE_API_KEY=sk-not-needed
```

### Local Network Ollama

```bash
# .env
OPENAI_COMPATIBLE_BASE_URL=http://192.168.1.100:11434/v1
OPENAI_COMPATIBLE_API_KEY=sk-not-needed
```

## Troubleshooting

```bash
# Check if containers are running
docker compose ps

# View all logs
docker compose logs

# Test provider connectivity
docker exec opencode curl http://host.docker.internal:11434/v1/models

# Test OpenCode API (with auth)
curl -u "opencode:your-password" http://localhost:4096/global/health

# Restart everything
docker compose restart
```
